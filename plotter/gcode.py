"""Trazos -> G-code para la Bambu Lab A1 con el módulo de pluma, y empaquetado .gcode.3mf.

A diferencia del G-code de inicio original del módulo (que es el de impresión normal:
calienta, purga filamento, limpia la boquilla y nivela), aquí no se calienta ni se
extruye nada: sólo se hace homing, se espera a que coloques la pluma y se dibuja.
"""
import hashlib
import io
import zipfile
import math
import re
from xml.sax.saxutils import escape
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import pathops

BED = 256.0  # la A1 imprime en 256 x 256 mm
PAPER_SIZES = {
    "carta": (215.9, 279.4),
    "media_carta": (139.7, 215.9),
    "a4": (210.0, 297.0),
    "a5": (148.0, 210.0),
}
MATERIALS = {
    'PLA': {'id': 'GFA00', 'profile': 'Bambu PLA Basic @BBL A1', 'density': '1.24', 'vendor': 'Bambu Lab'},
    'PETG': {'id': 'GFG99', 'profile': 'Generic PETG @BBL A1', 'density': '1.27', 'vendor': 'Generic'},
}


def material_profile(cfg=None):
    kind = (cfg or {}).get('printer', {}).get('material', 'PLA')
    if kind not in MATERIALS:
        raise ValueError('Elige PLA o PETG como material de referencia del archivo.')
    return dict(MATERIALS[kind], type=kind)


def validate_config(cfg):
    """Rechaza valores no finitos y movimientos incompatibles con este perfil A1."""
    material_profile(cfg)
    if cfg.get('printer', {}).get('transport', 'stream') not in ('stream', 'gcode_file', 'project_file'):
        raise ValueError('Elige comandos directos, G-code puro o paquete 3MF como forma de envío.')
    ranges = {
        "paper": {"width": (20, 600), "height": (20, 600), "bed_x": (-256, 256),
                  "bed_y": (-256, 256), "margin": (0, 100)},
        "pen": {"offset_x": (-100, 100), "offset_y": (-100, 100), "z_down": (0.5, 30),
                "z_up": (1, 35), "draw_speed": (1, 80), "travel_speed": (1, 200),
                "z_speed": (1, 30), "accel": (100, 3000)},
    }
    for section, fields in ranges.items():
        for key, (lo, hi) in fields.items():
            try:
                value = float(cfg[section][key])
            except (TypeError, ValueError, KeyError):
                raise ValueError(f"Revisa {section}.{key}: debe ser un número.")
            if not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError(f"Revisa {section}.{key}: usa un valor entre {lo} y {hi}.")
    if float(cfg['pen']['z_up']) - float(cfg['pen']['z_down']) < 1:
        raise ValueError("La altura de viaje debe superar la de escritura al menos 1 mm.")
    if cfg['paper'].get('size') not in (*PAPER_SIZES, 'custom'):
        raise ValueError("Elige un tamaño de hoja válido.")
    x0, y0, x1, y1 = drawable(cfg)
    if x1 - x0 < 10 or y1 - y0 < 10:
        raise ValueError("No queda una zona útil de al menos 10 × 10 mm. Revisa hoja, posición y márgenes.")


def paper_dims(paper):
    w, h = PAPER_SIZES.get(paper.get("size"), (float(paper.get("width", 215.9)), float(paper.get("height", 279.4))))
    return (h, w) if paper.get("landscape") else (w, h)


def reach(cfg):
    """Zona que alcanza la pluma, en coordenadas de la hoja (x0, y0, x1, y1), sin márgenes."""
    paper, pen = cfg["paper"], cfg["pen"]
    pw, ph = paper_dims(paper)
    bx, by = float(paper["bed_x"]), float(paper["bed_y"])
    ox, oy = float(pen["offset_x"]), float(pen["offset_y"])
    lo_x, hi_x = max(0.0, ox), min(BED, BED + ox)
    lo_y, hi_y = max(0.0, oy), min(BED, BED + oy)
    return (max(0.0, lo_x - bx), max(0.0, by + ph - hi_y), min(pw, hi_x - bx), min(ph, by + ph - lo_y))


def drawable(cfg):
    """Zona útil: alcance de la pluma menos los márgenes de la hoja."""
    pw, ph = paper_dims(cfg["paper"])
    m = float(cfg["paper"]["margin"])
    x0, y0, x1, y1 = reach(cfg)
    return (max(m, x0), max(m, y0), min(pw - m, x1), min(ph - m, y1))


