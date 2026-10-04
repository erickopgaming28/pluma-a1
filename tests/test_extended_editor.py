import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import app as server
from plotter import gcode, sketch, pathops


class ExtendedImageTests(unittest.TestCase):
    def test_all_styles_accept_extended_settings_and_return_finite_paths(self):
        rgb = np.full((160, 220, 3), 255, np.uint8)
        cv2.ellipse(rgb, (110, 80), (60, 55), 0, 0, 360, (80, 80, 80), 2)
        cv2.line(rgb, (70, 70), (150, 90), (140, 140, 140), 3)
        for mode in ('trazo', 'contornos', 'fotolinea', 'boceto', 'rayado', 'retrato', 'puntillismo'):
            with self.subTest(mode=mode):
                layers, height = sketch.make_sketch(rgb, {'mode': mode, 'detail': 300,
                    'photo_cleaning': 200, 'shade': 300, 'contrast': 150}, 80)
                self.assertGreater(height, 0)
                self.assertGreater(sum(map(len, layers.values())), 0)
                for paths in layers.values():
                    self.assertTrue(all(np.isfinite(p).all() for p in paths))

    def test_extended_detail_recovers_faint_fine_edges(self):
        rgb = np.full((200, 200, 3), 255, np.uint8)
        cv2.line(rgb, (25, 80), (175, 80), (248, 248, 248), 1)
        normal, _ = sketch.make_sketch(rgb, {'mode': 'contornos', 'detail': 100}, 80)
        extended, _ = sketch.make_sketch(rgb, {'mode': 'contornos', 'detail': 300}, 80)
        self.assertEqual(len(normal[0]), 0)
        self.assertGreater(len(extended[0]), 0)

    def test_extended_shading_strengthens_tones_and_keeps_blank_paper_empty(self):
        rgb = np.full((80, 100, 3), 210, np.uint8)
        normal, _ = sketch.make_sketch(rgb, {'mode': 'rayado', 'shade': 100}, 50)
        extended, _ = sketch.make_sketch(rgb, {'mode': 'rayado', 'shade': 300}, 50)
        self.assertGreater(sum(pathops.length(p) for p in extended[0]), sum(pathops.length(p) for p in normal[0]))
        white = np.full_like(rgb, 255)
        for mode in ('contornos', 'fotolinea', 'trazo', 'boceto', 'rayado'):
            paths, _ = sketch.make_sketch(white, {'mode': mode, 'detail': 300, 'shade': 300}, 50)
            self.assertFalse(any(paths.values()))

    def test_invalid_extended_settings_are_rejected_in_every_style(self):
        rgb = np.full((30, 30, 3), 255, np.uint8)
        for key in ('detail', 'photo_cleaning', 'shade', 'contrast', 'brightness'):
            for value in (301, float('nan'), float('inf'), True, 'invalid'):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    sketch.make_sketch(rgb, {key: value}, 50)


class MarginLayoutTests(unittest.TestCase):
    def setUp(self):
        self.saved = copy.deepcopy(server.config)
        server.config.clear(); server.config.update(copy.deepcopy(server.DEFAULT_CONFIG))
        server.config['paper'].update(size='a5', bed_x=40, bed_y=10)
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / 'config.json'
        self.patch = patch.object(server, 'CONFIG_FILE', self.file)
        self.patch.start()
        self.client = server.app.test_client()

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()
        server.config.clear(); server.config.update(self.saved)

    def test_independent_margins_persist_without_moving_or_changing_calibration(self):
        pen = copy.deepcopy(server.config['pen'])
        physical = gcode.reach(server.config)
        margins = dict(left=20, top=50, right=30, bottom=15)
        with patch.object(server.link, 'send_gcode') as send, patch.object(server.direct_job, '_active', True):
            response = self.client.post('/api/paper-layout', json={'margins': margins})
        self.assertEqual(response.status_code, 200, response.json)
        send.assert_not_called()
        self.assertEqual(server.config['pen'], pen)
        self.assertEqual(gcode.reach(server.config), physical)
        self.assertEqual(gcode.drawable(server.config), (20, 50, 118, 195))
        self.assertTrue(self.file.exists())
        self.assertEqual(self.client.post('/api/paper-layout', json={'margins': None}).status_code, 200)
        self.assertIsNone(server.config['paper']['margins'])

    def test_invalid_margins_are_atomic(self):
        before = copy.deepcopy(server.config)
        for value in ({}, {'left': 1}, dict(left=-1, top=12, right=12, bottom=12),
                      dict(left=True, top=12, right=12, bottom=12),
                      dict(left=100, top=100, right=100, bottom=100)):
            response = self.client.post('/api/paper-layout', json={'margins': value})
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(server.config, before)
            self.assertFalse(self.file.exists())
        self.assertEqual(self.client.post('/api/paper-layout', json={'pen': {'z_down': 1}}).status_code, 400)

    def test_guide_margins_do_not_override_physical_send_limit(self):
        server.config['paper']['margins'] = dict(left=25, top=50, right=25, bottom=25)
        # This lies outside the margin guide but remains physically reachable.
        paths = [{'name': 'Ink', 'paths': [np.array([[15., 65.], [20., 70.]])]}]
        gcode.build_gcode(paths, server.config, pen_ready=True)
        paths[0]['paths'][0][:, 0] = -10
        with self.assertRaises(ValueError):
            gcode.build_gcode(paths, server.config, pen_ready=True)
