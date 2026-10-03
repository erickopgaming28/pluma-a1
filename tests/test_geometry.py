"""Offline checks of machine files and the pen's physical coordinate mapping.

Only pure generators are called: these tests never connect to a printer.
"""
import copy
import io
import math
import re
import unittest
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

from app import DEFAULT_CONFIG
from plotter import gcode


def moves(text):
    """Read explicit linear moves, retaining Z when an XY line omits it."""
    position = {"X": None, "Y": None, "Z": None}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        command = line.split()[0]
        if command not in ("G0", "G1"):
            continue
        params = {key: float(value) for key, value in re.findall(
            r"\b([XYZEF])(-?\d+(?:\.\d+)?)", line
        )}
        before = dict(position)
        position.update({key: value for key, value in params.items() if key in position})
        yield number, command, params, before, dict(position)


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(DEFAULT_CONFIG)
        self.cfg["paper"].update(size="a5", landscape=False, bed_x=54, bed_y=23)
        self.cfg["pen"].update(level_bed=False)
        self.paths = [
            np.array([[40., 70.], [55., 70.], [55., 85.]]),
            np.array([[90., 100.], [105., 110.]]),
        ]
        self.layers = [{"name": "Azul", "color": "#2743b8", "paths": self.paths}]

    def assert_machine_bounds(self, text):
        found = False
        for number, _, params, _, _ in moves(text):
            found = True
            self.assertNotIn("E", params, f"Extrusion at line {number}")
            for axis in ("X", "Y", "Z"):
                if axis in params:
                    self.assertTrue(math.isfinite(params[axis]))
                    self.assertGreaterEqual(params[axis], 0, f"{axis} at line {number}")
                    self.assertLessEqual(params[axis], 256, f"{axis} at line {number}")
        self.assertTrue(found, "A motion job must contain real G0/G1 commands")

    def test_machine_package_contains_commands_and_no_mesh_or_stl(self):
        text, stats = gcode.build_gcode(self.layers, self.cfg, pen_ready=True)
        package = gcode.build_3mf(text, self.layers, self.cfg, stats["seconds"])
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            self.assertFalse(any(name.lower().endswith(".stl") for name in archive.namelist()))
            code = archive.read("Metadata/plate_1.gcode").decode("utf-8")
            self.assertEqual(code, text)
            self.assertRegex(code, r"(?m)^G0 X")
            self.assertRegex(code, r"(?m)^G1 X")
            self.assertNotRegex(code, r"(?m)^M(?:104|109|140|190)\s+S[1-9]")
            models = [name for name in archive.namelist() if name.lower().endswith(".model")]
            self.assertTrue(models)
            for name in models:
                root = ET.fromstring(archive.read(name))
                tags = {node.tag.rsplit("}", 1)[-1] for node in root.iter()}
                self.assertTrue(tags.isdisjoint({"object", "mesh", "vertices", "triangles"}), name)
            config = ET.fromstring(archive.read("Metadata/model_settings.config"))
            declared_code = config.find("./plate/metadata[@key='gcode_file']").get("value")
            self.assertEqual(declared_code, "Metadata/plate_1.gcode")
        self.assert_machine_bounds(text)

    def test_tip_reproduces_paths_with_signed_offsets_and_landscape(self):
        for landscape in (False, True):
            for offset_x in (-35, 35):
                for offset_y in (-25, 25):
                    with self.subTest(landscape=landscape, x=offset_x, y=offset_y):
                        cfg = copy.deepcopy(self.cfg)
                        cfg["paper"]["landscape"] = landscape
                        cfg["pen"].update(offset_x=offset_x, offset_y=offset_y)
                        text, _ = gcode.build_gcode(self.layers, cfg, pen_ready=True)
                        self.assert_machine_bounds(text)
                        self.assertNotRegex(text, r"(?m)^G28\b")
                        self.assertNotRegex(text, r"(?m)^G29\b")
                        height = 148 if landscape else 210
                        starts, written = [], []
                        for _, command, params, _, after in moves(text):
                            if "X" not in params or "Y" not in params:
                                continue
                            # Locate the physical tip from the nozzle position and
                            # measure it relative to the paper's lower-left corner.
                            sheet_point = [
                                after["X"] + offset_x - cfg["paper"]["bed_x"],
                                height - (after["Y"] + offset_y - cfg["paper"]["bed_y"]),
                            ]
                            if command == "G0":
                                starts.append(sheet_point)
                            elif after["Z"] == cfg["pen"]["z_down"]:
                                written.append(sheet_point)
                        np.testing.assert_allclose(starts, [path[0] for path in self.paths], atol=.01)
                        np.testing.assert_allclose(written, np.vstack([path[1:] for path in self.paths]), atol=.01)

    def test_reachable_edges_are_valid_and_outside_edges_are_rejected(self):
        for offset_x, offset_y in ((30, 0), (-30, 0), (0, 30), (0, -30)):
            with self.subTest(x=offset_x, y=offset_y):
                cfg = copy.deepcopy(self.cfg)
                cfg["paper"].update(size="custom", width=256, height=256,
                                    bed_x=0, bed_y=0, margin=0)
                cfg["pen"].update(offset_x=offset_x, offset_y=offset_y)
                # The physical tip and nozzle must both remain over the bed.
                left, right = max(0, offset_x), min(256, 256 + offset_x)
                bottom, top = max(0, offset_y), min(256, 256 + offset_y)
                outline = np.array([[left, 256-bottom], [right, 256-bottom],
                                    [right, 256-top], [left, 256-top], [left, 256-bottom]])
                layers = [{"name": "Azul", "color": "#2743b8", "paths": [outline]}]
                text, _ = gcode.build_gcode(layers, cfg, pen_ready=True)
                self.assert_machine_bounds(text)
                if offset_x > 0:
                    outside = np.array([[left - 1, 128], [left + 1, 128]])
                elif offset_x < 0:
                    outside = np.array([[right - 1, 128], [right + 1, 128]])
                elif offset_y > 0:
                    outside = np.array([[128, 256-bottom-1], [128, 256-bottom+1]])
                else:
                    outside = np.array([[128, 256-top-1], [128, 256-top+1]])
                layers[0]["paths"] = [outside]
                with self.assertRaisesRegex(ValueError, "alcance"):
                    gcode.build_gcode(layers, cfg, pen_ready=True)

    def test_pen_lifts_before_every_travel_including_color_changes(self):
        layers = self.layers + [{"name": "Rojo", "color": "#c8322b",
                                "paths": [np.array([[60., 90.], [70., 90.]])]}]
        for ready in (False, True):
            with self.subTest(pen_ready=ready):
                text, _ = gcode.build_gcode(layers, self.cfg, pen_ready=ready)
                self.assert_machine_bounds(text)
                travels, written = 0, 0
                for number, command, params, before, after in moves(text):
                    if not ("X" in params or "Y" in params):
                        continue
                    self.assertIsNotNone(before["Z"], f"Unknown clearance at line {number}")
                    if command == "G0":
                        travels += 1
                        self.assertGreaterEqual(before["Z"], self.cfg["pen"]["z_up"],
                                                f"Travel without clearance at line {number}")
                    elif after["Z"] == self.cfg["pen"]["z_down"]:
                        written += 1
                        self.assertEqual(before["Z"], self.cfg["pen"]["z_down"])
                    else:
                        self.assertGreaterEqual(before["Z"], self.cfg["pen"]["z_up"],
                                                f"Parking without clearance at line {number}")
                self.assertGreaterEqual(travels, 3)
                self.assertEqual(written, 4)
                self.assertIn("M400 U1", text, "The color change must pause")

    def test_adjust_and_paper_guide_stay_in_bounds_and_travel_raised(self):
        jobs = (gcode.build_adjust_gcode(self.cfg), gcode.build_guide_gcode(self.cfg)[0])
        for text in jobs:
            with self.subTest(job=text.splitlines()[2]):
                self.assert_machine_bounds(text)
                self.assertIn("M400 U1", text)
                for number, _, params, before, _ in moves(text):
                    if "X" in params or "Y" in params:
                        self.assertIsNotNone(before["Z"])
                        self.assertGreaterEqual(before["Z"], self.cfg["pen"]["z_up"],
                                                f"Unraised setup travel at line {number}")


if __name__ == "__main__":
    unittest.main()
