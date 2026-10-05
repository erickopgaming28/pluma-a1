"""Offline import of real vector geometry; the result contains portable pen paths.

SVG follows the W3C path/transform conventions. STL silhouette is the geometric
union of the projected triangles (no raster tracing). DXF and KiCad imports are
bounded subsets and always report unsupported content rather than inventing it.
"""
import json
import math
import re
import struct
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import numpy as np

MAX_POINTS = 120_000
MAX_PATHS = 30_000
MAX_FACES = 20_000
NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"
NUM = re.compile(NUMBER)
TOKEN = re.compile(r"[a-zA-Z]|" + NUMBER)
IDENTITY = np.eye(3)


def number(value, label='valor', low=None, high=None):
    if isinstance(value, bool):
        raise ValueError(f'Revisa {label}: debe ser un número finito.')
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f'Revisa {label}: debe ser un número finito.')
    if not math.isfinite(result) or (low is not None and result < low) or (high is not None and result > high):
        raise ValueError(f'Revisa {label}: está fuera del intervalo permitido.')
    return result


def _warn(warnings, text):
    if text not in warnings:
        warnings.append(text)


def _transform(paths, matrix):
    return [np.column_stack([p, np.ones(len(p))]) @ matrix.T[:, :2] for p in paths]


def _translation(x, y):
    return np.array([[1., 0., x], [0., 1., y], [0., 0., 1.]])


def _rotation(degrees):
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.], [s, c, 0.], [0., 0., 1.]])


def _scale(x, y=None):
    return np.diag([x, x if y is None else y, 1.])


def _checked_paths(paths):
    out, count = [], 0
    for p in paths:
        p = np.asarray(p, dtype=np.float64)
        if p.ndim != 2 or p.shape[1] != 2 or not np.isfinite(p).all():
            raise ValueError('El archivo contiene coordenadas inválidas.')
        if len(p) < 2:
            continue
        # Repeated points do not add geometry, and zero-length paths are discarded.
        p = p[np.r_[True, np.any(np.abs(np.diff(p, axis=0)) > 1e-12, axis=1)]]
        if len(p) < 2:
            continue
        count += len(p)
        if count > MAX_POINTS or len(out) >= MAX_PATHS:
            raise ValueError('El archivo tiene demasiados trazos. Simplifícalo o importa una parte.')
        out.append(p)
    if not out:
        raise ValueError('No se encontraron trazos dibujables en el archivo.')
    return out


def validate_vector(vector):
    """Untrusted saved paths must remain finite, normalized and bounded."""
    if not isinstance(vector, dict) or not isinstance(vector.get('paths'), list):
        raise ValueError('El elemento vectorial no contiene trazos válidos.')
    aspect = number(vector.get('aspect'), 'la proporción', .00001, 100_000)
    paths, count = [], 0
    if not 1 <= len(vector['paths']) <= MAX_PATHS:
        raise ValueError('El elemento vectorial tiene demasiados trazos o está vacío.')
    for raw in vector['paths']:
        if not isinstance(raw, list) or len(raw) < 4 or len(raw) % 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in raw):
            raise ValueError('Los trazos vectoriales deben contener pares de coordenadas.')
        count += len(raw) // 2
        if count > MAX_POINTS:
            raise ValueError('El elemento vectorial tiene demasiados puntos.')
        p = np.asarray(raw, dtype=np.float64).reshape(-1, 2)
        if not np.isfinite(p).all() or np.any(p < -1e-8) or np.any(p > 1 + 1e-8):
            raise ValueError('Las coordenadas vectoriales deben estar entre 0 y 1.')
        paths.append(np.clip(p, 0, 1))
    return paths, aspect


def render_vector(vector, width):
    width = number(width, 'el ancho vectorial', .1, 400)
    paths, aspect = validate_vector(vector)
    height = width * aspect
    if not .000001 <= height <= 2000:
        raise ValueError('El alto vectorial excede el límite de 2000 mm. Revisa su escala.')
    return [p * [width, height] for p in paths], height


def import_file(data, name, projection='xy', scale=1, edge_mode='outline'):
    if not isinstance(data, bytes) or not data or len(data) > 20 * 1024 * 1024:
        raise ValueError('Carga un archivo de hasta 20 MB.')
    extension = Path(name).suffix.lower()
    unit_scale = number(scale, 'la escala en milímetros', .000001, 1_000_000)
    warnings = []
    source = {'name': Path(name).name[:180], 'format': extension.lstrip('.'), 'unit_scale': unit_scale}
    if extension == '.stl':
        paths = _stl(data, projection, edge_mode, warnings)
        source.update(projection=projection, mode=edge_mode)
    elif extension == '.svg':
        paths = _svg(data, warnings)
    elif extension == '.dxf':
        paths = _dxf(data, warnings)
    elif extension in ('.kicad_sch', '.sch'):
        paths = _kicad(data, warnings) if extension == '.kicad_sch' else _legacy_schematic(data, warnings)
    else:
        raise ValueError('Usa SVG, STL, DXF, .kicad_sch o .sch. Exporta otros esquemas a SVG o PDF.')
    paths = _checked_paths([p * unit_scale for p in paths])
    points = np.vstack(paths)
    minimum, maximum = points.min(0), points.max(0)
    extent = maximum - minimum
    # A single horizontal or vertical wire still has a selectable box.
    dimensions = np.maximum(extent, .1)
    padding = (dimensions - extent) / 2
    normalized = [np.round((p - minimum + padding) / dimensions, 10).ravel().tolist() for p in paths]
    w, h = map(float, dimensions)
    source.update(original_width_mm=w, original_height_mm=h)
    vector = {'paths': normalized, 'aspect': h / w, 'source': source, 'warnings': warnings}
    validate_vector(vector)
    return {'type': 'vector', 'vector': vector, 'w': w, 'h': h,
            'segment_count': sum(len(p) - 1 for p in paths), 'warnings': warnings}


def _curve(points, tolerance=.08, depth=0):
    """Adaptive de Casteljau, bounded recursion, preserving both endpoints."""
    points = np.asarray(points, dtype=float)
    delta = points[-1] - points[0]
    length = np.linalg.norm(delta)
    deviations = (np.abs(_cross(delta, points[1:-1] - points[0])) / length
                  if length > 1e-12 else np.linalg.norm(points[1:-1] - points[0], axis=1))
    if depth >= 12 or max(deviations, default=0) <= tolerance:
        return np.array([points[0], points[-1]])
    left, right, current = [points[0]], [points[-1]], points
    while len(current) > 1:
        current = (current[:-1] + current[1:]) / 2
        left.append(current[0]); right.append(current[-1])
    return np.vstack([_curve(left, tolerance, depth + 1)[:-1], _curve(right[::-1], tolerance, depth + 1)])


