import copy
import io
import unittest

import numpy as np
from PIL import Image

import app as server
from plotter import sketch, gcode


class ImageEditingTests(unittest.TestCase):
    def setUp(self):
        self.saved = copy.deepcopy(server.config)
        server.config.clear()
        server.config.update(copy.deepcopy(server.DEFAULT_CONFIG))
        server.config['paper'].update(size='a5', bed_x=40, bed_y=10)
        self.client = server.app.test_client()
        self.rgb = np.full((100, 200, 3), 255, np.uint8)
        self.rgb[40:60, 10:90] = 0
        self.rgb[10:90, 140:160] = 0
        data = io.BytesIO()
        Image.fromarray(self.rgb).save(data, format='PNG')
        data.seek(0)
        response = self.client.post('/api/image/upload', data={'file': (data, 'two-strokes.png')})
        self.assertEqual(response.status_code, 200)
        self.image_id = response.json['id']

    def tearDown(self):
        server.images.pop(self.image_id, None)
        server.config.clear()
        server.config.update(self.saved)

    def element(self, opts, width=80):
        return self.client.post('/api/element', json={'type': 'image', 'image': self.image_id, 'w': width, 'opts': opts})

    def test_crop_changes_actual_paths_and_aspect_without_changing_original(self):
        result = self.element({'mode': 'trazo', 'crop': {'x': 0, 'y': .2, 'w': .5, 'h': .6}})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertAlmostEqual(result.json['h'], 48)
        layers = server.renders[result.json['render_id']]['layers']
        self.assertEqual(len(layers[0]), 1)
        path = layers[0][0]
        self.assertLess(float(np.ptp(path[:, 1])), 2)
        self.assertGreater(float(np.ptp(path[:, 0])), 40)
        np.testing.assert_array_equal(server.images[self.image_id]['rgb'], self.rgb)
        composed = self.client.post('/api/compose', json={'items': [{'render_id': result.json['render_id'], 'x': 30, 'y': 70}]})
        self.assertEqual(composed.status_code, 200)
        stored = server.jobs[composed.json['job']]['pages'][0]
        np.testing.assert_allclose(stored[0]['paths'][0], path + [30, 70])
        exported = self.client.get('/api/export', query_string={'job': composed.json['job'], 'fmt': 'svg'})
        self.assertEqual(exported.data.decode(), gcode.build_svg(stored, server.config))

    def test_central_stroke_has_one_path_borders_keep_two_sides(self):
        rgb = np.full((100, 200, 3), 255, np.uint8)
        rgb[35:65, 10:190] = 0
        central, _ = sketch.make_sketch(rgb, {'mode': 'trazo', 'detail': 100}, 100)
        edges, _ = sketch.make_sketch(rgb, {'mode': 'contornos', 'detail': 100}, 100)
        self.assertEqual(len(central[0]), 1)
        self.assertLess(float(np.ptp(np.concatenate(central[0])[:, 1])), 1)
        self.assertGreater(float(np.ptp(np.concatenate(edges[0])[:, 1])), 10)

    def test_threshold_controls_which_strokes_are_kept(self):
        rgb = np.full((100, 200, 3), 255, np.uint8)
        rgb[25:35, 15:185] = 70
        rgb[65:75, 15:185] = 190
        low, _ = sketch.make_sketch(rgb, {'mode': 'trazo', 'threshold': 120}, 100)
        high, _ = sketch.make_sketch(rgb, {'mode': 'trazo', 'threshold': 220}, 100)
        self.assertEqual(len(low[0]), 1)
        self.assertEqual(len(high[0]), 2)

    def test_bad_crop_is_rejected(self):
        for crop in ({}, 'bad', {'x': -.1, 'y': 0, 'w': 1, 'h': 1},
                     {'x': .5, 'y': 0, 'w': .6, 'h': 1}, {'x': 0, 'y': 0, 'w': 0, 'h': 1},
                     {'x': 0, 'y': float('nan'), 'w': 1, 'h': 1},
                     {'x': True, 'y': 0, 'w': 1, 'h': 1}, {'x': 0, 'y': 0, 'w': .001, 'h': .001}):
            with self.subTest(crop=crop):
                result = self.element({'mode': 'trazo', 'crop': crop})
                self.assertEqual(result.status_code, 400, result.json)

    def test_unknown_style_and_bad_threshold_are_rejected(self):
        for opts in ({'mode': 'bad'}, {'mode': 'trazo', 'threshold': 0}, {'mode': 'trazo', 'threshold': float('inf')}):
            self.assertEqual(self.element(opts).status_code, 400)

    def test_full_crop_matches_uncropped_output_and_all_styles_use_crop(self):
        crop = {'x': 0, 'y': 0, 'w': 1, 'h': 1}
        original, height = sketch.make_sketch(self.rgb, {'mode': 'trazo'}, 80)
        restored, restored_h = sketch.make_sketch(self.rgb, {'mode': 'trazo', 'crop': crop}, 80)
        self.assertEqual(height, restored_h)
        for before, after in zip(original[0], restored[0]):
            np.testing.assert_array_equal(before, after)
        for mode in ('trazo', 'contornos', 'boceto', 'rayado'):
            result = self.element({'mode': mode, 'crop': {'x': .5, 'y': 0, 'w': .5, 'h': 1}}, width=60)
            self.assertEqual(result.status_code, 200, result.json)
            self.assertEqual(result.json['h'], 60)

    def test_preview_is_original_and_missing_original_has_clear_error(self):
        response = self.client.get('/api/image/' + self.image_id + '/preview')
        self.assertEqual(response.mimetype, 'image/png')
        np.testing.assert_array_equal(np.asarray(Image.open(io.BytesIO(response.data))), self.rgb)
        self.assertEqual(self.client.get('/api/image/missing/preview').status_code, 404)

    def test_multicolor_central_stroke_uses_darkest_pen_without_hatching(self):
        mono, _ = sketch.make_sketch(self.rgb, {'mode': 'trazo'}, 100)
        multi, _ = sketch.make_sketch(self.rgb, {'mode': 'trazo'}, 100, ['#ff0000', '#000000'])
        self.assertEqual(set(multi), {1})
        self.assertEqual(len(mono[0]), len(multi[1]))
        for before, after in zip(mono[0], multi[1]):
            np.testing.assert_array_equal(before, after)


if __name__ == '__main__':
    unittest.main()
