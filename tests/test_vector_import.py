"""Real file fixtures, geometry/fidelity checks, and offline Flask roundtrips."""
import copy
import io
import struct
import unittest
from unittest.mock import patch

import numpy as np

from plotter import vector_import as vector


def svg(body, width=40, height=20):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" viewBox="0 0 {width} {height}">{body}</svg>'.encode()


def binary_stl(triangles, header=b'solid binary can start with solid'):
    data = header.ljust(80, b'\0') + struct.pack('<I', len(triangles))
    for tri in triangles:
        data += struct.pack('<12fH', 0, 0, 0, *np.asarray(tri).ravel(), 0)
    return data


def ascii_stl(triangles):
    out = ['solid fixture']
    for tri in triangles:
        out.append('facet normal 0 0 0\nouter loop')
        out.extend('vertex ' + ' '.join(map(str, p)) for p in tri)
        out.append('endloop\nendfacet')
    return ('\n'.join(out) + '\nendsolid fixture').encode()


def dxf(pairs, units=4, blocks=()):
    header = [(0, 'SECTION'), (2, 'HEADER'), (9, '$INSUNITS'), (70, units), (0, 'ENDSEC')]
    if blocks:
        header += [(0, 'SECTION'), (2, 'BLOCKS'), *blocks, (0, 'ENDSEC')]
    return '\n'.join(str(v) for pair in [*header, (0, 'SECTION'), (2, 'ENTITIES'), *pairs, (0, 'ENDSEC'), (0, 'EOF')] for v in pair).encode()


def restored(result):
    return vector.render_vector(result['vector'], result['w'])[0]


