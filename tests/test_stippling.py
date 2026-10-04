import copy
import io
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import CancelledError
from unittest.mock import patch
import numpy as np
from PIL import Image
import app as server
from plotter import sketch, pathops, gcode


class StipplingTests(unittest.TestCase):
    def gradient(self):
        rgb = np.full((100, 240, 3), 255, np.uint8)
        for i, gray in enumerate((220, 160, 80)):
            rgb[10:90, 10+i*80:70+i*80] = gray
        return rgb

    def test_points_preserve_tonal_density_without_contours(self):
        paths, height = sketch.make_sketch(self.gradient(), dict(mode='puntillismo', shade=100, dot_spacing=.7),120)
        counts = [sum(5+i*40 < p[0,0] < 35+i*40 and 5 < p[0,1] < 45 for p in paths[0]) for i in range(3)]
        self.assertGreater(counts[1], counts[0]*1.5)
        self.assertGreater(counts[2], counts[1]*1.5)
        self.assertTrue(all(p.shape == (2,2) and np.array_equal(p[0],p[1]) for p in paths[0]))
        self.assertEqual(pathops.stats(paths[0])['draw_mm'],0)
        self.assertTrue(all(np.isfinite(p).all() and (p >= 0).all() and (p <= [120,height]).all() for p in paths[0]))

    def test_white_and_zero_density_have_no_points(self):
        for rgb, shade in [(np.full((60,60,3),255,np.uint8),300),(self.gradient(),0)]:
            paths,_ = sketch.make_sketch(rgb,dict(mode='puntillismo',shade=shade),60)
            self.assertFalse(any(paths.values()))

    def test_spacing_strength_seed_and_crop(self):
        options=dict(mode='puntillismo',shade=100,dot_spacing=.7,crop=dict(x=.05,y=.1,w=.9,h=.8))
        a,h=sketch.make_sketch(self.gradient(),options,100)
        same,_=sketch.make_sketch(self.gradient(),options,100)
        sparse,_=sketch.make_sketch(self.gradient(),dict(options,dot_spacing=1.4),100)
        dense,_=sketch.make_sketch(self.gradient(),dict(options,shade=200),100)
        self.assertGreater(len(a[0]),len(sparse[0])*3)
        self.assertGreater(len(dense[0]),len(a[0]))
        np.testing.assert_array_equal(a[0],same[0])
        self.assertAlmostEqual(h,80/216*100)

    def test_invalid_spacing_is_rejected(self):
        for spacing in (0,.29,10.1,float('nan'),True,'bad'):
            with self.subTest(spacing=spacing),self.assertRaises(ValueError):
                sketch.make_sketch(self.gradient(),dict(mode='puntillismo',dot_spacing=spacing),100)

    def test_large_tall_crop_has_bounded_sampling_and_cancels(self):
        checks=[]
        rgb=np.full((1000,40,3),80,np.uint8)
        with patch.object(sketch.cv2,'remap',wraps=sketch.cv2.remap) as remap:
            with self.assertRaises(CancelledError):
                sketch.make_sketch(rgb,dict(mode='puntillismo',dot_spacing=.3),400,
                    progress=lambda p,m: checks.append(p),cancelled=lambda: bool(checks and checks[-1]>=45))
        self.assertLessEqual(remap.call_args.args[1].size,40000)

    def test_multicolor_points_are_separate_contacts(self):
        rgb=np.full((80,120,3),255,np.uint8);rgb[10:70,10:50]=[255,0,0];rgb[10:70,70:110]=[0,0,255]
        paths,_=sketch.make_sketch(rgb,dict(mode='puntillismo',shade=100),60,pens=['#ff0000','#0000ff'])
        self.assertEqual(set(paths),{0,1})
        self.assertTrue(all(np.array_equal(p[0],p[1]) for layer in paths.values() for p in layer))

    def test_api_rotates_exports_contacts_and_never_moves_printer(self):
        saved=copy.deepcopy(server.config)
        server.config.clear();server.config.update(copy.deepcopy(server.DEFAULT_CONFIG))
        server.config['paper'].update(size='a5',bed_x=40,bed_y=10)
        client=server.app.test_client()
        data=io.BytesIO();Image.fromarray(self.gradient()).save(data,format='PNG');data.seek(0)
        try:
            with tempfile.TemporaryDirectory() as tmp,patch.object(server,'IMAGE_CACHE',Path(tmp)),patch.object(server.link,'send_gcode') as send:
                image=client.post('/api/image/upload',data={'file':(data,'synthetic-tones.png')}).json['id']
                r=client.post('/api/element',json=dict(type='image',image=image,w=60,pen=1,opts=dict(mode='puntillismo',shade=100)))
                self.assertEqual(r.status_code,200,r.json);self.assertEqual(r.json['preview_width'],.3)
                dots=sum(len(l['paths']) for l in r.json['layers'])
                job=client.post('/api/compose',json={'items':[dict(render_id=r.json['render_id'],x=35,y=70,rotation=37)]})
                self.assertFalse(job.json['outside'])
                svg=client.get('/api/export',query_string={'job':job.json['job'],'fmt':'svg'}).data.decode()
                self.assertEqual(svg.count('<circle '),dots);self.assertNotIn('<polyline ',svg)
                text=client.get('/api/export',query_string={'job':job.json['job'],'fmt':'gcode'}).data.decode()
                # Each travel is followed by a separate down/contact/up; no XY joins.
                lines=text.splitlines();contacts=0
                for i,line in enumerate(lines):
                    if line.startswith('G0 X') and i+3<len(lines) and lines[i+1].startswith('G1 Z') and lines[i+2].startswith('G1 X'):
                        self.assertEqual(line.split()[1:3],lines[i+2].split()[1:3])
                        self.assertTrue(lines[i+3].startswith('G1 Z'));contacts+=1
                self.assertEqual(contacts,dots)
                self.assertIn('M73 P',text)
                send.assert_not_called()
        finally:
            server.images.pop(locals().get('image'),None)
            server.config.clear();server.config.update(saved)
