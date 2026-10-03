"""Obtiene sólo el bloque de configuración de un archivo A1 ya presente en microSD."""
import io
import re
import ssl
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from plotter.gcode import normalize_pen_config
from plotter.printer import _ImplicitFTPS, USER

p = app.config['printer']
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
ftp = _ImplicitFTPS(context=ctx, timeout=15)
try:
    ftp.connect(p['ip'], 990)
    ftp.login(USER, p['access_code'])
    ftp.prot_p()
    ftp.cwd('cache')
    ftp.voidcmd('TYPE I')
    candidates = sorted((ftp.size(n), n) for n in ftp.nlst() if n.endswith('.3mf'))
    data = io.BytesIO()
    ftp.retrbinary('RETR ' + candidates[0][1], data.write)
finally:
    ftp.close()
with zipfile.ZipFile(data) as archive:
    text = archive.read('Metadata/plate_1.gcode').decode('utf-8')
(app.ROOT / '.diagnostic-reference.3mf').write_bytes(data.getvalue())
config = text.split('; CONFIG_BLOCK_START', 1)[1].split('; CONFIG_BLOCK_END', 1)[0]
lines = []
for line in config.splitlines():
    if not line.startswith('; '):
        continue
    key, sep, value = line[2:].partition(' = ')
    if not sep:
        continue
    if key == 'printer_model' and value != 'Bambu Lab A1':
        raise ValueError('La referencia no es una A1.')
    # Las plantillas de impresión no tienen lugar en un perfil de pluma.
    if 'gcode' in key and key != 'gcode_flavor':
        value = ''
    if 'temp' in key and not any(s in key for s in ('range', 'formula', 'difference')):
        # Sólo campos térmicos numéricos, mantiene la cantidad de extrusores.
        if re.fullmatch(r'[\d.,;\- ]+', value):
            value = re.sub(r'-?\d+(?:\.\d+)?', '0', value)
    lines.append(f'; {key} = {value}')
target = app.ROOT / 'plotter' / 'a1_reference_config.txt'
target.write_text('\n'.join(normalize_pen_config(lines)) + '\n', encoding='utf-8')
print('Configuración A1 preparada: un solo PLA de referencia, plantillas vacías y temperaturas cero.')