def estimate_seconds(st, pen):
    hop = max(0.1, float(pen["z_up"]) - float(pen["z_down"]))
    t = st["draw_mm"] / float(pen["draw_speed"]) + st["travel_mm"] / float(pen["travel_speed"])
    t += st["lifts"] * (2 * hop / float(pen["z_speed"]) + 0.15)
    return t * 1.2 + 45  # aceleraciones + homing


PEN_PRELOAD = 0.5  # mm que la pluma queda apretada al escribir, respecto a la altura de ajuste

_A1_CONFIG = [
    '; CONFIG_BLOCK_START',
    '; printer_model = Bambu Lab A1',
    '; printer_settings_id = Bambu Lab A1 0.4 nozzle',
    '; gcode_flavor = marlin',
    '; nozzle_diameter = 0.4',
    '; filament_type = PLA',
    '; filament_diameter = 1.75',
    '; filament_density = 1.24',
    '; filament_colour = #2743B8',
    '; filament_settings_id = Pluma A1 sin extrusion',
    '; nozzle_temperature = 0',
    '; nozzle_temperature_initial_layer = 0',
    '; hot_plate_temp = 0',
    '; hot_plate_temp_initial_layer = 0',
    '; textured_plate_temp = 0',
    '; textured_plate_temp_initial_layer = 0',
    '; curr_bed_type = Textured PEI Plate',
    '; initial_layer_print_height = 0.2',
    '; layer_height = 0.2',
    '; printable_area = 0x0,256x0,256x256,0x256',
    '; printable_height = 256',
    '; CONFIG_BLOCK_END',
]
_REFERENCE = Path(__file__).with_name('a1_reference_config.txt')
if _REFERENCE.exists():
    _A1_CONFIG = _REFERENCE.read_text(encoding='utf-8').splitlines()


def normalize_pen_config(lines, material='PLA'):
    """Un material de referencia; nunca heredar rutinas de impresión."""
    profile = material_profile({'printer': {'material': material}})
    values = {}
    for line in lines:
        if line.startswith('; '):
            key, sep, value = line[2:].partition(' = ')
            if sep:
                values[key] = value
    types = values.get('filament_type', 'PLA').split(';')
    count = len(types)
    index = types.index('PLA') if 'PLA' in types else 0
    material_keys = {
        'chamber_temperatures', 'nozzle_temperature', 'nozzle_temperature_initial_layer',
        'nozzle_temperature_range_high', 'nozzle_temperature_range_low',
        'temperature_vitrification', 'pressure_advance', 'volumetric_speed_coefficients',
        'first_x_layer_fan_speed', 'first_x_layer_part_fan_speed', 'full_fan_speed_layer',
        'hole_coef_1', 'hole_coef_2', 'hole_coef_3', 'hole_limit_max', 'hole_limit_min',
        'impact_strength_z', 'ironing_fan_speed', 'long_retractions_when_ec',
        'no_slow_down_for_cooling_on_outwalls', 'overhang_fan_speed', 'overhang_fan_threshold',
        'overhang_threshold_participating_cooling', 'override_process_overhang_speed',
        'pre_start_fan_time', 'reduce_fan_stop_start_freq', 'required_nozzle_HRC',
        'retraction_distances_when_ec', 'slow_down_for_layer_cooling',
        'slow_down_layer_time', 'slow_down_min_speed',
    }
    for key, value in list(values.items()):
        if key.startswith('filament_dev_'):
            # Secado de material no forma parte de un trabajo de pluma.
            del values[key]
            continue
        # Bambu Studio serializa vectores numéricos con coma y strings con ;.
        # Se conservan listas ajenas al material, como máquinas compatibles.
        if key.startswith(('filament_', 'default_filament_')) or '_plate_temp' in key or key in material_keys:
            delimiter = ';' if ';' in value else ','
            parts = re.split(delimiter + r'(?=(?:[^"]*"[^"]*")*[^"]*$)', value)
            if count > 1 and len(parts) == count:
                values[key] = parts[index]
        if 'gcode' in key and key != 'gcode_flavor':
            values[key] = ''
        if 'temp' in key and not any(s in key for s in ('range', 'formula', 'difference')):
            if re.fullmatch(r'[\d.,;\- ]+', values[key]):
                values[key] = re.sub(r'-?\d+(?:\.\d+)?', '0', values[key])
    values.update({
        'filament_type': profile['type'], 'filament_ids': profile['id'],
        'filament_settings_id': '"' + profile['profile'] + '"',
        'default_filament_profile': '"' + profile['profile'] + '"',
        'filament_vendor': '"' + profile['vendor'] + '"',
        'filament_colour': '#2743B8', 'filament_multi_colour': '#2743B8',
        'default_filament_colour': '#2743B8',
        'filament_density': profile['density'], 'filament_diameter': '1.75',
        'filament_self_index': '1', 'filament_map': '1', 'filament_map_2': '0',
        'filament_nozzle_map': '0', 'filament_volume_map': '0',
        'nozzle_temperature': '0', 'nozzle_temperature_initial_layer': '0',
        'flush_volumes_matrix': '0', 'flush_volumes_vector': '0,0',
        'filament_start_gcode': '', 'filament_end_gcode': '',
    })
    return ['; CONFIG_BLOCK_START', *(f'; {k} = {v}' for k, v in values.items()), '; CONFIG_BLOCK_END']


