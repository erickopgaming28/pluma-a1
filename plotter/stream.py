"""Dibujo local por MQTT con confirmación de ejecución, sin archivos de impresión.

Un ACK sólo confirma aceptación. Hasta cuatro paquetes consecutivos forman
una unidad de hasta 20 s nominales. En modo continuo el marcador intermedio
acredita avance del intérprete, NO fin físico: no vacía la cola con M400.
Pausas, cancelación, rutinas aisladas y fin requieren M400 y marcador fresco.
Los valores 200/201 son marcadores comprobados por proyectos de la comunidad,
no una garantía publicada por Bambu. El diagnóstico de la app debe probarlos.
"""
from dataclasses import dataclass
import math
import re
import threading
import time


MAX_BYTES = 512
MAX_LINES = 24
MAX_BLOCK_SECONDS = 20.0
MAX_GROUP_PACKETS = 4
LOOKAHEAD_SECONDS = 12.0
_BARRIER_SIZE = len("M400\nM1002 gcode_claim_action : 200\n".encode("ascii"))
_NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
_COMMAND = re.compile(r"^(G(?:0|1|28|29(?:\.2)?)|M(?:104|140|106|83|220|17|204|400|73))(?:\s|$)")
_PARAM = re.compile(rf"^([A-Z])({_NUMBER})$")


@dataclass(frozen=True)
class _Block:
    lines: tuple
    long: bool = False
    seconds: float = 0.0
    isolated: bool = False


@dataclass(frozen=True)
class _Pause:
    message: str


@dataclass(frozen=True)
class _Layer:
    number: int


def _parameters(command, tokens):
    values = {}
    for token in tokens:
        if command == "G28" and token in ("X", "Y", "Z"):
            letter, value = token, 0.0
        else:
            match = _PARAM.fullmatch(token)
            if not match:
                raise ValueError(f"Parámetro no permitido en dibujo directo: {token}.")
            letter, value = match.group(1), float(match.group(2))
        if letter in values or not math.isfinite(value):
            raise ValueError("El programa contiene parámetros repetidos o no finitos.")
        if letter == "E":
            raise ValueError("El dibujo directo no permite extrusión.")
        values[letter] = value
    return values


def _check_command(command, params):
    allowed = {
        "G0": "XYZF", "G1": "XYZF", "G28": "XYZ", "G29": "AXYIJ",
        "G29.2": "S", "M104": "S", "M140": "S", "M106": "PS",
        "M83": "", "M220": "S", "M17": "", "M204": "S",
        "M400": "SPU", "M73": "LPR",
    }
    if any(key not in allowed[command] for key in params):
        raise ValueError(f"Parámetros no permitidos para {command}.")
    for axis in "XYZ":
        if axis in params and not 0 <= params[axis] <= 256:
            raise ValueError(f"El movimiento {axis} queda fuera de 0 a 256 mm.")
    if "F" in params and params["F"] <= 0:
        raise ValueError("La velocidad del movimiento debe ser positiva.")
    if command in ("G0", "G1") and not params:
        raise ValueError("El movimiento no contiene coordenadas ni velocidad.")
    if command == "G28" and any(value != 0 for value in params.values()):
        raise ValueError("El homing no admite coordenadas de destino.")
    if command in ("M104", "M140") and params != {"S": 0.0}:
        raise ValueError("El dibujo directo requiere la boquilla y la cama en frío.")
    if command == "G29.2" and params not in ({"S": 0.0}, {"S": 1.0}):
        raise ValueError("La compensación de cama requiere S0 o S1.")
    if command == "G29":
        if set(params) != set("AXYIJ") or params["A"] != 1:
            raise ValueError("La nivelación requiere el área A1 X Y I J generada por la app.")
        if params["I"] <= 0 or params["J"] <= 0 or params["X"] + params["I"] > 256.01 or params["Y"] + params["J"] > 256.01:
            raise ValueError("El área de nivelación queda fuera de la cama.")
    if command == "M106":
        if "S" not in params or not 0 <= params["S"] <= 255 or ("P" in params and (not params["P"].is_integer() or not 0 <= params["P"] <= 3)):
            raise ValueError("El ventilador requiere S entre 0 y 255 y un canal válido.")
    if command == "M220" and (set(params) != {"S"} or not 0 < params["S"] <= 100):
        raise ValueError("La velocidad global debe estar entre 1 y 100 por ciento.")
    if command == "M204" and (set(params) != {"S"} or not 0 < params["S"] <= 6000):
        raise ValueError("La aceleración debe ser positiva y no superar 6000.")
    if command == "M400":
        if len(params) > 1 or ("U" in params and params["U"] != 1) or ("S" in params and not 0 <= params["S"] <= 40) or ("P" in params and not 0 <= params["P"] <= 40000):
            raise ValueError("Espera no permitida en dibujo directo.")
    if command == "M73":
        if not params or ("L" in params and (len(params) != 1 or not params["L"].is_integer() or params["L"] < 1)):
            raise ValueError("La capa de pluma debe ser un número entero positivo.")
        if "P" in params and not 0 <= params["P"] <= 100:
            raise ValueError("Progreso inválido en el programa.")
        if "R" in params and params["R"] < 0:
            raise ValueError("Tiempo restante inválido en el programa.")


