"""Prueba de apertura/ejecución 3MF: sólo M73 y M400, sin movimiento ni calor."""
import json
import sys
import time
import uuid
import zipfile
import io
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from plotter import gcode, printer


def main():
    link = printer.PrinterLink()
    events = []
    original = link._on_message
    last = {}
    def receive(client, userdata, message):
        original(client, userdata, message)
        current = {k: link.status.get(k) for k in ('gcode_state', 'mc_percent', 'print_error', 'layer_num')}
        if current != last:
            events.append(dict(current, at=round(time.monotonic(), 2)))
            last.clear()
            last.update(current)
    link._on_message = receive
    try:
        link.ensure(app.config['printer'])
        if not link.wait_connected() or not link.wait_status():
            raise ValueError('No se obtuvo conexión y estado reciente.')
        if link.status.get('gcode_state') not in ('IDLE', 'FINISH'):
            raise ValueError('La impresora no está libre. No se envió el diagnóstico.')
        text = gcode.build_diagnostic_gcode(app.config)
        if '--pause' in sys.argv:
            text = text.replace('M400 S6', 'M400 U1', 1)
        filename = 'pluma_diagnostico_' + uuid.uuid4().hex[:8] + '.gcode.3mf'
        data = gcode.build_3mf(text, [], app.config, 12)
        if '--reference-package' in sys.argv:
            output = io.BytesIO()
            with zipfile.ZipFile(app.ROOT / '.diagnostic-reference.3mf') as reference, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
                for info in reference.infolist():
                    blob = reference.read(info.filename)
                    if info.filename == 'Metadata/plate_1.gcode':
                        blob = text.encode('utf-8')
                    elif info.filename == 'Metadata/plate_1.gcode.md5':
                        blob = hashlib.md5(text.encode('utf-8')).hexdigest().encode()
                    target.writestr(info.filename, blob)
            data = output.getvalue()
        printer.upload(app.config['printer'], filename, data)
        events.clear()
        if '--ftp' in sys.argv:
            original_command = link.command
            def with_ftp(payload, **kwargs):
                payload['url'] = 'ftp:///' + filename
                return original_command(payload, **kwargs)
            link.command = with_ftp
        if '--native-sd' in sys.argv:
            original_command = link.command
            def with_sd(payload, **kwargs):
                payload.pop('url', None)
                payload['md5'] = 'from_sd_card'
                return original_command(payload, **kwargs)
            link.command = with_sd
        ack = link.start_print(filename, 'diagnostico_sin_movimiento')
        end = time.monotonic() + 24
        next_push = 0
        while time.monotonic() < end:
            if time.monotonic() >= next_push:
                link.client.publish(link._topic('request'), json.dumps({'pushing': {'sequence_id': '0', 'command': 'pushall'}}))
                next_push = time.monotonic() + 2
            if '--pause' in sys.argv and link.status.get('gcode_state') == 'PAUSE' and link.status.get('subtask_name') == 'diagnostico_sin_movimiento':
                link.command({'command': 'stop', 'param': ''})
                break
            time.sleep(0.2)
        result = {'file': filename,
                  'ack': {k: ack.get(k) for k in ('command', 'sequence_id', 'result', 'reason', 'err_code')} if ack else None,
                  'events': events, 'final': link.snapshot()}
        Path('diagnostico-a1.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=True))
    finally:
        link.close()


if __name__ == '__main__':
    main()
