"""Measurement from tracked points: no video decoding, so these run in milliseconds.

The points are projected from simulated flights through simulated cameras,
which is exactly what a perfect detector would hand the measurement.
"""

import threading
import time
import unittest

import numpy as np

from crickcoach import netsim
from crickcoach.drill import drill_plan
from crickcoach.flight import combine, measure
from crickcoach.geometry import (
    apply_h,
    camera_from_ground,
    ground_plane,
    homography,
    side_plane,
)
from crickcoach.machine_link import MachineLink, command_for, read_radar_line, serve
from crickcoach.pitch import describe, length_name, line_name


def seen(camera: netsim.Camera, truth: netsim.Truth, fps: float = 50.0, until: float = 0.75) -> list[dict]:
    """The ball as a perfect detector would see it, every frame after release."""
    end = min(truth.release_t + until, truth.path[-1, 0] - 0.01)  # the ball leaves the scene into the back net
    times = np.arange(truth.release_t + 1 / fps, end, 1 / fps)
    pts = truth.position(times)
    uv, _ = camera.project(pts)
    return [
        {"t": float(t), "x": float(u / camera.width), "y": float(v / camera.height)}
        for t, (u, v) in zip(times, uv)
        if 0 <= u < camera.width and 0 <= v < camera.height
    ]


class PhysicsTests(unittest.TestCase):
    def test_the_ball_pitches_where_it_was_aimed(self):
        for pace, length in ((140, 7.0), (110, 1.0), (95, 11.0)):
            truth = netsim.simulate(netsim.Delivery(pace, -0.1, netsim.PITCH_LENGTH - length))
            self.assertAlmostEqual(truth.length_m, length, delta=0.02)
            self.assertAlmostEqual(truth.bounce_x, -0.1, delta=0.02)

    def test_a_fast_ball_reaches_the_batter_in_about_half_a_second(self):
        truth = netsim.simulate(netsim.Delivery(140, 0.0, netsim.PITCH_LENGTH - 7.0))
        self.assertGreater(truth.at_stumps_t - truth.release_t, 0.48)
        self.assertLess(truth.at_stumps_t - truth.release_t, 0.65)

    def test_a_full_straight_ball_hits_the_stumps(self):
        truth = netsim.simulate(netsim.Delivery(120, 0.0, netsim.PITCH_LENGTH - 1.0, release_x=0.0))
        self.assertTrue(truth.hits_stumps)


class GeometryTests(unittest.TestCase):
    def test_homography_maps_the_corners_and_rejects_a_bad_order(self):
        src = [(0.2, 0.8), (0.8, 0.8), (0.8, 0.2), (0.2, 0.2)]
        dst = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        matrix = homography(src, dst)
        self.assertAlmostEqual(apply_h(matrix, 0.5, 0.5)[0], 0.5, places=6)
        self.assertAlmostEqual(apply_h(matrix, 0.2, 0.2)[1], 1.0, places=6)
        with self.assertRaises(ValueError):
            homography([(0, 0), (0, 0), (0, 0), (0, 0)], dst)

    def test_the_camera_behind_the_bowler_is_recovered_from_four_clicks(self):
        camera = netsim.behind_camera()
        calibration = {"kind": "behind", "corners": netsim.behind_clicks(camera)}
        found = camera_from_ground(ground_plane(calibration, camera.width, camera.height), camera.width, camera.height)
        self.assertAlmostEqual(found.k[0, 0], camera.fx, delta=1.0)
        np.testing.assert_allclose(found.centre, camera.centre, atol=0.01)

    def test_side_plane_reads_height_and_distance(self):
        camera = netsim.side_camera()
        calibration = {"kind": "side", **netsim.side_clicks(camera)}
        plane = side_plane(calibration, camera.width, camera.height)
        uv, _ = camera.project(np.array([[0.0, 13.0, 0.5]]))
        y, z = apply_h(plane, *uv[0])
        self.assertAlmostEqual(y, 13.0, delta=0.01)
        self.assertAlmostEqual(z, 0.5, delta=0.01)


