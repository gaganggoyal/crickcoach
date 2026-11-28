"""The raw reels: rendered nets whose truth is in footage/manifest.json.

These decode real H.264 and run the whole tracker, so they take a few seconds.
"""

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from crickcoach import NAME, ORIGIN
from crickcoach.app import create_app
from crickcoach.deliveries import find_deliveries
from crickcoach.flight import analyse_clip
from crickcoach.footage import clip, manifest

REELS = ("side_two_balls", "side_good_length", "behind_off_good", "pair_side", "pair_behind")


def wait_ready(client: TestClient, session_id: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        session = client.get(f"/api/sessions/{session_id}").json()
        if session["status"] in {"ready", "error"} and not session.get("job"):
            return session
        time.sleep(0.2)
    raise AssertionError("the session never finished")


class ReelTests(unittest.TestCase):
    def test_manifest_names_vansh_and_every_reel_exists(self):
        data = manifest()
        self.assertEqual(data["coach"], NAME)
        self.assertEqual(data["origin"], ORIGIN)
        for name in REELS:
            self.assertTrue(clip(name)["path"].exists(), name)

    def test_the_session_reel_holds_two_balls(self):
        item = clip("side_two_balls")
        segments, how = find_deliveries(item["path"], item["calibration"])
        self.assertEqual(how, "flights")
        self.assertEqual(len(segments), item["expect"]["deliveries"])
        for segment, truth in zip(segments, item["truth"]):
            self.assertLessEqual(segment.t0, truth["release_t"])
            self.assertGreaterEqual(segment.t1, truth["bounce_t"])

    def test_single_ball_reels_read_their_truth(self):
        for name in ("side_good_length", "behind_off_good"):
            with self.subTest(name):
                item = clip(name)
                truth = item["truth"][0]
                expect = item["expect"]
                measured = analyse_clip(item["path"], truth["release_t"] - 1.0, truth["release_t"] + 1.6, item["calibration"])
                self.assertNotEqual(measured["confidence"], "none", measured["note"])
                self.assertEqual(measured["pitch"]["length"], expect["length"])
                self.assertAlmostEqual(measured["pitch"]["length_m"], expect["length_m"], delta=expect["length_tolerance_m"])
                if "line" in expect:
                    self.assertEqual(measured["pitch"]["line"], expect["line"])
                    self.assertAlmostEqual(measured["pitch"]["offset_m"], expect["offset_m"], delta=expect["offset_tolerance_m"])
                else:
                    self.assertAlmostEqual(measured["pace_kph"], expect["pace_kph"], delta=expect["pace_tolerance_kph"])


class RawReelApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_health_is_vansh_on_vansh_best(self):
        health = self.client.get("/api/health").json()
        self.assertEqual(health["name"], "VANSH")
        self.assertEqual(health["origin"], "https://vansh.best")
        page = self.client.get("/")
        self.assertIn("VANSH", page.text)
        self.assertIn("https://vansh.best", page.text)

    def test_two_cameras_line_up_and_share_one_ball(self):
        session = self.client.post("/api/demo/pair").json()
        session = wait_ready(self.client, session["id"])
        self.assertEqual(session["status"], "ready", session.get("error"))
        expect = clip("pair_behind")["expect"]
        self.assertAlmostEqual(session["clock_offset"], expect["clock_offset"], delta=0.05)
        self.assertEqual(len(session["deliveries"]), 1)
        delivery = session["deliveries"][0]
        self.assertEqual(set(delivery["tracks"]), {"side", "behind"})
        self.assertAlmostEqual(delivery["pitch"]["length_m"], expect["length_m"], delta=expect["length_tolerance_m"])
        self.assertIsNotNone(delivery["pitch"]["line"])
        self.assertAlmostEqual(delivery["pace_kph"], expect["pace_kph"], delta=expect["pace_tolerance_kph"])

    def test_the_session_reel_is_cut_and_tracked_on_upload(self):
        item = clip("side_two_balls")
        # The lane remembers the calibration, as it would after the coach calibrates once.
        first = self.client.post("/api/demo/tracked").json()
        wait_ready(self.client, first["id"])
        self.client.post(f"/api/sessions/{first['id']}/calibration/side", json=item["calibration"])
        with item["path"].open("rb") as handle:
            response = self.client.post(
                "/api/sessions",
                data={"name": "Tuesday nets", "role": "bowling"},
                files={"side": ("side.mp4", handle, "video/mp4")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        session = wait_ready(self.client, response.json()["id"])
        self.assertEqual(session["status"], "ready", session.get("error"))
        self.assertEqual(session["found_by"], "flights")
        self.assertEqual(len(session["deliveries"]), 2)
        lengths = [delivery["pitch"]["length"] for delivery in session["deliveries"]]
        self.assertEqual(lengths, ["good", "yorker"])


if __name__ == "__main__":
    unittest.main()
