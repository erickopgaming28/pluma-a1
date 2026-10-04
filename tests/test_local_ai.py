import copy
import json
import threading
import time
import unittest
from concurrent.futures import CancelledError
from unittest.mock import patch

import numpy as np

import app as server
from plotter import local_ai, sketch


ADVICE = dict(mode='retrato', reason='Conservar los tonos del rostro y limpiar textura.',
              detail=40, photo_cleaning=120, shade=100, contrast=0, brightness=0)


class FakeResponse:
    status = 200
    def __init__(self, lines): self.lines = iter(lines)
    def readline(self, _): return next(self.lines, b'')


class LocalAiTests(unittest.TestCase):
    def test_restrict_options_and_respect_goal(self):
        advice = dict(ADVICE, contrast=999, printer='G28', shade=999)
        result = local_ai.validate_advice(advice, 'lineas')
        self.assertEqual(result['opts']['mode'], 'fotolinea')
        self.assertEqual(result['opts']['contrast'], 40)
        self.assertEqual(result['opts']['shade'], 130)
        self.assertNotIn('printer', result['opts'])
        for value in (True, float('nan'), '20', None):
            with self.assertRaises(ValueError): local_ai.validate_advice(dict(ADVICE, detail=value))
        with self.assertRaises(ValueError): local_ai.validate_advice(dict(ADVICE, mode='commands'))

    def test_only_local_vision_models_are_listed(self):
        tags = {'models': [dict(name='text', capabilities=['completion']),
                           dict(name='qwen2.5vl:7b', capabilities=['vision']),
                           dict(name='remote', capabilities=['vision'], remote_host='cloud.test'),
                           dict(name='model-cloud', capabilities=['vision'])]}
        with patch.object(local_ai, '_request', return_value=tags):
            self.assertEqual(local_ai.models()['models'], ['qwen2.5vl:7b'])
        with patch.object(local_ai, '_request', side_effect=ConnectionRefusedError):
            self.assertFalse(local_ai.models()['available'])
        with self.assertRaises(ValueError): local_ai.models('https://external.test')

    def test_lmstudio_capability_metadata(self):
        data = {'models': [dict(key='vision', type='llm', format='gguf', capabilities={'vision': True}),
                           dict(key='text', type='llm', format='gguf', capabilities={'vision': False})]}
        with patch.object(local_ai, '_request', return_value=data):
            self.assertEqual(local_ai.models('lmstudio')['models'], ['vision'])

    def test_streaming_both_providers_without_mutating_pixels(self):
        rgb = np.full((20, 30, 3), 180, np.uint8); original = rgb.copy()
        for provider in ('ollama', 'lmstudio'):
            chunk = ({'message': {'content': json.dumps(ADVICE)}, 'done': True} if provider == 'ollama'
                     else {'type': 'message.delta', 'content': json.dumps(ADVICE)})
            line = json.dumps(chunk).encode() + b'\n'
            lines = [line] if provider == 'ollama' else [b'event: message.delta\n', b'data: ' + line, b'\n', b'data: {"type":"chat.end"}\n']
            metadata = {'capabilities': ['vision']} if provider == 'ollama' else {'models':[{'key':'vision','format':'gguf','capabilities':{'vision':True,'reasoning':{'allowed_options':['off']}}}]}
            with patch.object(local_ai, 'models', return_value={'models': ['vision']}), \
                 patch.object(local_ai, '_request', return_value=metadata), \
                 patch.object(local_ai.http.client, 'HTTPConnection') as connection:
                connection.return_value.getresponse.return_value = FakeResponse(lines)
                result = local_ai.analyze(rgb, 'vision', provider=provider)
                self.assertEqual(result['opts']['mode'], 'retrato')
                self.assertEqual(connection.call_args.args[:2], ('127.0.0.1', local_ai.PROVIDERS[provider]))
                payload = json.loads(connection.return_value.request.call_args.args[2])
                self.assertNotIn('tools', payload)
            np.testing.assert_array_equal(rgb, original)

    def test_cancel_and_remote_model_never_send_pixels(self):
        rgb = np.full((20, 20, 3), 128, np.uint8)
        with patch.object(local_ai, 'models', return_value={'models': ['vision']}), \
             patch.object(local_ai, '_request', return_value={'capabilities': ['vision'], 'remote_host': 'external'}), \
             patch.object(local_ai.http.client, 'HTTPConnection') as connection:
            with self.assertRaises(ValueError): local_ai.analyze(rgb, 'vision')
            connection.assert_not_called()
        with patch.object(local_ai, 'models', return_value={'models': ['vision']}), \
             patch.object(local_ai, '_request', return_value={'capabilities': ['vision']}), \
             patch.object(local_ai.http.client, 'HTTPConnection') as connection:
            with self.assertRaises(CancelledError): local_ai.analyze(rgb, 'vision', cancelled=lambda: True)
            connection.assert_not_called()

    def test_cancel_interrupts_cold_model_wait(self):
        canceled, released = threading.Event(), threading.Event()
        with patch.object(local_ai, 'models', return_value={'models': ['vision']}), \
             patch.object(local_ai, '_request', return_value={'capabilities': ['vision']}), \
             patch.object(local_ai.http.client, 'HTTPConnection') as connection:
            connection.return_value.sock.shutdown.side_effect = lambda *_: released.set()
            def wait_response():
                threading.Timer(.1, canceled.set).start()
                self.assertTrue(released.wait(2))
                raise ConnectionResetError()
            connection.return_value.getresponse.side_effect = wait_response
            with self.assertRaises(CancelledError):
                local_ai.analyze(np.zeros((20,20,3),np.uint8), 'vision', cancelled=canceled.is_set)

    def test_api_analyzes_crop_only_without_printer_commands(self):
        server.images['ai-test'] = {'rgb': np.full((40,60,3), 180, np.uint8), 'name':'synthetic'}
        original = server.images['ai-test']['rgb'].copy()
        received = []
        def analyze(rgb, *_): received.append(rgb.shape); return local_ai.validate_advice(ADVICE)
        try:
            with patch.object(local_ai,'analyze',side_effect=analyze), \
                 patch.object(server.link,'ensure') as connect, patch.object(server.direct_job,'start') as send:
                client = server.app.test_client()
                result = client.post('/api/ai/tasks',json={'image':'ai-test','model':'vision','crop':{'x':0,'y':0,'w':.5,'h':1}})
                self.assertEqual(result.status_code,202)
                for _ in range(50):
                    task=client.get('/api/ai/tasks/'+result.json['task']).json
                    if task['state']=='done': break
                    time.sleep(.01)
                self.assertEqual(task['state'],'done'); self.assertEqual(received,[(40,30,3)])
                connect.assert_not_called(); send.assert_not_called()
            np.testing.assert_array_equal(server.images['ai-test']['rgb'],original)
        finally: server.images.pop('ai-test',None)

    def test_simplification_reduces_texture_without_erasing_eye_shapes(self):
        rng=np.random.default_rng(4)
        g=np.clip(180+rng.normal(0,12,(200,200)),0,255).astype(np.uint8)
        import cv2
        cv2.ellipse(g,(100,100),(35,10),0,0,360,20,3)
        raw=sketch._photo_lines(g,.8,.6,2)
        clean=sketch._photo_lines(g,.8,.6,2,True)
        self.assertLess(len(clean),len(raw))
        points=np.concatenate(clean)
        self.assertTrue(np.any((abs(points[:,0]-100)<40)&(abs(points[:,1]-100)<15)))


if __name__ == '__main__': unittest.main()