_A1_CONFIG = normalize_pen_config(_A1_CONFIG)


def _header(title, total, n_layers, cfg=None):
    minutes, seconds = divmod(int(total), 60)
    material = material_profile(cfg)
    return [
        '; HEADER_BLOCK_START',
        '; Pluma A1 - sin extrusion',
        f'; {title}',
        f'; total layer number: {n_layers}',
        f'; model printing time: {minutes}m {seconds}s; total estimated time: {minutes}m {seconds}s',
        '; total filament length [mm] : 0.00',
        '; total filament volume [cm^3] : 0.00',
        '; total filament weight [g] : 0.00',
        '; filament_density: ' + material['density'],
        '; filament_diameter: 1.75',
        '; max_z_height: 40.00',
        '; filament: 1',
        '; HEADER_BLOCK_END',
        '', *normalize_pen_config(_A1_CONFIG, material['type']), '', '; EXECUTABLE_BLOCK_START',
    ]


def build_diagnostic_gcode(cfg=None):
    """Diagnóstico de ejecución: sólo progreso y espera, sin mover ni calentar."""
    return '\n'.join(_header('diagnostico sin movimiento', 12, 1, cfg) + [
        'M73 P0 R1', 'M400 S6', 'M73 P50 R1', 'M400 S6', 'M73 P100 R0',
        '; EXECUTABLE_BLOCK_END', '',
    ])


def _start(title, total, n_layers, pen_ready=False, cfg=None):
    """Arranque común: en frío, sin extruir, con el homing propio de la A1.

    pen_ready: la pluma ya está ajustada y puesta; se conserva la referencia de todos
    los ejes, sin homing, y se confía en que no se hayan apagado ni movido a mano."""
    home = ["; Ejes conservados: requiere referencia vigente, sin apagar ni mover ejes"] if pen_ready else [
        "G28 ; homing (la pluma debe estar retirada o levantada)"]
    return [
        *_header(title, total, n_layers, cfg),
        f"M73 P0 R{max(1, round(total / 60))}",
        "M104 S0 ; boquilla fria",
        "M140 S0 ; cama fria",
        "M106 S0",
        "M106 P2 S0",
        "G90",
        "M83",
        "M220 S100",
        "M17 ; activar motores con sus valores predeterminados",
        *home,
        "G90",
        *(["G29.2 S1 ; reactivar compensacion guardada, sin volver a sondear"]
          if pen_ready and (cfg or {}).get('pen', {}).get('level_bed', True) else []),
    ]


def _pen_stop(cfg, message):
    """Lleva la pluma al centro de la zona útil de la hoja, a la altura de ajuste, y espera."""
    paper, pen = cfg["paper"], cfg["pen"]
    _, ph = paper_dims(paper)
    x0, y0, x1, y1 = drawable(cfg)
    nx = float(paper["bed_x"]) + (x0 + x1) / 2 - float(pen["offset_x"])
    ny = float(paper["bed_y"]) + ph - (y0 + y1) / 2 - float(pen["offset_y"])
    nx, ny = min(max(nx, 0.0), BED), min(max(ny, 0.0), BED)
    return [
        f"G1 Z{float(pen['z_up']) + 4:.2f} F1200",
        f"G0 X{nx:.2f} Y{ny:.2f} F9000",
        f"G1 Z{float(pen['z_down']) + PEN_PRELOAD:.2f} F600",
        "M400",
        f"M400 U1 ; PAUSA: {message}",
    ]


