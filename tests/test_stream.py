import re
import threading
import time
import unittest

from plotter.stream import DirectJob, compile_program, _group_events, MAX_BYTES, MAX_LINES, MAX_BLOCK_SECONDS, MAX_GROUP_PACKETS


def program(*lines):
    return "\n".join(("; HEADER", "M104 S220", "; EXECUTABLE_BLOCK_START", *lines, "; EXECUTABLE_BLOCK_END", "M140 S80"))


class FakeLink:
    def __init__(self, auto=False, ack=None):
        self.connected = True
        self.key = ("test", "serial", "access")
        self.last_status = time.monotonic()
        self.status = {"stg_cur": 0, "print_error": 0}
        self.stage_version = 0
        self.sent = []
        self.auto = auto
        self.ack = {"result": "SUCCESS"} if ack is None else ack
        self.lock = threading.Lock()
        self.pushed = 0

    def send_gcode(self, text, wait=6):
        with self.lock:
            self.sent.append(text)
            if self.auto and "gcode_claim_action" in text:
                self.confirm(text)
            return self.ack

    def confirm(self, text=None):
        text = text or self.sent[-1]
        self.status["stg_cur"] = int(re.search(r"gcode_claim_action : (\d+)", text).group(1))
        self.stage_version += 1
        self.last_status = time.monotonic()

    def request_status(self):
        self.pushed += 1