def _arc(start, end, rx, ry, rotation=0, large=0, sweep=1, tolerance=.08):
    """SVG elliptical arc endpoint parameterization (W3C appendix F)."""
    start, end = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-12 or ry < 1e-12 or np.linalg.norm(end - start) < 1e-12:
        return np.array([start, end])
    phi = math.radians(rotation)
    c, s = math.cos(phi), math.sin(phi)
    rot = np.array([[c, -s], [s, c]])
    x, y = rot.T @ ((start - end) / 2)
    correction = (x / rx) ** 2 + (y / ry) ** 2
    if correction > 1:
        rx *= math.sqrt(correction); ry *= math.sqrt(correction)
    numerator = max(0., rx * rx * ry * ry - rx * rx * y * y - ry * ry * x * x)
    denominator = rx * rx * y * y + ry * ry * x * x
    coefficient = (-1 if bool(large) == bool(sweep) else 1) * math.sqrt(numerator / max(denominator, 1e-30))
    center_local = coefficient * np.array([rx * y / ry, -ry * x / rx])
    center = rot @ center_local + (start + end) / 2
    u = (np.array([x, y]) - center_local) / [rx, ry]
    v = (-np.array([x, y]) - center_local) / [rx, ry]
    theta = math.atan2(u[1], u[0])
    delta = math.atan2(_cross(u, v), np.dot(u, v))
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    count = min(2048, max(2, int(math.ceil(abs(delta) / max(.01, 2 * math.acos(max(-1, 1 - tolerance / max(rx, ry)))))) + 1))
    angles = np.linspace(theta, theta + delta, count)
    result = np.column_stack([rx * np.cos(angles), ry * np.sin(angles)]) @ rot.T + center
    result[0], result[-1] = start, end
    return result


def _svg_path(data, tolerance):
    if re.sub(r'[\s,]+', '', TOKEN.sub('', data)):
        raise ValueError('El SVG contiene instrucciones de trazado inválidas.')
    tokens = TOKEN.findall(data)
    out, path, pos, command, previous, control = [], [], np.zeros(2), None, None, None
    index, count = 0, 0
    arity = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'T': 2, 'A': 7}
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]; index += 1
            if command.upper() == 'Z':
                if path:
                    path.append(path[0].copy()); pos = path[0].copy(); out.append(np.array(path)); path = []
                previous, command, control = 'Z', None, None
                continue
        if command is None or command.upper() not in arity:
            raise ValueError('El SVG contiene un comando de trazado desconocido.')
        tag = command.upper(); amount = arity[tag]
        if index + amount > len(tokens) or any(t.isalpha() for t in tokens[index:index + amount]):
            raise ValueError('El SVG contiene un trazo incompleto.')
        values = np.array([number(v, 'las coordenadas SVG') for v in tokens[index:index + amount]])
        index += amount
        relative = command.islower()
        offset = pos if relative else np.zeros(2)
        if tag == 'M':
            if path:
                out.append(np.array(path))
            pos = values + offset; path = [pos.copy()]
            command = 'l' if relative else 'L'
        else:
            if not path:
                path = [pos.copy()]
            if tag == 'L':
                pos = values + offset; path.append(pos.copy())
            elif tag in ('H', 'V'):
                axis = 0 if tag == 'H' else 1
                pos = pos.copy(); pos[axis] = values[0] + (pos[axis] if relative else 0); path.append(pos.copy())
            elif tag in ('C', 'S', 'Q', 'T'):
                pts = values.reshape(-1, 2) + offset
                if tag in ('S', 'T'):
                    expected = ('C', 'S') if tag == 'S' else ('Q', 'T')
                    first = 2 * pos - control if previous in expected and control is not None else pos
                    pts = np.vstack([first, pts])
                path.extend(_curve(np.vstack([pos, pts]), tolerance)[1:])
                control = pts[-2].copy(); pos = pts[-1].copy()
            else:
                if values[3] not in (0, 1) or values[4] not in (0, 1):
                    raise ValueError('El SVG contiene banderas de arco inválidas.')
                end = values[5:] + offset
                path.extend(_arc(pos, end, *values[:5], tolerance=tolerance)[1:]); pos = end
        if tag not in ('C', 'S', 'Q', 'T'):
            control = None
        previous = tag
        count += 1
        if len(path) > MAX_POINTS or count > MAX_POINTS:
            raise ValueError('El SVG contiene demasiados puntos.')
    if path:
        out.append(np.array(path))
    return out


def _svg_transform(text):
    matrix = IDENTITY.copy()
    matches = list(re.finditer(r'(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)', text))
    if re.sub(r'[\s,]+', '', re.sub(r'(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)', '', text)):
        raise ValueError('El SVG contiene una transformación no admitida.')
    for match in matches:
        name, raw = match.groups()
        values = [number(v, 'la transformación SVG') for v in NUM.findall(raw)]
        if name == 'matrix' and len(values) == 6:
            a, b, c, d, e, f = values; transform = np.array([[a, c, e], [b, d, f], [0, 0, 1]])
        elif name == 'translate' and len(values) in (1, 2):
            transform = _translation(values[0], values[1] if len(values) > 1 else 0)
        elif name == 'scale' and len(values) in (1, 2):
            transform = _scale(*values)
        elif name == 'rotate' and len(values) in (1, 3):
            transform = _rotation(values[0])
            if len(values) == 3:
                transform = _translation(*values[1:]) @ transform @ _translation(-values[1], -values[2])
        elif name in ('skewX', 'skewY') and len(values) == 1:
            transform = IDENTITY.copy(); transform[0 if name == 'skewX' else 1, 1 if name == 'skewX' else 0] = math.tan(math.radians(values[0]))
        else:
            raise ValueError('El SVG contiene una transformación incompleta.')
        matrix = matrix @ transform
    return matrix


def _length_mm(raw):
    match = re.fullmatch(r'\s*(' + NUMBER + r')\s*(mm|cm|in|pt|pc|px)?\s*', raw or '')
    if not match:
        return None
    value = number(match[1], 'las dimensiones SVG', 0)
    return value * {'mm': 1, 'cm': 10, 'in': 25.4, 'pt': 25.4 / 72, 'pc': 25.4 / 6, 'px': 25.4 / 96, None: 25.4 / 96}[match[2]]


