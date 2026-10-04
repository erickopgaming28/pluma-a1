"""Pure contact-pattern, saved-mesh and photographic-tone regressions."""
import copy
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

import app as server
from plotter import gcode, sketch, pathops, stream


class ContactAndPhotoTests(unittest.TestCase):
    def setUp(self):
        self.saved = copy.deepcopy(server.config)
        server.config.clear(); server.config.update(copy.deepcopy(server.DEFAULT_CONFIG))
        server.config['paper'].update(size='a5', bed_x=40, bed_y=10)
        self.paths = [{'name': 'Azul', 'paths': [np.array([[30., 60.], [60., 90.]])]}]

    def tearDown(self):
        server.config.clear(); server.config.update(self.saved)

    def test_ready_drawing_reactivates_saved_mesh_without_homing_or_probe(self):
        text, _ = gcode.build_gcode(self.paths, server.config, pen_ready=True)
        self.assertIn('G29.2 S1', text)
        self.assertNotRegex(text, r'(?m)^G(?:28|29)\s')
        self.assertLess(text.index('G29.2 S1'), text.index('G0 X'))
        stream.compile_program(text)
        server.config['pen']['level_bed'] = False
        text, _ = gcode.build_gcode(self.paths, server.config, pen_ready=True)
        self.assertNotIn('G29.2 S1', text)

    def test_adjust_probes_whole_bed_then_enables_compensation_before_pen_contact(self):
        text = gcode.build_adjust_gcode(server.config)
        self.assertIn('G29 A1 X0.0 Y0.0 I256.0 J256.0', text)
        probe, enabled, contact = text.index('G29 A1'), text.rindex('G29.2 S1'), text.index('G1 Z3.50')
        self.assertLess(text.index('G29.2 S1'), probe)
        self.assertLess(probe, enabled); self.assertLess(enabled, contact)
        stream.compile_program(text)

    def test_fresh_leveling_covers_tip_and_nozzle_with_offset(self):
        text, _ = gcode.build_gcode(self.paths, server.config, pen_ready=False)
        self.assertIn('G29 A1 X70.0 Y130.0 I30.0 J65.0', text)
        self.assertLess(text.index('G29 A1'), text.rindex('G29.2 S1'))

    def test_contact_preview_covers_nine_zones_without_connecting_or_sending(self):
        with patch.object(server.link, 'ensure') as connect, patch.object(server.direct_job, 'start') as send:
            response = server.app.test_client().post('/api/contact-test', json={})
        self.assertEqual(response.status_code, 200)
        connect.assert_not_called(); send.assert_not_called()
        paths = server.jobs[response.json['job']]['pages'][0][0]['paths']
        self.assertEqual(len(paths), 18)
        centers = np.array([path.mean(0) for path in paths[::2]])
        self.assertEqual(len(set(centers[:, 0])), 3)
        self.assertEqual(len(set(centers[:, 1])), 3)
        x0, y0, x1, y1 = gcode.drawable(server.config)
        self.assertGreater(np.ptp(centers[:, 1]), (y1 - y0) * .8)
        self.assertTrue(np.all(centers >= [x0, y0]) and np.all(centers <= [x1, y1]))
        text, _ = gcode.build_gcode(server.jobs[response.json['job']]['pages'][0], server.config, pen_ready=True)
        stream.compile_program(text)

    def tone_image(self):
        # Tone regions deliberately share the binary mask. Eye edges must survive.
        image = Image.new('RGB', (400, 400), (240, 240, 240))
        draw = ImageDraw.Draw(image)
        draw.ellipse((60, 30, 340, 360), fill=(140, 140, 140))
        for x in (115, 245): draw.ellipse((x, 120, x + 40, 150), fill=(60, 60, 60))
        draw.line((200, 165, 190, 205, 210, 205), fill=(75, 75, 75), width=4)
        draw.arc((155, 220, 245, 275), 10, 170, fill=(70, 70, 70), width=4)
        return np.asarray(image)

    def test_photo_preserves_internal_tone_edges_lost_by_binary_centerline(self):
        rgb = self.tone_image()
        photo, height = sketch.make_sketch(rgb, {'mode': 'fotolinea', 'detail': 95}, 100)
        binary, _ = sketch.make_sketch(rgb, {'mode': 'trazo', 'detail': 95}, 100)
        def eye_points(layers):
            points = np.concatenate(layers[0]) * 4
            return sum((points[:, 0] > 105) & (points[:, 0] < 165) & (points[:, 1] > 110) & (points[:, 1] < 160))
        self.assertEqual(height, 100)
        self.assertGreater(eye_points(photo), eye_points(binary) + 10)
        for path in photo[0]:
            self.assertTrue(np.isfinite(path).all())
            self.assertTrue((path >= 0).all() and (path <= 100).all())

    def test_photo_cleaning_reduces_noise_and_flat_image_is_empty(self):
        rng = np.random.default_rng(4)
        rgb = np.repeat(np.clip(180 + rng.normal(0, 10, (200, 200, 1)), 0, 255).astype(np.uint8), 3, axis=2)
        raw, _ = sketch.make_sketch(rgb, {'mode': 'fotolinea', 'photo_cleaning': 0}, 80)
        clean, _ = sketch.make_sketch(rgb, {'mode': 'fotolinea', 'photo_cleaning': 100}, 80)
        self.assertLess(pathops.stats(clean[0])['draw_mm'], pathops.stats(raw[0])['draw_mm'])
        empty, _ = sketch.make_sketch(np.full((100, 100, 3), 180, np.uint8), {'mode': 'fotolinea'}, 80)
        self.assertEqual(empty[0], [])

    def test_photo_parameters_crop_and_darkest_pen(self):
        rgb = self.tone_image()
        for cleaning in (-1, 301, float('nan')):
            with self.assertRaises(ValueError):
                sketch.make_sketch(rgb, {'mode': 'fotolinea', 'photo_cleaning': cleaning}, 80)
        layers, height = sketch.make_sketch(rgb, {'mode': 'fotolinea', 'crop': {'x': 0, 'y': 0, 'w': .5, 'h': 1}},
                                          80, ['#ff0000', '#000000'])
        self.assertEqual(height, 160)
        self.assertEqual(set(layers), {1})


if __name__ == '__main__': unittest.main()
