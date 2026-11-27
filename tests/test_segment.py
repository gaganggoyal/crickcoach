import unittest

from crickcoach.deliveries import match_offset, motion_bursts
from crickcoach.pitch import clean_line, describe, target_for
from crickcoach.segment import segment_motion


class SegmentTests(unittest.TestCase):
    def test_two_bursts_become_two_deliveries(self):
        times = [index / 10 for index in range(80)]
        energy = [0.04] * len(times)
        for index, moment in enumerate(times):
            if 1.0 <= moment <= 2.0 or 5.0 <= moment <= 6.2:
                energy[index] = 0.9
        segments = segment_motion(times, energy, duration=8.0)
        self.assertEqual(len(segments), 2)
        self.assertLess(segments[0].t1, segments[1].t0)
        self.assertGreaterEqual(segments[0].t0, 0)
        self.assertLessEqual(segments[1].t1, 8.0)

    def test_flat_clip_stays_one_delivery(self):
        times = [index / 10 for index in range(30)]
        segments = segment_motion(times, [0.2] * len(times), duration=3.0)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].t0, 0)
        self.assertIn("one delivery", segments[0].note)

    def test_padding_stays_inside_the_clip(self):
        times = [index / 10 for index in range(20)]
        energy = [0.0] * len(times)
        energy[1] = 1
        energy[2] = 1
        energy[3] = 1
        energy[4] = 1
        segments = segment_motion(times, energy, duration=1.9)
        self.assertEqual(len(segments), 1)
        self.assertGreaterEqual(segments[0].t0, 0)
        self.assertLessEqual(segments[0].t1, 1.9)


class PitchTests(unittest.TestCase):
    def test_names_a_good_length_ball_outside_off(self):
        mark = describe(7.0, -0.3)
        self.assertEqual(mark["line"], "outside off")
        self.assertEqual(mark["length"], "good")
        self.assertEqual(mark["text"], "Good length, 7.0 m, outside off")

    def test_yorker_and_short_are_opposite_ends(self):
        self.assertEqual(describe(0.8, 0.0)["length"], "yorker")
        self.assertEqual(describe(12.0, 0.0)["length"], "bouncer")

    def test_a_side_camera_mark_has_no_line(self):
        mark = describe(5.0, None)
        self.assertIsNone(mark["line"])
        self.assertIn("Line not seen", mark["text"])

    def test_old_line_names_still_reach_the_machine(self):
        self.assertEqual(clean_line("off"), "outside off")
        self.assertEqual(target_for("off", "good")["offset_m"], -0.30)


class MotionBurstTests(unittest.TestCase):
    def test_two_bursts_become_two_windows(self):
        times = [index / 10 for index in range(300)]
        energy = [0.004] * len(times)
        for index, moment in enumerate(times):
            if 3.0 <= moment <= 4.5 or 18.0 <= moment <= 19.5:
                energy[index] = 0.05
        segments = motion_bursts(times, energy, duration=30.0)
        self.assertEqual(len(segments), 2)
        self.assertLess(segments[0].t1, segments[1].t0)

    def test_a_flat_clip_stays_whole(self):
        times = [index / 10 for index in range(50)]
        segments = motion_bursts(times, [0.01] * len(times), duration=5.0)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].t0, 0.0)


class ClockTests(unittest.TestCase):
    def test_bounces_line_up_two_cameras(self):
        offset, matches = match_offset([3.33, 17.41, 31.35], [1.03, 15.12, 29.04])
        self.assertAlmostEqual(offset, -2.3, places=2)
        self.assertEqual(matches, 3)

    def test_no_bounces_keeps_the_clock(self):
        self.assertEqual(match_offset([], [1.0]), (0.0, 0))


if __name__ == "__main__":
    unittest.main()