class DirectJobTests(unittest.TestCase):
    def wait_for(self, condition, seconds=1.0):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if condition():
                return
            time.sleep(0.003)
        self.fail("El estado esperado no llegó.")

    def job(self, **kwargs):
        return DirectJob(block_timeout=0.2, long_timeout=0.3, poll_interval=0.003, **kwargs)

    def test_ack_without_marker_never_advances_or_completes(self):
        link = FakeLink()
        job = self.job()
        job.start(program("G1 Z8 F1200", "M73 L2", "G1 X10 Y10 F1200"), "prueba", link)
        self.wait_for(lambda: len(link.sent) == 1)
        self.assertTrue(job.active)
        self.assertEqual(job.snapshot()["percent"], 0)
        self.wait_for(lambda: not job.active)
        self.assertEqual(job.snapshot()["state"], "FAILED")
        self.assertEqual(len(link.sent), 1)
        self.assertNotEqual(job.snapshot()["percent"], 100)

    def test_fresh_matching_barrier_advances_and_finishes(self):
        link = FakeLink()
        job = self.job()
        job.start(program("G1 Z8 F1200", "M73 L2", "G1 X10 Y10 F1200"), "prueba", link)
        self.wait_for(lambda: len(link.sent) == 1)
        first_marker = int(re.search(r"action : (\d+)", link.sent[0]).group(1))
        link.status["stg_cur"] = first_marker  # misma versión: estado antiguo
        time.sleep(0.02)
        self.assertEqual(len(link.sent), 1)
        self.assertEqual(job.snapshot()["percent"], 0)
        link.confirm(link.sent[0])
        self.wait_for(lambda: len(link.sent) == 2)
        self.assertEqual(job.snapshot()["layer"], 2)
        self.assertGreater(job.snapshot()["percent"], 0)
        self.assertNotIn(f"action : {first_marker}", link.sent[1])
        link.confirm(link.sent[1])
        self.wait_for(lambda: not job.active)
        self.assertEqual(job.snapshot()["state"], "FINISH")
        self.assertEqual(job.snapshot()["percent"], 100)

    def test_missing_ack_does_not_retry_or_lift(self):
        link = FakeLink()
        link.ack = None
        job = self.job()
        job.start(program("G1 Z3 F1200", "M73 L2", "G1 X10 F1200"), "prueba", link, z_up=8)
        self.wait_for(lambda: not job.active)
        self.assertEqual(job.snapshot()["state"], "FAILED")
        self.assertEqual(len(link.sent), 1)
        self.assertNotIn("Z8", link.sent[0])

    def test_u1_is_software_pause_after_previous_execution(self):
        link = FakeLink()
        job = self.job()
        job.start(program("G1 Z3 F1200", "M400 U1 ; PAUSA: Cambia la pluma", "G1 X10 F1200"), "prueba", link)
        self.wait_for(lambda: len(link.sent) == 1)
        self.assertNotEqual(job.snapshot()["state"], "PAUSE")
        link.confirm()
        self.wait_for(lambda: job.snapshot()["state"] == "PAUSE")
        self.assertIn("Cambia la pluma", job.snapshot()["message"])
        self.assertEqual(len(link.sent), 1)
        self.assertTrue(job.active)
        job.control("resume")
        self.wait_for(lambda: len(link.sent) == 2)
        self.assertFalse(any("U1" in text for text in link.sent))
        link.confirm()
        self.wait_for(lambda: not job.active)
        self.assertEqual(job.snapshot()["state"], "FINISH")

    def test_manual_pause_waits_for_current_block(self):
        link = FakeLink()
        job = self.job()
        job.start(program("G1 Z3 F1200", "M73 L2", "G1 X10 F1200"), "prueba", link)
        self.wait_for(lambda: len(link.sent) == 1)
        job.control("pause")
        self.assertNotEqual(job.snapshot()["state"], "PAUSE")
        link.confirm()
        self.wait_for(lambda: job.snapshot()["state"] == "PAUSE")
        self.assertEqual(len(link.sent), 1)
        job.control("resume")
        self.wait_for(lambda: len(link.sent) == 2)
        link.confirm()
        self.wait_for(lambda: not job.active)

    def test_stop_waits_for_barrier_then_only_lifts(self):
        link = FakeLink()
        job = self.job()
        job.start(program("G1 Z3 F1200", "M73 L2", "G1 X10 F1200"), "prueba", link, z_up=8)
        self.wait_for(lambda: len(link.sent) == 1)
        job.control("stop")
        self.assertEqual(job.snapshot()["state"], "STOPPING")
        time.sleep(0.02)
        self.assertEqual(len(link.sent), 1)
        link.confirm()
        self.wait_for(lambda: len(link.sent) == 2)
        self.assertIn("G1 Z8.00 F600", link.sent[1])
        self.assertNotIn("X10", link.sent[1])
        link.confirm()
        self.wait_for(lambda: not job.active)
        self.assertEqual(job.snapshot()["state"], "CANCELED")

    def test_stop_with_uncertain_execution_never_lifts(self):
        link = FakeLink()
        job = self.job()
        job.start(program("G1 Z3 F1200", "M73 L2", "G1 X10 F1200"), "prueba", link, z_up=8)
        self.wait_for(lambda: len(link.sent) == 1)
        job.control("stop")
        self.wait_for(lambda: not job.active)
        self.assertEqual(job.snapshot()["state"], "FAILED")
        self.assertEqual(len(link.sent), 1)

    def test_disconnect_and_printer_error_fail_without_more_blocks(self):
        for failure in ("disconnect", "error", "changed", "native_print"):
            with self.subTest(failure=failure):
                link = FakeLink()
                job = self.job()
                job.start(program("G1 Z3 F1200", "M73 L2", "G1 X10 F1200"), "prueba", link)
                self.wait_for(lambda: len(link.sent) == 1)
                if failure == "disconnect":
                    link.connected = False
                elif failure == "error":
                    link.status["print_error"] = 42
                elif failure == "changed":
                    link.key = ("different", "serial", "access")
                else:
                    link.status["gcode_state"] = "RUNNING"
                self.wait_for(lambda: not job.active)
                self.assertEqual(job.snapshot()["state"], "FAILED")
                self.assertEqual(len(link.sent), 1)

    def test_blocks_are_bounded_and_home_level_are_separate(self):
        link = FakeLink(auto=True)
        job = self.job()
        text = program("M104 S0", "G28", "G90", "G29 A1 X0 Y0 I100 J100", *(f"G1 X{i}.123 Y100.123 F1200" for i in range(90)))
        job.start(text, "prueba", link)
        self.wait_for(lambda: not job.active)
        self.assertEqual(job.snapshot()["state"], "FINISH")
        for payload in link.sent:
            self.assertLessEqual(len(payload.encode("ascii")), MAX_BYTES)
            self.assertLessEqual(len(payload.splitlines()), MAX_LINES)
            commands = payload.splitlines()[:-2]
            if any(line.startswith(("G28", "G29 ")) for line in commands):
                self.assertEqual(len(commands), 1)
        self.assertFalse(any("S220" in payload or "S80" in payload for payload in link.sent))

    def test_validation_rejects_unsafe_program_before_any_send(self):
        bad_lines = ("M104 S200", "M140 S60", "G1 E1 F200", "G1 X257 F100", "G1 Z-1 F100", "G1 Xnan F100", "G1 X1 Finf", "G1 X1 F0", "G91", "G92 Z0", "M18", "G29 A1 X250 Y0 I20 J20", "M400 U0")
        for line in bad_lines:
            with self.subTest(line=line):
                link = FakeLink(auto=True)
                with self.assertRaises(ValueError):
                    self.job().start(program("G1 X1 F12000", line), "prueba", link)
                self.assertEqual(link.sent, [])
        with self.assertRaises(ValueError):
            compile_program("G1 X10 F100")

    def test_long_slow_segments_keep_geometry_endpoints_and_modal_feed(self):
        text = program("G90", "G1 X0 Y0 Z8 F12000", "G1 X200 Y100 Z12 F60", "G1 X256 Y200", "G1 X128")
        events, _ = compile_program(text)
        blocks = [event for event in events if hasattr(event, "lines")]
        self.assertGreater(len(blocks), 10)
        for block in blocks:
            self.assertLessEqual(block.seconds, MAX_BLOCK_SECONDS)
        lines = [line for block in blocks for line in block.lines]
        self.assertIn("G1 X200 Y100 Z12 F60", lines)
        self.assertIn("G1 X256 Y200", lines)
        self.assertEqual(lines[-1], "G1 X128")
        inside_segment = False
        for line in lines:
            if line == "G1 X0 Y0 Z8 F12000":
                inside_segment = True
                continue
            if inside_segment:
                values = {token[0]: float(token[1:]) for token in line.split()[1:]}
                self.assertAlmostEqual(values["Y"], values["X"] * 0.5, places=5)
                self.assertAlmostEqual(values["Z"], 8 + values["X"] * 0.02, places=5)
                if line == "G1 X200 Y100 Z12 F60":
                    inside_segment = False
        link = FakeLink(auto=True)
        job = self.job()
        job.start(text, "lento", link)
        self.wait_for(lambda: not job.active)
        self.assertEqual(job.snapshot()["state"], "FINISH")

    def test_unknown_position_requires_fast_positioning_before_any_send(self):
        link = FakeLink(auto=True)
        with self.assertRaisesRegex(ValueError, "posicionamiento"):
            self.job().start(program("G90", "M104 S0", "G1 X200 F60"), "lento", link)
        self.assertEqual(link.sent, [])
        events, _ = compile_program(program("G90", "G1 X100 Y100 F12000", "G1 X110 F600"))
        first_motion = next(event for event in events if hasattr(event, "lines") and any(line.startswith("G1") for line in event.lines))
        self.assertEqual(first_motion.lines, ("G1 X100 Y100 F12000",))
        self.assertLessEqual(first_motion.seconds, MAX_BLOCK_SECONDS)

    def test_dwells_and_speed_override_use_duration_budget(self):
        events, _ = compile_program(program("M400 S35", "M400 P35000"))
        blocks = [event for event in events if hasattr(event, "lines")]
        self.assertTrue(all(block.seconds <= MAX_BLOCK_SECONDS for block in blocks))
        self.assertAlmostEqual(sum(block.seconds for block in blocks), 70.0)
        lines = [line for block in blocks for line in block.lines]
        self.assertEqual(sum(float(line.split()[1][1:]) for line in lines if " S" in line), 35.0)
        self.assertEqual(sum(float(line.split()[1][1:]) for line in lines if " P" in line), 35000.0)
        events, _ = compile_program(program("G1 X0 F12000", "M220 S50", "G1 X20 F60"))
        slow_blocks = [event for event in events if hasattr(event, "lines") and any(line.startswith("G1") and "F12000" not in line for line in event.lines)]
        self.assertGreater(len(slow_blocks), 1)
        self.assertAlmostEqual(sum(block.seconds for block in slow_blocks), 40.0)

    def test_homing_resets_position_and_feed_estimates(self):
        with self.assertRaisesRegex(ValueError, "velocidad F"):
            compile_program(program("G1 X0 F12000", "G28", "G1 X10"))
        with self.assertRaisesRegex(ValueError, "posicionamiento"):
            compile_program(program("G1 X0 F12000", "G29 A1 X0 Y0 I100 J100", "G1 X10 F60"))

    def test_four_packets_share_only_one_final_barrier_and_progress(self):
        text = program("G1 X0 Y0 Z3 F12000", *(f"G1 X{i} Y10 F2400" for i in range(1, 121)))
        link = FakeLink()
        job = self.job()
        job.start(text, "continuo", link)
        self.wait_for(lambda: len(link.sent) == 1)
        link.confirm()
        self.wait_for(lambda: len(link.sent) == 5)
        confirmed_percent = job.snapshot()["percent"]
        self.assertFalse(any("gcode_claim_action" in item for item in link.sent[1:4]))
        self.assertIn("gcode_claim_action", link.sent[4])
        self.assertEqual(sum("gcode_claim_action" in item for item in link.sent[1:]), 1)
        time.sleep(0.025)
        self.assertEqual(len(link.sent), 5)  # ningún quinto cuerpo pendiente de ejecución
        self.assertEqual(job.snapshot()["percent"], confirmed_percent)
        self.wait_for(lambda: not job.active)
        self.assertEqual(job.snapshot()["state"], "FAILED")
        self.assertEqual(job.snapshot()["percent"], confirmed_percent)

    def test_groups_obey_total_duration_packet_limit_and_isolation(self):
        text = program("M104 S0", "G28", "G90", "G1 X0 Y0 Z3 F12000",
                       *(f"G1 X{(i % 2) * 8} F12000" for i in range(90)),
                       *(f"G1 X{(i % 2) * 8} F600" for i in range(160)),
                       "M73 L2", "G1 X10 F12000", "M400 U1", "G29 A1 X0 Y0 I100 J100",
                       "G1 X10 Y10 Z3 F12000")
        events, _ = compile_program(text)
        groups = [item for item in _group_events(events) if isinstance(item, tuple)]
        self.assertTrue(any(len(group) > 1 for group in groups))
        for group in groups:
            self.assertLessEqual(len(group), MAX_GROUP_PACKETS)
            if any(block.isolated or block.long for block in group):
                self.assertEqual(len(group), 1)
            if not group[0].long:
                self.assertLessEqual(sum(block.seconds for block in group), MAX_BLOCK_SECONDS)
        layers_and_pauses = [item for item in _group_events(events) if not isinstance(item, tuple)]
        self.assertEqual(len(layers_and_pauses), 2)
        link = FakeLink(auto=True)
        job = self.job()
        job.start(text.replace("M400 U1", "M400"), "acotado", link)
        self.wait_for(lambda: not job.active)
        self.assertEqual(job.snapshot()["state"], "FINISH")
        for payload in link.sent:
            self.assertLessEqual(len(payload.encode("ascii")), MAX_BYTES)
            self.assertLessEqual(len(payload.splitlines()), MAX_LINES)

    def test_missing_or_rejected_body_ack_stops_without_barrier_or_lift(self):
        for ack in (None, {"result": "FAILED", "reason": "rejected"}, {"result": "SUCCESS", "err_code": 3}):
            with self.subTest(ack=ack):
                class FailureLink(FakeLink):
                    def send_gcode(self, text, wait=6):
                        self.sent.append(text)
                        if len(self.sent) == 1:
                            self.confirm(text)
                            return {"result": "SUCCESS"}
                        if len(self.sent) == 3:
                            return ack
                        return {"result": "SUCCESS"}
                link = FailureLink()
                job = self.job()
                job.start(program("G1 X0 Y0 Z3 F12000", *(f"G1 X{i} F2400" for i in range(1, 121))), "fallo", link, z_up=8)
                self.wait_for(lambda: not job.active)
                self.assertEqual(job.snapshot()["state"], "FAILED")
                self.assertEqual(len(link.sent), 3)
                self.assertFalse(any("gcode_claim_action" in text or "Z8" in text for text in link.sent[1:]))
                self.assertEqual(job.snapshot()["percent"], 0)

    def test_pause_mid_group_drains_then_resumes_only_unsent_packets(self):
        self._interrupt_mid_group("pause")

    def test_stop_mid_group_drains_and_lifts_from_confirmed_z_only(self):
        self._interrupt_mid_group("stop")

    def test_continuous_packets_have_no_forced_stop_until_final_drain(self):
        link = FakeLink()
        job = self.job()
        text = program('G1 X0 Y0 Z3 F12000', *(f'G1 X{i} Y10 F2400' for i in range(1, 121)))
        job.start(text, 'continuo', link, continuous=True)
        self.wait_for(lambda: len(link.sent) == 1)
        link.confirm()
        first_executed = job.snapshot()['executed_percent']
        index = 1
        while True:
            self.wait_for(lambda: len(link.sent) > index)
            payload = link.sent[index]
            if payload.splitlines()[0] == 'M400':
                break
            self.assertNotIn('M400', payload.splitlines())
            if 'gcode_claim_action' in payload:
                link.confirm(payload)
            index += 1
        self.assertLess(job.snapshot()['percent'], 100)
        self.assertEqual(job.snapshot()['executed_percent'], first_executed)
        self.assertNotEqual(job.snapshot()['state'], 'FINISH')
        link.confirm(payload)
        self.wait_for(lambda: not job.active)
        self.assertEqual(job.snapshot()['state'], 'FINISH')
        self.assertEqual(job.snapshot()['executed_percent'], 100)

    def test_continuous_pause_and_cancel_drain_accepted_packets(self):
        for action in ('pause', 'stop'):
            with self.subTest(action=action):
                self._interrupt_mid_group(action, continuous=True)

    def test_continuous_program_pause_waits_for_physical_barrier(self):
        link = FakeLink(auto=True)
        job = self.job()
        job.start(program('G1 X0 Y0 Z3 F12000', *(f'G1 X{i} F2400' for i in range(1, 90)),
                          'M400 U1 ; PAUSA: Cambia pluma', 'G1 X90 F2400'), 'pausa', link, continuous=True)
        self.wait_for(lambda: job.snapshot()['state'] == 'PAUSE')
        self.assertEqual(link.sent[-1].splitlines()[0], 'M400')
        self.assertIn('Cambia pluma', job.snapshot()['message'])
        job.control('resume')
        self.wait_for(lambda: not job.active)
        self.assertEqual(job.snapshot()['state'], 'FINISH')

    def _interrupt_mid_group(self, action, continuous=False):
        class WaitingLink(FakeLink):
            def __init__(self):
                super().__init__()
                self.awaiting_ack = threading.Event()
                self.release_ack = threading.Event()

            def send_gcode(self, text, wait=6):
                self.sent.append(text)
                if len(self.sent) == 1:
                    self.confirm(text)
                elif len(self.sent) == 2:
                    self.awaiting_ack.set()
                    self.release_ack.wait(1.0)
                elif self.auto and "gcode_claim_action" in text:
                    self.confirm(text)
                return {"result": "SUCCESS"}

        # 22 comandos llenan el primer cuerpo; Z20 queda en el paquete siguiente.
        first = [f"G1 X{i} F2400" for i in range(1, 22)] + ["G1 Z5 F1200"]
        later = ["G1 Z20 F1200"] + [f"G1 X{i} F2400" for i in range(22, 91)]
        link = WaitingLink()
        job = self.job()
        job.start(program("G1 X0 Y0 Z3 F12000", *first, *later), "interrumpido", link, z_up=8, continuous=continuous)
        self.assertTrue(link.awaiting_ack.wait(1.0))
        job.control(action)
        percent_before = job.snapshot()["percent"]
        link.release_ack.set()
        self.wait_for(lambda: len(link.sent) == 3)
        self.assertEqual(link.sent[2].splitlines()[0], "M400")
        self.assertFalse(any("G1" in line for line in link.sent[2].splitlines()))
        self.assertFalse(any("Z20" in text for text in link.sent))
        self.assertEqual(job.snapshot()["percent"], percent_before)
        time.sleep(0.025)
        self.assertEqual(len(link.sent), 3)
        link.confirm(link.sent[2])
        if action == "pause":
            self.wait_for(lambda: job.snapshot()["state"] == "PAUSE")
            self.assertGreater(job.snapshot()["percent"], percent_before)
            self.assertEqual(len(link.sent), 3)
            link.auto = True
            job.control("resume")
            self.wait_for(lambda: not job.active)
            self.assertEqual(job.snapshot()["state"], "FINISH")
            motion_packets = [text for text in link.sent if "G1" in text]
            self.assertEqual(sum("G1 Z5 F1200" in text for text in motion_packets), 1)
            self.assertEqual(sum("G1 Z20 F1200" in text for text in motion_packets), 1)
        else:
            self.wait_for(lambda: len(link.sent) == 4)
            self.assertIn("G1 Z8.00 F600", link.sent[3])
            self.assertFalse(any("Z20" in text for text in link.sent))
            link.confirm(link.sent[3])
            self.wait_for(lambda: not job.active)
            self.assertEqual(job.snapshot()["state"], "CANCELED")
            self.assertGreater(job.snapshot()["percent"], percent_before)


if __name__ == "__main__":
    unittest.main()