def _svg(data, warnings):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('El SVG no debe contener entidades ni referencias XML externas.')
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError):
        raise ValueError('No se pudo leer el SVG.')
    if root.tag.split('}')[-1] != 'svg':
        raise ValueError('El archivo no tiene una raíz SVG válida.')
    ids = {node.get('id'): node for node in root.iter() if node.get('id')}
    if any(node.tag.split('}')[-1] == 'style' for node in root.iter()):
        raise ValueError('El SVG usa una hoja de estilos. Exporta una copia con los estilos en cada objeto.')
    viewbox = [number(v, 'el viewBox') for v in NUM.findall(root.get('viewBox', ''))]
    width, height = _length_mm(root.get('width')), _length_mm(root.get('height'))
    if width is None or height is None:
        _warn(warnings, 'SVG sin tamaño físico completo: se usan 96 píxeles por pulgada. Revisa su tamaño en mm.')
    matrix = _scale(25.4 / 96)
    if viewbox:
        if len(viewbox) != 4 or viewbox[2] <= 0 or viewbox[3] <= 0:
            raise ValueError('El SVG contiene un viewBox inválido.')
        if width is None:
            width = viewbox[2] * 25.4 / 96
        if height is None:
            height = viewbox[3] * 25.4 / 96
        sx, sy = width / viewbox[2], height / viewbox[3]
        aspect = root.get('preserveAspectRatio', 'xMidYMid meet')
        if 'slice' in aspect:
            raise ValueError('El SVG usa un recorte de viewport (slice). Exporta los trazos con el recorte aplicado.')
        if aspect != 'none':
            sx = sy = max(sx, sy) if 'slice' in aspect else min(sx, sy)
        matrix = _scale(sx, sy) @ _translation(-viewbox[0], -viewbox[1])
    paths, visited, point_count = [], 0, 0

    def walk(node, parent, inherited, chain=(), referenced=False):
        nonlocal visited, point_count
        visited += 1
        if visited > 50_000 or len(chain) > 20:
            raise ValueError('El SVG tiene demasiados objetos o referencias recursivas.')
        tag = node.tag.split('}')[-1]
        style = dict(inherited)
        style.update({k: v.strip() for k, v in (part.split(':', 1) for part in node.get('style', '').split(';') if ':' in part)})
        for key in ('display', 'visibility', 'fill', 'stroke', 'opacity', 'clip-path', 'mask', 'stroke-dasharray', 'filter', 'marker-start', 'marker-mid', 'marker-end'):
            if key in node.attrib:
                style[key] = node.get(key)
        if style.get('display') == 'none' or style.get('visibility') in ('hidden', 'collapse') or style.get('opacity') == '0':
            return
        if style.get('clip-path', 'none') != 'none' or style.get('mask', 'none') != 'none':
            raise ValueError('El SVG usa recortes o máscaras. Exporta una copia con los recortes aplicados a los trazos.')
        if style.get('stroke-dasharray', 'none') != 'none':
            _warn(warnings, 'Las líneas discontinuas SVG se importaron continuas. Convierte los guiones a trazos para conservarlos.')
        if style.get('filter', 'none') != 'none':
            _warn(warnings, 'Los filtros SVG no modifican los trazos importados; revisa la vista previa.')
        if any(style.get(key, 'none') != 'none' for key in ('marker-start', 'marker-mid', 'marker-end')):
            _warn(warnings, 'Se omitieron marcadores SVG como flechas. Expándelos a trazos para conservarlos.')
        transform = parent @ _svg_transform(node.get('transform', ''))
        n = lambda key, default=0: number(node.get(key, default), 'las coordenadas SVG')
        if tag in ('defs', 'symbol', 'clipPath', 'mask') and not referenced:
            return
        if tag == 'symbol' and node.get('viewBox'):
            raise ValueError('El SVG usa símbolos con su propio viewBox. Exporta una copia con las referencias expandidas a trazos.')
        if tag == 'use':
            ref = node.get('href', node.get('{http://www.w3.org/1999/xlink}href', ''))
            if not ref.startswith('#') or ref[1:] not in ids:
                _warn(warnings, 'Se omitieron referencias SVG externas o ausentes. Usa una exportación autocontenida.')
                return
            if ref in chain:
                raise ValueError('El SVG contiene referencias circulares.')
            walk(ids[ref[1:]], transform @ _translation(n('x'), n('y')), style, chain + (ref,), True)
            return
        local = []
        # Curve tolerance scales with the final physical size, including group transforms.
        magnification = max(np.linalg.norm(transform[:2, :2], axis=0))
        tolerance = .04 / max(magnification, 1e-8)
        if tag == 'path':
            local = _svg_path(node.get('d', ''), tolerance)
        elif tag == 'line':
            local = [np.array([[n('x1'), n('y1')], [n('x2'), n('y2')]])]
        elif tag in ('polyline', 'polygon'):
            nums = [number(v, 'los puntos SVG') for v in NUM.findall(node.get('points', ''))]
            if len(nums) % 2:
                raise ValueError('El SVG tiene una lista de puntos incompleta.')
            pts = np.array(nums).reshape(-1, 2)
            if tag == 'polygon' and len(pts):
                pts = np.vstack([pts, pts[0]])
            local = [pts]
        elif tag == 'rect':
            x, y, w, h = n('x'), n('y'), n('width'), n('height')
            if min(w, h) < 0:
                raise ValueError('El SVG contiene dimensiones negativas.')
            rx, ry = min(abs(n('rx', n('ry'))), w / 2), min(abs(n('ry', n('rx'))), h / 2)
            if rx and ry:
                local = _svg_path(f'M{x+rx} {y} H{x+w-rx} A{rx} {ry} 0 0 1 {x+w} {y+ry} V{y+h-ry} A{rx} {ry} 0 0 1 {x+w-rx} {y+h} H{x+rx} A{rx} {ry} 0 0 1 {x} {y+h-ry} V{y+ry} A{rx} {ry} 0 0 1 {x+rx} {y} Z', tolerance)
            else:
                local = [np.array([[x, y], [x+w, y], [x+w, y+h], [x, y+h], [x, y]])]
        elif tag in ('circle', 'ellipse'):
            rx, ry = (n('r'), n('r')) if tag == 'circle' else (n('rx'), n('ry'))
            if min(rx, ry) < 0:
                raise ValueError('El SVG contiene radios negativos.')
            if rx and ry:
                angles = np.linspace(0, 2 * math.pi, min(2049, max(17, int(2 * math.pi * math.sqrt(max(rx, ry) / max(tolerance, 1e-8))) + 1)))
                local = [np.column_stack([n('cx') + rx * np.cos(angles), n('cy') + ry * np.sin(angles)])]
        elif tag in ('text', 'tspan'):
            _warn(warnings, 'Se omitió texto SVG editable. Exporta el texto convertido a trazos para conservar etiquetas y valores.')
            return
        elif tag in ('image', 'foreignObject'):
            _warn(warnings, 'Se omitieron imágenes incrustadas; importa la imagen o el PDF por separado.')
            return
        elif tag == 'style':
            raise ValueError('El SVG usa una hoja de estilos. Exporta una copia con los estilos en cada objeto.')
        elif tag == 'svg' and node is not root:
            raise ValueError('El SVG contiene páginas anidadas. Exporta una sola página con sus trazos.')
        elif tag not in ('svg', 'g', 'a', 'defs', 'symbol', 'title', 'desc', 'metadata', 'namedview'):
            if tag in ('script', 'filter', 'linearGradient', 'radialGradient', 'pattern'):
                return
            _warn(warnings, f'Se omitió un objeto SVG no admitido: {tag}.')
        if local:
            # White fill backgrounds and explicit invisible geometry are not pen strokes.
            fill, stroke = style.get('fill', 'black'), style.get('stroke', 'none')
            if stroke == 'none' and fill.lower() in ('none', 'white', '#fff', '#ffffff'):
                local = []
            elif fill != 'none' and tag != 'line':
                _warn(warnings, 'Los rellenos se dibujan como contornos; el grosor y color originales no se convierten en tinta sólida.')
            paths.extend(_transform(local, transform))
            point_count += sum(len(p) for p in local)
            if point_count > MAX_POINTS:
                raise ValueError('El SVG contiene demasiados puntos.')
        for child in node:
            walk(child, transform, style, chain, referenced)

    walk(root, matrix, {})
    return paths


