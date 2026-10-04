import threading
import time
import unittest
import copy
import io
import json
from pathlib import Path
from concurrent.futures import CancelledError

import numpy as np

from plotter import pathops
from plotter.conversion import ConversionQueue
from plotter.stream import DirectJob
from test_stream import FakeLink, program
from PIL import Image, ImageDraw


class LargePathTests(unittest.TestCase):
    def test_spatial_order_matches_exact_greedy_order(self):
        for quantized in (False, True):
            rng = np.random.default_rng(7)
            paths = rng.uniform(-30, 100, (800, 3, 2))
            if quantized:
                paths = np.round(paths / 10) * 10
            expected = pathops.nn_order(list(paths))
            actual = pathops._indexed_order(list(paths), (0, 0))
            for before, after in zip(expected, actual):
                np.testing.assert_array_equal(before, after)

    def test_large_order_preserves_every_stroke_and_can_be_canceled(self):
        rng = np.random.default_rng(8)
        paths = list(rng.uniform(0, 150, (6000, 2, 2)))
        ordered = pathops.nn_order(paths)
        self.assertEqual(len(ordered), len(paths))
        canonical = lambda p: min(tuple(p.ravel()), tuple(p[::-1].ravel()))
        self.assertEqual({canonical(p) for p in paths}, {canonical(p) for p in ordered})
        with self.assertRaises(CancelledError):
            pathops.nn_order(paths, cancelled=lambda: True)


class ConversionQueueTests(unittest.TestCase):
    def wait(self, queue, token, state):
        end = time.monotonic() + 2
        while time.monotonic() < end:
            data = queue.snapshot(token)
            if data['state'] == state:
                return data
            time.sleep(.003)
        self.fail('La conversión no terminó en el estado esperado.')

    def test_cancel_running_and_queued_work_does_not_replace_new_result(self):
        queue = ConversionQueue()
        started, release = threading.Event(), threading.Event()
        def slow(progress, canceled):
            started.set(); release.wait(1)
            progress(50, 'Ordenando')
            return 'obsolete'
        try:
            first = queue.start(slow)
            self.assertTrue(started.wait(1))
            second = queue.start(lambda progress, canceled: 'second')
            queue.cancel(first); queue.cancel(second)
            latest = queue.start(lambda progress, canceled: 'latest')
            release.set()
            self.assertEqual(self.wait(queue, latest, 'done')['result'], 'latest')
            self.assertEqual(queue.snapshot(first)['state'], 'canceled')
            self.assertEqual(queue.snapshot(second)['state'], 'canceled')
            self.assertNotIn('result', queue.snapshot(first))
        finally:
            release.set(); queue._executor.shutdown(wait=True, cancel_futures=True)

    def test_queue_limit_and_errors_are_visible(self):
        queue = ConversionQueue(limit=1)
        started, release = threading.Event(), threading.Event()
        def slow(progress, canceled):
            started.set(); release.wait(1)
            raise ValueError('Imagen inválida')
        try:
            token = queue.start(slow)
            self.assertTrue(started.wait(1))
            with self.assertRaises(ValueError):
                queue.start(slow)
            release.set()
            self.assertEqual(self.wait(queue, token, 'failed')['message'], 'Imagen inválida')
        finally:
            release.set(); queue._executor.shutdown(wait=True, cancel_futures=True)