def compile_program(text):
    """Valida todo antes de enviar y devuelve eventos y cantidad de comandos.

    Sólo acepta el bloque ejecutable de nuestros archivos generados. G90 se
    admite como modo absoluto; G91 y todos los comandos ajenos se rechazan.
    """
    lines = str(text).splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == "; EXECUTABLE_BLOCK_START"]
    ends = [i for i, line in enumerate(lines) if line.strip() == "; EXECUTABLE_BLOCK_END"]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError("El programa requiere un único bloque ejecutable generado por la app.")
    events, pending = [], []
    count = 0
    pending_seconds = 0.0
    position = {axis: None for axis in "XYZ"}
    feed = None
    speed_factor = 1.0

    def flush(isolated=False):
        nonlocal count, pending_seconds
        if pending:
            events.append(_Block(tuple(pending), seconds=pending_seconds, isolated=isolated))
            count += len(pending)
            pending.clear()
            pending_seconds = 0.0

    def append(line, seconds=0.0):
        nonlocal pending_seconds
        if not math.isfinite(seconds) or not 0 <= seconds <= MAX_BLOCK_SECONDS:
            raise ValueError("El movimiento supera el tiempo de un bloque seguro. Aumenta la velocidad de posicionamiento o dibujo.")
        line_bytes = len(line.encode("ascii")) + 1
        if line_bytes + _BARRIER_SIZE > MAX_BYTES:
            raise ValueError("Una línea del programa supera el tamaño permitido.")
        current_bytes = sum(len(item.encode("ascii")) + 1 for item in pending)
        if len(pending) + 1 > MAX_LINES - 2 or current_bytes + line_bytes + _BARRIER_SIZE > MAX_BYTES or pending_seconds + seconds > MAX_BLOCK_SECONDS:
            flush()
        pending.append(line)
        pending_seconds += seconds

    def number(value):
        return f"{value:.6f}".rstrip("0").rstrip(".") or "0"

    for raw in lines[starts[0] + 1:ends[0]]:
        code, _, comment = raw.partition(";")
        code = " ".join(code.strip().upper().split())
        if not code:
            continue
        if code == "G90":
            command, params = "G90", {}
        else:
            match = _COMMAND.match(code)
            if not match:
                raise ValueError(f"Comando no permitido en dibujo directo: {code.split()[0]}.")
            command = match.group(1)
            params = _parameters(command, code.split()[1:])
            _check_command(command, params)
        if command == "M73":
            if "L" in params:
                flush()
                events.append(_Layer(int(params["L"])))
            continue  # progreso/tiempo generado no acredita movimiento terminado
        if command == "M400" and "U" in params:
            flush()
            events.append(_Pause(comment.strip().removeprefix("PAUSA:").strip() or "Ajusta la pluma y pulsa Reanudar."))
            continue
        if len(code.encode("ascii")) + 1 + _BARRIER_SIZE > MAX_BYTES:
            raise ValueError("Una línea del programa supera el tamaño permitido.")
        if command in ("G28", "G29"):
            flush()
            events.append(_Block((code,), long=True, isolated=True))
            count += 1
            # Las rutinas de firmware pueden mover varios ejes y modificar el
            # avance modal. No inventar su posición o velocidad al terminar.
            position = {axis: None for axis in "XYZ"}
            feed = None
            continue
        if command == "M220":
            speed_factor = params["S"] / 100.0
        if command in ("G0", "G1"):
            if "F" in params:
                feed = params["F"]
            axes = [axis for axis in "XYZ" if axis in params]
            if not axes:
                append(code)
                continue
            if feed is None:
                raise ValueError("El primer desplazamiento después del inicio o nivelación debe indicar su velocidad F.")
            mm_second = feed / 60.0 * speed_factor
            unknown = any(position[axis] is None for axis in axes)
            if unknown:
                # La peor distancia desde cualquier punto de la cama hasta el
                # destino. No subdividir sin conocer el origen del segmento.
                distance = math.sqrt(sum(max(params[axis], 256.0 - params[axis]) ** 2 for axis in axes))
                seconds = distance / mm_second
                if not math.isfinite(seconds) or seconds > MAX_BLOCK_SECONDS:
                    raise ValueError("El primer posicionamiento es demasiado lento desde una posición desconocida. Usa una velocidad de posicionamiento mayor antes de dibujar.")
                flush()
                append(code, seconds)
                flush(isolated=True)  # el posicionamiento sin origen conocido va solo
            else:
                origin = {axis: position[axis] for axis in axes}
                distance = math.sqrt(sum((params[axis] - origin[axis]) ** 2 for axis in axes))
                seconds = distance / mm_second
                if not math.isfinite(seconds) or seconds / 19.0 > 10000:
                    raise ValueError("La velocidad del dibujo es demasiado baja para dividir el movimiento en bloques seguros.")
                # Un segundo de margen evita que el redondeo del punto intermedio
                # agote los 20 s del presupuesto. El último extremo queda exacto.
                fragments = max(1, math.ceil(seconds / 19.0))
                previous = dict(origin)
                for index in range(1, fragments + 1):
                    target = {axis: params[axis] if index == fragments else float(number(origin[axis] + (params[axis] - origin[axis]) * index / fragments)) for axis in axes}
                    duration = math.sqrt(sum((target[axis] - previous[axis]) ** 2 for axis in axes)) / mm_second
                    if index == fragments:
                        line = code  # conserva exactamente el destino original
                    else:
                        line = command + " " + " ".join(axis + number(target[axis]) for axis in axes)
                        if "F" in params:
                            line += " " + next(token for token in code.split()[1:] if token.startswith("F"))
                    append(line, duration)
                    previous = target
            for axis in axes:
                position[axis] = params[axis]
            continue
        if command == "M400" and ("S" in params or "P" in params):
            unit = "S" if "S" in params else "P"
            seconds = params[unit] / (1000.0 if unit == "P" else 1.0)
            pieces = max(1, math.ceil(seconds / MAX_BLOCK_SECONDS))
            for _ in range(pieces):
                duration = seconds / pieces
                append(f"M400 {unit}{number(duration * (1000.0 if unit == 'P' else 1.0))}", duration)
            continue
        append(code)
    flush()
    if not count:
        raise ValueError("No hay comandos de dibujo para enviar.")
    return tuple(events), count