def _cross(a, b):
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def _stl(data, projection, edge_mode, warnings):
    axes = {'xy': (0, 1), 'xz': (0, 2), 'yz': (1, 2)}
    if projection not in axes or edge_mode not in ('outline', 'features'):
        raise ValueError('Elige vista XY, XZ o YZ y contorno o aristas.')
    count = struct.unpack_from('<I', data, 80)[0] if len(data) >= 84 else 0
    binary = count > 0 and len(data) == 84 + 50 * count
    if binary:
        if count > MAX_FACES:
            raise ValueError(f'El STL excede {MAX_FACES} triángulos. Simplifica la malla antes de importarla.')
        dtype = np.dtype([('normal', '<f4', 3), ('vertices', '<f4', (3, 3)), ('attribute', '<u2')])
        triangles = np.frombuffer(data, dtype=dtype, count=count, offset=84)['vertices'].astype(float)
    else:
        try:
            text = data.decode('ascii')
        except UnicodeDecodeError:
            raise ValueError('El STL binario está truncado o tiene un número de triángulos incorrecto.')
        if not text.lstrip().lower().startswith('solid'):
            raise ValueError('No se pudo leer el STL ASCII o binario.')
        raw = re.findall(r'\bvertex\s+(' + NUMBER + r')\s+(' + NUMBER + r')\s+(' + NUMBER + r')', text, re.I)
        if not raw or len(raw) % 3 or len(raw) != len(re.findall(r'\bvertex\b', text, re.I)) or len(raw) // 3 > MAX_FACES:
            raise ValueError(f'El STL tiene triángulos incompletos o excede {MAX_FACES} triángulos.')
        triangles = np.array(raw, dtype=float).reshape(-1, 3, 3)
    if not np.isfinite(triangles).all():
        raise ValueError('El STL contiene coordenadas no finitas.')
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 1e-15
    if not valid.all():
        _warn(warnings, 'Se omitieron triángulos degenerados del STL.')
    triangles, normals = triangles[valid], normals[valid] / lengths[valid, None]
    if not len(triangles):
        raise ValueError('El STL no contiene triángulos con superficie.')
    projected = triangles[:, :, axes[projection]].copy()
    projected[:, :, 1] *= -1  # world up to paper down
    paths = _triangle_union_outline(projected)
    if not paths:
        paths = _unique_segments([edge for tri in projected for edge in (tri[[0, 1]], tri[[1, 2]], tri[[2, 0]])])
        _warn(warnings, 'Esta vista del STL no tiene superficie proyectada; sólo se dibujan segmentos.')
    if edge_mode == 'features':
        extent = max(float(np.ptp(triangles.reshape(-1, 3), axis=0).max()), 1e-12)
        adjacency = defaultdict(list)
        for face, triangle in enumerate(triangles):
            for a, b in ((0, 1), (1, 2), (2, 0)):
                key = tuple(sorted(tuple(np.round(v / extent, 9)) for v in triangle[[a, b]]))
                adjacency[key].append((face, a, b))
        extra = []
        for owners in adjacency.values():
            if len(owners) != 2 or float(np.dot(normals[owners[0][0]], normals[owners[1][0]])) < math.cos(math.radians(30)):
                face, a, b = owners[0]
                extra.append(projected[face, [a, b]])
        paths = _unique_segments(paths + extra)
        _warn(warnings, 'Aristas STL de 30° o más: incluye aristas ocultas. La vista no es un plano técnico con eliminación de líneas ocultas.')
    _warn(warnings, 'STL no guarda unidades: confirma cuántos mm representa una unidad y revisa las medidas. Es una proyección 2D para pluma.')
    return _join_segments(paths)


def _triangle_union_outline(projected):
    """Exact segment clipping against triangle union, accelerated with spatial bins.

    Each triangle edge contributes only the parameter intervals whose outward
    side is not covered by another triangle. Intersections retain their actual
    coordinates; holes and concave outlines survive (unlike a convex hull).
    """
    extent = max(float(np.ptp(projected.reshape(-1, 2), axis=0).max()), 1e-12)
    origin = projected.reshape(-1, 2).min(0)
    triangles = (projected - origin) / extent
    signed = _cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    triangles = triangles[np.abs(signed) > 1e-12]
    signed = signed[np.abs(signed) > 1e-12]
    triangles[signed < 0] = triangles[signed < 0][:, [0, 2, 1]]
    if not len(triangles):
        return []
    seen, unique = set(), []
    for tri in triangles:
        key = tuple(sorted(tuple(np.round(v, 10)) for v in tri))
        if key not in seen:
            seen.add(key); unique.append(tri)
    triangles = np.array(unique)
    bins, size = defaultdict(list), min(64, max(1, int(math.sqrt(len(triangles)))))
    lows = np.floor(triangles.min(1) * size).astype(int)
    highs = np.floor(triangles.max(1) * size).astype(int)
    memberships = 0
    for i, (low, high) in enumerate(zip(lows, highs)):
        memberships += int(np.prod(high - low + 1))
        if memberships > 2_000_000:
            raise ValueError('La proyección STL contiene demasiadas superficies superpuestas. Simplifica la malla.')
        for x in range(low[0], high[0] + 1):
            for y in range(low[1], high[1] + 1):
                bins[x, y].append(i)
    edges, seen = [], set()
    for tri in triangles:
        for a, b in ((0, 1), (1, 2), (2, 0)):
            edge = tri[[a, b]]
            key = tuple(sorted(tuple(np.round(v, 10)) for v in edge))
            if key not in seen:
                seen.add(key); edges.append(edge)
    out, work = [], 0
    for edge in edges:
        a, b = edge; direction = b - a
        low, high = np.floor(edge.min(0) * size).astype(int), np.floor(edge.max(0) * size).astype(int)
        indices = set()
        for x in range(low[0], high[0] + 1):
            for y in range(low[1], high[1] + 1):
                indices.update(bins[x, y])
        candidates = triangles[list(indices)]
        work += len(candidates)
        if work > 8_000_000:
            raise ValueError('La proyección STL es demasiado compleja. Simplifica la malla o importa otra vista.')
        candidates = candidates[np.min(_cross(direction, candidates - a), axis=1) < -1e-10]
        intervals = []
        if len(candidates):
            segments = np.roll(candidates, -1, axis=1) - candidates
            intercept = _cross(segments, a - candidates)
            slope = _cross(segments, direction)
            lower, upper = np.zeros(len(candidates)), np.ones(len(candidates))
            valid = np.ones(len(candidates), dtype=bool)
            for j in range(3):
                positive, negative = slope[:, j] > 1e-12, slope[:, j] < -1e-12
                crossing = np.divide(-intercept[:, j], slope[:, j], out=np.zeros(len(candidates)), where=np.abs(slope[:, j]) > 1e-12)
                lower[positive] = np.maximum(lower[positive], crossing[positive])
                upper[negative] = np.minimum(upper[negative], crossing[negative])
                valid &= (np.abs(slope[:, j]) > 1e-12) | (intercept[:, j] >= -1e-10)
            valid &= upper - lower > 1e-10
            intervals = sorted(zip(np.clip(lower[valid], 0, 1), np.clip(upper[valid], 0, 1)))
        cursor = 0.
        for start, end in intervals:
            if start > cursor + 1e-9:
                out.append(np.array([a + cursor * direction, a + start * direction]) * extent + origin)
            cursor = max(cursor, end)
        if cursor < 1 - 1e-9:
            out.append(np.array([a + cursor * direction, b]) * extent + origin)
    return out