def build_adjust_gcode(cfg):
    """Trabajo «Ajustar pluma»: la A1 se calibra con la boquilla, lleva la pluma al centro
    a la altura de escritura y espera a que el usuario la baje hasta tocar la hoja."""
    validate_config(cfg)
    paper, pen = cfg["paper"], cfg["pen"]
    _, ph = paper_dims(paper)
    g = _start("ajustar pluma", 150, 1, cfg=cfg)
    if pen.get("level_bed", True):
        # La punta está desplazada respecto a la boquilla: sondear toda la cama
        # permite reutilizar la malla en ambas zonas al cambiar el diseño.
        g += _level(0, 0, BED, BED)
    g += _pen_stop(cfg, "baja la pluma hasta tocar la hoja, apriétala y pulsa Reanudar")
    return "\n".join(g + ["", "G1 Z40 F1200 ; levanta la pluma", "M400", "M73 P100 R0", '; EXECUTABLE_BLOCK_END', ""])


def _level(x0, y0, x1, y1):
    """Nivelado automático de la A1 sólo en la zona que se va a usar (mismo G29 que su arranque de fábrica)."""
    w, h = max(10.0, x1 - x0), max(10.0, y1 - y0)
    x0, y0 = min(x0, BED - w), min(y0, BED - h)  # la zona nunca se sale de la cama
    return [
        "G1 Z5 F1200",
        "G1 X0 Y0 F12000",
        "G29.2 S1 ; activar compensacion antes del sondeo, como en el perfil A1",
        f"G29 A1 X{x0:.1f} Y{y0:.1f} I{w:.1f} J{h:.1f}",
        "M400",
        "G29.2 S1 ; compensacion de cama activada despues del sondeo",
    ]


_END = [
    "",
    "G1 Z40 F1200 ; levanta la pluma",
    "G1 X10 Y250 F9000 ; acerca la hoja",
    "M400",
    "M204 S6000",
    "M73 P100 R0",
    '; EXECUTABLE_BLOCK_END',
    "",
]


def guide_points(cfg):
    """Puntos que la pluma señala para ubicar la hoja: sus cuatro esquinas, o el punto más
    cercano de la hoja cuando la esquina queda fuera de los 256 x 256 mm que alcanza la A1.
    Devuelve [(nombre, x, y)] en mm de hoja."""
    pw, ph = paper_dims(cfg["paper"])
    x0, y0, x1, y1 = reach(cfg)
    corners = [("inferior izquierda", 0.0, ph), ("inferior derecha", pw, ph),
               ("superior derecha", pw, 0.0), ("superior izquierda", 0.0, 0.0)]
    out = []
    for name, cx, cy in corners:
        x, y = min(max(cx, x0), x1), min(max(cy, y0), y1)
        dx, dy = abs(x - cx), abs(y - cy)
        if dx < 0.5 and dy < 0.5:
            label = f"Esquina {name} de la hoja"
        else:
            side = "izquierdo" if "izquierda" in name else "derecho"
            edge = "superior" if "superior" in name else "inferior"
            parts = []
            if dy >= 0.5:
                parts.append(f"{dy:.0f} mm hacia dentro desde el borde {edge}")
            if dx >= 0.5:
                parts.append(f"{dx:.0f} mm hacia dentro desde el borde {side}")
            label = f"Esquina {name}: la pluma no llega, señala un punto " + " y ".join(parts)
        out.append((label, x, y))
    return out


def build_guide_gcode(cfg):
    """Trabajo para ubicar la hoja: la pluma va a cada esquina y espera a que pulses Reanudar."""
    validate_config(cfg)
    paper, pen = cfg["paper"], cfg["pen"]
    _, ph = paper_dims(paper)
    bx, by = float(paper["bed_x"]), float(paper["bed_y"])
    ox, oy = float(pen["offset_x"]), float(pen["offset_y"])
    z_touch = float(pen["z_down"]) + 1.0  # punta apenas sobre el papel, sin apretar
    z_hop = z_touch + 6.0
    points = guide_points(cfg)
    g = _start("ubicar hoja", 120, len(points), cfg=cfg)
    g += ["G1 Z40 F1200", "G1 X128 Y128 F9000", "M400", "M400 U1 ; PAUSA: coloca la pluma y pulsa Reanudar"]
    for i, (label, x, y) in enumerate(points):
        nx = min(max(bx + x - ox, 0.0), BED)
        ny = min(max(by + ph - y - oy, 0.0), BED)
        g += ["", f"; {label}", f"M73 L{i + 1}", f"G1 Z{z_hop:.2f} F1200", f"G0 X{nx:.2f} Y{ny:.2f} F6000",
              f"G1 Z{z_touch:.2f} F600", "M400", "M400 U1 ; PAUSA: acomoda la hoja y pulsa Reanudar"]
    return "\n".join(g + _END), [p[0] for p in points]


