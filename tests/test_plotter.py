import copy
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from unittest.mock import MagicMock
from types import SimpleNamespace

import numpy as np
from PIL import Image
import app as server
from plotter import gcode, sketch, printer


class PlotterTests(unittest.TestCase):
    def setUp(self):
        self.previous = copy.deepcopy(server.config)
        server.config.clear()
        server.config.update(copy.deepcopy(server.DEFAULT_CONFIG))
        server.jobs.clear()
        server.renders.clear()
        self.client = server.app.test_client()
        self.layers = [{'name': 'Azul', 'color': '#2743b8',
                        'paths': [np.array([[30., 240.], [50., 240.]])]}]

    def tearDown(self):
        server.config.clear()
        server.config.update(self.previous)

    def test_cold_motion_and_ready_mode(self):
        text, _ = gcode.build_gcode(self.layers, server.config)
        self.assertIn('M400 U1', text)
        self.assertIn('G28', text)
        self.assertNotRegex(text, r'(?m)^G[01].*\bE[-\d]')
        self.assertNotRegex(text, r'(?m)^M10[49] S[1-9]')
        ready, _ = gcode.build_gcode(self.layers, server.config, pen_ready=True)
        self.assertNotIn('G28', ready)
        self.assertNotIn('G29 A1', ready)

    def test_all_jobs_have_executable_envelope(self):
        cfg = server.config
        jobs = [gcode.build_gcode(self.layers, cfg)[0], gcode.build_adjust_gcode(cfg),
                gcode.build_guide_gcode(cfg)[0], gcode.build_diagnostic_gcode()]
        for text in jobs:
            self.assertEqual(text.count('; EXECUTABLE_BLOCK_START'), 1)
            self.assertEqual(text.count('; EXECUTABLE_BLOCK_END'), 1)
            self.assertIn('; CONFIG_BLOCK_START', text)
            start = text.index('; EXECUTABLE_BLOCK_START')
            end = text.index('; EXECUTABLE_BLOCK_END')
            for line in text.splitlines():
                if line and not line.startswith(';'):
                    self.assertTrue(start < text.index(line) < end)
        diagnostic = gcode.build_diagnostic_gcode()
        commands = [line.split()[0] for line in diagnostic.splitlines() if line and not line.startswith(';')]
        self.assertEqual(set(commands), {'M73', 'M400'})

    def test_pause_cannot_be_disabled_after_home(self):
        server.config['pen']['pause_for_pen'] = False
        text, _ = gcode.build_gcode(self.layers, server.config)
        self.assertIn('M400 U1', text)

    def test_bad_coordinates_and_config(self):
        for point in ((float('nan'), 240), (-1, 240), (30, 0)):
            layer = copy.deepcopy(self.layers)
            layer[0]['paths'][0][0] = point
            with self.assertRaises(ValueError):
                gcode.build_gcode(layer, server.config)
        for key, value in (('draw_speed', 0), ('z_up', 3), ('offset_y', float('inf'))):
            cfg = copy.deepcopy(server.config)
            cfg['pen'][key] = value
            with self.assertRaises(ValueError):
                gcode.validate_config(cfg)

    def test_package_checksum_and_svg(self):
        text, st = gcode.build_gcode(self.layers, server.config)
        data = gcode.build_3mf(text, self.layers, server.config, st['seconds'])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            code = archive.read('Metadata/plate_1.gcode')
            self.assertEqual(archive.read('Metadata/plate_1.gcode.md5').decode(), hashlib.md5(code).hexdigest().upper())
            self.assertIn('N2S', archive.read('Metadata/slice_info.config').decode())
        self.assertIn('width="215.9mm"', gcode.build_svg(self.layers, server.config))

    def test_material_declaration_matches_external_mapping(self):
        text = gcode.build_diagnostic_gcode()
        values = dict(line[2:].split(' = ', 1) for line in text.splitlines() if line.startswith('; ') and ' = ' in line)
        self.assertEqual(values['filament_type'], 'PLA')
        self.assertEqual(values['filament_ids'], 'GFA00')
        for key in ('filament_diameter', 'filament_density', 'filament_self_index', 'nozzle_temperature', 'nozzle_temperature_initial_layer'):
            self.assertNotIn(',', values[key])
            self.assertNotIn(';', values[key])
        self.assertEqual(values['nozzle_temperature'], '0')
        self.assertEqual(values['filament_start_gcode'], '')
        self.assertEqual(gcode.normalize_pen_config(gcode._A1_CONFIG), gcode._A1_CONFIG)
        normalized = gcode.normalize_pen_config([
            '; filament_type = PETG;PLA;PLA;TPU',
            '; printable_area = 0x0,256x0,256x256,0x256',
            '; nozzle_temperature = 240,220,220,220',
            '; machine_start_gcode = M104 S220',
        ])
        self.assertIn('; printable_area = 0x0,256x0,256x256,0x256', normalized)
        self.assertIn('; nozzle_temperature = 0', normalized)
        self.assertIn('; machine_start_gcode = ', normalized)
        data = gcode.build_3mf(text, [], server.config, 12)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            filaments = ET.fromstring(archive.read('Metadata/slice_info.config')).findall('./plate/filament')
        self.assertEqual(len(filaments), 1)
        self.assertEqual(filaments[0].get('type'), values['filament_type'])
        self.assertEqual(filaments[0].get('tray_info_idx'), values['filament_ids'])
        self.assertEqual(filaments[0].get('used_g'), '0.00')
        link = printer.PrinterLink()
        with patch.object(link, 'command', return_value={'result': 'success'}) as command:
            link.start_print('prueba.gcode.3mf', 'prueba')
        payload = command.call_args.args[0]
        self.assertEqual(len(payload['ams_mapping']), len(filaments))
        self.assertEqual(payload['ams_mapping'], [-1])
        self.assertEqual(payload['ams_mapping2'], [{'ams_id': 255, 'slot_id': 0}])
        for key in ('use_ams', 'bed_leveling', 'flow_cali', 'vibration_cali', 'layer_inspect'):
            self.assertFalse(payload[key])

    def test_config_transaction_and_stale_job(self):
        old = copy.deepcopy(server.config)
        response = self.client.post('/api/config', json={'pen': {'z_up': 0}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(old, server.config)
        job = server.make_job([{0: self.layers[0]['paths']}], 'prueba')
        self.assertEqual(self.client.post('/api/preflight', json={'job': job['job']}).status_code, 200)
        server.config['pen']['offset_y'] += 1
        self.assertEqual(self.client.get('/api/export?job=' + job['job']).status_code, 400)

    def test_petg_matches_all_generated_jobs(self):
        cfg = copy.deepcopy(server.config)
        cfg['printer']['material'] = 'PETG'
        jobs = [gcode.build_gcode(self.layers, cfg)[0], gcode.build_adjust_gcode(cfg),
                gcode.build_guide_gcode(cfg)[0], gcode.build_diagnostic_gcode(cfg)]
        for text in jobs:
            self.assertIn('; filament_type = PETG\n', text)
            self.assertIn('; filament_ids = GFG99\n', text)
            self.assertIn('; filament_density: 1.27\n', text)
            self.assertNotRegex(text, r'(?m)^G[01].*\bE[-\d]')
            self.assertNotRegex(text, r'(?m)^M(?:10[49]|1[49]0) S[1-9]')
            with zipfile.ZipFile(io.BytesIO(gcode.build_3mf(text, [], cfg, 12))) as archive:
                filament = ET.fromstring(archive.read('Metadata/slice_info.config')).find('./plate/filament')
                self.assertEqual(filament.get('type'), 'PETG')
                self.assertEqual(filament.get('tray_info_idx'), 'GFG99')
        old = copy.deepcopy(server.config)
        response = self.client.post('/api/config', json={'printer': {'material': 'desconocido'}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(server.config, old)

    def test_render_tokens_and_text_export(self):
        r = self.client.post('/api/element', json={'id': 'test', 'type': 'text', 'text': 'Hola, ¿cómo estás? Ñ á é', 'w': 100, 'opts': {'size': 8}})
        self.assertEqual(r.status_code, 200, r.json)
        token = r.json['render_id']
        other = self.client.post('/api/element', json={'id': 'test', 'type': 'text', 'text': 'Otro', 'w': 100}).json
        self.assertNotEqual(token, other['render_id'])
        job = self.client.post('/api/compose', json={'items': [{'render_id': token, 'x': 30, 'y': 100}]}).json
        self.assertEqual(self.client.get('/api/export?fmt=svg&job=' + job['job']).status_code, 200)
        self.assertEqual(self.client.get('/api/export?job=' + job['job'] + '&page=-1').status_code, 400)
        server.config['pen']['offset_x'] = 1
        self.assertEqual(self.client.post('/api/compose', json={'items': [{'render_id': token, 'x': 30, 'y': 100}]}).status_code, 400)

    def test_image_conversion_and_spacing(self):
        image = np.full((80, 100, 3), 255, np.uint8)
        image[20:60, 30:70] = 30
        layers, height = sketch.make_sketch(image, {'mode': 'boceto'}, 60)
        self.assertTrue(layers[0])
        self.assertEqual(height, 48)
        with self.assertRaises(ValueError):
            sketch.make_sketch(image, {'hatch_spacing': 0}, 60)

    def test_cross_origin_and_secret(self):
        response = self.client.post('/api/config', json={}, headers={'Origin': 'http://otro.example'})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('access_code', server.public_config()['printer'])

    def test_accepted_order_is_not_started_job(self):
        fake = MagicMock()
        fake.ensure.return_value = True
        fake.wait_connected.return_value = True
        fake.wait_status.return_value = True
        fake.status = {'gcode_state': 'IDLE', 'print_error': 0}
        fake.start_print.return_value = {'result': 'success', 'reason': 'success'}
        with patch.object(server, 'link', fake), patch.object(server.printer, 'upload'), \
                patch.object(server.time, 'monotonic', side_effect=[0, 16]):
            with self.assertRaisesRegex(ValueError, 'no se observó el inicio'):
                server._dispatch(gcode.build_diagnostic_gcode(), [], 12, 'diagnostico', transport='project_file')
        self.assertEqual(server.last_dispatch['phase'], 'error')
        self.assertEqual(server.last_dispatch['ack']['result'], 'success')

    def test_printer_error_blocks_upload_even_when_idle(self):
        fake = MagicMock()
        fake.ensure.return_value = fake.wait_connected.return_value = fake.wait_status.return_value = True
        fake.status = {'gcode_state': 'IDLE', 'print_error': 0x05004004}
        with patch.object(server, 'link', fake), patch.object(server.printer, 'upload') as upload:
            with self.assertRaisesRegex(ValueError, '0500-4004'):
                server._dispatch(gcode.build_diagnostic_gcode(), [], 12, 'diagnostico')
        upload.assert_not_called()
        fake.start_print.assert_not_called()

    def test_ack_does_not_replace_actual_printer_state(self):
        link = printer.PrinterLink()
        link.status = {'gcode_state': 'IDLE', 'gcode_file': 'anterior.gcode.3mf', 'subtask_name': 'anterior'}
        payload = {'print': {'command': 'project_file', 'result': 'success', 'sequence_id': '22',
                             'gcode_state': 'RUNNING', 'subtask_name': 'nuevo'}}
        link._on_message(None, None, SimpleNamespace(payload=json.dumps(payload).encode()))
        self.assertEqual(link.status['subtask_name'], 'anterior')
        self.assertEqual(link.status['gcode_state'], 'IDLE')
        self.assertEqual(link.status_version, 0)
        self.assertEqual(link.acks.get_nowait()['sequence_id'], '22')

    def test_status_must_arrive_after_refresh_request(self):
        link = printer.PrinterLink()
        link.connected = True
        link.key = ('localhost', 'test', 'test')
        link.client = MagicMock()
        link.last_status = 1
        link.status_version = 1
        with patch.object(printer.time, 'monotonic', side_effect=[0, 7]):
            self.assertFalse(link.wait_status())
        def report(*args):
            link._on_message(None, None, SimpleNamespace(payload=b'{"print":{"gcode_state":"IDLE","print_error":0}}'))
        link.client.publish.side_effect = report
        self.assertTrue(link.wait_status())

    def test_start_confirmation_matches_fresh_file(self):
        for matching in (False, True):
            fake = MagicMock()
            fake.ensure.return_value = fake.wait_connected.return_value = fake.wait_status.return_value = True
            fake.status = {'gcode_state': 'IDLE', 'print_error': 0}
            fake.status_version = 1
            def accept(filename, title):
                fake.status_version = 2
                fake.status.update(gcode_state='RUNNING', subtask_name=title,
                                   gcode_file=filename if matching else 'otro.gcode.3mf')
                return {'result': 'success'}
            fake.start_print.side_effect = accept
            with patch.object(server, 'link', fake), patch.object(server.printer, 'upload'), \
                    patch.object(server.time, 'monotonic', side_effect=[0, 0, 16]), patch.object(server.time, 'sleep'):
                if matching:
                    self.assertIn('Inicio confirmado', server._dispatch(gcode.build_diagnostic_gcode(), [], 12, 'diagnostico', transport='project_file'))
                else:
                    with self.assertRaisesRegex(ValueError, 'no se observó el inicio'):
                        server._dispatch(gcode.build_diagnostic_gcode(), [], 12, 'diagnostico', transport='project_file')


if __name__ == '__main__':
    unittest.main()