def _unique_segments(paths):
    if not paths:
        return []
    extent = max(float(np.ptp(np.vstack(paths), axis=0).max()), 1e-12)
    seen, out = set(), []
    for path in paths:
        for a, b in zip(path[:-1], path[1:]):
            if np.linalg.norm(b - a) <= extent * 1e-9:
                continue
            key = tuple(sorted(tuple(np.round(v / extent, 9)) for v in (a, b)))
            if key not in seen:
                seen.add(key); out.append(np.array([a, b]))
    return out


def _join_segments(paths):
    """Join degree-two outline edges without connecting crossings or branches."""
    edges = _merge_collinear_segments(_unique_segments(paths))
    if not edges:
        return []
    extent = max(float(np.ptp(np.vstack(edges), axis=0).max()), 1e-12)
    adjacency = defaultdict(list)
    keys = []
    for i, p in enumerate(edges):
        key = [tuple(np.round(v / extent, 8)) for v in p]
        keys.append(key)
        for end, point in enumerate(key):
            adjacency[point].append((i, end))
    used, out = set(), []
    # Open chains first, then closed loops.
    starts = [(i, end) for i, key in enumerate(keys) for end in (0, 1) if len(adjacency[key[end]]) != 2]
    starts.extend((i, 0) for i in range(len(edges)))
    for i, end in starts:
        if i in used:
            continue
        path = [edges[i][end].copy()]
        while i not in used:
            used.add(i); path.append(edges[i][1 - end].copy())
            point = keys[i][1 - end]
            if len(adjacency[point]) != 2:
                break
            following = [(j, e) for j, e in adjacency[point] if j not in used]
            if not following:
                break
            i, end = following[0]
        out.append(np.array(path))
    return out


def _merge_collinear_segments(edges):
    """Union coincident boundary intervals from overlapping projected faces."""
    if not edges:
        return []
    extent = max(float(np.ptp(np.vstack(edges), axis=0).max()), 1e-12)
    groups = defaultdict(list)
    frames = {}
    for edge in edges:
        direction = edge[1] - edge[0]
        direction /= np.linalg.norm(direction)
        if direction[0] < -1e-10 or (abs(direction[0]) <= 1e-10 and direction[1] < 0):
            direction *= -1
        normal = np.array([-direction[1], direction[0]])
        offset = np.dot(normal, edge[0])
        key = (*np.round(direction, 8), round(float(offset / extent), 8))
        frames[key] = (direction, normal, offset)
        groups[key].append(tuple(sorted(edge @ direction)))
    out = []
    for key, intervals in groups.items():
        direction, normal, offset = frames[key]
        start, end = sorted(intervals)[0]
        for low, high in sorted(intervals)[1:]:
            if low <= end + extent * 1e-8:
                end = max(end, high)
            else:
                out.append(np.array([start * direction + offset * normal, end * direction + offset * normal]))
                start, end = low, high
        out.append(np.array([start * direction + offset * normal, end * direction + offset * normal]))
    return out


def _circular(center, radius, start=0., delta=2 * math.pi):
    radius = number(radius, 'el radio', 0)
    count = min(2049, max(3, int(math.ceil(abs(delta) * math.sqrt(max(radius, .1) / .04))) + 1))
    angles = np.linspace(start, start + delta, count)
    return np.asarray(center) + radius * np.column_stack([np.cos(angles), np.sin(angles)])


def _bulge(start, end, bulge):
    start, end = np.asarray(start), np.asarray(end)
    direction = end - start
    if abs(bulge) < 1e-12 or np.linalg.norm(direction) < 1e-12:
        return np.array([start, end])
    center = (start + end) / 2 + np.array([-direction[1], direction[0]]) * (1 - bulge * bulge) / (4 * bulge)
    radius = np.linalg.norm(start - center)
    angle = math.atan2(start[1] - center[1], start[0] - center[0])
    out = _circular(center, radius, angle, 4 * math.atan(bulge))
    out[0], out[-1] = start, end
    return out


def _decode(data):
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('cp1252', errors='replace')


def _technical_text(text, position, size, angle=0, justify='center', vertical='center'):
    """Readable annotation strokes from the app's actual single-line font."""
    from . import fonts
    font = fonts.get_font('HersheySans1')
    paths, x, baseline = [], 0., 0.
    text = str(text).replace('~{', '').replace('}', '')
    if len(text) > 2000:
        raise ValueError('El esquema contiene una etiqueta demasiado larga.')
    for char in text:
        if char == '\n':
            x = 0.; baseline += size * 1.5
            continue
        advance, strokes = font.glyph(char)
        for p in strokes:
            paths.append(p * [size / font.upm, -size / font.upm] + [x, baseline])
        x += advance * size / font.upm
    if not paths:
        return []
    points = np.vstack(paths); low, high = points.min(0), points.max(0)
    shift = np.array([{'left': low[0], 'right': high[0]}.get(justify, (low[0] + high[0]) / 2),
                      {'top': low[1], 'bottom': high[1]}.get(vertical, (low[1] + high[1]) / 2)])
    return _transform([p - shift for p in paths], _translation(*position) @ _rotation(angle))


