"""Pluma A1: servidor local. Convierte texto e imágenes en trazos de pluma para la Bambu Lab A1."""
import copy
import io
import json
import re
import socket
import threading
import time
import unicodedata
import uuid
import webbrowser
from urllib.request import urlopen
from urllib.parse import urlsplit
from pathlib import Path

import numpy as np
from flask import Flask, jsonify, request, send_file
from werkzeug.serving import ThreadedWSGIServer

from plotter import fonts, gcode, handwriting, pathops, printer, sketch, stream, conversion

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
PORT = 8765
APP_VERSION = '2026.10.03.14'
WORKSPACE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, str(ROOT).casefold()))

DEFAULT_CONFIG = {
    "printer": {"ip": "", "serial": "", "access_code": "", "name": "", "material": "PLA", "transport": "stream"},
    "paper": {"size": "carta", "width": 215.9, "height": 279.4, "landscape": False,
              "bed_x": 20.0, "bed_y": 0.0, "margin": 12.0},
    "pen": {"offset_x": 0.0, "offset_y": -35.0, "z_down": 3.0, "z_up": 6.0,
            "draw_speed": 40, "travel_speed": 150, "z_speed": 20, "accel": 2500,
            "pause_for_pen": True, "level_bed": True},
    "pens": [
        {"name": "Azul", "color": "#2743b8"},
        {"name": "Negro", "color": "#1d1d22"},
        {"name": "Rojo", "color": "#c8322b"},
        {"name": "Verde", "color": "#2a8a4a"},
    ],
}

app = Flask(__name__, static_folder="static", static_url_path="")
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
dispatch_lock = threading.Lock()
last_dispatch = {}
link = printer.PrinterLink()
direct_job = stream.DirectJob()
image_conversions = conversion.ConversionQueue()
direct_job_kind = ''
stream_failure_cleared = False
direct_command = {}
images = {}  # id -> RGB
jobs = {}    # id -> {"title", "pages": [[{"pen","name","color","paths"}]]}
renders = {}  # id de elemento -> {pluma: trazos medidos desde la esquina del elemento}


def load_config():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if isinstance(cfg.get(k), dict) and isinstance(v, dict):
                    cfg[k].update(v)
                elif k in cfg:
                    cfg[k] = v
        except ValueError:
            pass
    return cfg


config = load_config()


def public_config():
    """La configuración que ve la interfaz: el código de acceso nunca sale del programa."""
    cfg = copy.deepcopy(config)
    cfg["printer"]["has_code"] = bool(cfg["printer"].pop("access_code", ""))
    return cfg


