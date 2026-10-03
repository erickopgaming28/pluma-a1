"""Texto -> trazos con aspecto de letra a mano."""
import math
import re

import numpy as np

from . import pathops

ASCENT = 0.8  # fracción del cuerpo por encima de la línea base
PEN_MARK = 0xE000  # {n} en el texto se convierte en un carácter privado que cambia de pluma
_MARK = re.compile(r"\{(\d)\}")


def _is_mark(ch):
    return PEN_MARK <= ord(ch) < PEN_MARK + 16


def _noise1d(rng, wavelength, terms=3):
    """Ruido suave: suma de senos con fase aleatoria, amplitud ~1."""
    w = 2 * math.pi / (wavelength * rng.uniform(0.6, 1.6, terms))
    ph = rng.uniform(0, 2 * math.pi, terms)
    amp = rng.uniform(0.5, 1.0, terms)
    amp /= amp.sum()
    return lambda x: sum(a * np.sin(x * wi + p) for a, wi, p in zip(amp, w, ph))


def _wrap(text, font, s, spacing, width):
    """Parte el texto en renglones (listas de palabras)."""
    adv = lambda word: sum(font.glyph(c)[0] for c in word) * s * spacing
    space = font.glyph(" ")[0] * s
    lines = []
    for para in text.replace("\r", "").replace("\t", "    ").split("\n"):
        cur, cur_w = [], 0.0
        for word in para.split(" "):
            # una palabra más ancha que el renglón se corta por letras
            while adv(word) > width and len(word) > 1:
                k = len(word) - 1
                while k > 1 and adv(word[:k]) > width:
                    k -= 1
                if cur:
                    lines.append(cur)
                lines.append([word[:k]])
                cur, cur_w, word = [], 0.0, word[k:]
            w = adv(word)
            add = w + (space if cur else 0.0)
            if cur and cur_w + add > width:
                lines.append(cur)
                cur, cur_w = [word], w
            else:
                cur.append(word)
                cur_w += add
        lines.append(cur)
    return lines


def render_text(text, font, o, area_w, area_h, n_pens=1, pen=0):
    """Devuelve una lista de páginas; cada página es {pluma: [trazos en mm]}.

    Con n_pens > 1, un marcador {n} en el texto cambia a la pluma n desde ese punto."""
    text = _MARK.sub(lambda m: chr(PEN_MARK + int(m.group(1))) if n_pens > 1 else "", text)
    size = float(o.get("size", 8))
    s = size / font.upm
    h = float(o.get("human", 55)) / 100.0
    spacing = 1.0 + float(o.get("letter_spacing", 0)) / 100.0
    lh = size * float(o.get("line_spacing", 1.1))
    slant = math.tan(math.radians(float(o.get("slant", 0))))
    align = o.get("align", "left")
    rng = np.random.default_rng(int(o.get("seed", 1)))
    k = size / 8.0  # las imperfecciones crecen con el tamaño de letra
    space = font.glyph(" ")[0] * s

    lines = _wrap(text, font, s, spacing, area_w)
    per_page = max(1, int((area_h - size) // lh) + 1)
    wob_x = _noise1d(rng, 9.0 * k)
    wob_y = _noise1d(rng, 7.0 * k)

    pages = []
    for start in range(0, len(lines), per_page):
        strokes = []
        for li, words in enumerate(lines[start:start + per_page]):
            nominal = sum(font.glyph(c)[0] for w in words for c in w) * s * spacing + space * max(0, len(words) - 1)
            x = {"center": (area_w - nominal) / 2, "right": area_w - nominal}.get(align, 0.0)
            x = max(0.0, x) + rng.normal(0, 0.6 * h * k)
            x0 = x
            base0 = size * ASCENT + li * lh + rng.normal(0, 0.25 * h * k)
            slope = rng.normal(0, 0.004 * h)
            drift = _noise1d(rng, 45.0 * k)
            for wi, word in enumerate(words):
                if wi:
                    x += space * (1 + rng.normal(0, 0.15 * h))
                word_strokes = []
                for ch in word:
                    if _is_mark(ch):
                        pen = min(max(ord(ch) - PEN_MARK, 1), n_pens) - 1
                        if o.get("join", True):
                            word_strokes = pathops.merge_close(word_strokes, 0.05 * size)
                        strokes.extend((word_pen, p) for p in word_strokes)
                        word_strokes = []
                        continue
                    word_pen = pen
                    adv, glyph = font.glyph(ch)
                    gs = s * (1 + rng.normal(0, 0.03 * h))
                    rot = rng.normal(0, 0.03 * h)
                    c, sn = math.cos(rot), math.sin(rot)
                    base = base0 + slope * (x - x0) + 0.35 * h * k * drift(x) + rng.normal(0, 0.1 * h * k)
                    cx = adv * gs / 2
                    for st in glyph:
                        px = st[:, 0] * gs + slant * st[:, 1] * gs - cx
                        py = st[:, 1] * gs
                        word_strokes.append(np.column_stack([x + cx + px * c - py * sn, base - (px * sn + py * c)]))
                    x += adv * s * spacing * (1 + rng.normal(0, 0.03 * h))
                if o.get("join", True):
                    word_strokes = pathops.merge_close(word_strokes, 0.05 * size)
                strokes.extend((pen, p) for p in word_strokes)

        out = {}
        for pn, p in strokes:
            if h > 0:
                p = pathops.subdivide(p, 0.6)
                amp = 0.07 * h * k
                p = p + amp * np.column_stack([wob_x(p[:, 1] * 1.3 + p[:, 0] * 0.4), wob_y(p[:, 0] + p[:, 1] * 0.5)])
            out.setdefault(pn, []).append(pathops.simplify(p, 0.02))
        pages.append(out)
    return pages or [{}]
