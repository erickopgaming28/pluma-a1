"""Carga de fuentes SVG de un solo trazo (EMS / Hershey)."""
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

# (archivo, nombre visible) en el orden en que aparecen en la interfaz
CATALOG = [
    ("EMSAllure", "Cursiva elegante"),
    ("HersheyScript1", "Cursiva clásica"),
    ("EMSFelix", "Caligráfica"),
    ("EMSElfin", "Manuscrita"),
    ("EMSTech", "Letra técnica"),
    ("EMSReadabilityItalic", "Imprenta inclinada"),
    ("EMSReadability", "Imprenta"),
    ("HersheyScriptMed", "Cursiva gruesa"),
    ("EMSNixish", "Máquina de escribir"),
    ("EMSNixishItalic", "Máquina inclinada"),
    ("HersheySans1", "Simple"),
    ("HersheySerifMed", "Libro"),
    ("HersheySerifMedItalic", "Libro inclinada"),
    ("HersheyGothEnglish", "Gótica"),
    ("EMSOsmotron", "Futurista"),
]

_NUM = re.compile(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?")
_SVG_NS = "{http://www.w3.org/2000/svg}"


def _parse_d(d):
    """Los glifos sólo usan M/L absolutos: cada M abre un trazo nuevo."""
    strokes = []
    for chunk in d.split("M"):
        nums = [float(n) for n in _NUM.findall(chunk)]
        if len(nums) >= 4:
            strokes.append(np.array(nums[: len(nums) // 2 * 2], dtype=np.float64).reshape(-1, 2))
    return strokes


class Font:
    def __init__(self, font_id, label):
        self.id = font_id
        self.label = label
        root = ET.parse(FONT_DIR / f"{font_id}.svg").getroot()
        font = root.find(f".//{_SVG_NS}font")
        face = font.find(f"{_SVG_NS}font-face")
        self.upm = float(face.get("units-per-em", 1000))
        self.default_adv = float(font.get("horiz-adv-x", 400))
        self.glyphs = {}
        for g in font.findall(f"{_SVG_NS}glyph"):
            ch = g.get("unicode")
            if not ch or len(ch) != 1:
                continue
            adv = float(g.get("horiz-adv-x", self.default_adv))
            self.glyphs[ch] = (adv, _parse_d(g.get("d", "")))

    def glyph(self, ch):
        """Devuelve (avance, trazos) en unidades de la fuente."""
        g = self.glyphs.get(ch)
        if g is None and 0xE000 <= ord(ch) < 0xE010:
            return (0.0, [])  # marcador de cambio de pluma: no ocupa espacio
        if g is None:
            # sin glifo: intenta con la letra base (p. ej. ǎ -> a)
            base = unicodedata.normalize("NFD", ch)[0]
            g = self.glyphs.get(base)
        if g is None:
            g = (self.glyphs.get(" ", (self.default_adv, []))[0], [])
        return g


_cache = {}


def get_font(font_id):
    labels = dict(CATALOG)
    if font_id not in labels:
        font_id = CATALOG[0][0]
    if font_id not in _cache:
        _cache[font_id] = Font(font_id, labels[font_id])
    return _cache[font_id]