def lan_url():
    """Dirección para abrir la app desde otro dispositivo de la misma red (p. ej. el celular)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.255.255", 9))  # no envía nada: sólo elige la interfaz de red local
            return f"http://{s.getsockname()[0]}:{PORT}"
    except OSError:
        return ""


def geometry():
    return {"paper": gcode.paper_dims(config["paper"]), "reach": gcode.reach(config), "drawable": gcode.drawable(config)}


def job_signature(cfg=None):
    cfg = cfg if cfg is not None else config
    return json.dumps(dict({k: cfg[k] for k in ('paper', 'pen', 'pens')}, material=cfg['printer'].get('material', 'PLA')), sort_keys=True)


@app.before_request
def guard_origin():
    if request.method == 'POST':
        origin = request.headers.get('Origin')
        if origin and urlsplit(origin).netloc != request.host:
            raise ValueError('Abre la app directamente desde su dirección local para realizar esta acción.')


def pen_info(i, cfg=None):
    pens = (config if cfg is None else cfg)["pens"]
    return pens[min(i, len(pens) - 1)]


def make_job(pages, title):
    """pages: lista de {pluma: trazos}. Guarda el trabajo y devuelve su versión para la interfaz."""
    job_id = uuid.uuid4().hex[:10]
    stored, out = [], []
    for page in pages:
        layers, view = [], []
        total = {"draw_mm": 0.0, "travel_mm": 0.0, "lifts": 0}
        for i in sorted(page):
            paths = [p for p in page[i] if len(p) > 1]
            if not paths:
                continue
            info = pen_info(i)
            layers.append({"pen": i, "name": info["name"], "color": info["color"], "paths": paths})
            st = pathops.stats(paths)
            for k in total:
                total[k] += st[k]
            view.append({"pen": i, "name": info["name"], "color": info["color"],
                         "paths": pathops.to_flat(paths), "draw_mm": round(st["draw_mm"])})
        total["seconds"] = gcode.estimate_seconds(total, config["pen"]) if layers else 0
        stored.append(layers)
        out.append({"layers": view, "stats": total})
    jobs[job_id] = {"title": title, "pages": stored, "signature": job_signature()}
    while len(jobs) > 8:
        jobs.pop(next(iter(jobs)))
    return {"job": job_id, "pages": out, "geometry": geometry()}


def safe_name(text):
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")[:24]
    return t or "dibujo"


def font_sample(font):
    pages = handwriting.render_text("Hola", font, {"size": 10, "human": 0, "seed": 1}, 500, 50)
    paths = pages[0].get(0, [])
    if not paths:
        return {"d": "", "box": [0, 0, 10, 10]}
    allp = np.vstack(paths)
    x0, y0 = allp.min(0) - 1
    x1, y1 = allp.max(0) + 1
    d = " ".join("M" + " L".join(f"{x - x0:.1f} {y - y0:.1f}" for x, y in p) for p in paths)
    return {"d": d, "box": [0, 0, round(float(x1 - x0), 1), round(float(y1 - y0), 1)]}


@app.errorhandler(Exception)
def on_error(e):
    if isinstance(e, ValueError):
        return jsonify({"error": str(e)}), 400
    code = getattr(e, "code", 500)
    return jsonify({"error": f"Error interno: {e}"}), code if isinstance(code, int) else 500


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get('/api/health')
def api_health():
    """Identifica la aplicación abierta sin conectar a la impresora."""
    return jsonify(app_id='pluma-a1', workspace_id=WORKSPACE_ID, app_version=APP_VERSION)


@app.get("/api/state")
def api_state():
    font_list = []
    for fid, label in fonts.CATALOG:
        font_list.append({"id": fid, "label": label, "sample": font_sample(fonts.get_font(fid))})
    return jsonify({"config": public_config(), "fonts": font_list, "geometry": geometry(),
                    'app_version': APP_VERSION, 'features': {'async_images': True, 'motion_settings': True},
                    "paper_sizes": gcode.PAPER_SIZES, "lan_url": lan_url()})


@app.post("/api/config")
def api_config():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        raise ValueError('Los ajustes deben ser un objeto.')
    if dispatch_lock.locked() or direct_job.active:
        raise ValueError('Espera a que termine el movimiento o cancela el trabajo antes de cambiar los ajustes.')
    candidate = copy.deepcopy(config)
    printer_in = data.get("printer")
    if isinstance(printer_in, dict):
        printer_in.pop("has_code", None)
        if not printer_in.get("access_code"):
            printer_in.pop("access_code", None)  # vacío = conservar el código ya guardado
    for k in ("printer", "paper", "pen"):
        if isinstance(data.get(k), dict):
            candidate[k].update({key: value for key, value in data[k].items() if key in DEFAULT_CONFIG[k]})
    if isinstance(data.get("pens"), list) and data["pens"]:
        candidate["pens"] = [{"name": str(p.get("name", "Pluma")).replace('\n', ' ').replace('\r', ' ')[:20], "color": str(p.get("color", "#000000"))}
                          for p in data["pens"][:8]]
    if any(not re.fullmatch(r'#[0-9a-fA-F]{6}', p['color']) for p in candidate['pens']):
        raise ValueError('Usa colores de pluma válidos.')
    gcode.validate_config(candidate)
    temporary = CONFIG_FILE.with_suffix('.tmp')
    temporary.write_text(json.dumps(candidate, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(CONFIG_FILE)
    config.clear()
    config.update(candidate)
    return jsonify({"config": public_config(), "geometry": geometry()})


@app.post('/api/motion-settings')
def api_motion_settings():
    """Guarda velocidades del próximo programa, sin tocar la calibración ni mover."""
    data = request.get_json(force=True)
    allowed = {'draw_speed', 'travel_speed', 'z_speed', 'accel'}
    if not isinstance(data, dict) or not data or set(data) - allowed:
        raise ValueError('Sólo se permiten velocidades de dibujo, viaje, elevación y aceleración.')
    if not dispatch_lock.acquire(blocking=False):
        raise ValueError('Espera a que termine la preparación del envío para guardar la velocidad.')
    try:
        candidate = copy.deepcopy(config)
        candidate['pen'].update(data)
        gcode.validate_config(candidate)
        temporary = CONFIG_FILE.with_suffix('.tmp')
        temporary.write_text(json.dumps(candidate, indent=2, ensure_ascii=False), encoding='utf-8')
        temporary.replace(CONFIG_FILE)
        config['pen'].update(data)
        return jsonify(pen=config['pen'], next_job_only=True,
                       message='Velocidades guardadas para el próximo dibujo. El trabajo en curso conserva su velocidad.')
    finally:
        dispatch_lock.release()


def _area():
    x0, y0, x1, y1 = gcode.drawable(config)
    if x1 - x0 < 10 or y1 - y0 < 10:
        raise ValueError("La hoja queda fuera del alcance de la pluma. Revisa la posición de la hoja en Ajustes.")
    return x0, y0, x1 - x0, y1 - y0


@app.post("/api/text")
def api_text():
    d = request.get_json(force=True)
    o = d.get("opts", {})
    x0, y0, w, h = _area()
    multi = d.get("color_mode") == "multi"
    pen = int(d.get("pen", 0))
    font = fonts.get_font(o.get("font"))
    pages = handwriting.render_text(d.get("text", ""), font, o, w, h,
                                    n_pens=len(config["pens"]) if multi else 1, pen=0 if multi else pen)
    off = np.array([x0, y0])
    pages = [{k: [p + off for p in v] for k, v in page.items()} for page in pages]
    title = safe_name(re.sub(r"\{\d\}", "", d.get("text", ""))[:40])
    return jsonify(make_job(pages, title))


@app.post("/api/image/upload")
def api_image_upload():
    f = request.files.get("file")
    if not f:
        raise ValueError("No llegó ninguna imagen.")
    try:
        rgb = sketch.load_image(f.read())
    except Exception:
        raise ValueError("No pude abrir ese archivo como imagen.")
    image_id = uuid.uuid4().hex[:10]
    images[image_id] = {"rgb": rgb, "name": Path(f.filename or "imagen").stem}
    while len(images) > 12:
        images.pop(next(iter(images)))
    return jsonify({"id": image_id, "width": rgb.shape[1], "height": rgb.shape[0]})


@app.get('/api/image/<image_id>/preview')
def api_image_preview(image_id):
    item = images.get(image_id)
    if not item:
        return jsonify(error='Vuelve a cargar la imagen: el programa ya no conserva el original.'), 404
    from PIL import Image
    data = io.BytesIO()
    Image.fromarray(item['rgb']).save(data, format='PNG')
    data.seek(0)
    return send_file(data, mimetype='image/png', max_age=0)


@app.post("/api/image")
def api_image():
    d = request.get_json(force=True)
    o = d.get("opts", {})
    item = images.get(d.get("id"))
    if not item:
        raise ValueError("Vuelve a cargar la imagen.")
    rgb = item["rgb"]
    x0, y0, w, h = _area()
    cropped = sketch.crop_image(rgb, o.get('crop'))
    aspect = cropped.shape[0] / cropped.shape[1]
    width = min(w, h / aspect) * float(o.get("scale", 90)) / 100.0
    multi = d.get("color_mode") == "multi"
    layers, height = sketch.make_sketch(rgb, o, width, [p["color"] for p in config["pens"]] if multi else None)
    oy = {"top": 0.0, "bottom": h - height}.get(o.get("valign"), (h - height) / 2)
    off = np.array([x0 + (w - width) / 2, y0 + oy])
    pen = int(d.get("pen", 0))
    page = {(k if multi else pen): [p + off for p in v] for k, v in layers.items()}
    return jsonify(make_job([page], safe_name(item["name"])))


@app.post("/api/element")
def api_element():
    """Convierte un elemento (cuadro de texto o imagen) en trazos medidos desde su propia esquina,
    para que la interfaz pueda moverlo por la hoja sin volver a convertirlo."""
    return jsonify(_convert_element(request.get_json(force=True)))


@app.post('/api/element/tasks')
def api_element_task():
    d = request.get_json(force=True)
    if d.get('type') != 'image':
        raise ValueError('La preparación en segundo plano es para imágenes.')
    cfg = copy.deepcopy(config)
    task = image_conversions.start(lambda progress, cancelled: _convert_element(d, cfg, progress, cancelled))
    return jsonify(task=task), 202


@app.get('/api/element/tasks/<task_id>')
def api_element_task_status(task_id):
    return jsonify(image_conversions.snapshot(task_id))


@app.post('/api/element/tasks/<task_id>/cancel')
def api_element_task_cancel(task_id):
    return jsonify(image_conversions.cancel(task_id))


def _convert_element(d, cfg=None, progress=None, cancelled=None):
    cfg = copy.deepcopy(config) if cfg is None else cfg
    o = d.get("opts", {})
    x0, _, x1, _ = gcode.drawable(cfg)
    area_w = x1 - x0
    multi = d.get("color_mode") == "multi"
    pen = int(d.get("pen", 0))
    w = min(max(float(d.get("w") or area_w), 15.0), 400.0)
    if d.get("type") == "image":
        item = images.get(d.get("image"))
        if not item:
            raise ValueError("Vuelve a cargar la imagen.")
        layers, h = sketch.make_sketch(item["rgb"], o, w, [p["color"] for p in cfg["pens"]] if multi else None,
                                      progress=progress, cancelled=cancelled)
        if not multi:
            layers = {pen: v for v in layers.values()}
    else:
        font = fonts.get_font(o.get("font"))
        # sin límite de alto: el cuadro crece hacia abajo con el texto
        layers = handwriting.render_text(d.get("text", ""), font, o, w, 1e6,
                                         n_pens=len(cfg["pens"]) if multi else 1, pen=0 if multi else pen)[0]
        size = float(o.get("size", 8))
        h = max([float(p[:, 1].max()) for v in layers.values() for p in v] + [size]) + 0.2 * size
    render_id = uuid.uuid4().hex
    renders[render_id] = {'layers': layers, 'signature': job_signature(cfg), 'w': w, 'h': h}
    while len(renders) > 60:
        renders.pop(next(iter(renders)))
    view = []
    for i in sorted(layers):
        info = pen_info(i, cfg)
        view.append({"pen": i, "name": info["name"], "color": info["color"], "paths": pathops.to_flat(layers[i])})
    return {"w": w, "h": h, "layers": view, "render_id": render_id}


@app.post("/api/compose")
def api_compose():
    """Junta los elementos ya convertidos, cada uno en su posición, en un trabajo listo para enviar."""
    d = request.get_json(force=True)
    page = {}
    for it in d.get("items", []):
        rendered = renders.get(str(it.get("render_id")))
        if rendered is None or rendered['signature'] != job_signature():
            raise ValueError("stale")
        layers = rendered['layers']
        values = (it.get('x'), it.get('y'), it.get('rotation', 0))
        if any(isinstance(value, bool) for value in values):
            raise ValueError('Revisa la posición y el ángulo del elemento.')
        try:
            x, y, rotation = map(float, values)
        except (ValueError, TypeError):
            raise ValueError('Revisa la posición y el ángulo del elemento.')
        if not np.isfinite([x, y, rotation]).all():
            raise ValueError('La posición y el ángulo deben ser números finitos.')
        angle = np.deg2rad(rotation % 360)
        c, s = np.cos(angle), np.sin(angle)
        center = np.array([rendered['w'] / 2, rendered['h'] / 2])
        matrix = np.array([[c, -s], [s, c]])
        off = np.array([x, y])
        for i, paths in layers.items():
            page.setdefault(i, []).extend(np.round((p - center) @ matrix.T + center + off, 9) for p in paths)
    x0, y0, x1, y1 = gcode.reach(config)
    pts = [p for v in page.values() for p in v]
    outside = any(p[:, 0].min() < x0 or p[:, 0].max() > x1 or p[:, 1].min() < y0 or p[:, 1].max() > y1
                  for p in pts)
    job = make_job([page], safe_name(d.get("title") or "dibujo"))
    for layer in job["pages"][0]["layers"]:
        layer.pop("paths")  # la interfaz ya tiene los trazos de cada elemento
    job["outside"] = outside
    return jsonify(job)


@app.post("/api/calibration")
def api_calibration():
    """Marca de prueba: una cruz que debe quedar a 30 mm del borde izquierdo y 30 mm del borde inferior."""
    _, ph = gcode.paper_dims(config["paper"])
    cx, cy = 30.0, ph - 30.0
    a = np.array
    paths = [
        a([[cx - 10, cy], [cx + 10, cy]]), a([[cx, cy - 10], [cx, cy + 10]]),
        a([[cx - 5, cy - 5], [cx + 5, cy - 5], [cx + 5, cy + 5], [cx - 5, cy + 5], [cx - 5, cy - 5]]),
    ]
    return jsonify(make_job([{0: paths}], "calibracion"))


@app.post('/api/contact-test')
def api_contact_test():
    """Prepara nueve cruces; no conecta ni mueve la impresora."""
    x0, y0, x1, y1 = gcode.drawable(config)
    inset = min(10., (x1 - x0) / 4, (y1 - y0) / 4)
    radius = min(3., inset / 2)
    paths = []
    for y in np.linspace(y0 + inset, y1 - inset, 3):
        for x in np.linspace(x0 + inset, x1 - inset, 3):
            paths.extend([np.array([[x - radius, y], [x + radius, y]]),
                          np.array([[x, y - radius], [x, y + radius]])])
    return jsonify(make_job([{0: paths}], 'prueba_apoyo_9_zonas'))


def _build(job_id, page, layer, pen_ready=False):
    job = jobs.get(job_id)
    if not job:
        raise ValueError("La vista previa caducó; cambia cualquier ajuste para regenerarla.")
    if job.get('signature') != job_signature():
        raise ValueError('Los ajustes cambiaron. Regenera la vista previa antes de exportar o enviar.')
    try:
        page_index = int(page)
        if page_index < 0:
            raise ValueError()
        layers = job["pages"][page_index]
    except (ValueError, TypeError, IndexError):
        raise ValueError('La página seleccionada no existe.')
    title = job["title"]
    if layer not in (None, "", "all"):
        try:
            layer_index = int(layer)
            if layer_index < 0:
                raise ValueError()
            layers = [layers[layer_index]]
        except (ValueError, TypeError, IndexError):
            raise ValueError('La pluma seleccionada no existe.')
        title += "_" + safe_name(layers[0]["name"])
    text, st = gcode.build_gcode(layers, config, title, bool(pen_ready))
    return text, layers, st, title


@app.get("/api/export")
def api_export():
    text, layers, st, title = _build(request.args.get("job"), request.args.get("page", 0), request.args.get("layer"),
                                     request.args.get("ready") == "1")
    if request.args.get("fmt") == "gcode":
        return send_file(io.BytesIO(text.encode()), as_attachment=True, download_name=f"{title}.gcode", mimetype="text/plain")
    if request.args.get('fmt') == 'svg':
        return send_file(io.BytesIO(gcode.build_svg(layers, config).encode()), as_attachment=True,
                         download_name=f'{title}.svg', mimetype='image/svg+xml')
    data = gcode.build_3mf(text, layers, config, st["seconds"])
    return send_file(io.BytesIO(data), as_attachment=True, download_name=f"{title}.gcode.3mf", mimetype="application/octet-stream")


def _transport(value=None):
    value = value or config['printer'].get('transport', 'stream')
    if value not in ('stream', 'gcode_file', 'project_file'):
        raise ValueError('Forma de envío desconocida.')
    return value


def _printer_available(allow_probe=False):
    if direct_job.active:
        raise ValueError('Hay una transmisión de comandos en curso. Usa sus controles de pausa o cancelación.')
    if not allow_probe and (direct_command.get('phase') == 'unknown' or
                           (direct_job.snapshot().get('state') == 'FAILED' and not stream_failure_cleared)):
        raise ValueError('La ejecución anterior quedó sin confirmar. Revisa la A1 y usa «Comprobar comandos» antes de enviar más movimientos.')
    if not link.ensure(config['printer']):
        raise ValueError('Primero configura IP, número de serie y código de acceso en Ajustes.')
    if not link.wait_connected():
        raise ValueError(link.error or 'No pude conectar con la A1. Revisa que esté encendida y en la misma red.')
    if not link.wait_status():
        raise ValueError('No hay un estado reciente de la A1. Reconecta antes de enviar.')
    if link.status.get('print_error'):
        raise ValueError(printer.print_error_message(link.status['print_error']))
    if link.status.get('gcode_state') not in ('IDLE', 'FINISH'):
        raise ValueError('La A1 no está libre. Termina o cancela el trabajo desde su pantalla.')


def _dispatch(text, layers, seconds, title, transport=None, kind='draw'):
    global direct_job_kind, stream_failure_cleared
    transport = _transport(transport)
    if not dispatch_lock.acquire(blocking=False):
        raise ValueError('Ya hay un envío en curso. Espera a que termine.')
    try:
        last_dispatch.clear()
        last_dispatch.update({'phase': 'sending', 'title': title, 'transport': transport,
                              'message': 'Comprobando conexión para transmitir comandos…' if transport == 'stream' else 'Subiendo archivo y esperando inicio…'})
        _printer_available()
        if transport == 'stream':
            stream.compile_program(text)
            link.send_gcode_wait('M400', timeout=12)
            direct_job_kind, stream_failure_cleared = kind, False
            direct_job.start(text, title, link, z_up=float(config['pen']['z_up']), continuous=True)
            message = 'Transmisión de comandos iniciada. Mantén esta PC encendida y usa las pausas de la app para colocar la pluma.'
            last_dispatch.update({'phase': 'streaming', 'message': message})
        else:
            message = _dispatch_locked(text, layers, seconds, title, transport)
            last_dispatch.update({'phase': 'started', 'message': message})
        return message
    except Exception as error:
        last_dispatch.update({'phase': 'error', 'message': str(error)})
        raise
    finally:
        dispatch_lock.release()


def _dispatch_locked(text, layers, seconds, title, transport='project_file'):
    """Sube el trabajo a la impresora y lo inicia. Devuelve el mensaje para la interfaz."""
    p = config["printer"]
    if not link.ensure(p):
        raise ValueError("Primero configura la impresora (IP, número de serie y código de acceso) en Ajustes.")
    if not link.wait_connected():
        raise ValueError(link.error or "No pude conectar con la impresora. ¿Está encendida y en la misma red?")
    if not link.wait_status():
        raise ValueError('No hay un estado reciente de la impresora. Reconecta antes de enviar.')
    if link.status.get("gcode_state") in ("RUNNING", "PREPARE", "PAUSE"):
        raise ValueError("La impresora está ocupada con otro trabajo.")
    if link.status.get('print_error'):
        raise ValueError(printer.print_error_message(link.status['print_error']))
    if link.status.get('gcode_state') not in ('IDLE', 'FINISH'):
        raise ValueError('La A1 todavía no confirma que esté libre. Revisa su pantalla antes de enviar.')
    pure = transport == 'gcode_file'
    data = text.encode('utf-8') if pure else gcode.build_3mf(text, layers, config, seconds)
    extension = '.gcode' if pure else '.gcode.3mf'
    filename = f"pluma_{title}_{uuid.uuid4().hex[:8]}{extension}"
    last_dispatch['file'] = filename
    try:
        printer.upload(p, filename, data)
    except Exception as e:
        raise ValueError(f"No pude subir el archivo a la impresora ({e}). Revisa que tenga la tarjeta microSD puesta "
                         "y que el código de acceso sea el correcto.")
    status_version = link.status_version
    ack = link.start_gcode_file(filename) if pure else link.start_print(filename, title)
    last_dispatch['ack'] = {k: ack.get(k) for k in ('result', 'reason', 'err_code')} if ack else None
    if ack is None:
        raise ValueError(f'El archivo «{filename}» llegó a la microSD, pero no hubo confirmación de inicio. '
                         'No repitas el envío todavía: revisa la pantalla y el estado de la impresora. '
                         'Si sigue inactiva, comprueba Modo solo LAN y Modo desarrollador.')
    if ack.get("result", "").lower() != "success":
        raise ValueError("La impresora rechazó la orden de inicio"
                         + (f" ({ack.get('reason')})" if ack.get("reason") else "")
                         + ". En firmware reciente hay que activar «Modo solo LAN» y «Modo desarrollador» en la impresora. "
                           f"El archivo «{filename}» ya está en la microSD: también puedes iniciarlo desde la pantalla.")
    end = time.monotonic() + 15
    while time.monotonic() < end:
        if link.status.get('print_error'):
            raise ValueError(printer.print_error_message(link.status['print_error']))
        if link.status.get('gcode_state') == 'FAILED':
            raise ValueError(f'La impresora falló al abrir «{filename}». Revisa su pantalla.')
        reported_file = str(link.status.get('gcode_file', '')).replace('\\', '/').rsplit('/', 1)[-1]
        if link.status_version > status_version and reported_file == filename and link.status.get('gcode_state') in ('PREPARE', 'RUNNING', 'PAUSE'):
            return 'Inicio confirmado por la impresora. Revisa su pantalla antes de colocar la pluma.'
        time.sleep(0.2)
    raise ValueError(f'La orden fue aceptada, pero no se observó el inicio de «{filename}». '
                     'El archivo está en la microSD. Comprueba en la pantalla si puede abrirse o muestra un error. '
                     'No repitas el envío mientras revisas el resultado.')


@app.post("/api/send")
def api_send():
    d = request.get_json(force=True)
    text, layers, st, title = _build(d.get("job"), d.get("page", 0), d.get("layer"), d.get("pen_ready"))
    transport = _transport(d.get('transport'))
    return jsonify({"ok": True, 'transport': transport, "message": _dispatch(text, layers, st["seconds"], title, transport)})


@app.post('/api/preflight')
def api_preflight():
    d = request.get_json(force=True)
    text, layers, st, title = _build(d.get('job'), d.get('page', 0), d.get('layer'), d.get('pen_ready'))
    pw, ph = gcode.paper_dims(config['paper'])
    x0, y0, x1, y1 = gcode.drawable(config)
    warnings = []
    if x0 > config['paper']['margin'] or y0 > config['paper']['margin'] or x1 < pw - config['paper']['margin'] or y1 < ph - config['paper']['margin']:
        warnings.append('Parte de la hoja queda fuera del alcance. Sólo se dibuja dentro de la zona útil.')
    return jsonify({'ok': True, 'seconds': st['seconds'], 'lifts': st['lifts'], 'pens': len(layers),
                    'warnings': warnings, 'area': [round(x1-x0, 1), round(y1-y0, 1)]})


@app.post("/api/adjust")
def api_adjust():
    """La A1 se calibra con la boquilla y deja la pluma en el centro para ajustarla a mano."""
    d = request.get_json(silent=True) or {}
    transport = _transport(d.get('transport'))
    return jsonify({"ok": True, 'transport': transport, "message": _dispatch(gcode.build_adjust_gcode(config), [], 150, "ajustar_pluma", transport, 'adjust')})


@app.post('/api/printer/diagnostic')
def api_diagnostic():
    d = request.get_json(silent=True) or {}
    transport = _transport(d.get('transport'))
    return jsonify({'ok': True, 'transport': transport, 'message': _dispatch(gcode.build_diagnostic_gcode(config), [], 12, 'diagnostico_sin_movimiento', transport, 'diagnostic')})


@app.post("/api/guide")
def api_guide():
    """La pluma señala cada esquina de la hoja y espera en pausa entre una y otra."""
    text, names = gcode.build_guide_gcode(config)
    d = request.get_json(silent=True) or {}
    transport = _transport(d.get('transport'))
    return jsonify({"ok": True, 'transport': transport, "message": _dispatch(text, [], 120, "ubicar_hoja", transport, 'guide'), "points": names})


@app.get("/api/printer/status")
def api_status():
    p = config["printer"]
    configured = link.ensure(p)
    return jsonify(dict(link.snapshot(), configured=configured, name=p.get("name", ""),
                        last_dispatch=last_dispatch, local_job=dict(direct_job.snapshot(), kind=direct_job_kind),
                        direct_command=direct_command, transport=_transport(),
                        material=p.get('material', 'PLA'), app_version=APP_VERSION))


@app.post("/api/printer/discover")
def api_discover():
    return jsonify({"printers": printer.discover()})


def _direct_gcode(d):
    """Controles acotados; nunca acepta G-code libre enviado por el navegador."""
    action = d.get('action')
    if action == 'probe':
        return 'M400', 12
    if action == 'home':
        if d.get('clear_for_home') is not True:
            raise ValueError('Retira la pluma y la hoja y despeja la cama antes de referenciar los ejes.')
        return 'M104 S0\nM140 S0\nG90\nG28', 180
    if d.get('homing_confirmed') is not True:
        raise ValueError('Confirma que los ejes están referenciados y que la zona está libre.')
    if action == 'jog':
        axis = d.get('axis')
        if axis not in ('X', 'Y', 'Z'):
            raise ValueError('Elige el eje X, Y o Z.')
        try:
            if isinstance(d.get('distance'), bool):
                raise ValueError()
            distance = float(d.get('distance'))
        except (TypeError, ValueError):
            raise ValueError('El desplazamiento debe ser de 0.1, 1, 5 o 10 mm.')
        if abs(distance) not in (0.1, 1, 5, 10) or (axis == 'Z' and abs(distance) > 1):
            raise ValueError('Usa pasos de 0.1, 1, 5 o 10 mm; en Z, como máximo 1 mm.')
        feed = 300 if axis == 'Z' else 1200
        motion = f'G91\nG1 {axis}{distance:g} F{feed}'
    elif action in ('pen_up', 'pen_down'):
        gcode.validate_config(config)
        if action == 'pen_down' and d.get('pen_ready') is not True:
            raise ValueError('Primero calibra la altura de escritura; después confirma el ajuste de la pluma.')
        z = float(config['pen']['z_up' if action == 'pen_up' else 'z_down'])
        motion = f'G90\nG1 Z{z:.2f} F300'
    else:
        raise ValueError('Comando directo desconocido.')
    # La misma conservación de límites y modo de referencia usada por Bambu Studio.
    return ('M211 S\nM211 X1 Y1 Z1\nM1002 push_ref_mode\n' + motion
            + '\nM1002 pop_ref_mode\nM211 R'), 45


@app.post('/api/printer/direct')
def api_direct():
    global stream_failure_cleared
    d = request.get_json(force=True)
    if not isinstance(d, dict):
        raise ValueError('El comando debe ser un objeto.')
    text, timeout = _direct_gcode(d)
    if not dispatch_lock.acquire(blocking=False):
        raise ValueError('Hay un envío en curso. Espera a que termine antes de mover la A1.')
    sent = False
    try:
        _printer_available(allow_probe=d.get('action') == 'probe')
        direct_command.clear()
        direct_command.update({'phase': 'sending', 'action': d['action'], 'command': text,
                               'message': 'Esperando que la A1 complete el comando…'})
        sent = True
        command, ack = link.send_gcode_wait(text, timeout=timeout)
        message = ('La A1 acepta comandos directos y confirmó la señal de finalización, sin movimiento ni calentamiento.'
                   if d['action'] == 'probe' else 'La A1 confirmó el final del comando. Comprueba la posición de la pluma.')
        direct_command.update({'phase': 'complete', 'message': message, 'command': command,
                               'ack': {k: ack.get(k) for k in ('result', 'reason', 'sequence_id')}})
        if d['action'] == 'probe':
            stream_failure_cleared = True
        return jsonify({'ok': True, **direct_command})
    except Exception as error:
        if sent or direct_command.get('phase') != 'unknown':
            direct_command.update({'phase': 'unknown' if sent else 'error', 'message': str(error), 'command': text})
        else:
            # Un segundo clic rechazado no borra la incertidumbre del comando anterior.
            direct_command['message'] = str(error)
        raise
    finally:
        dispatch_lock.release()


@app.post("/api/printer/level")
def api_level():
    if not dispatch_lock.acquire(blocking=False):
        raise ValueError('Ya hay un movimiento o un envío en curso.')
    try:
        return _level_locked()
    finally:
        dispatch_lock.release()


def _level_locked():
    """Lanza la nivelación de cama nativa de Bambu Lab."""
    if direct_job.active:
        raise ValueError('Ya hay un movimiento o un envío en curso.')
    _printer_available()
    if not link.ensure(config["printer"]):
        raise ValueError("Primero configura la impresora en Ajustes.")
    if not link.wait_connected():
        raise ValueError(link.error or "No pude conectar con la impresora. ¿Está encendida y en la misma red?")
    if not link.wait_status():
        raise ValueError('No hay estado reciente de la A1. Revisa la conexión antes de nivelar.')
    if link.status.get('print_error'):
        raise ValueError(printer.print_error_message(link.status['print_error']))
    if link.status.get("gcode_state") in ("RUNNING", "PREPARE", "PAUSE"):
        raise ValueError("La impresora está ocupada con otro trabajo.")
    ack = link.level_bed()
    if ack is not None and ack.get("result", "").lower() != "success":
        raise ValueError("La impresora rechazó la nivelación"
                         + (f" ({ack.get('reason')})" if ack.get("reason") else "")
                         + ". Puedes lanzarla desde su pantalla: Ajustes → Calibración.")
    return jsonify({"ok": True, "message": "Nivelación enviada. Mira la impresora: tarda unos minutos."
                    if ack else "Orden enviada, pero la impresora no confirmó: revisa su pantalla."})


@app.post("/api/printer/control")
def api_control():
    action = request.get_json(force=True).get("action")
    if action not in ("pause", "resume", "stop"):
        raise ValueError("Acción desconocida.")
    if direct_job.active:
        direct_job.control(action)
        return jsonify({'ok': True, 'message': direct_job.snapshot().get('message', '')})
    if not link.connected:
        raise ValueError("No hay conexión con la impresora.")
    ack = link.command({"command": action, "param": ""}, wait=3.0)
    if ack is None or str(ack.get('result', '')).lower() != 'success':
        raise ValueError('La impresora no confirmó la orden. Revisa su pantalla antes de continuar.')
    return jsonify({"ok": True})


def open_running_app():
    """Al abrir otra vez el BAT, reutiliza una instancia identificada de Pluma A1."""
    base = f'http://127.0.0.1:{PORT}'
    for endpoint in ('/api/health', '/api/printer/status'):
        try:
            with urlopen(base + endpoint, timeout=2) as response:
                data = json.load(response)
            if not isinstance(data, dict):
                return False
            if endpoint == '/api/health':
                if data.get('app_id') != 'pluma-a1' or data.get('workspace_id') != WORKSPACE_ID:
                    return False
            elif not ('app_version' in data and 'last_dispatch' in data and 'configured' in data):
                return False
            print('Pluma A1 ya esta funcionando. Abriendo la aplicacion...')
            if data.get('app_version') != APP_VERSION:
                print('Para cargar cambios nuevos, cierra la ventana que inicio la app y vuelve a abrir el BAT.')
            webbrowser.open(base)
            return True
        except (OSError, ValueError, TypeError):
            continue
    return False


def main():
    if open_running_app():
        return 0

    class SingleAppServer(ThreadedWSGIServer):
        allow_reuse_address = False

        def server_bind(self):
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    try:
        server = SingleAppServer('0.0.0.0', PORT, app)
    except SystemExit:
        print('Pluma A1 ya está abierta, o su dirección está ocupada. Cierra la ventana anterior antes de abrir el BAT de nuevo.')
        raise
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    print(f"Pluma A1 en esta PC:      http://127.0.0.1:{PORT}")
    print(f"Pluma A1 desde el celular: {lan_url() or '(sin red local)'}   (misma red WiFi)")
    print("Cierra esta ventana para salir.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        link.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
