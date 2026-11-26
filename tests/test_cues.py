import unittest

from crickcoach.cues import angle_deg, coach, parse_frames
from crickcoach.cues import Point
from crickcoach.samples import (
    batting_clean_frames,
    batting_fault_frames,
    bowling_clean_frames,
    bowling_fault_frames,
)


class AngleTests(unittest.TestCase):
    def test_right_angle_and_straight_line(self):
        right = angle_deg(Point(0, 0), Point(0, 1), Point(1, 1))
        straight = angle_deg(Point(0, 0), Point(1, 0), Point(2, 0))
        self.assertAlmostEqual(right, 90, places=3)
        self.assertAlmostEqual(straight, 180, places=3)


class BowlingCueTests(unittest.TestCase):
    def test_clean_action_has_no_fixes(self):
        cues = coach("bowling", parse_frames(bowling_clean_frames()))
        self.assertEqual(len(cues), 3)
        self.assertTrue(all(cue.severity == "good" for cue in cues))
        self.assertIn("arm", {cue.code for cue in cues})
        self.assertIn("head", {cue.code for cue in cues})

    def test_faults_mark_the_arm_head_and_knee(self):
        cues = {cue.code: cue for cue in coach("bowling", parse_frames(bowling_fault_frames()))}
        self.assertEqual(cues["arm"].severity, "fix")
        self.assertEqual(cues["head"].severity, "fix")
        self.assertEqual(cues["knee"].severity, "fix")

    def test_empty_frame_does_not_invent_a_player(self):
        cues = coach("bowling", parse_frames([{"t": 0, "points": []}]))
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].code, "nobody")


class BattingCueTests(unittest.TestCase):
    def test_clean_stance(self):
        cues = coach("batting", parse_frames(batting_clean_frames()))
        self.assertEqual(len(cues), 3)
        self.assertTrue(all(cue.severity == "good" for cue in cues))

    def test_faults_mark_knees_head_and_hands(self):
        cues = {cue.code: cue for cue in coach("batting", parse_frames(batting_fault_frames()))}
        self.assertEqual(cues["knee"].severity, "fix")
        self.assertEqual(cues["head"].severity, "fix")
        self.assertEqual(cues["hands"].severity, "fix")


if __name__ == "__main__":
    unittest.main()
