import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app as server
from plotter import gcode, printer, stream


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(server.config)
        server.config.clear()
        server.config.update(copy.deepcopy(server.DEFAULT_CONFIG))
        self.client = server.app.test_client()
        self.job = MagicMock()
        self.job.active = False
        self.job.snapshot.return_value = {'active': False, 'state': '', 'job': ''}
        self.fake = MagicMock()
        self.fake.connected = True
        self.fake.ensure.return_value = True
        self.fake.wait_connected.return_value = True
        self.fake.wait_status.return_value = True
        self.fake.status = {'gcode_state': 'IDLE', 'print_error': 0}
        self.fake.send_gcode_wait.return_value = ('M400\nM400\nM1002 gcode_claim_action : 200\n', {'result': 'success'})
        self.fake.snapshot.return_value = {'connected': True, 'state': 'IDLE'}
        self.previous_direct = dict(server.direct_command)
        server.direct_command.clear()
        self.patches = [patch.object(server, 'link', self.fake), patch.object(server, 'direct_job', self.job)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        server.config.clear()
        server.config.update(self.config)
        server.direct_command.clear()
        server.direct_command.update(self.previous_direct)

    def test_probe_is_cold_no_motion_and_preserves_secrets(self):
        response = self.client.post('/api/printer/direct', json={'action': 'probe'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(self.fake.send_gcode_wait.call_args.args[0], 'M400')
        self.assertEqual(response.json['phase'], 'complete')
        self.assertNotIn('access_code', json.dumps(response.json))

    def test_manual_validation_precedes_network(self):
        invalid = [
            {'action': 'jog', 'axis': 'X', 'distance': 1},
            {'action': 'jog', 'axis': 'Z', 'distance': 5, 'homing_confirmed': True},
            {'action': 'jog', 'axis': 'E', 'distance': 1, 'homing_confirmed': True},
            {'action': 'jog', 'axis': 'X', 'distance': float('nan'), 'homing_confirmed': True},
            {'action': 'jog', 'axis': 'X', 'distance': True, 'homing_confirmed': True},
            {'action': 'home'},
            {'action': 'pen_down', 'homing_confirmed': True},
            {'action': 'custom', 'param': 'M104 S250'},
        ]
        for payload in invalid:
            response = self.client.post('/api/printer/direct', json=payload)
            self.assertEqual(response.status_code, 400, (payload, response.json))
        self.fake.ensure.assert_not_called()
        self.fake.send_gcode_wait.assert_not_called()

    def test_jog_keeps_limits_and_reference_mode(self):
        text, _ = server._direct_gcode({'action': 'jog', 'axis': 'Y', 'distance': -5, 'homing_confirmed': True})
        self.assertIn('G91\nG1 Y-5 F1200', text)
        self.assertIn('M211 X1 Y1 Z1', text)
        self.assertIn('M1002 push_ref_mode', text)
        self.assertTrue(text.endswith('M1002 pop_ref_mode\nM211 R'))
        self.assertNotIn('G28', text)

    def test_uncertain_motion_blocks_until_probe(self):
        self.fake.send_gcode_wait.side_effect = ValueError('Sin confirmación')
        response = self.client.post('/api/printer/direct', json={'action': 'jog', 'axis': 'X', 'distance': 1, 'homing_confirmed': True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(server.direct_command['phase'], 'unknown')
        self.fake.send_gcode_wait.reset_mock()
        response = self.client.post('/api/printer/direct', json={'action': 'pen_up', 'homing_confirmed': True})
        self.assertEqual(response.status_code, 400)
        self.fake.send_gcode_wait.assert_not_called()
        self.assertEqual(server.direct_command['phase'], 'unknown')
        response = self.client.post('/api/printer/direct', json={'action': 'pen_up', 'homing_confirmed': True})
        self.assertEqual(response.status_code, 400)
        self.fake.send_gcode_wait.assert_not_called()
        self.fake.send_gcode_wait.side_effect = None
        self.assertEqual(self.client.post('/api/printer/direct', json={'action': 'probe'}).status_code, 200)

    def test_stream_never_uploads_a_model_or_file(self):
        text = gcode.build_diagnostic_gcode(server.config)
        with patch.object(printer, 'upload') as upload:
            server._dispatch(text, [], 12, 'diagnostico', transport='stream', kind='diagnostic')
        upload.assert_not_called()
        self.fake.start_print.assert_not_called()
        self.fake.start_gcode_file.assert_not_called()
        self.fake.send_gcode_wait.assert_called_once_with('M400', timeout=12)
        self.job.start.assert_called_once()
        self.assertEqual(self.job.start.call_args.args[0], text)

    def test_active_stream_owns_motion_and_config(self):
        self.job.active = True
        for endpoint, payload in [('/api/printer/direct', {'action': 'probe'}), ('/api/printer/level', {}), ('/api/config', {})]:
            self.assertEqual(self.client.post(endpoint, json=payload).status_code, 400)
        response = self.client.post('/api/printer/control', json={'action': 'pause'})
        self.assertEqual(response.status_code, 200)
        self.job.control.assert_called_once_with('pause')
        self.fake.command.assert_not_called()

    def test_all_generated_programs_are_streamable(self):
        layers = [{'name': 'Azul', 'color': '#2743b8', 'paths': [__import__('numpy').array([[30., 240.], [50., 240.]])]}]
        for text in (gcode.build_gcode(layers, server.config)[0], gcode.build_gcode(layers, server.config, pen_ready=True)[0],
                     gcode.build_adjust_gcode(server.config), gcode.build_guide_gcode(server.config)[0], gcode.build_diagnostic_gcode(server.config)):
            events, count = stream.compile_program(text)
            self.assertTrue(events)
            self.assertGreater(count, 0)


class MqttTests(unittest.TestCase):
    def test_direct_ack_is_not_telemetry(self):
        link = printer.PrinterLink()
        ack = {'command': 'gcode_line', 'sequence_id': '30', 'result': 'success', 'stg_cur': 200, 'gcode_state': 'RUNNING'}
        link._on_message(None, None, SimpleNamespace(payload=json.dumps({'print': ack}).encode()))
        self.assertEqual(link.acks.get_nowait(), ack)
        self.assertEqual(link.status, {})
        self.assertEqual(link.stage_version, 0)
        link._on_message(None, None, SimpleNamespace(payload=b'{"print":{"stg_cur":200,"gcode_state":"IDLE"}}'))
        self.assertEqual(link.stage_version, 1)
        self.assertEqual(link.status['stg_cur'], 200)

    def test_no_automatic_replay_and_sequence_matching(self):
        link = printer.PrinterLink()
        link.connected = True
        link.key = ('ip', 'serial', 'code')
        link.client = MagicMock()
        def publish(topic, blob, **kwargs):
            command = json.loads(blob)['print']
            link.acks.put({'command': 'gcode_line', 'sequence_id': str(int(command['sequence_id']) - 1), 'result': 'success'})
            link.acks.put({'command': 'gcode_line', 'sequence_id': command['sequence_id'], 'result': 'success'})
            return SimpleNamespace(rc=0)
        link.client.publish.side_effect = publish
        self.assertEqual(link.send_gcode('G1 X1 F100')['result'], 'success')
        self.assertEqual(link.client.publish.call_count, 1)
        self.assertEqual(link.client.publish.call_args.kwargs['qos'], 0)

    def test_missing_ack_does_not_mean_completed(self):
        link = printer.PrinterLink()
        link.connected = True
        link.key = ('ip', 'serial', 'code')
        with patch.object(link, 'send_gcode', return_value=None) as send:
            with self.assertRaisesRegex(ValueError, 'No hubo respuesta'):
                link.send_gcode_wait('G1 X1 F100', timeout=.01)
        self.assertEqual(send.call_count, 1)

    def test_direct_barrier_requires_new_different_stage(self):
        link = printer.PrinterLink()
        link.connected = True
        link.key = ('ip', 'serial', 'code')
        link.status = {'stg_cur': '200'}
        def stale_ack(text):
            link.stage_version += 1  # un pushall del estado anterior no es fin del comando
            return {'result': 'success'}
        with patch.object(link, 'send_gcode', side_effect=stale_ack) as send, \
                patch.object(printer.time, 'monotonic', side_effect=[0, 1]):
            with self.assertRaisesRegex(ValueError, 'no confirmó'):
                link.send_gcode_wait('M400', timeout=.01)
        self.assertIn('gcode_claim_action : 201', send.call_args.args[0])
        def completed(text):
            link.stage_version += 1
            link.status['stg_cur'] = 201
            return {'result': 'success'}
        with patch.object(link, 'send_gcode', side_effect=completed):
            text, ack = link.send_gcode_wait('M400', timeout=.1)
        self.assertEqual(ack['result'], 'success')
        self.assertIn('gcode_claim_action : 201', text)


if __name__ == '__main__':
    unittest.main()