def build_gcode(layers, cfg, title="dibujo", pen_ready=False):
    """layers: [{"name", "color", "paths"}], un elemento por pluma, en mm de hoja
    (origen arriba-izquierda, y hacia abajo). Lanza ValueError si no cabe."""
    validate_config(cfg)
    title = str(title).replace('\n', ' ').replace('\r', ' ')
    paper, pen = cfg["paper"], cfg["pen"]
    pw, ph = paper_dims(paper)
    bx, by = float(paper["bed_x"]), float(paper["bed_y"])
    ox, oy = float(pen["offset_x"]), float(pen["offset_y"])
    z_down, z_up = float(pen["z_down"]), float(pen["z_up"])
    if z_down < 0.5 or z_up <= z_down:
        raise ValueError("Revisa las alturas de la pluma: 'pluma abajo' debe ser ≥ 0.5 mm y menor que 'pluma arriba'.")
    f_draw = float(pen["draw_speed"]) * 60
    f_travel = float(pen["travel_speed"]) * 60
    f_z = float(pen["z_speed"]) * 60

    # hoja -> cama, y de pluma a boquilla (la boquilla es lo que mueve el firmware)
    conv = []
    rx0, ry0, rx1, ry1 = reach(cfg)
    for layer in layers:
        for p in layer["paths"]:
            p = np.asarray(p)
            if p.ndim != 2 or p.shape[1] != 2 or not np.isfinite(p).all():
                raise ValueError("El diseño contiene coordenadas inválidas. Vuelve a convertirlo.")
            if len(p) and (p[:, 0].min() < 0 or p[:, 0].max() > pw or p[:, 1].min() < 0 or p[:, 1].max() > ph):
                raise ValueError("Hay trazos fuera de la hoja. Mueve o reduce el elemento.")
            if len(p) and (p[:, 0].min() < rx0 or p[:, 0].max() > rx1 or p[:, 1].min() < ry0 or p[:, 1].max() > ry1):
                raise ValueError('El dibujo excede el alcance de la pluma sobre la cama. Mueve o reduce el elemento.')
        qs = [np.column_stack([bx + p[:, 0] - ox, by + ph - p[:, 1] - oy]) for p in layer["paths"] if len(p) > 1]
        if qs:
            conv.append((layer["name"], qs))
    if not conv:
        raise ValueError("No hay nada que dibujar.")
    allp = np.vstack([q for _, qs in conv for q in qs])
    if not np.isfinite(allp).all() or allp.min() < 0 or allp.max() > BED:
        raise ValueError("El dibujo se sale de la zona que alcanza la pluma. Hazlo más pequeño o ajusta la posición de la hoja.")

    st = {"draw_mm": 0.0, "travel_mm": 0.0, "lifts": 0}
    for layer in layers:
        for k, v in pathops.stats(layer["paths"]).items():
            st[k] += v
    total = estimate_seconds(st, pen)
    level = pen.get("level_bed", True) and not pen_ready
    if level:
        total += 75
    g = _start(title, total, len(conv), pen_ready, cfg)
    if level:
        # Incluir tanto el recorrido de la boquilla como el de la punta.
        measured_area = np.vstack([allp, allp + [ox, oy]])
        lo, hi = np.clip(measured_area.min(0), 0, BED), np.clip(measured_area.max(0), 0, BED)
        g += _level(lo[0], lo[1], hi[0], hi[1])

    done, next_mark = 0.0, 2.0
    for i, (name, qs) in enumerate(conv):
        g += ["", f"; ===== pluma {i + 1}/{len(conv)}: {name} =====", "M204 S6000"]
        # entre colores la pausa es obligatoria; al inicio no hace falta si la pluma ya está ajustada
        if i > 0 or not pen_ready:
            g += _pen_stop(cfg, f"baja la pluma '{name}' hasta tocar la hoja y pulsa Reanudar")
        g += [f"M73 L{i + 1}", f"M204 S{int(pen['accel'])}", f"G1 Z{z_up:.2f} F1200"]
        for q in qs:
            g.append(f"G0 X{q[0, 0]:.2f} Y{q[0, 1]:.2f} F{f_travel:.0f}")
            g.append(f"G1 Z{z_down:.2f} F{f_z:.0f}")
            g.append(f"G1 X{q[1, 0]:.2f} Y{q[1, 1]:.2f} F{f_draw:.0f}")
            g.extend(f"G1 X{x:.2f} Y{y:.2f}" for x, y in q[2:])
            g.append(f"G1 Z{z_up:.2f} F{f_z:.0f}")
            done += float(np.hypot(*np.diff(q, axis=0).T).sum())
            pct = 100 * done / max(st["draw_mm"], 1e-6)
            if pct >= next_mark:
                g.append(f"M73 P{min(99, int(pct))} R{max(0, round(total * (1 - pct / 100) / 60))}")
                next_mark = pct + 2

    return "\n".join(g + _END), dict(st, seconds=total, pens=len(conv))