class VectorParsingTests(unittest.TestCase):
    def test_svg_native_mm_and_nested_transform_use_preserve_strokes(self):
        data = svg('<defs><path id="wire" d="M0 0H10V5" fill="none" stroke="black"/></defs>'
                   '<g transform="translate(3 7)"><use href="#wire" transform="scale(2)"/></g>')
        result = vector.import_file(data, 'circuit.svg')
        self.assertEqual((result['w'], result['h']), (20, 10))
        np.testing.assert_allclose(restored(result)[0], [[0, 0], [20, 0], [20, 10]])

    def test_svg_curves_arcs_relative_commands_and_closure_are_actual_paths(self):
        data = svg('<path fill="none" stroke="black" d="M0 0 c0 10 10 10 10 0 s10 -10 10 0 q5 10 10 0 t10 0 a5 5 0 0 1 10 0 l0 10h-10v-5z"/>', 50, 30)
        result = vector.import_file(data, 'curves.svg')
        path = restored(result)[0]
        self.assertGreater(len(path), 35)
        np.testing.assert_allclose(path[0], path[-1])
        self.assertTrue(np.isfinite(path).all())
        self.assertAlmostEqual(result['w'], 50)

    def test_svg_hides_background_and_explains_text_and_raster_omissions(self):
        data = svg('<rect width="40" height="20" fill="white"/><g display="none"><line x2="40" y2="20"/></g>'
                   '<line x1="2" y1="3" x2="12" y2="8" stroke="black"/><text x="2" y="3">R1</text><image href="http://invalid/never-fetch"/>')
        result = vector.import_file(data, 'schema.svg')
        self.assertEqual((result['w'], result['h']), (10, 5))
        self.assertEqual(len(result['vector']['paths']), 1)
        self.assertEqual(len(result['warnings']), 2)

    def test_svg_external_entities_clips_css_and_recursion_are_rejected(self):
        files = [b'<!DOCTYPE svg [<!ENTITY steal SYSTEM "file:///private">]><svg>&steal;</svg>',
                 svg('<g clip-path="url(#clip)"><line x2="10"/></g>'),
                 svg('<style>path{stroke:black}</style><path d="M0 0L10 10"/>'),
                 svg('<defs><use id="loop" href="#loop"/></defs><use href="#loop"/>')]
        for data in files:
            with self.subTest(data=data), self.assertRaises(ValueError):
                vector.import_file(data, 'a.svg')

    def test_stl_ascii_and_binary_deduplicate_faces_and_hide_triangulation(self):
        triangles = [[[0, 0, 0], [20, 0, 0], [20, 10, 0]], [[0, 0, 0], [20, 10, 0], [0, 10, 0]]]
        triangles += [list(reversed(t)) for t in triangles]
        for data in (binary_stl(triangles), ascii_stl(triangles)):
            with self.subTest(binary=data[:20]):
                result = vector.import_file(data, 'plate.stl', scale=2)
                self.assertEqual((result['w'], result['h']), (40, 20))
                self.assertEqual(result['segment_count'], 4)
                self.assertEqual(len(result['vector']['paths']), 1)
                p = restored(result)[0]
                self.assertAlmostEqual(np.linalg.norm(np.diff(p, axis=0), axis=1).sum(), 120)

    def test_stl_union_preserves_concavity_and_intersected_triangle_boundaries(self):
        # Union of two partially overlapping rectangles is L-shaped, not a hull.
        tris = np.array([[[0, 0], [20, 0], [20, 10]], [[0, 0], [20, 10], [0, 10]],
                         [[0, 0], [10, 0], [10, 20]], [[0, 0], [10, 20], [0, 20]]], float)
        paths = vector._join_segments(vector._triangle_union_outline(tris))
        self.assertEqual(len(paths), 1)
        p = paths[0]
        self.assertTrue(any(np.allclose(point, [10, 10]) for point in p))
        self.assertAlmostEqual(np.linalg.norm(np.diff(p, axis=0), axis=1).sum(), 80)

    def test_stl_union_preserves_holes(self):
        tris = []
        for x0, y0, x1, y1 in [(0, 0, 30, 10), (0, 20, 30, 30), (0, 10, 10, 20), (20, 10, 30, 20)]:
            tris += [[[x0, y0], [x1, y0], [x1, y1]], [[x0, y0], [x1, y1], [x0, y1]]]
        paths = vector._join_segments(vector._triangle_union_outline(np.array(tris, float)))
        self.assertEqual(len(paths), 2)
        perimeters = sorted(np.linalg.norm(np.diff(p, axis=0), axis=1).sum() for p in paths)
        np.testing.assert_allclose(perimeters, [40, 120])

    def test_stl_all_projections_use_actual_mesh_dimensions(self):
        tri = [[[0, 0, 0], [20, 10, 5], [0, 10, 0]]]
        for projection, dims in [('xy', (20, 10)), ('xz', (20, 5)), ('yz', (10, 5))]:
            result = vector.import_file(binary_stl(tri), 'a.stl', projection=projection, edge_mode='features')
            np.testing.assert_allclose([result['w'], result['h']], dims)
            self.assertTrue(any('ocultas' in warning for warning in result['warnings']))

    def test_bad_stl_and_invalid_projection_or_scale_are_rejected(self):
        tri = [[[0, 0, 0], [20, 0, 0], [0, 10, 0]]]
        cases = [(binary_stl(tri)[:-1], {}), (binary_stl(tri), {'projection': 'zz'}),
                 (binary_stl(tri), {'scale': True}), (binary_stl(tri), {'scale': float('nan')}),
                 (ascii_stl(tri).replace(b'vertex 20', b'vertex nan'), {})]
        for data, options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                vector.import_file(data, 'a.stl', **options)

    def test_dxf_units_bulges_blocks_and_line_placement(self):
        blocks = [(0, 'BLOCK'), (2, 'resistor'), (10, 0), (20, 0), (0, 'LINE'), (10, 0), (20, 0), (11, 2), (21, 1), (0, 'ENDBLK')]
        data = dxf([(0, 'INSERT'), (2, 'resistor'), (10, 5), (20, 7), (41, 2), (42, 2),
                    (0, 'LWPOLYLINE'), (70, 0), (10, 0), (20, 0), (42, 1), (10, 2), (20, 0)], units=1, blocks=blocks)
        result = vector.import_file(data, 'circuit.dxf')
        self.assertAlmostEqual(result['w'], 9 * 25.4)
        self.assertAlmostEqual(result['h'], 10 * 25.4)
        paths = restored(result)
        self.assertTrue(any(len(p) > 3 for p in paths))
        self.assertAlmostEqual(np.linalg.norm(paths[0][-1] - paths[0][0]), math_sqrt(20) * 25.4)

    def test_dxf_reports_unsupported_entities_and_rejects_binary_and_ocs(self):
        good_line = [(0, 'LINE'), (10, 0), (20, 0), (11, 10), (21, 10)]
        result = vector.import_file(dxf(good_line + [(0, 'SPLINE')]), 'a.dxf')
        self.assertTrue(any('SPLINE' in warning for warning in result['warnings']))
        for data in [b'AutoCAD Binary DXF\r\n', dxf(good_line + [(210, 1)])]:
            with self.assertRaises(ValueError):
                vector.import_file(data, 'a.dxf')

    def test_kicad_embedded_symbols_wires_unit_selection_rotation_and_mirror(self):
        data = b'''(kicad_sch (version 20250114)
          (lib_symbols (symbol "Test:Asym"
            (symbol "Asym_0_1" (polyline (pts (xy 0 0) (xy 3 0) (xy 3 2))))
            (symbol "Asym_2_1" (circle (center 0 0) (radius 100)))))
          (wire (pts (xy 10 10) (xy 20 10)))
          (symbol (lib_id "Test:Asym") (at 10 10 90) (unit 1) (mirror y)))'''
        result = vector.import_file(data, 'example.kicad_sch')
        self.assertEqual((result['w'], result['h']), (12, 3))
        paths = restored(result)
        # Mirrored local (0,0),(3,0),(3,2) then CCW 90 rotates to (0,0),(0,3),(-2,3).
        self.assertTrue(any(len(p) == 3 and np.allclose(p, [[2, 0], [2, 3], [0, 3]]) for p in paths))

    def test_kicad_visible_reference_and_value_are_real_font_paths(self):
        data = b'''(kicad_sch (version 20250114)
          (wire (pts (xy 0 0) (xy 30 0)))
          (symbol (lib_id "Missing:R") (at 15 0 0) (unit 1)
             (property "Reference" "R1" (at 15 -3 0) (effects (font (size 1.27 1.27))))
             (property "Value" "10k" (at 15 3 0) (effects (font (size 1.27 1.27))))
             (property "Footprint" "Hidden" (at 15 100 0) (effects (font (size 1.27 1.27)) hide))))'''
        result = vector.import_file(data, 'r.kicad_sch')
        self.assertGreater(len(result['vector']['paths']), 5)
        self.assertLess(result['h'], 10)
        self.assertTrue(any('Falta el símbolo' in warning for warning in result['warnings']))

    def test_legacy_schematic_preserves_wires_and_warns_missing_symbol_geometry(self):
        data = b'''EESchema Schematic File Version 4
Wire Wire Line
1000 1000 2000 1500
$Comp
L Device:R R1
$EndComp
$EndSCHEMATC'''
        result = vector.import_file(data, 'old.sch')
        np.testing.assert_allclose([result['w'], result['h']], [25.4, 12.7])
        self.assertTrue(any('omitieron los cuerpos' in warning for warning in result['warnings']))

    def test_saved_paths_reject_nonfinite_out_of_bounds_booleans_and_limit_abuse(self):
        good = {'paths': [[0., 0., 1., 1.]], 'aspect': 1.}
        for bad in [dict(good, aspect=True), dict(good, aspect=float('inf')),
                    dict(good, paths=[[0, 0, float('nan'), 1]]), dict(good, paths=[[0, 0, 2, 1]]),
                    dict(good, paths=[[0, 0, True, 1]]), dict(good, paths=[[0, 0, 1]]),
                    dict(good, paths=[[0, 0, 1, 1]] * (vector.MAX_PATHS + 1))]:
            with self.subTest(bad=str(bad)[:80]), self.assertRaises(ValueError):
                vector.validate_vector(bad)