class SideMeasureTests(unittest.TestCase):
    def setUp(self):
        self.camera = netsim.side_camera()
        self.calibration = {"kind": "side", **netsim.side_clicks(self.camera)}

    def test_length_and_pace_on_the_stump_line(self):
        truth = netsim.simulate(netsim.Delivery(136, 0.0, netsim.PITCH_LENGTH - 6.5, release_x=0.0), 1.0)
        measured = measure(seen(self.camera, truth), self.calibration, self.camera.width, self.camera.height)
        self.assertEqual(measured["confidence"], "ok")
        self.assertAlmostEqual(measured["pitch"]["length_m"], 6.5, delta=0.1)
        self.assertEqual(measured["pitch"]["length"], "good")
        self.assertIsNone(measured["pitch"]["line"])
        self.assertAlmostEqual(measured["pace_kph"], 136, delta=2.0)
        self.assertAlmostEqual(measured["bounce_t"], truth.bounce_t, delta=0.006)

    def test_a_short_ball_is_short(self):
        truth = netsim.simulate(netsim.Delivery(128, 0.0, netsim.PITCH_LENGTH - 11.0, release_x=0.0), 1.0)
        measured = measure(seen(self.camera, truth), self.calibration, self.camera.width, self.camera.height)
        self.assertEqual(measured["pitch"]["length"], "bouncer")

    def test_too_few_sightings_is_not_a_ball(self):
        measured = measure([{"t": 0.0, "x": 0.5, "y": 0.5}] * 3, self.calibration)
        self.assertEqual(measured["confidence"], "none")


class BehindMeasureTests(unittest.TestCase):
    def test_line_and_length_from_behind(self):
        camera = netsim.behind_camera()
        calibration = {"kind": "behind", "corners": netsim.behind_clicks(camera)}
        truth = netsim.simulate(netsim.Delivery(130, -0.3, netsim.PITCH_LENGTH - 7.2), 1.0)
        measured = measure(seen(camera, truth), calibration, camera.width, camera.height)
        self.assertEqual(measured["confidence"], "ok")
        self.assertAlmostEqual(measured["pitch"]["length_m"], 7.2, delta=0.3)
        self.assertAlmostEqual(measured["pitch"]["offset_m"], -0.3, delta=0.05)
        self.assertEqual(measured["pitch"]["line"], "outside off")
        self.assertIsNone(measured["pace_kph"])

    def test_clicks_a_little_off_move_the_line_a_little(self):
        # A coach's clicks land a pixel or two off. Through the ground homography
        # that is a centimetre or two of line; through a camera fitted from the
        # clicks it was 15 cm on some balls and not others.
        camera = netsim.behind_camera()
        worst = 0.0
        for seed in range(4):
            calibration = {"kind": "behind", "corners": netsim.behind_clicks(camera, jitter_px=1.5, seed=seed)}
            for line, length in ((-0.3, 7.2), (-0.1, 6.0), (0.05, 8.5), (-0.2, 4.5)):
                truth = netsim.simulate(netsim.Delivery(125, line, netsim.PITCH_LENGTH - length), 1.0)
                measured = measure(seen(camera, truth), calibration, camera.width, camera.height)
                worst = max(worst, abs(measured["pitch"]["offset_m"] - truth.bounce_x))
        self.assertLess(worst, 0.06)


class CombineTests(unittest.TestCase):
    side = {
        "camera": "side",
        "confidence": "ok",
        "pace_kph": 128.0,
        "pitch": describe(6.6, None),
        "stumps": {"height_m": 0.5, "offset_m": None, "would_hit": None},
        "parallax": {"distance_m": 12.0, "sign": 1},
    }
    behind = {
        "camera": "behind",
        "confidence": "ok",
        "pace_kph": None,
        "pitch": describe(6.9, -0.3),
        "stumps": {"height_m": 0.9, "offset_m": 0.02, "would_hit": False},
    }

    def test_side_length_behind_line_and_a_parallax_corrected_pace(self):
        merged = combine(self.side, self.behind)
        self.assertEqual(merged["pitch"]["length_m"], 6.6)
        self.assertEqual(merged["pitch"]["line"], "outside off")
        # 0.3 m further from a leg-side camera than the stumps reads 2.5 % slow.
        self.assertAlmostEqual(merged["pace_kph"], 128.0 * 12.3 / 12.0, delta=0.11)
        self.assertTrue(merged["stumps"]["would_hit"])  # side height 0.5 m, behind line 2 cm

    def test_a_left_hander_has_the_off_side_on_the_other_side(self):
        merged = combine(self.side, self.behind, batter="left")
        self.assertEqual(merged["pitch"]["line"], "down leg")

    def test_an_unsure_side_reading_gives_way_to_a_sure_one_from_behind(self):
        merged = combine({**self.side, "confidence": "low"}, self.behind)
        self.assertEqual(merged["pitch"]["length_m"], 6.9)


