"""Imagen -> trazos estilo boceto a lápiz (contornos + rayado)."""
import io
import math

import cv2
import numpy as np
from PIL import Image, ImageOps

from . import pathops
from concurrent.futures import CancelledError

WORK_SIDE = 1000  # lado mayor de la imagen de trabajo, en px
_OFFS = [(0, 1), (1, 0), (0, -1), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)]


def load_image(data):
    """Bytes de imagen -> RGB uint8 (respeta EXIF, fondo blanco)."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, "white")
        img = Image.alpha_composite(bg, img)
    g = np.asarray(img.convert("RGB"))
    f = min(1.0, 1600 / max(g.shape[:2]))
    if f < 1:
        g = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return g


def _trace(skel):
    """Recorre un esqueleto de 1 px y devuelve polilíneas en px (x, y)."""
    H, W = skel.shape
    pad = np.zeros((H + 2, W + 2), np.uint8)
    pad[1:-1, 1:-1] = skel > 0
    deg = sum(np.roll(np.roll(pad, dy, 0), dx, 1) for dy, dx in _OFFS) * pad
    rows = [bytearray(r.tobytes()) for r in pad]
    seen = [bytearray(W + 2) for _ in range(H + 2)]

    def walk(y, x):
        path = []
        while True:
            for dy, dx in _OFFS:
                ny, nx = y + dy, x + dx
                if rows[ny][nx] and not seen[ny][nx]:
                    break
            else:
                return path
            y, x = ny, nx
            seen[y][x] = 1
            path.append((x - 1, y - 1))

    ys, xs = np.nonzero(pad)
    order = np.argsort(deg[ys, xs] != 1, kind="stable")  # primero los extremos
    paths = []
    for i in order:
        y, x = int(ys[i]), int(xs[i])
        if seen[y][x]:
            continue
        seen[y][x] = 1
        fwd = walk(y, x)
        back = walk(y, x)
        pts = back[::-1] + [(x - 1, y - 1)] + fwd
        if len(pts) > 1:
            paths.append(np.array(pts, dtype=np.float64))
    return paths


def _smooth(p, win=5):
    if len(p) <= win:
        return p
    k = np.ones(win) / win
    q = p.copy()
    h = win // 2
    q[h:-h, 0] = np.convolve(p[:, 0], k, "valid")
    q[h:-h, 1] = np.convolve(p[:, 1], k, "valid")
    return q


def _contours(g, detail, ppm):
    sigma = 3.0 + (0.8 - 3.0) * detail
    hi = 140 + (40 - 140) * detail
    blur = cv2.GaussianBlur(g, (0, 0), sigma)
    edges = cv2.Canny(blur, hi * 0.4, hi, L2gradient=True)
    edges = cv2.ximgproc.thinning(edges)
    min_len = (4.0 + (1.0 - 4.0) * detail) * ppm
    out = []
    for p in _trace(edges):
        if len(p) < min_len:
            continue
        out.append(pathops.simplify(_smooth(p), 0.6))
    return out


def crop_image(rgb, crop):
    """Recorte normalizado sobre el original; no modifica la imagen guardada."""
    if crop is None:
        return rgb
    try:
        values = [crop[k] for k in ('x', 'y', 'w', 'h')]
        if any(isinstance(v, bool) for v in values):
            raise ValueError()
        x, y, w, h = map(float, values)
        if not np.isfinite([x, y, w, h]).all() or min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.000000001 or y + h > 1.000000001:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('El recorte debe estar dentro de la imagen y tener ancho y alto positivos.')
    H, W = rgb.shape[:2]
    x0, y0 = int(math.floor(x * W + 1e-8)), int(math.floor(y * H + 1e-8))
    x1, y1 = min(W, int(math.ceil((x + w) * W - 1e-8))), min(H, int(math.ceil((y + h) * H - 1e-8)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise ValueError('El recorte debe conservar al menos 2 × 2 píxeles.')
    return rgb[y0:y1, x0:x1]


def _centerlines(g, detail, ppm, threshold):
    """Un trazo por el centro de las zonas oscuras, sin sus dos bordes."""
    mask = cv2.threshold(cv2.GaussianBlur(g, (0, 0), 0.6), threshold, 255, cv2.THRESH_BINARY_INV)[1]
    skeleton = cv2.ximgproc.thinning(mask)
    min_len = (4.0 - 3.0 * detail) * ppm
    return [pathops.simplify(_smooth(p), 0.6) for p in _trace(skeleton) if len(p) >= max(2, min_len)]


def _hatch_layer(dark, thr, angle, spacing, offset, ppm, rng):
    """Líneas paralelas donde la oscuridad supera thr, con pulso de lápiz."""
    H, W = dark.shape
    a = math.radians(angle)
    d = np.array([math.cos(a), math.sin(a)])
    n = np.array([-d[1], d[0]])
    c = np.array([W / 2, H / 2])
    half = math.hypot(W, H) / 2
    t = np.arange(-half, half, 1.0)
    min_run = max(3, int(1.2 * ppm))
    out = []
    flip = False
    o = -half + offset
    while o < half:
        jit = rng.normal(0, 0.1 * spacing)
        da = math.radians(rng.normal(0, 1.2))
        dd = np.array([math.cos(a + da), math.sin(a + da)])
        pts = c + n * (o + jit) + t[:, None] * dd
        xi = np.rint(pts[:, 0]).astype(int)
        yi = np.rint(pts[:, 1]).astype(int)
        ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
        m = np.zeros(len(t), bool)
        m[ok] = dark[yi[ok], xi[ok]] > thr
        edges = np.flatnonzero(np.diff(np.concatenate([[0], m.view(np.int8), [0]])))
        runs = []
        for s, e in zip(edges[::2], edges[1::2]):
            if e - s < min_run:
                continue
            p0, p1 = pts[s], pts[e - 1]
            mid = (p0 + p1) / 2 + n * rng.normal(0, 0.12 * ppm)
            runs.append(np.array([p0, mid, p1]))
        if flip:
            runs = [r[::-1] for r in runs[::-1]]
        if runs:
            flip = not flip
        out.extend(runs)
        o += spacing
    return out


def _hatch(dark, o, ppm, rng, turn=0.0):
    """dark: mapa 0..1 de cuánta tinta pide cada píxel."""
    dark = cv2.GaussianBlur(dark.astype(np.float32), (0, 0), 2.0)
    shade = float(o.get("shade", 50)) / 100.0
    sp = float(o.get("hatch_spacing", 1.2)) * ppm
    ang = float(o.get("hatch_angle", 45)) + turn
    shift = (shade - 0.5) * 0.5
    if o.get("cross", True):
        layers = [(0.28, ang, 0), (0.46, ang + 90, 0), (0.62, ang, sp / 2), (0.78, ang + 90, sp / 2)]
    else:
        layers = [(0.28, ang, 0), (0.50, ang, sp / 2), (0.70, ang, sp / 4), (0.70, ang, 3 * sp / 4)]
    out = []
    for thr, a, off in layers:
        out.extend(_hatch_layer(dark, thr - shift, a, sp, off, ppm, rng))
    return out


def _hex_rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def make_sketch(rgb, o, width_mm, pens=None, progress=None, cancelled=None):
    """Devuelve ({pluma: trazos en mm, origen arriba-izquierda}, alto en mm).

    pens: lista de colores "#rrggbb" para separar la imagen por plumas; None = un solo color."""
    def report(percent, message):
        if cancelled and cancelled():
            raise CancelledError()
        if progress:
            progress(percent, message)

    report(5, 'Preparando el recorte y el tamaño de la imagen…')
    if not math.isfinite(width_mm) or width_mm <= 0:
        raise ValueError('El ancho del dibujo debe ser mayor que cero.')
    spacing = float(o.get('hatch_spacing', 1.2))
    if not math.isfinite(spacing) or not 0.3 <= spacing <= 10:
        raise ValueError('La separación del sombreado debe estar entre 0.3 y 10 mm.')
    rgb = crop_image(rgb, o.get('crop'))
    f = min(1.0, WORK_SIDE / max(rgb.shape[:2]))
    img = cv2.resize(rgb, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else rgb.copy()
    H, W = img.shape[:2]
    ppm = W / width_mm

    contrast = 1.0 + float(o.get("contrast", 0)) / 100.0
    bright = float(o.get("brightness", 0)) * 1.27
    img = np.clip((img.astype(np.float32) - 128) * contrast + 128 + bright, 0, 255).astype(np.uint8)
    if o.get("invert"):
        img = 255 - img
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    rng = np.random.default_rng(int(o.get("seed", 1)))
    mode = o.get("mode", "boceto")
    report(20, 'Convirtiendo la imagen en líneas…')
    if mode not in ('boceto', 'contornos', 'rayado', 'trazo'):
        raise ValueError('Elige un estilo de dibujo válido.')
    lines = _contours(g, float(o.get("detail", 55)) / 100.0, ppm) if mode in ("boceto", "contornos") else []
    if mode == 'trazo':
        threshold = float(o.get('threshold', 160))
        if not math.isfinite(threshold) or not 1 <= threshold <= 254:
            raise ValueError('El umbral del trazo debe estar entre 1 y 254.')
        lines = _centerlines(g, float(o.get('detail', 55)) / 100.0, ppm, threshold)
    layers = {}
    if not pens or len(pens) < 2:
        report(40, 'Preparando los trazos y las sombras…')
        hatch = _hatch(1.0 - g / 255.0, o, ppm, rng) if mode in ("boceto", "rayado") else []
        report(65, 'Ordenando el recorrido de la pluma…')
        layers[0] = pathops.nn_order(hatch, cancelled=cancelled) + pathops.nn_order(lines, cancelled=cancelled)
    else:
        # cada píxel se asigna a la pluma de color más parecido; la cantidad de tinta
        # es lo lejos que está del blanco del papel
        pal = np.array([_hex_rgb(c) for c in pens], np.uint8).reshape(1, -1, 3)
        pal_lab = cv2.cvtColor(pal, cv2.COLOR_RGB2LAB).astype(np.float32)[0]
        lab = cv2.cvtColor(cv2.GaussianBlur(img, (0, 0), 1.5), cv2.COLOR_RGB2LAB).astype(np.float32)
        near = ((lab[:, :, None, :] - pal_lab[None, None]) ** 2).sum(3).argmin(2)
        ink = 1.0 - img.min(2).astype(np.float32) / 255.0
        darkest = int(pal_lab[:, 0].argmin())  # los contornos van con la pluma más oscura
        for k in range(len(pens)):
            report(40 + int(50 * k / len(pens)), f'Preparando los trazos del color {k + 1} de {len(pens)}…')
            hatch = _hatch(ink * (near == k), o, ppm, rng, turn=25.0 * k) if mode in ("boceto", "rayado") else []
            paths = pathops.nn_order(hatch, cancelled=cancelled) + (pathops.nn_order(lines, cancelled=cancelled) if k == darkest else [])
            if paths:
                layers[k] = paths
    report(95, 'Preparando la vista previa…')
    return {k: [p / ppm for p in v] for k, v in layers.items()}, H / ppm