class LargeStreamTests(unittest.TestCase):
    def wait(self, condition, seconds=3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if condition():
                return
            time.sleep(.003)
        self.fail('El trabajo no alcanzó el estado esperado.')

    def test_final_barrier_allows_buffered_motion_beyond_base_timeout(self):
        class SlowFinish(FakeLink):
            def send_gcode(self, text, wait=6):
                self.sent.append(text)
                if text.startswith('M400\n'):
                    threading.Timer(.12, lambda: self.confirm(text)).start()
                elif 'gcode_claim_action' in text:
                    self.confirm(text)
                return {'result': 'SUCCESS'}
        link = SlowFinish()
        job = DirectJob(block_timeout=.025, poll_interval=.002)
        job.start(program('G1 X0 Y0 Z3 F12000', *(f'G1 X{i} F2400' for i in range(1, 21))),
                  'cola', link, continuous=True)
        self.wait(lambda: any(p.startswith('M400\n') for p in link.sent))
        time.sleep(.04)
        self.assertTrue(job.active)
        self.assertNotEqual(job.snapshot()['state'], 'FINISH')
        self.wait(lambda: not job.active)
        self.assertEqual(job.snapshot()['state'], 'FINISH')

    def test_delayed_intermediate_marker_cannot_claim_final_completion(self):
        class DelayedFinal(FakeLink):
            def send_gcode(self, text, wait=6):
                self.sent.append(text)
                if text.startswith('M400\n'):
                    # A freshly received 200/201 belongs to a prior checkpoint.
                    self.status['stg_cur'] = 200; self.stage_version += 1
                elif 'gcode_claim_action' in text:
                    self.confirm(text)
                return {'result': 'SUCCESS'}
        link = DelayedFinal()
        job = DirectJob(block_timeout=.2, poll_interval=.002)
        job.start(program('G1 X0 Y0 Z3 F12000', 'G1 X1 F2400'), 'señal final', link, continuous=True)
        self.wait(lambda: bool(link.sent) and link.sent[-1].startswith('M400\n'))
        self.assertRegex(link.sent[-1], r'action : 20[45]')
        time.sleep(.03)
        self.assertTrue(job.active)
        link.confirm()
        self.wait(lambda: not job.active)
        self.assertEqual(job.snapshot()['state'], 'FINISH')
        self.assertFalse(job.snapshot()['ink_verified'])

    def test_feeding_is_paced_without_extra_stop_commands(self):
        clock_value = [0.0]
        def clock():
            clock_value[0] += .05
            return clock_value[0]
        class TimedLink(FakeLink):
            def __init__(self):
                super().__init__(auto=True); self.times = []; self.waits = []
            def send_gcode(self, text, wait=6):
                self.times.append(clock_value[0]); self.waits.append(wait)
                return super().send_gcode(text, wait)
        link = TimedLink()
        job = DirectJob(block_timeout=.2, poll_interval=.001, lookahead_seconds=.1, clock=clock)
        job.start(program('G1 X0 Y0 Z3 F12000', *(f'G1 X{i / 100:.2f} F2400' for i in range(1, 201))),
                  'ritmo', link, continuous=True)
        self.wait(lambda: not job.active)
        self.assertEqual(job.snapshot()['state'], 'FINISH')
        # Posicionamiento aislado y fin; ningún M400 en el recorrido normal.
        self.assertEqual(sum('M400' in p.splitlines() for p in link.sent), 2)
        # Short segments must not incur an invented 25 ms delay apiece.
        self.assertLess(link.times[-1] - link.times[1], 3)
        self.assertGreater(max(link.waits), 6)

    def test_pause_during_pacing_does_not_publish_another_body(self):
        class PauseLink(FakeLink):
            def send_gcode(self, text, wait=6):
                result = super().send_gcode(text, wait)
                if len(self.sent) == 2:
                    job.control('pause')
                return result
        link = PauseLink(auto=True)
        job = DirectJob(block_timeout=.2, poll_interval=.002, lookahead_seconds=.1)
        job.start(program('G1 X0 Y0 Z3 F12000', *(f'G1 X{i / 100:.2f} F2400' for i in range(1, 120))),
                  'pausar', link, continuous=True)
        self.wait(lambda: job.snapshot()['state'] == 'PAUSE')
        self.assertEqual(len(link.sent), 3)
        self.assertTrue(link.sent[-1].startswith('M400\n'))
        job.control('stop')
        self.wait(lambda: not job.active)
        self.assertEqual(job.snapshot()['state'], 'CANCELED')

    def test_thirty_thousand_commands_complete_without_loss_or_replay(self):
        link = FakeLink(auto=True)
        job = DirectJob(poll_interval=.001, clock=lambda: time.monotonic() * 1e6)
        motions = [f'G1 X{i % 200} Y100 F2400' for i in range(30000)]
        job.start(program('G1 X0 Y0 Z3 F12000', *motions), 'grande', link, continuous=True)
        self.wait(lambda: not job.active, seconds=15)
        self.assertEqual(job.snapshot()['state'], 'FINISH')
        actual = [line for payload in link.sent for line in payload.splitlines() if line.startswith('G1 X')]
        self.assertEqual(actual, ['G1 X0 Y0 Z3 F12000', *motions])
        self.assertEqual(job.snapshot()['sent_lines'], job.snapshot()['total_lines'])


class LargePipelineTests(unittest.TestCase):
    def test_large_image_upload_conversion_rotation_export_and_full_transmission(self):
        import app as server
        from plotter.stream import compile_program, _Block
        saved = copy.deepcopy(server.config)
        image_id = None
        try:
            server.config.clear(); server.config.update(copy.deepcopy(server.DEFAULT_CONFIG))
            server.config['paper'].update(size='a5', bed_x=40, bed_y=10)
            client = server.app.test_client()
            image = Image.new('RGB', (4000, 4000), 'white')
            draw = ImageDraw.Draw(image)
            for row in range(70):
                for col in range(70):
                    x, y = 40 + col * 56, 40 + row * 56
                    draw.ellipse((x - 15, y - 15, x + 15, y + 15), fill='black')
            data = io.BytesIO(); image.save(data, format='PNG'); data.seek(0)
            started = time.monotonic()
            uploaded = client.post('/api/image/upload', data={'file': (data, 'large.png')})
            self.assertEqual(uploaded.status_code, 200)
            image_id = uploaded.json['id']
            response = client.post('/api/element/tasks', json={'type': 'image', 'image': image_id,
                'w': 120, 'opts': {'mode': 'contornos', 'detail': 100}})
            self.assertEqual(response.status_code, 202)
            token, end = response.json['task'], time.monotonic() + 30
            while time.monotonic() < end:
                task = client.get('/api/element/tasks/' + token).json
                if task['state'] in ('done', 'failed'): break
                time.sleep(.01)
            self.assertEqual(task['state'], 'done', task.get('message'))
            result = task['result']
            strokes = sum(len(layer['paths']) for layer in result['layers'])
            self.assertGreaterEqual(strokes, 4096)
            composed = client.post('/api/compose', json={'items': [{'render_id': result['render_id'],
                'x': 14, 'y': 55, 'rotation': 10}]})
            self.assertEqual(composed.status_code, 200)
            self.assertFalse(composed.json['outside'])
            exported = client.get('/api/export', query_string={'job': composed.json['job'], 'fmt': 'gcode', 'ready': '1'})
            self.assertEqual(exported.status_code, 200)
            text = exported.data.decode()
            events, total = compile_program(text)
            expected = [line for event in events if isinstance(event, _Block) for line in event.lines if line != 'M400']
            self.assertGreater(total, 30000)
            prepared = time.monotonic()
            link = FakeLink(auto=True)
            job = DirectJob(clock=lambda: time.monotonic() * 1e6)
            job.start(text, 'imagen grande', link, continuous=True)
            end = time.monotonic() + 30
            while job.active and time.monotonic() < end: time.sleep(.01)
            self.assertFalse(job.active)
            self.assertEqual(job.snapshot()['state'], 'FINISH')
            actual = [line for packet in link.sent for line in packet.splitlines()
                      if line != 'M400' and not line.startswith('M1002 gcode_claim_action')]
            self.assertEqual(actual, expected)
            report = {'image_pixels': [4000, 4000], 'strokes': strokes, 'commands': total,
                      'packets': len(link.sent), 'preparation_seconds': round(prepared - started, 3),
                      'fake_transmission_seconds': round(time.monotonic() - prepared, 3),
                      'state': job.snapshot()['state'], 'lost_or_repeated_commands': 0, 'hardware_used': False}
            destination = Path(__file__).parent / 'artifacts' / 'large-image-report.json'
            destination.parent.mkdir(exist_ok=True)
            destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
        finally:
            if image_id: server.images.pop(image_id, None)
            server.config.clear(); server.config.update(saved)


if __name__ == '__main__':
    unittest.main()