def build_svg(layers, cfg):
    """Exportación vectorial en milímetros; conserva los trazos y colores."""
    w, h = paper_dims(cfg['paper'])
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">']
    for layer in layers:
        parts.append(f'<g fill="none" stroke="{escape(layer["color"])}" stroke-width="0.3" stroke-linecap="round" stroke-linejoin="round">')
        for p in layer['paths']:
            points = ' '.join(f'{x:.3f},{y:.3f}' for x, y in p)
            parts.append(f'<polyline points="{points}"/>')
        parts.append('</g>')
    return '\n'.join(parts + ['</svg>'])


def thumbnail(layers, cfg, side=512):
    pw, ph = paper_dims(cfg["paper"])
    k = (side - 24) / max(pw, ph)
    img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0, y0 = (side - pw * k) / 2, (side - ph * k) / 2
    d.rectangle([x0, y0, x0 + pw * k, y0 + ph * k], fill="#fbfaf6")
    for layer in layers:
        for p in layer["paths"]:
            d.line([(x0 + x * k, y0 + y * k) for x, y in p], fill=layer["color"], width=1)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Default Extension="gcode" ContentType="text/x.gcode"/>
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
 <Relationship Target="/Metadata/plate_1.png" Id="rel-2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>
</Relationships>"""
_MODEL = """<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
 <metadata name="Application">BambuStudio-01.09.00.00</metadata>
 <resources/>
 <build/>
</model>"""
_MODEL_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="plater_name" value=""/>
    <metadata key="locked" value="false"/>
    <metadata key="gcode_file" value="Metadata/plate_1.gcode"/>
    <metadata key="thumbnail_file" value="Metadata/plate_1.png"/>
  </plate>
</config>"""
_SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value="01.09.00.00"/>
  </header>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="printer_model_id" value="N2S"/>
    <metadata key="nozzle_diameters" value="0.4"/>
    <metadata key="extruder_type" value="0"/>
    <metadata key="nozzle_volume_type" value="0"/>
    <metadata key="timelapse_type" value="0"/>
    <metadata key="prediction" value="{seconds}"/>
    <metadata key="weight" value="0.00"/>
    <metadata key="outside" value="false"/>
    <metadata key="support_used" value="false"/>
    <metadata key="label_object_enabled" value="false"/>
    <filament id="1" tray_info_idx="{material_id}" type="{material_type}" color="#2743B8" used_m="0.00" used_g="0.00"/>
  </plate>
</config>"""


def build_3mf(gcode, layers, cfg, seconds):
    material = material_profile(cfg)
    data = gcode.encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", _MODEL)
        z.writestr("Metadata/model_settings.config", _MODEL_SETTINGS)
        z.writestr("Metadata/slice_info.config", _SLICE_INFO.format(seconds=int(seconds), material_id=material['id'], material_type=material['type']))
        z.writestr("Metadata/plate_1.png", thumbnail(layers, cfg))
        z.writestr("Metadata/plate_1.gcode", data)
        z.writestr("Metadata/plate_1.gcode.md5", hashlib.md5(data).hexdigest().upper())
    return buf.getvalue()
