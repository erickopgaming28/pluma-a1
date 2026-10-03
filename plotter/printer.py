"""Conexión por red local con la Bambu Lab A1: búsqueda (SSDP), subida (FTPS) y control (MQTT)."""
import ftplib
import io
import json
import queue
import socket
import ssl
import threading
import time
import random
import uuid

import paho.mqtt.client as mqtt

USER = "bblp"  # usuario fijo de las Bambu en LAN; la contraseña es el código de acceso


def print_error_message(code):
    try:
        number = int(code or 0)
    except (ValueError, TypeError):
        return 'La A1 comunica un error. Revisa su pantalla.'
    if not number:
        return ''
    hex_code = f'{number:08X}'
    label = hex_code[:4] + '-' + hex_code[4:]
    if number == 0x05004004:
        return f'La A1 indica equipo ocupado ({label}). En su pantalla termina o cancela el trabajo anterior y cierra el aviso antes de enviar otro.'
    if number == 0x05004003:
        return f'La A1 no pudo leer el archivo ({label}). Usa el archivo actualizado y revisa su pantalla.'
    return f'La A1 comunica el error {label}. Revisa su pantalla antes de enviar otro trabajo.'


def discover(seconds=6.0):
    """Escucha los anuncios que las impresoras Bambu emiten en la red local."""
    found = {}
    socks = []
    for port in (2021, 1990):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", port))
            s.settimeout(0.4)
            socks.append(s)
        except OSError:
            pass
    end = time.time() + seconds
    while time.time() < end and socks:
        for s in socks:
            try:
                data, addr = s.recvfrom(4096)
            except OSError:
                continue
            head = {}
            for line in data.decode("utf-8", "ignore").split("\r\n"):
                k, _, v = line.partition(":")
                head[k.strip().lower()] = v.strip()
            serial = head.get("usn")
            if serial and "bambu" in head.get("nt", "") + "".join(head):
                found[serial] = {
                    "serial": serial,
                    "ip": head.get("location") or addr[0],
                    "name": head.get("devname.bambu.com", ""),
                    "model": head.get("devmodel.bambu.com", ""),
                }
    for s in socks:
        s.close()
    return list(found.values())


