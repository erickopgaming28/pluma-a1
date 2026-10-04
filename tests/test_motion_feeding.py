"""Delayed telemetry, nominal-rate feeding and next-job-only speed settings."""
import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import app as server
from plotter import gcode
from plotter.stream import DirectJob
import test_stream as stream_tests
from test_stream import FakeLink, program


class FeedingTests(unittest.TestCase):
    wait_for = stream_tests.DirectJobTests.wait_for

    def test_silent_intermediate_telemetry_does_not_starve_next_packets(self):
        class SilentLink(FakeLink):
            def send_gcode(self, text, wait=6):
                self.sent.append(text)
                if len(self.sent) == 1: self.confirm(text)
                return {'result': 'SUCCESS'}
        link = SilentLink()
        # This short route fits inside the window. Real time keeps the final
        # confirmation pending while the test inspects it, on every platform.
        job = DirectJob(block_timeout=2, poll_interval=.002)
        motions = [f'G1 X{i / 10:.2f} Y10 F2400' for i in range(1, 401)]
        job.start(program('G1 X0 Y0 Z3 F12000', *motions), 'sin telemetría intermedia', link, continuous=True)
        self.wait_for(lambda: bool(link.sent) and link.sent[-1].startswith('M400\n'))
        actual = [line for packet in link.sent[1:-1] for line in packet.splitlines()]
        self.assertEqual(actual, motions)
        self.assertTrue(job.active)
        self.assertLess(job.snapshot()['percent'], 100)
        self.assertEqual(job.snapshot()['executed_percent'], 0)
        self.assertEqual(sum('gcode_claim_action' in packet for packet in link.sent), 2)
        link.confirm()
        self.wait_for(lambda: not job.active)
        self.assertEqual(job.snapshot()['state'], 'FINISH')

    def test_steady_feeding_is_not_slowed_by_confirmation_timeout_margin(self):
        now = [0.0]
        def clock():
            now[0] += .05
            return now[0]
        class TimedLink(FakeLink):
            def __init__(self):
                super().__init__(auto=True)
                self.times = []
            def send_gcode(self, text, wait=6):
                self.times.append(now[0])
                return super().send_gcode(text, wait)
        link = TimedLink()
        job = DirectJob(poll_interval=.001, lookahead_seconds=1, clock=clock)
        # 200 traversals of 10 mm at 100 mm/s = 20 seconds of nominal motion.
        motions = [f'G1 X{10 if i % 2 == 0 else 0} F6000' for i in range(200)]
        job.start(program('G1 X0 Y0 Z3 F12000', *motions), 'ritmo nominal', link, continuous=True)
        self.wait_for(lambda: not job.active, seconds=3)
        self.assertEqual(job.snapshot()['state'], 'FINISH')
        feed_time = link.times[-1] - link.times[1]
        self.assertGreater(feed_time, 15)  # rolling window still regulates the sender
        self.assertLess(feed_time, 24)  # old 1.5x budget forced about 30 s of feeding


class MotionSettingsTests(unittest.TestCase):
    def setUp(self):
        self.saved = copy.deepcopy(server.config)
        server.config.clear(); server.config.update(copy.deepcopy(server.DEFAULT_CONFIG))
        server.config['paper'].update(size='a5', bed_x=40, bed_y=10)
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / 'config.json'
        self.file_patch = patch.object(server, 'CONFIG_FILE', self.cfg)
        self.file_patch.start()
        self.client = server.app.test_client()

    def tearDown(self):
        self.file_patch.stop(); self.tmp.cleanup()
        server.config.clear(); server.config.update(self.saved)

    def test_save_next_job_speeds_preserves_calibration_and_does_not_send(self):
        paths = [{'name': 'Azul', 'paths': [np.array([[30., 60.], [60., 90.]])]}]
        before, _ = gcode.build_gcode(paths, server.config, pen_ready=True)
        saved_pen = dict(server.config['pen'])
        with patch.object(server.direct_job, '_active', True), patch.object(server.link, 'send_gcode') as send:
            response = self.client.post('/api/motion-settings', json={'draw_speed': 60, 'travel_speed': 180})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['next_job_only'])
        send.assert_not_called()
        for key in ('z_down', 'z_up', 'offset_x', 'offset_y', 'level_bed'):
            self.assertEqual(server.config['pen'][key], saved_pen[key])
        after, _ = gcode.build_gcode(paths, server.config, pen_ready=True)
        self.assertIn('F3600', after); self.assertIn('F10800', after)
        self.assertIn('F2400', before)
        self.assertNotIn('G28', after)

    def test_invalid_speeds_and_geometry_cannot_be_saved_through_speed_endpoint(self):
        before = copy.deepcopy(server.config)
        for data in ({'draw_speed': 81}, {'travel_speed': 201}, {'draw_speed': 0},
                     {'accel': 3001}, {'z_speed': 31}, {'z_down': 0}, {}):
            with self.subTest(data=data):
                response = self.client.post('/api/motion-settings', json=data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(server.config, before)
                self.assertFalse(self.cfg.exists())