def _dxf(data, warnings):
    text = _decode(data)
    if text.startswith('AutoCAD Binary DXF'):
        raise ValueError('Exporta el DXF en formato ASCII; el DXF binario no está admitido.')
    lines = text.splitlines()
    if len(lines) % 2:
        raise ValueError('El DXF tiene un par de códigos incompleto.')
    pairs = []
    for a, b in zip(lines[::2], lines[1::2]):
        try:
            code = int(a.strip())
        except ValueError:
            raise ValueError('El DXF contiene un código de grupo inválido.')
        pairs.append((code, b.strip()))
    if not any(code == 0 and value == 'SECTION' for code, value in pairs):
        raise ValueError('El archivo no es un DXF ASCII válido.')
    units = 0
    for index, pair in enumerate(pairs[:-1]):
        if pair == (9, '$INSUNITS'):
            units = int(number(pairs[index + 1][1], 'las unidades DXF', 0, 24))
    factors = {0: 1., 1: 25.4, 2: 304.8, 3: 1_609_344., 4: 1., 5: 10., 6: 1000., 7: 1_000_000., 8: .0000254, 9: .0254, 10: 914.4, 11: 1e-7, 12: 1e-6, 13: .001, 14: 100., 15: 10_000., 16: 100_000.}
    if units not in factors:
        raise ValueError('Las unidades astronómicas o especiales de este DXF no están admitidas. Expórtalo en mm.')
    if units == 0:
        _warn(warnings, 'DXF sin unidad declarada: se interpreta una unidad como 1 mm. Confirma la escala.')
    records, current = [], None
    for code, value in pairs:
        if code == 0:
            current = [(code, value)]; records.append(current)
        elif current is not None:
            current.append((code, value))
    entities, blocks, section, active = [], {}, '', None
    for record in records:
        kind = record[0][1]
        get = lambda code, default='': next((v for c, v in record if c == code), default)
        if kind == 'SECTION':
            section = get(2)
        elif kind == 'ENDSEC':
            section = ''; active = None
        elif section == 'BLOCKS' and kind == 'BLOCK':
            active = get(2); blocks[active] = {'base': [number(get(10, 0)), number(get(20, 0))], 'records': []}
        elif section == 'BLOCKS' and kind == 'ENDBLK':
            active = None
        elif section == 'BLOCKS' and active:
            blocks[active]['records'].append(record)
        elif section == 'ENTITIES':
            entities.append(record)

    point_count = 0

    def draw(records, matrix, chain=()):
        nonlocal point_count
        if len(chain) > 16:
            raise ValueError('El DXF contiene demasiados bloques anidados.')
        out, index = [], 0
        while index < len(records):
            record = records[index]; index += 1
            kind = record[0][1]
            get = lambda code, default=0: next((v for c, v in record if c == code), default)
            n = lambda code, default=0: number(get(code, default), 'las coordenadas DXF')
            point = lambda code=10: np.array([n(code), n(code + 10)])
            local = []
            if get(60, '0') == '1':
                continue
            if n(210) or n(220) or n(230, 1) not in (0, 1):
                raise ValueError('El DXF usa un plano OCS inclinado. Exporta la vista a XY antes de importarla.')
            if any(n(code) for code in (30, 31, 32, 33, 38)):
                _warn(warnings, 'Las coordenadas Z del DXF se proyectaron a XY.')
            if kind == 'LINE':
                local = [np.array([point(), point(11)])]
            elif kind in ('CIRCLE', 'ARC'):
                start = math.radians(n(50)) if kind == 'ARC' else 0
                delta = (math.radians(n(51)) - start) % (2 * math.pi) if kind == 'ARC' else 2 * math.pi
                local = [_circular(point(), n(40), start, delta)]
            elif kind == 'ELLIPSE':
                major, ratio = point(11), n(40, 1)
                if not 0 < ratio <= 1:
                    raise ValueError('El DXF contiene una elipse inválida.')
                minor = np.array([-major[1], major[0]]) * ratio
                start, end = n(41), n(42, 2 * math.pi)
                angles = np.linspace(start, start + ((end - start) % (2 * math.pi) or 2 * math.pi), 129)
                local = [point() + np.cos(angles)[:, None] * major + np.sin(angles)[:, None] * minor]
            elif kind in ('LWPOLYLINE', 'POLYLINE'):
                vertices = []
                if kind == 'LWPOLYLINE':
                    for code, value in record:
                        if code == 10:
                            vertices.append([number(value), 0., 0.])
                        elif code in (20, 42) and vertices:
                            vertices[-1][1 if code == 20 else 2] = number(value)
                else:
                    while index < len(records) and records[index][0][1] == 'VERTEX':
                        vr = dict(records[index]); index += 1
                        vertices.append([number(vr.get(10, 0)), number(vr.get(20, 0)), number(vr.get(42, 0))])
                    if int(n(70)) & (16 | 64):
                        _warn(warnings, 'Se omitió una malla DXF; exporta su vista como líneas o usa STL.'); continue
                if len(vertices) > 1:
                    if int(n(70)) & 1:
                        vertices.append(vertices[0])
                    sections = [_bulge(a[:2], b[:2], a[2]) for a, b in zip(vertices[:-1], vertices[1:])]
                    local = [np.vstack([p[:-1] for p in sections] + [sections[-1][-1:]])]
            elif kind in ('SOLID', 'TRACE', '3DFACE'):
                order = (10, 11, 12, 13) if kind == '3DFACE' else (10, 11, 13, 12)
                local = [np.array([point(code) for code in order] + [point()])]
            elif kind == 'INSERT':
                name = str(get(2, ''))
                if name not in blocks:
                    _warn(warnings, f'Se omitió un bloque DXF ausente: {name[:60]}.'); continue
                if name in chain:
                    raise ValueError('El DXF contiene referencias circulares entre bloques.')
                if n(70, 1) != 1 or n(71, 1) != 1:
                    raise ValueError('El DXF usa una matriz de bloques. Descompónla antes de importar.')
                base = blocks[name]['base']
                transform = _translation(*point()) @ _rotation(n(50)) @ _scale(n(41, 1), n(42, 1)) @ _translation(-base[0], -base[1])
                out.extend(draw(blocks[name]['records'], matrix @ transform, chain + (name,)))
            elif kind in ('TEXT', 'MTEXT'):
                # Original CAD typography can be arbitrary; all text is visibly reported as a substitution.
                content = str(get(1, '')) if kind == 'TEXT' else ''.join(v for c, v in record if c in (1, 3))
                content = content.replace('\\P', '\n')
                content = re.sub(r'\\[A-Za-z][^;]*;', '', content).replace('{', '').replace('}', '')
                local = _technical_text(content, point(), n(40, 2.5), n(50), 'left', 'bottom')
                _warn(warnings, 'Las etiquetas DXF usan letra técnica de un trazo; comprueba su posición y caracteres.')
            elif kind not in ('SEQEND', 'ENDSEC', 'EOF', 'POINT'):
                _warn(warnings, f'Se omitió una entidad DXF no admitida: {kind[:40]}. Explótala o exporta SVG.')
            out.extend(_transform(local, matrix))
            point_count += sum(len(p) for p in local)
            if point_count > MAX_POINTS:
                raise ValueError('El DXF contiene demasiados puntos.')
        return out

    # DXF Y points up; the editor's Y points down.
    return draw(entities, _scale(factors[units], -factors[units]))


