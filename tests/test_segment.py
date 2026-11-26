import unittest

from crickcoach.pitch import describe_pitch
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
    def test_names_a_good_length_off_stump_ball(self):
        mark = describe_pitch(0.30, 0.55)
        self.assertEqual(mark["line"], "off")
        self.assertEqual(mark["length"], "good")

    def test_yorker_and_bouncer_are_opposite_ends(self):
        self.assertEqual(describe_pitch(0.5, 0.95)["length"], "yorker")
        self.assertEqual(describe_pitch(0.5, 0.05)["length"], "bouncer")

    def test_values_outside_the_pitch_are_clamped(self):
        mark = describe_pitch(4, -1)
        self.assertEqual(mark["x"], 1)
        self.assertEqual(mark["y"], 0)


if __name__ == "__main__":
    unittest.main()