class PitchNameTests(unittest.TestCase):
    def test_length_bands_follow_ball_tracking_pitch_maps(self):
        self.assertEqual(length_name(1.0), "yorker")
        self.assertEqual(length_name(4.0), "full")
        self.assertEqual(length_name(7.0), "good")
        self.assertEqual(length_name(9.0), "short")
        self.assertEqual(length_name(12.0), "bouncer")

    def test_line_bands_are_measured_from_the_stumps(self):
        self.assertEqual(line_name(-0.9), "wide off")
        self.assertEqual(line_name(-0.3), "outside off")
        self.assertEqual(line_name(-0.1), "off stump")
        self.assertEqual(line_name(0.0), "middle")
        self.assertEqual(line_name(0.1), "leg stump")
        self.assertEqual(line_name(0.4), "down leg")


class DrillTests(unittest.TestCase):
    def test_the_hardest_zone_is_drilled_not_the_most_common(self):
        deliveries = (
            [{"pitch": describe(7.0, -0.3), "outcome": "defended", "pace_kph": 125}] * 6
            + [{"pitch": describe(7.0, -0.3), "outcome": "missed", "pace_kph": 126}]
            + [{"pitch": describe(1.0, 0.0), "outcome": "bowled", "pace_kph": 131}] * 2
        )
        plan = drill_plan(deliveries, count=6)
        self.assertEqual(len(plan["balls"]), 6)
        self.assertEqual(plan["balls"][0]["length"], "yorker")
        self.assertEqual(plan["balls"][0]["line"], "middle")
        self.assertEqual(plan["balls"][0]["pace_kph"], 131)
        self.assertEqual(plan["balls"][0]["length_m"], 1.0)

    def test_length_only_balls_still_make_a_drill(self):
        plan = drill_plan([{"pitch": describe(4.0, None), "outcome": "edged"}], count=3)
        self.assertEqual(plan["balls"][0]["length"], "full")
        self.assertEqual(plan["balls"][0]["line"], "outside off")
        self.assertIn("line", plan["reason"])

    def test_a_clean_session_grooves_the_corridor(self):
        plan = drill_plan([{"pitch": describe(7.0, 0.0), "outcome": "defended"}], count=4)
        self.assertEqual(plan["balls"][0]["line"], "outside off")
        self.assertIn("Groove", plan["reason"])

    def test_the_old_hit_outcome_means_bowled(self):
        plan = drill_plan([{"pitch": describe(3.0, 0.0), "outcome": "hit"}], count=1)
        self.assertEqual(plan["balls"][0]["length"], "full")


class MachineLinkTests(unittest.TestCase):
    item = {"id": "abcd1234", "pace_kph": 120, "line": "off stump", "length": "good", "length_m": 7.0,
            "offset_m": -0.1, "batter": "right", "note": ""}

    def test_simulator_records_a_ball_without_a_socket(self):
        record = MachineLink(None).bowl(self.item)
        self.assertEqual(record["via"], "simulator")
        self.assertEqual(record["length_m"], 7.0)

    def test_a_left_hander_mirrors_the_aim(self):
        command = command_for({**self.item, "batter": "left"})
        self.assertEqual(command["offset_m"], 0.1)

    def test_edge_mode_never_opens_a_socket(self):
        link = MachineLink("edge")
        self.assertTrue(link.is_edge)
        self.assertEqual(link.mode, "edge")

    def test_tcp_machine_replies_ok(self):
        log = []
        stop = threading.Event()
        thread = threading.Thread(target=serve, args=("127.0.0.1", 18765, stop, log), daemon=True)
        thread.start()
        time.sleep(0.15)
        try:
            record = MachineLink("127.0.0.1:18765").bowl(self.item)
        finally:
            stop.set()
            thread.join(1)
        self.assertEqual(record["via"], "127.0.0.1:18765")
        self.assertEqual(log[0]["id"], "abcd1234")
        self.assertEqual(log[0]["length_m"], 7.0)

    def test_radar_line(self):
        self.assertEqual(read_radar_line("PACE 138.2\n"), 138.2)
        self.assertIsNone(read_radar_line("hello"))


if __name__ == "__main__":
    unittest.main()
