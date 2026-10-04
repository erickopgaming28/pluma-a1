import copy
import io
import tempfile
from pathlib import Path
import unittest
from concurrent.futures import CancelledError
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

import app as server
from plotter import sketch, pathops, gcode


class PortraitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_patch = patch.object(server, 'IMAGE_CACHE', Path(self.tmp.name) / 'images')
        self.cache_patch.start()

    def tearDown(self):
        self.cache_patch.stop(); self.tmp.cleanup()

    def gradient(self):
        rgb = np.full((180, 240, 3), 255, np.uint8)
        for i, tone in enumerate((220, 160, 80)):
            rgb[20:160, 10 + i*80:70 + i*80] = tone
        return rgb

    def test_portrait_keeps_tone_density_and_blank_background(self):
        rgb = self.gradient()
        for style in ('suave', 'rayado'):
            with self.subTest(style=style):
                paths, height = sketch.make_sketch(rgb, dict(mode='retrato', portrait_style=style,
                    shade=100, hatch_spacing=.35, hatch_angle=0, detail=0), 120)
                # Three equal-size gray patches must receive increasing ink length.
                length = []
                for i in range(3):
                    lo, hi = 5 + i*40, 35 + i*40
                    length.append(sum(pathops.length(p) for p in paths[0]
                                      if lo <= p[:, 0].mean() <= hi and 10 < p[:, 1].mean() < 80))
                self.assertGreater(length[1], length[0] * 1.3)
                self.assertGreater(length[2], length[1] * 1.3)
                self.assertEqual(height, 90)
                self.assertTrue(all(np.isfinite(p).all() and (p >= 0).all() and (p <= [120, height]).all() for p in paths[0]))
                blank, _ = sketch.make_sketch(np.full_like(rgb, 255), dict(mode='retrato', portrait_style=style, shade=300), 120)
                self.assertFalse(any(blank.values()))

    def test_portrait_shade_zero_preserves_edges_without_tone_marks(self):
        rgb = self.gradient()
        edges, _ = sketch.make_sketch(rgb, dict(mode='retrato', shade=0), 120)
        shaded, _ = sketch.make_sketch(rgb, dict(mode='retrato', shade=100, hatch_spacing=.4), 120)
        self.assertGreater(pathops.stats(shaded[0])['draw_mm'], pathops.stats(edges[0])['draw_mm'] * 2)

    def test_diffusion_cancels_during_conversion(self):
        checks = []
        def progress(percent, message): checks.append(percent)
        with self.assertRaises(CancelledError):
            sketch.make_sketch(self.gradient(), dict(mode='retrato', hatch_spacing=.3), 200,
                               progress=progress, cancelled=lambda: bool(checks and checks[-1] >= 45))
        self.assertTrue(any(p >= 45 for p in checks))

    def test_sampling_is_bounded_by_source_resolution_for_tall_crops(self):
        rgb = np.full((1000, 40, 3), 255, np.uint8)
        rgb[300:400, 10:30] = 0
        # The physical image is far taller than the bed, but conversion must be bounded.
        with patch.object(sketch.cv2, 'remap', wraps=sketch.cv2.remap) as remap:
            sketch.make_sketch(rgb, dict(mode='retrato', hatch_spacing=.3), 400)
        mapping = remap.call_args.args[1]
        self.assertLessEqual(mapping.shape[0], 1417)

    def test_portrait_api_composes_rotates_exports_and_never_moves_printer(self):
        saved = copy.deepcopy(server.config)
        server.config.clear(); server.config.update(copy.deepcopy(server.DEFAULT_CONFIG))
        server.config['paper'].update(size='a5', bed_x=40, bed_y=10)
        data = io.BytesIO(); Image.fromarray(self.gradient()).save(data, format='PNG'); data.seek(0)
        client = server.app.test_client()
        try:
            with patch.object(server.link, 'send_gcode') as send:
                image = client.post('/api/image/upload', data={'file': (data, 'generated-tones.png')}).json['id']
                response = client.post('/api/element', json=dict(type='image', image=image, w=60, pen=1,
                    opts=dict(mode='retrato', shade=100, hatch_spacing=.4)))
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json['preview_width'], .3)
                job = client.post('/api/compose', json={'items':[dict(render_id=response.json['render_id'],x=35,y=70,rotation=37)]})
                self.assertFalse(job.json['outside'])
                for fmt in ('svg', 'gcode'):
                    self.assertEqual(client.get('/api/export', query_string={'job':job.json['job'],'fmt':fmt}).status_code,200)
                send.assert_not_called()
        finally:
            server.images.pop(locals().get('image'),None)
            server.config.clear(); server.config.update(saved)

    def test_unknown_finish_is_rejected(self):
        with self.assertRaises(ValueError):
            sketch.make_sketch(self.gradient(), {'mode':'retrato','portrait_style':'unknown'},120)

    def test_uploaded_original_survives_loss_of_memory_cache(self):
        data=io.BytesIO(); Image.fromarray(self.gradient()).save(data,format='PNG'); data.seek(0)
        client=server.app.test_client()
        image=client.post('/api/image/upload',data={'file':(data,'generated-tones.png')}).json['id']
        server.images.pop(image)
        response=client.get('/api/image/'+image+'/preview')
        self.assertEqual(response.status_code,200)
        np.testing.assert_array_equal(np.asarray(Image.open(io.BytesIO(response.data))),self.gradient())
        self.assertIsNone(server.get_image('../../config.json'))
        server.images.pop(image,None)


if __name__ == '__main__': unittest.main()
