import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from crickcoach import NAME, ORIGIN
from crickcoach.app import create_app
from crickcoach.flight import analyse_clip
from crickcoach.footage import clip, manifest
from crickcoach.video import cut_video


class FootageTests(unittest.TestCase):
    def test_manifest_names_vansh(self):
        data = manifest()
        self.assertEqual(data["coach"], NAME)
        self.assertEqual(data["origin"], ORIGIN)
        for name in ("side_two_balls", "side_good_length", "behind_off_good"):
            self.assertTrue(clip(name)["path"].exists(), name)

    def test_side_reel_cuts_into_two_deliveries(self):
        segments = cut_video(clip("side_two_balls")["path"])
        self.assertEqual(len(segments), manifest()["clips"]["side_two_balls"]["expect"]["deliveries"])

    def test_side_reel_reads_a_good_length(self):
        item = clip("side_good_length")
        measured = analyse_clip(item["path"], 0.0, item["duration"], item["calibration"])
        expect = item["expect"]
        self.assertEqual(measured["pitch"]["length"], expect["length"])
        self.assertGreaterEqual(measured["pace_kph"], expect["pace_min"])
        self.assertLessEqual(measured["pace_kph"], expect["pace_max"])
        self.assertEqual(measured["hit_stumps"], expect["hit_stumps"])

    def test_behind_reel_reads_off_stump(self):
        item = clip("behind_off_good")
        measured = analyse_clip(item["path"], 0.0, item["duration"], item["calibration"])
        expect = item["expect"]
        self.assertEqual(measured["pitch"]["line"], expect["line"])
        self.assertEqual(measured["pitch"]["length"], expect["length"])
        self.assertEqual(measured["hit_stumps"], expect["hit_stumps"])


class RawReelApiTests(unittest.TestCase):
    def setUp(self):
        import tempfile

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

    def test_raw_reels_load_through_the_api(self):
        side = self.client.post("/api/demo/session").json()
        self.assertEqual(side["status"], "ready", side.get("error"))
        self.assertEqual(len(side["deliveries"]), 2)
        tracked = self.client.post("/api/demo/tracked").json()
        self.assertEqual(tracked["status"], "ready", tracked.get("error"))
        self.assertEqual(tracked["deliveries"][0]["pitch"]["length"], "good")
        behind = self.client.post("/api/demo/behind").json()
        self.assertEqual(behind["status"], "ready", behind.get("error"))
        pitch = behind["deliveries"][0]["pitch"]
        self.assertEqual(pitch["line"], "off")
        self.assertEqual(pitch["length"], "good")

    def test_upload_of_the_raw_side_reel(self):
        path = clip("side_two_balls")["path"]
        with path.open("rb") as handle:
            response = self.client.post(
                "/api/sessions",
                data={"name": "VANSH reel", "role": "bowling"},
                files={"side": ("side_two_balls.mp4", handle, "video/mp4")},
            )
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["status"], "ready", body.get("error"))
        self.assertEqual(len(body["deliveries"]), 2)


if __name__ == "__main__":
    unittest.main()
