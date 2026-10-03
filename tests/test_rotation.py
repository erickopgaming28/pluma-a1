import copy
import math
import unittest

import numpy as np

import app as server
from plotter import gcode


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.saved = copy.deepcopy(server.config)
        server.config.clear()
        server.config.update(copy.deepcopy(server.DEFAULT_CONFIG))
        server.config['paper'].update(size='a5', bed_x=40, bed_y=10)
        self.client = server.app.test_client()

    def tearDown(self):
        server.config.clear()
        server.config.update(self.saved)

    def element(self):
        response = self.client.post('/api/element', json={
            'type': 'text', 'text': 'Hola', 'w': 70, 'opts': {'size': 8, 'human': 0}})
        self.assertEqual(response.status_code, 200)
        return response.json

    def test_rotation_uses_element_center_and_export_matches(self):
        rendered = self.element()
        source = server.renders[rendered['render_id']]['layers'][0]
        center = np.array([rendered['w'] / 2, rendered['h'] / 2])
        for angle in (0, 90, -90, 180, 37, 397):
            with self.subTest(angle=angle):
                response = self.client.post('/api/compose', json={'items': [{
                    'render_id': rendered['render_id'], 'x': 45, 'y': 80, 'rotation': angle}]})
                self.assertEqual(response.status_code, 200, response.json)
                job = response.json['job']
                stored = server.jobs[job]['pages'][0][0]['paths']
                a = math.radians(angle)
                matrix = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
                for before, after in zip(source, stored):
                    np.testing.assert_allclose(after, (before - center) @ matrix.T + center + [45, 80], atol=1e-8)
                    self.assertAlmostEqual(float(np.linalg.norm(np.diff(before, axis=0), axis=1).sum()),
                                           float(np.linalg.norm(np.diff(after, axis=0), axis=1).sum()), places=6)
                export = self.client.get('/api/export', query_string={'job': job, 'fmt': 'svg'})
                self.assertEqual(export.status_code, 200)
                self.assertEqual(export.data.decode(), gcode.build_svg(server.jobs[job]['pages'][0], server.config))

    def test_rotated_outside_work_is_flagged_and_cannot_be_sent(self):
        rendered = self.element()
        response = self.client.post('/api/compose', json={'items': [{
            'render_id': rendered['render_id'], 'x': 5, 'y': 2, 'rotation': 90}]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['outside'])
        check = self.client.post('/api/preflight', json={'job': response.json['job'], 'pen_ready': True})
        self.assertEqual(check.status_code, 400)

    def test_nonfinite_or_invalid_pose_is_rejected(self):
        rendered = self.element()
        for field, value in [('rotation', True), ('rotation', 'abc'), ('rotation', float('nan')),
                             ('rotation', float('inf')), ('x', float('inf')), ('y', None)]:
            item = {'render_id': rendered['render_id'], 'x': 45, 'y': 80, 'rotation': 0, field: value}
            response = self.client.post('/api/compose', json={'items': [item]})
            self.assertEqual(response.status_code, 400, (field, response.json))

    def test_same_transform_applies_to_image_paths(self):
        token = 'image-rotation-test'
        source = np.array([[0., 0.], [20., 0.], [20., 10.]])
        server.renders[token] = {'layers': {0: [source]}, 'w': 20, 'h': 10, 'signature': server.job_signature()}
        response = self.client.post('/api/compose', json={'items': [{
            'render_id': token, 'x': 40, 'y': 50, 'rotation': 90}]})
        self.assertEqual(response.status_code, 200)
        actual = server.jobs[response.json['job']]['pages'][0][0]['paths'][0]
        np.testing.assert_allclose(actual, [[55, 45], [55, 65], [45, 65]])