def _sexpr(text):
    tokens = re.finditer(r'"(?:\\.|[^"\\])*"|;[^\n]*|[()]|[^\s();]+', text)
    stack, root, count = [], None, 0
    for match in tokens:
        token = match[0]
        if token.startswith(';'):
            continue
        count += 1
        if count > 1_000_000 or len(stack) > 100:
            raise ValueError('El esquema KiCad tiene demasiados objetos o niveles anidados.')
        if token == '(':
            node = []
            if stack:
                stack[-1].append(node)
            elif root is None:
                root = node
            else:
                raise ValueError('El esquema KiCad tiene varias raíces.')
            stack.append(node)
        elif token == ')':
            if not stack:
                raise ValueError('El esquema KiCad tiene paréntesis incompletos.')
            stack.pop()
        else:
            if not stack:
                raise ValueError('El esquema KiCad contiene datos fuera de su raíz.')
            if token.startswith('"'):
                try:
                    token = json.loads(token)
                except json.JSONDecodeError:
                    raise ValueError('El esquema KiCad contiene una cadena de texto inválida.')
            stack[-1].append(token)
    if stack or not root or root[0] != 'kicad_sch':
        raise ValueError('El archivo no es un esquema .kicad_sch válido.')
    return root


def _children(node, tag):
    return [value for value in node if isinstance(value, list) and value and value[0] == tag]


def _child(node, tag, default=None):
    return next(iter(_children(node, tag)), [tag] + ([] if default is None else list(default)))


def _xy(node, tag='at', default=(0, 0)):
    values = (node if node and node[0] == tag else _child(node, tag, default))[1:]
    if len(values) < 2:
        raise ValueError('El esquema contiene una posición incompleta.')
    return np.array([number(v, 'las coordenadas del esquema') for v in values[:2]])


def _three_point_arc(start, mid, end):
    start, mid, end = map(np.asarray, (start, mid, end))
    matrix = 2 * np.array([mid - start, end - start])
    if abs(np.linalg.det(matrix)) < 1e-12:
        return np.array([start, mid, end])
    center = np.linalg.solve(matrix, [np.dot(mid, mid) - np.dot(start, start), np.dot(end, end) - np.dot(start, start)])
    angles = [math.atan2(p[1] - center[1], p[0] - center[0]) for p in (start, mid, end)]
    delta = (angles[2] - angles[0]) % (2 * math.pi)
    if (angles[1] - angles[0]) % (2 * math.pi) > delta:
        delta -= 2 * math.pi
    out = _circular(center, np.linalg.norm(start - center), angles[0], delta)
    out[0], out[-1] = start, end
    return out


def _kicad_text(node, warnings, content=None):
    effects = _child(node, 'effects')
    hidden = _children(effects, 'hide')
    if 'hide' in effects or (hidden and (len(hidden[0]) == 1 or hidden[0][1] in ('yes', 'true'))):
        return []
    text = content if content is not None else (node[1] if len(node) > 1 and not isinstance(node[1], list) else '')
    at = _child(node, 'at', (0, 0, 0))[1:]
    position = [number(v, 'la posición de la etiqueta') for v in at[:2]]
    angle = number(at[2]) if len(at) > 2 else 0
    size = _child(_child(effects, 'font'), 'size', (1.27, 1.27))[1:]
    justify = _child(effects, 'justify')[1:]
    _warn(warnings, 'Las etiquetas del esquema usan letra técnica de un trazo; comprueba caracteres y alineación.')
    return _technical_text(text, position, number(size[0], 'el tamaño de letra', .01, 100), -angle,
                           next((v for v in justify if v in ('left', 'right')), 'center'),
                           next((v for v in justify if v in ('top', 'bottom')), 'center'))


def _kicad_graphic(node, warnings):
    tag = node[0]
    if tag in ('wire', 'bus', 'polyline', 'bezier'):
        pts = np.array([_xy(p, 'xy') for p in _children(_child(node, 'pts'), 'xy')])
        if tag == 'bezier':
            if len(pts) != 4:
                raise ValueError('El esquema contiene una curva incompleta.')
            return [_curve(pts, .04)]
        return [pts] if len(pts) >= 2 else []
    if tag == 'rectangle':
        a, b = _xy(node, 'start'), _xy(node, 'end')
        return [np.array([a, [b[0], a[1]], b, [a[0], b[1]], a])]
    if tag == 'circle':
        return [_circular(_xy(node, 'center'), number(_child(node, 'radius', (.5,))[1]))]
    if tag == 'arc':
        return [_three_point_arc(_xy(node, 'start'), _xy(node, 'mid'), _xy(node, 'end'))]
    if tag == 'bus_entry':
        a, delta = _xy(node), _xy(node, 'size')
        return [np.array([a, a + delta])]
    if tag == 'junction':
        diameter = number(_child(node, 'diameter', (.8,))[1]) or .8
        return [_circular(_xy(node), diameter / 2)]
    if tag == 'no_connect':
        a = _xy(node)
        return [a + [[-.4, -.4], [.4, .4]], a + [[-.4, .4], [.4, -.4]]]
    if tag in ('text', 'label', 'global_label', 'hierarchical_label', 'property'):
        if tag in ('global_label', 'hierarchical_label'):
            _warn(warnings, 'Las etiquetas globales y jerárquicas conservan el texto, pero no sus marcos de dirección.')
        return _kicad_text(node, warnings, node[2] if tag == 'property' and len(node) > 2 else None)
    if tag == 'pin':
        hidden = _children(node, 'hide')
        if 'hide' in node or (hidden and (len(hidden[0]) == 1 or hidden[0][1] in ('yes', 'true'))):
            return []
        at = _child(node, 'at', (0, 0, 0))[1:]
        a = np.array([number(v) for v in at[:2]])
        angle = math.radians(number(at[2]) if len(at) > 2 else 0)
        length = number(_child(node, 'length', (2.54,))[1], 'el largo del pin', 0)
        direction = np.array([math.cos(angle), math.sin(angle)])
        paths = [np.array([a, a + direction * length])]
        if len(node) > 2 and node[2] != 'line':
            _warn(warnings, 'Algunos pines especiales se dibujaron como líneas; exporta SVG para conservar burbujas y marcas lógicas.')
        if _children(node, 'name') or _children(node, 'number'):
            _warn(warnings, 'Se omitieron nombres y números individuales de pines; las referencias y valores del símbolo sí se conservan.')
        return paths
    if tag in ('image', 'text_box', 'table'):
        _warn(warnings, f'Se omitió un objeto KiCad: {tag}. Exporta SVG o PDF para conservarlo.')
    return []