class _ImplicitFTPS(ftplib.FTP_TLS):
    """FTPS implícito (puerto 990) reutilizando la sesión TLS, como exige la impresora."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, session=self.sock.session)
        return conn, size

    def storbinary(self, cmd, fp, blocksize=32768, callback=None, rest=None):
        # igual que el original pero sin conn.unwrap(), que se queda colgado con la A1
        self.voidcmd("TYPE I")
        with self.transfercmd(cmd, rest) as conn:
            while buf := fp.read(blocksize):
                conn.sendall(buf)
        return self.voidresp()


def upload(p, filename, data):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ftp = _ImplicitFTPS(context=ctx, timeout=20)
    ftp.connect(p["ip"], 990)
    try:
        ftp.login(USER, p["access_code"])
        ftp.prot_p()
        ftp.storbinary(f"STOR {filename}", io.BytesIO(data))
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


class PrinterLink:
    """Mantiene una conexión MQTT con la impresora y su último estado conocido."""

    def __init__(self):
        self.client = None
        self.key = None
        self.connected = False
        self.error = ""
        self.status = {}
        self.acks = queue.Queue()
        self.lock = threading.Lock()
        self.command_lock = threading.Lock()
        self.sequence = random.randint(20000, 28000)
        self.last_status = 0
        self.stage_version = 0
        self.status_version = 0

    def ensure(self, p):
        key = (p.get("ip"), p.get("serial"), p.get("access_code"))
        if not all(key):
            self.close()
            return False
        with self.lock:
            if key == self.key and self.client:
                return True
            self._close()
            self.key, self.status, self.error = key, {}, ""
            self.last_status = 0
            self.status_version = 0
            self.stage_version = 0
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"pluma-a1-{uuid.uuid4().hex[:12]}")
            c.username_pw_set(USER, key[2])
            c.tls_set(cert_reqs=ssl.CERT_NONE)
            c.tls_insecure_set(True)
            c.on_connect = self._on_connect
            c.on_disconnect = self._on_disconnect
            c.on_message = self._on_message
            c.reconnect_delay_set(2, 20)
            try:
                c.connect_async(key[0], 8883, keepalive=30)
                c.loop_start()
            except Exception as e:
                self.error = f"No se pudo conectar: {e}"
                return False
            self.client = c
        return True

    def _close(self):
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
        self.client, self.key, self.connected = None, None, False

    def close(self):
        with self.lock:
            self._close()

    def _topic(self, kind):
        return f"device/{self.key[1]}/{kind}"

    def _on_connect(self, client, userdata, flags, rc, props=None):
        if rc.is_failure:
            self.connected = False
            self.error = "La impresora rechazó la conexión: revisa el código de acceso y el número de serie."
            return
        self.connected, self.error = True, ""
        client.subscribe(self._topic("report"))
        client.publish(self._topic("request"), json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))

    def _on_disconnect(self, client, userdata, flags, rc, props=None):
        self.connected = False
        self.last_status = 0

    def _on_message(self, client, userdata, msg):
        try:
            d = json.loads(msg.payload)
        except ValueError:
            return
        pr = d.get("print")
        if isinstance(pr, dict):
            if pr.get("command") in ("project_file", "gcode_file", "gcode_line", "pause", "resume", "stop", "calibration") and "result" in pr:
                self.acks.put(pr)
                return  # aceptar una orden no cambia el estado real del trabajo
            self.status.update(pr)  # la A1 manda sólo los campos que cambian
            if 'stg_cur' in pr:
                self.stage_version += 1
            if 'gcode_state' in pr:
                self.last_status = time.monotonic()
                self.status_version += 1

    def wait_connected(self, seconds=6.0):
        end = time.time() + seconds
        while time.time() < end and not self.connected and not self.error:
            time.sleep(0.1)
        return self.connected

    def command(self, payload, wait=6.0):
        with self.command_lock:
            return self._command(payload, wait)

    def wait_status(self, seconds=6.0):
        version = self.status_version
        self.request_status()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.connected and self.status_version > version:
                return True
            time.sleep(0.1)
        return False

    def request_status(self):
        if self.client and self.connected:
            self.client.publish(self._topic('request'), json.dumps({'pushing': {'sequence_id': '0', 'command': 'pushall'}}))

    def _command(self, payload, wait=6.0):
        """Publica una orden y espera la respuesta de la impresora (o None si no contesta)."""
        if not self.client or not self.connected:
            raise ValueError('No hay conexión con la impresora. Reconecta antes de enviar una orden.')
        while not self.acks.empty():
            self.acks.get_nowait()
        self.sequence += 1
        sequence = str(self.sequence)
        # QoS 0 evita reentrega automática de un movimiento relativo (QoS 1 es at-least-once).
        info = self.client.publish(self._topic("request"), json.dumps({"print": dict(payload, sequence_id=sequence)}), qos=0)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ValueError('No se pudo entregar la orden. Revisa el estado de la A1 antes de volver a intentarlo.')
        end = time.monotonic() + wait
        while time.monotonic() < end:
            try:
                ack = self.acks.get(timeout=max(0.001, end - time.monotonic()))
            except queue.Empty:
                return None
            if ack.get('command') == payload['command'] and str(ack.get('sequence_id')) == sequence:
                return ack
        return None

    def send_gcode(self, text, wait=6.0):
        """Comandos de consola de Studio; el ACK sólo indica aceptación, no fin físico."""
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 4096:
            raise ValueError('El bloque de comandos está vacío o es demasiado grande.')
        return self.command({'command': 'gcode_line', 'param': text.rstrip() + '\n'}, wait=wait)

    def start_gcode_file(self, filename):
        """Ruta de compatibilidad para G-code puro en microSD, sin metadatos de material."""
        return self.command({'command': 'gcode_file', 'param': filename})

    def send_gcode_wait(self, text, timeout=45.0):
        """Espera una barrera M400 y telemetría nueva; no reintenta movimientos inciertos.

        Los marcadores 200/201 son una comprobación de compatibilidad empírica del
        firmware, no un ACK de ejecución publicado por Bambu.
        """
        marker = 201 if str(self.status.get('stg_cur')) == '200' else 200
        version, key = self.stage_version, self.key
        command = text.rstrip() + f'\nM400\nM1002 gcode_claim_action : {marker}\n'
        ack = self.send_gcode(command)
        if ack is None:
            raise ValueError('No hubo respuesta al comando. No se reenviará: comprueba la A1 antes de continuar.')
        if str(ack.get('result', '')).lower() != 'success':
            raise ValueError('La A1 rechazó el comando' + (f" ({ack.get('reason')})" if ack.get('reason') else '')
                             + '. Revisa Modo solo LAN y Modo desarrollador.')
        end, next_push = time.monotonic() + timeout, 0
        while time.monotonic() < end:
            if not self.connected or self.key != key:
                raise ValueError('Se perdió la conexión. La ejecución quedó sin confirmar; revisa la A1.')
            if self.status.get('print_error'):
                raise ValueError(print_error_message(self.status['print_error']))
            if self.status.get('gcode_state', 'IDLE') not in ('IDLE', 'FINISH'):
                raise ValueError('La A1 inició un trabajo de impresión. No se enviarán más comandos manuales.')
            if self.stage_version > version and self.status.get('stg_cur') == marker:
                return command, ack
            if time.monotonic() >= next_push:
                self.request_status()
                next_push = time.monotonic() + 1
            time.sleep(0.1)
        raise ValueError('La A1 aceptó el comando, pero no confirmó que llegó al final de la cola. '
                         'No se enviarán más movimientos hasta comprobar la impresora.')

    def start_print(self, filename, title):
        return self.command({
            "command": "project_file",
            "param": "Metadata/plate_1.gcode",
            "project_id": "0", "profile_id": "0", "task_id": "0", "subtask_id": "0",
            "subtask_name": title,
            "file": filename,
            "url": f"ftp:///{filename}",
            "md5": "",
            "timelapse": False,
            "bed_type": "auto",
            "bed_leveling": False,
            "flow_cali": False,
            "vibration_cali": False,
            "layer_inspect": False,
            # Un material de referencia, asignado al soporte externo de la A1.
            # [] omite la tabla; 254/255 no son IDs válidos en la tabla plana.
            # https://synman.github.io/bambu-printer-manager/mqtt-protocol-reference/
            "ams_mapping": [-1],
            "ams_mapping2": [{"ams_id": 255, "slot_id": 0}],
            "use_ams": False,
        })

    def level_bed(self):
        """Nivelación de cama propia de la impresora (la misma del menú Calibración).
        option es una máscara de bits: 2 = sólo nivelación de cama."""
        return self.command({"command": "calibration", "option": 2})

    def snapshot(self):
        s = self.status
        return {
            "connected": self.connected,
            "error": self.error,
            "state": s.get("gcode_state", ""),
            "percent": s.get("mc_percent"),
            "remaining": s.get("mc_remaining_time"),
            "job": s.get("subtask_name", ""),
            "file": s.get("gcode_file", ""),
            "layer": s.get("layer_num"),
            "print_error": s.get('print_error', 0),
            "print_error_message": print_error_message(s.get('print_error', 0)),
        }