def math_sqrt(number):
    return float(np.sqrt(number))


class VectorApiTests(unittest.TestCase):
    def setUp(self):
        import app as server
        self.server = server
        self.saved = copy.deepcopy(server.config)
        server.config.clear(); server.config.update(copy.deepcopy(server.DEFAULT_CONFIG))
        server.config['paper'].update(size='a5', bed_x=40, bed_y=10)
        self.client = server.app.test_client()

    def tearDown(self):
        self.server.config.clear(); self.server.config.update(self.saved)

    def test_import_resize_persist_reload_rotate_compose_export_has_same_geometry(self):
        original = svg('<path stroke="black" fill="none" d="M0 0H40V20H0Z"/>')
        with patch.object(self.server.link, 'ensure', side_effect=AssertionError('printer must remain offline')):
            uploaded = self.client.post('/api/import-vector', data={'file': (io.BytesIO(original), 'circuit.svg')})
            self.assertEqual(uploaded.status_code, 200, uploaded.json)
            # Roundtrip through a saved JSON object, with no uploaded file or render token.
            persisted = copy.deepcopy(uploaded.json['vector'])
            response = self.client.post('/api/element', json={'type': 'vector', 'vector': persisted, 'w': 60, 'pen': 1})
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual((response.json['w'], response.json['h']), (60, 30))
            composed = self.client.post('/api/compose', json={'items': [{'render_id': response.json['render_id'], 'x': 45, 'y': 80, 'rotation': 90}]})
            self.assertEqual(composed.status_code, 200, composed.json)
            self.assertFalse(composed.json['outside'])
            job = self.server.jobs[composed.json['job']]
            self.assertEqual(job['pages'][0][0]['pen'], 1)
            paths = job['pages'][0][0]['paths']
            self.assertAlmostEqual(np.linalg.norm(np.diff(paths[0], axis=0), axis=1).sum(), 180)
            export = self.client.get('/api/export', query_string={'job': composed.json['job'], 'fmt': 'svg'})
            self.assertEqual(export.status_code, 200)
            self.assertIn(b'<polyline', export.data)

    def test_imported_vector_outside_is_flagged_and_physical_preflight_rejects(self):
        v = {'paths': [[0., 0., 1., 1.]], 'aspect': .5}
        response = self.client.post('/api/element', json={'type': 'vector', 'vector': v, 'w': 80})
        self.assertEqual(response.status_code, 200, response.json)
        job = self.client.post('/api/compose', json={'items': [{'render_id': response.json['render_id'], 'x': -1, 'y': 0}]}).json
        self.assertTrue(job['outside'])
        result = self.client.post('/api/preflight', json={'job': job['job'], 'pen_ready': True})
        self.assertEqual(result.status_code, 400)

    def test_vector_width_remains_exact_small_dimensions_and_bad_values_rejected(self):
        v = {'paths': [[0., 0., 1., 1.]], 'aspect': 2}
        response = self.client.post('/api/element', json={'type': 'vector', 'vector': v, 'w': .1})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['h'], .2)
        for value in [0, .09, 400.1, True, None, float('nan')]:
            result = self.client.post('/api/element', json={'type': 'vector', 'vector': v, 'w': value})
            self.assertEqual(result.status_code, 400, (value, result.json))

    def test_upload_does_not_accept_unsupported_missing_or_malformed_files(self):
        self.assertEqual(self.client.post('/api/import-vector', data={}).status_code, 400)
        for filename, data in [('a.svg', b'garbage'), ('a.step', b'garbage'), ('a.kicad_sch', b'(kicad_sch')]:
            result = self.client.post('/api/import-vector', data={'file': (io.BytesIO(data), filename)})
            self.assertEqual(result.status_code, 400, result.json)


if __name__ == '__main__':
    unittest.main()