def _kicad(data, warnings):
    root = _sexpr(_decode(data))
    library = {node[1]: node for node in _children(_child(root, 'lib_symbols'), 'symbol') if len(node) > 1}
    paths, point_count = [], 0

    def add(new_paths):
        nonlocal point_count
        point_count += sum(len(p) for p in new_paths)
        if point_count > MAX_POINTS:
            raise ValueError('El esquema KiCad contiene demasiados puntos.')
        paths.extend(new_paths)

    def symbol_geometry(name, unit, style, chain=()):
        if name in chain or len(chain) > 16:
            raise ValueError('El esquema KiCad tiene símbolos con herencia circular.')
        symbol = library.get(name)
        if symbol is None:
            _warn(warnings, f'Falta el símbolo incrustado {name[:70]}; no se inventó su geometría. Exporta SVG desde KiCad.')
            return []
        out = []
        extends = _child(symbol, 'extends')[1:]
        if extends:
            parent = extends[0]
            if parent not in library and ':' in name:
                parent = name.rsplit(':', 1)[0] + ':' + parent
            out.extend(symbol_geometry(parent, unit, style, chain + (name,)))
        for sub in _children(symbol, 'symbol'):
            match = re.search(r'_(\d+)_(\d+)$', str(sub[1]))
            if match and (int(match[1]) not in (0, unit) or int(match[2]) not in (0, style)):
                continue
            for graphic in sub[2:]:
                if isinstance(graphic, list) and graphic:
                    out.extend(_kicad_graphic(graphic, warnings))
        return out

    for node in root[1:]:
        if not isinstance(node, list) or not node:
            continue
        if node[0] == 'symbol':
            lib_id = _child(node, 'lib_id')[1:]
            name = lib_id[0] if lib_id else str(node[1])
            at = _child(node, 'at', (0, 0, 0))[1:]
            angle = number(at[2]) if len(at) > 2 else 0
            unit = int(number(_child(node, 'unit', (1,))[1]))
            style = int(number(_child(node, 'body_style', _child(node, 'convert', (1,))[1:])[1]))
            mirror = _child(node, 'mirror')[1:]
            # Library Y points up; schematic Y points down. Instance rotation is CCW.
            flip = _scale(1, -1)
            if mirror:
                if mirror[0] not in ('x', 'y'):
                    raise ValueError('El símbolo KiCad tiene un espejo desconocido.')
                flip = flip @ (_scale(1, -1) if mirror[0] == 'x' else _scale(-1, 1))
            matrix = _translation(*_xy(node)) @ _rotation(-angle) @ flip
            add(_transform(symbol_geometry(name, unit, style), matrix))
            for prop in _children(node, 'property'):
                add(_kicad_graphic(prop, warnings))
        elif node[0] == 'sheet':
            a, size = _xy(node), _xy(node, 'size')
            add([np.array([a, a + [size[0], 0], a + size, a + [0, size[1]], a])])
            for prop in _children(node, 'property'):
                add(_kicad_graphic(prop, warnings))
            _warn(warnings, 'Sólo se importó esta hoja. Las subhojas jerárquicas requieren archivos separados o una exportación PDF.')
        else:
            add(_kicad_graphic(node, warnings))
    _warn(warnings, 'Importación geométrica del esquema: no valida conexiones eléctricas ni genera una PCB.')
    return paths


def _legacy_schematic(data, warnings):
    text = _decode(data)
    if not text.startswith('EESchema Schematic File Version'):
        raise ValueError('El .sch no es un esquema antiguo de KiCad. Exporta tu programa de circuitos a SVG o PDF.')
    lines, paths, index = text.splitlines(), [], 0
    factor = .0254  # legacy schematic coordinates are mils
    while index < len(lines):
        line = lines[index].strip(); index += 1
        if line.startswith(('Wire ', 'Entry ')) and index < len(lines):
            values = [number(v) for v in NUM.findall(lines[index])]; index += 1
            if len(values) == 4:
                paths.append(np.array(values).reshape(2, 2) * factor)
        elif line.startswith(('Connection ', 'NoConn ')):
            values = [number(v) for v in NUM.findall(line)]
            if len(values) >= 2:
                a = np.array(values[-2:]) * factor
                if line.startswith('Connection '):
                    paths.append(_circular(a, .4))
                else:
                    paths.extend([a + [[-.4, -.4], [.4, .4]], a + [[-.4, .4], [.4, -.4]]])
        elif line.startswith('Text ') and index < len(lines):
            parts = line.split()
            content = lines[index]; index += 1
            if len(parts) >= 6:
                position = [number(parts[2]) * factor, number(parts[3]) * factor]
                rotation = -90 if int(number(parts[4])) in (1, 3) else 0
                paths.extend(_technical_text(content, position, number(parts[5]) * factor, rotation, 'left', 'bottom'))
                _warn(warnings, 'Las etiquetas del esquema usan letra técnica de un trazo; comprueba caracteres y alineación.')
        elif line == '$Comp':
            _warn(warnings, 'El .sch antiguo no incrusta las bibliotecas: se omitieron los cuerpos de componentes. Abre el proyecto en KiCad y exporta SVG o guarda .kicad_sch para conservarlos.')
        elif line.startswith('F '):
            match = re.match(r'F\s+[01]\s+"((?:\\.|[^"\\])*)"\s+([HV])\s+('+NUMBER+r')\s+('+NUMBER+r')\s+('+NUMBER+r')\s+(\S+)', line)
            if match and match[6] == '0000':
                paths.extend(_technical_text(match[1], [float(match[3]) * factor, float(match[4]) * factor], float(match[5]) * factor, -90 if match[2] == 'V' else 0))
        elif line == '$Sheet':
            _warn(warnings, 'Las hojas jerárquicas del .sch requieren una exportación SVG/PDF; sólo se importaron los trazos de esta hoja.')
    _warn(warnings, 'Importación geométrica del esquema: no valida conexiones eléctricas ni genera una PCB.')
    return paths
