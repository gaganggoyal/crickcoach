import threading
import time
import unittest

from crickcoach.drill import drill_plan
from crickcoach.flight import measure
from crickcoach.geometry import apply_h, homography
from crickcoach.machine_link import MachineLink, read_radar_line, serve
from crickcoach.track import track_frames


class GeometryTests(unittest.TestCase):
    def test_homography_maps_the_pitch_corners(self):
        src = [(0.2, 0.8), (0.8, 0.8), (0.8, 0.2), (0.2, 0.2)]
        dst = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        matrix = homography(src, dst)
        self.assertAlmostEqual(apply_h(matrix, 0.5, 0.5)[0], 0.5, places=4)
        self.assertAlmostEqual(apply_h(matrix, 0.5, 0.5)[1], 0.5, places=4)
        self.assertAlmostEqual(apply_h(matrix, 0.2, 0.2)[1], 1.0, places=3)


class FlightTests(unittest.TestCase):
    def test_side_camera_reads_length_and_pace(self):
        calibration = {
            "kind": "side",
            "bowling": {"x": 0.1, "y": 0.8},
            "batting": {"x": 0.9, "y": 0.8},
            "stump_top": {"x": 0.9, "y": 0.5},
        }
        points = [
            {"t": 0.0, "x": 0.1, "y": 0.5},
            {"t": 0.2, "x": 0.3, "y": 0.62},
            {"t": 0.4, "x": 0.45, "y": 0.74},
            {"t": 0.5, "x": 0.5, "y": 0.8},
            {"t": 0.7, "x": 0.62, "y": 0.7},
        ]
        measured = measure(points, calibration)
        self.assertEqual(measured["pitch"]["length"], "good")
        self.assertIsNone(measured["pitch"]["line"])
        self.assertAlmostEqual(measured["pace_kph"], 72.6, delta=1.5)
        self.assertFalse(measured["hit_stumps"])

    def test_side_camera_sees_a_ball_that_hits_the_stumps(self):
        calibration = {
            "kind": "side",
            "bowling": {"x": 0.1, "y": 0.8},
            "batting": {"x": 0.9, "y": 0.8},
            "stump_top": {"x": 0.9, "y": 0.5},
        }
        points = [
            {"t": 0.0, "x": 0.1, "y": 0.5},
            {"t": 0.2, "x": 0.4, "y": 0.65},
            {"t": 0.4, "x": 0.7, "y": 0.75},
            {"t": 0.6, "x": 0.9, "y": 0.78},
            {"t": 0.7, "x": 0.95, "y": 0.78},
        ]
        self.assertTrue(measure(points, calibration)["hit_stumps"])

    def test_behind_camera_reads_line_and_length(self):
        calibration = {
            "kind": "behind",
            "corners": [
                {"x": 0.2, "y": 0.8},
                {"x": 0.8, "y": 0.8},
                {"x": 0.8, "y": 0.2},
                {"x": 0.2, "y": 0.2},
            ],
        }
        points = [
            {"t": 0.0, "x": 0.20, "y": 0.80},
            {"t": 0.1, "x": 0.245, "y": 0.717},
            {"t": 0.2, "x": 0.29, "y": 0.635},
            {"t": 0.4, "x": 0.38, "y": 0.47},
            {"t": 0.55, "x": 0.55, "y": 0.47},
            {"t": 0.7, "x": 0.62, "y": 0.40},
        ]
        measured = measure(points, calibration)
        self.assertEqual(measured["pitch"]["line"], "off")
        self.assertEqual(measured["pitch"]["length"], "good")
        self.assertAlmostEqual(measured["pace_kph"], 101.2, delta=2)

    def test_tracker_follows_a_bright_ball(self):
        width, height = 40, 24
        frames = []
        for index in range(12):
            buffer = bytearray([10]) * (width * height)
            origin_x = 2 + index
            for y in range(6, 10):
                row = y * width
                for x in range(origin_x, origin_x + 4):
                    buffer[row + x] = 255
            frames.append(bytes(buffer))
        track = track_frames(frames, width, height)
        self.assertGreaterEqual(len(track), 6)
        self.assertGreater(track[-1]["x"], track[0]["x"])


class DrillTests(unittest.TestCase):
    def test_misses_set_the_drill(self):
        deliveries = [
            {"pitch": {"line": "leg", "length": "full"}, "outcome": "missed", "pace_kph": 110},
            {"pitch": {"line": "leg", "length": "full"}, "outcome": "edged", "pace_kph": 120},
            {"pitch": {"line": "off", "length": "good"}, "outcome": "defended", "pace_kph": 125},
        ]
        plan = drill_plan(deliveries, count=6)
        self.assertEqual(len(plan["balls"]), 6)
        self.assertEqual(plan["balls"][0]["line"], "leg")
        self.assertEqual(plan["balls"][0]["length"], "full")
        self.assertEqual(plan["balls"][0]["pace_kph"], 115)
        self.assertIn("beaten", plan["reason"])

    def test_a_clean_session_grooves_off_stump(self):
        plan = drill_plan([{"pitch": {"line": "middle", "length": "good"}, "outcome": "defended"}], count=4)
        self.assertEqual(plan["balls"][0]["line"], "off")
        self.assertEqual(plan["balls"][0]["length"], "good")
        self.assertIn("Groove", plan["reason"])


class MachineLinkTests(unittest.TestCase):
    def test_simulator_records_a_ball_without_a_socket(self):
        record = MachineLink(None).bowl(
            {"id": "abcd1234", "pace_kph": 120, "line": "off", "length": "good", "note": ""}
        )
        self.assertEqual(record["via"], "simulator")
        self.assertEqual(record["length"], "good")

    def test_tcp_machine_replies_ok(self):
        log = []
        stop = threading.Event()
        thread = threading.Thread(target=serve, args=("127.0.0.1", 18765, stop, log), daemon=True)
        thread.start()
        time.sleep(0.15)
        try:
            record = MachineLink("127.0.0.1:18765").bowl(
                {"id": "abcd1234", "pace_kph": 120, "line": "off", "length": "full", "note": "net"}
            )
        finally:
            stop.set()
            thread.join(1)
        self.assertEqual(record["via"], "127.0.0.1:18765")
        self.assertEqual(log[0]["id"], "abcd1234")

    def test_radar_line(self):
        self.assertEqual(read_radar_line("PACE 138.2\n"), 138.2)
        self.assertIsNone(read_radar_line("hello"))


if __name__ == "__main__":
    unittest.main()
