"""Preview actual generated paths for a local image, without using a printer.

Run with the image path and optionally "puntillismo". Outputs stay in ignored artifacts;
the source image is never copied into the public repository.
"""
import copy
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as server
from plotter import sketch, pathops, gcode


def raster(paths, width, height, stroke=.3, side=720):
    scale = side / width
    image = np.full((round(height * scale), side, 3), 255, np.uint8)
    for p in paths:
        if np.array_equal(p[0], p[-1]) and len(p) == 2:
            cv2.circle(image, tuple(np.rint(p[0] * scale).astype(int)), max(1,round(stroke * scale / 2)),
                       (30,30,30), -1, cv2.LINE_AA)
            continue
        cv2.polylines(image, [np.rint(p * scale).astype(np.int32)], False, (30, 30, 30),
                      max(1, round(stroke * scale)), cv2.LINE_AA)
    return Image.fromarray(image)


if __name__ == '__main__':
    source = sketch.load_image(Path(sys.argv[1]).read_bytes())
    options = dict(mode='retrato', detail=70, photo_cleaning=85, shade=100,
                   hatch_spacing=.35, hatch_angle=45, brightness=0, contrast=0, cross=True, portrait_style='suave')
    if len(sys.argv) > 2:
        assert sys.argv[2] == 'puntillismo'
        options.update(mode='puntillismo',dot_spacing=.7)
    prefix = options['mode']
    started = time.perf_counter()
    portrait, height = sketch.make_sketch(source, options, 90)
    elapsed = time.perf_counter() - started
    borders, _ = sketch.make_sketch(source, dict(mode='contornos', detail=100, brightness=50), 90)
    cfg = copy.deepcopy(server.DEFAULT_CONFIG)
    cfg['paper'].update(size='a5', bed_x=40, bed_y=10)
    layers = [{'name': 'Negro', 'color': '#1d1d22', 'paths': [p + [15, 50] for p in portrait[0]]}]
    text, stats = gcode.build_gcode(layers, cfg, 'portrait_preview', pen_ready=True)
    assert np.isfinite(np.concatenate(portrait[0])).all()
    assert all((p >= 0).all() and (p <= [90, height]).all() for p in portrait[0])
    out = Path(__file__).parent / 'artifacts'; out.mkdir(exist_ok=True)
    (out / f'{prefix}-recorrido.svg').write_text(gcode.build_svg(layers, cfg), encoding='utf-8')
    summary = dict(width_mm=90, height_mm=height, conversion_seconds=round(elapsed, 2),
                   draw_m=round(stats['draw_mm'] / 1000, 2), lifts=stats['lifts'],
                   estimated_minutes=round(stats['seconds'] / 60, 1),
                   commands=len(text.splitlines()), physical_motion=False)
    (out / f'{prefix}-metricas.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    thumb_h = round(height / 90 * 360)
    panels = [Image.fromarray(source), raster(borders[0], 90, height), raster(portrait[0], 90, height)]
    comparison = Image.new('RGB', (1140, thumb_h + 100), '#f1ede4')
    draw = ImageDraw.Draw(comparison)
    try: font = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 18)
    except OSError: font = ImageFont.load_default()
    finish = 'Puntillismo · contactos separados' if prefix == 'puntillismo' else 'Retrato con sombras · trazos reales'
    for index, (label, panel) in enumerate(zip(['Original', 'Sólo bordes · brillo +50', finish], panels)):
        x = 15 + index * 380
        draw.text((x, 12), label, fill='#23242a', font=font)
        comparison.paste(panel.resize((360, thumb_h), Image.Resampling.LANCZOS), (x, 44))
    draw.text((15, thumb_h + 59), 'Vista del recorrido: no es una prueba de tinta ni de presión del lápiz.', fill='#5d5b56', font=font)
    comparison.save(out / f'{prefix}-comparacion.png')
    print(json.dumps(summary))