def _group_events(events):
    """Agrupa transporte sin cruzar pausas, capas ni rutinas aisladas."""
    grouped, pending = [], []
    seconds = 0.0

    def flush():
        nonlocal seconds
        if pending:
            grouped.append(tuple(pending))
            pending.clear()
            seconds = 0.0

    for event in events:
        if not isinstance(event, _Block):
            flush()
            grouped.append(event)
        elif event.isolated or event.long:
            flush()
            grouped.append((event,))
        else:
            if len(pending) >= MAX_GROUP_PACKETS or seconds + event.seconds > MAX_BLOCK_SECONDS:
                flush()
            pending.append(event)
            seconds += event.seconds
    flush()
    return tuple(grouped)


class _Canceled(Exception):
    pass


class DirectJob:
    """Un trabajo por vez; pausa/cancelación drenan los paquetes ya aceptados.

    start(text, title, link, z_up=None) valida y arranca un hilo daemon.
    active permanece True durante envío, espera, pausa y cancelación pendiente.
    Nunca reintenta un movimiento cuando falta su ACK o su marcador de ejecución.
    """

    def __init__(self, block_timeout=45.0, long_timeout=180.0, poll_interval=0.05,
                 lookahead_seconds=LOOKAHEAD_SECONDS, clock=None):
        self.block_timeout = min(45.0, float(block_timeout))
        self.long_timeout = min(180.0, float(long_timeout))
        self.poll_interval = max(0.001, float(poll_interval))
        if self.block_timeout <= 0 or self.long_timeout <= 0:
            raise ValueError("El tiempo de espera debe ser positivo.")
        self.lookahead_seconds = float(lookahead_seconds)
        if not math.isfinite(self.lookahead_seconds) or not 0 < self.lookahead_seconds <= 60:
            raise ValueError('El adelanto de envío debe estar entre 0 y 60 segundos.')
        self._clock = clock or time.monotonic
        self._buffer_until = 0.0
        self._continuous = False
        self._condition = threading.Condition()
        self._active = False
        self._pause_requested = False
        self._stop_requested = False
        self._pause_message = ""
        self._thread = None
        self._data = {"state": "", "percent": 0, "job": "", "message": "", "layer": 0}

    @property
    def active(self):
        with self._condition:
            return self._active

    def snapshot(self):
        with self._condition:
            return dict(self._data, active=self._active,
                        queued_seconds=round(max(0.0, self._buffer_until - self._clock()), 1))

    def start(self, text, title, link, z_up=None, continuous=False):
        events, total = compile_program(text)
        if not isinstance(continuous, bool):
            raise ValueError('El modo continuo debe ser verdadero o falso.')
        if z_up is not None:
            z_up = float(z_up)
            if not math.isfinite(z_up) or not 0.5 <= z_up <= 256:
                raise ValueError("La altura para levantar la pluma debe estar entre 0.5 y 256 mm.")
        if not link.connected:
            raise ValueError("Conecta la impresora antes de dibujar por comandos.")
        with self._condition:
            if self._active:
                raise ValueError("Ya hay un dibujo directo activo.")
            self._active = True
            self._continuous = continuous
            self._buffer_until = self._clock()
            self._pause_requested = self._stop_requested = False
            self._pause_message = ""
            self._data = {"state": "PREPARE", "percent": 0, "executed_percent": 0, "continuous": continuous,
                          "job": str(title).replace("\n", " ").replace("\r", " ")[:120], "message": "Preparando comandos directos.", "layer": 0}
            self._thread = threading.Thread(target=self._run, args=(events, total, link, link.key, z_up), daemon=True, name="pluma-a1-directo")
            self._thread.start()
            return dict(self._data, active=True)

    def control(self, action):
        if action not in ("pause", "resume", "stop"):
            raise ValueError("Control directo desconocido.")
        with self._condition:
            if not self._active:
                raise ValueError("No hay un dibujo directo activo.")
            if action == "stop":
                self._stop_requested = True
                self._pause_requested = False
                self._data.update(state="STOPPING", message="Cancelación solicitada; esperando a que termine el recorrido ya aceptado por la A1.")
            elif action == "pause":
                if self._stop_requested:
                    raise ValueError("La cancelación ya está en curso.")
                self._pause_requested = True
                self._pause_message = "Dibujo en pausa. Pulsa Reanudar para continuar."
                self._data["message"] = "Pausa solicitada; espera a que termine el recorrido ya aceptado por la A1."
            elif not self._stop_requested:
                self._pause_requested = False
                self._pause_message = ""
                self._data.update(state="RUNNING", message="Continuando el dibujo por comandos.")
            self._condition.notify_all()
            return dict(self._data, active=self._active)

    def _health(self, link, key):
        if not link.connected or link.key != key:
            raise RuntimeError("Se perdió o cambió la conexión. La ejecución quedó sin confirmar; no se reenviaron movimientos.")
        error = link.status.get("print_error", 0)
        if error not in (None, "", 0, "0"):
            raise RuntimeError(f"La impresora informó un error ({error}). No se enviaron más movimientos.")
        if "gcode_state" in link.status and str(link.status["gcode_state"]).upper() not in ("IDLE", "FINISH"):
            raise RuntimeError("La impresora inició o mantiene otro trabajo. Se detuvo el envío de comandos directos; revisa la A1.")

    def _gate(self, link, key):
        while True:
            self._health(link, key)
            with self._condition:
                if self._stop_requested:
                    raise _Canceled()
                if not self._pause_requested:
                    self._data["state"] = "RUNNING"
                    return
                self._data.update(state="PAUSE", message=self._pause_message or "Pulsa Reanudar para continuar.")
                self._condition.wait(self.poll_interval)

    @staticmethod
    def _buffer_cost(block):
        # Presupuesto de alimentación, no prueba de movimiento físico.
        # Los segmentos cortos requieren margen para aceleración y procesado.
        moves = sum(line.split()[0] in ('G0', 'G1') for line in block.lines)
        return max(block.seconds * 1.5, block.seconds + moves * 0.025)

    def _pace(self, block, link, key):
        """Alimenta una ventana corta sin insertar M400 entre grupos normales."""
        # Un paquete largo puede superar la ventana: se admite con reserva,
        # sin esperar a vaciarla y provocar otra parada entre trazos.
        allowance = max(self.lookahead_seconds * .25,
                        self.lookahead_seconds - self._buffer_cost(block))
        while self._buffer_until - self._clock() > allowance:
            self._health(link, key)
            with self._condition:
                if self._pause_requested or self._stop_requested:
                    return False
                self._data.update(state='RUNNING', phase='feeding', message='Dibujando; alimentando el recorrido continuo sin adelantar toda la imagen.')
                self._condition.wait(min(self.poll_interval, max(0.001, self._buffer_until - self._clock() - allowance)))
        return True

    def _send_payload(self, payload, link, key, cost=0.0):
        self._health(link, key)
        sent_at = self._clock()
        remaining = max(0.0, self._buffer_until - sent_at) if self._continuous else 0.0
        # El firmware puede aplazar el ACK si su cola está ocupada. No reenviar.
        ack = link.send_gcode(payload, wait=max(6.0, remaining + cost + 6.0) if self._continuous else 6.0)
        self._health(link, key)
        if not isinstance(ack, dict) or str(ack.get("result", "")).lower() != "success" or ack.get("err_code", 0) not in (0, "0", None):
            reason = ack.get("reason", "sin respuesta") if isinstance(ack, dict) else "sin respuesta"
            raise RuntimeError(f"La impresora no confirmó los comandos ({reason}). La ejecución quedó incierta; no se reintentó.")
        if self._continuous:
            self._buffer_until = max(self._buffer_until, sent_at) + cost

    def _send_block(self, block, link, key, marker, drain=True):
        version = link.stage_version
        barrier = ("M400",) if drain else ()
        payload = "\n".join((*block.lines, *barrier, f"M1002 gcode_claim_action : {marker}", ""))
        cost = self._buffer_cost(block) if self._continuous and not block.long else 0.0
        self._send_payload(payload, link, key, cost)
        remaining = max(0.0, self._buffer_until - self._clock()) if self._continuous else 0.0
        timeout = self.long_timeout if block.long else self.block_timeout + remaining
        deadline = self._clock() + timeout
        with self._condition:
            self._data['phase'] = 'waiting_finish' if drain else 'feeding'
            if drain and self._continuous:
                self._data['message'] = 'Esperando a que la A1 termine los movimientos aceptados y confirme el final.'
        next_push = 0.0
        while True:
            self._health(link, key)
            try:
                matching = int(link.status.get("stg_cur", -1)) == marker
            except (ValueError, TypeError):
                matching = False
            if link.stage_version > version and matching:
                if drain:
                    self._buffer_until = self._clock()
                return
            now = self._clock()
            if now >= deadline:
                raise RuntimeError("No llegó la confirmación de ejecución del bloque. Puede seguir en movimiento; no se enviaron más comandos.")
            if now >= next_push and callable(getattr(link, "request_status", None)):
                link.request_status()
                next_push = now + 1.0
            # Una pausa o cancelación pendiente no cancela la barrera: la cola
            # ya enviada debe confirmarse antes de levantar o enviar otro bloque.
            with self._condition:
                self._condition.wait(min(self.poll_interval, deadline - now))

    def _send_group(self, blocks, link, key, marker, drain=True):
        """Devuelve paquetes acreditados y si quedó confirmado el fin físico.

        Los ACK se esperan en serie. Si se pide pausa/cancelación, no se publica
        otro cuerpo: una barrera sola drena lo aceptado y deja el resto pendiente.
        Un ACK perdido/rechazado sale por excepción sin publicar nada más.
        """
        accepted = []
        for index, block in enumerate(blocks):
            self._health(link, key)
            with self._condition:
                interrupted = self._pause_requested or self._stop_requested
            if not interrupted and self._continuous and not drain:
                interrupted = not self._pace(block, link, key)
            if interrupted:
                if accepted:
                    self._send_block(_Block(()), link, key, marker)
                return tuple(accepted), bool(accepted)
            if index == len(blocks) - 1:
                self._send_block(block, link, key, marker, drain=drain)
            else:
                self._send_payload("\n".join((*block.lines, "")), link, key,
                                   self._buffer_cost(block) if self._continuous else 0.0)
            accepted.append(block)
        return tuple(accepted), drain

    def _run(self, events, total, link, key, z_up):
        completed = 0
        known_z = None  # inferencia de comandos confirmados, no posición medida
        settled = False
        pending_lines = 0  # sólo resumen; no crece una lista con toda la imagen
        accepted_z = None  # se convierte en known_z sólo después de M400
        executed = 0

        def credit_execution():
            nonlocal known_z, executed, settled, pending_lines
            executed += pending_lines
            known_z = accepted_z
            pending_lines = 0
            settled = True
            with self._condition:
                self._data['executed_percent'] = min(99, int(executed * 100 / total))

        def drain_pending():
            nonlocal marker
            if pending_lines:
                self._send_block(_Block(()), link, key, marker)
                marker = 401 - marker
                credit_execution()

        def gate():
            with self._condition:
                interrupted = self._pause_requested or self._stop_requested
            if interrupted:
                drain_pending()
            self._gate(link, key)

        try:
            marker = 201 if str(link.status.get("stg_cur")) == "200" else 200
            for event in _group_events(events):
                gate()
                if isinstance(event, _Layer):
                    drain_pending()
                    with self._condition:
                        self._data["layer"] = event.number
                    continue
                if isinstance(event, _Pause):
                    drain_pending()
                    with self._condition:
                        self._pause_requested = True
                        self._pause_message = event.message
                    gate()
                    continue
                remaining = event
                isolated = any(block.isolated or block.long for block in event)
                if isolated:
                    drain_pending()
                while remaining:
                    gate()
                    accepted, drained = self._send_group(remaining, link, key, marker,
                                                         drain=not self._continuous or isolated)
                    if not accepted:
                        continue  # _gate procesa la solicitud antes de publicar
                    marker = 401 - marker
                    for block in accepted:
                        pending_lines += len(block.lines)
                        for line in block.lines:
                            if line.split()[0] in ('G28', 'G29'):
                                accepted_z = None
                            else:
                                for token in line.split()[1:]:
                                    if token.startswith('Z'):
                                        accepted_z = float(token[1:])
                    settled = False
                    if drained:
                        credit_execution()
                    remaining = remaining[len(accepted):]
                    completed += sum(len(block.lines) for block in accepted)
                    with self._condition:
                        self._data["percent"] = min(99, int(completed * 100 / total))
                        self._data.update(sent_lines=completed, total_lines=total)
                        if not self._stop_requested and not self._pause_requested:
                            self._data["message"] = ('Transmitiendo el recorrido continuo. El fin se confirma al terminar.'
                                                     if self._continuous and not drained else 'Grupo ejecutado y confirmado por la impresora.')
            drain_pending()
            self._health(link, key)
            with self._condition:
                if self._stop_requested:
                    raise _Canceled()
                self._data.update(state="FINISH", percent=100, executed_percent=100,
                                  message="Dibujo terminado; el fin del movimiento fue confirmado por la impresora.")
        except _Canceled:
            try:
                if settled and z_up is not None and known_z is not None:
                    # Nunca bajar desde una altura ya superior ni mover tras una
                    # pérdida de ACK/conexión/marcador.
                    target_z = max(z_up, known_z)
                    self._send_block(_Block(("G90", f"G1 Z{target_z:.2f} F600")), link, key, marker)
                    message = "Dibujo cancelado después de confirmar el bloque y levantar la pluma."
                else:
                    message = "Dibujo cancelado; no se enviaron más bloques. Revisa la altura de la pluma."
                with self._condition:
                    self._data.update(state="CANCELED", message=message)
            except Exception as exc:
                with self._condition:
                    self._data.update(state="FAILED", message=str(exc))
        except Exception as exc:
            with self._condition:
                self._data.update(state="FAILED", message=str(exc))
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()
