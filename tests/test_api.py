import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from crickcoach.app import create_app
from crickcoach.video import synthesize_two_deliveries


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_sample_faults_come_from_the_engine(self):
        response = self.client.get("/api/samples/bowling-faults")
        self.assertEqual(response.status_code, 200)
        codes = {cue["code"]: cue["severity"] for cue in response.json()["cues"]}
        self.assertEqual(codes["arm"], "fix")

    def test_live_cues_reject_a_bad_role(self):
        response = self.client.post("/api/live/cues", json={"role": "wicketkeeper", "frames": []})
        self.assertEqual(response.status_code, 400)

    def test_demo_clip_splits_into_two_deliveries(self):
        response = self.client.post("/api/demo/session")
        self.assertEqual(response.status_code, 200)
        session = response.json()
        self.assertEqual(session["status"], "ready", session.get("error"))
        self.assertEqual(len(session["deliveries"]), 2)
        media = self.client.get(f"/api/sessions/{session['id']}/media/side")
        self.assertEqual(media.status_code, 200)
        self.assertGreater(len(media.content), 500)

    def test_upload_marks_pitch_pace_and_queues_the_machine(self):
        folder = Path(self.tmp.name) / "clip"
        folder.mkdir()
        clip = folder / "net.mp4"
        synthesize_two_deliveries(clip)
        with clip.open("rb") as handle:
            response = self.client.post(
                "/api/sessions",
                data={"name": "Tuesday", "role": "bowling"},
                files={"side": ("net.mp4", handle, "video/mp4")},
            )
        self.assertEqual(response.status_code, 200)
        session = response.json()
        self.assertEqual(session["name"], "Tuesday")
        delivery = session["deliveries"][0]
        marked = self.client.post(
            f"/api/sessions/{session['id']}/deliveries/{delivery['id']}/pitch",
            json={"x": 0.3, "y": 0.55},
        )
        self.assertEqual(marked.json()["pitch"]["text"], "good on off")
        paced = self.client.post(
            f"/api/sessions/{session['id']}/deliveries/{delivery['id']}/pace",
            json={"pace_kph": 128},
        )
        self.assertEqual(paced.json()["pace_kph"], 128)
        queued = self.client.post(
            "/api/machine",
            json={"pace_kph": 128, "line": "off", "length": "good", "session_id": session["id"]},
        )
        self.assertEqual(queued.status_code, 200)
        listed = self.client.get("/api/machine")
        self.assertEqual(len(listed.json()["items"]), 1)
        removed = self.client.delete(f"/api/machine/{queued.json()['id']}")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get("/api/machine").json()["items"], [])

    def test_camera_url_must_be_rtsp(self):
        response = self.client.post(
            "/api/cameras",
            json={"side_url": "file:///etc/passwd", "seconds": 5},
        )
        self.assertEqual(response.status_code, 400)

    def test_snapshot_is_saved_in_the_log(self):
        sample = self.client.get("/api/samples/batting-clean").json()
        saved = self.client.post(
            "/api/snapshots",
            json={"name": "Open stance", "role": "batting", "frames": sample["frames"]},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["deliveries"][0]["cues"][0]["severity"], "good")
        listing = self.client.get("/api/sessions")
        self.assertEqual(listing.json()["sessions"][0]["name"], "Open stance")

    def test_tracked_sample_fills_length_and_pace(self):
        response = self.client.post("/api/demo/tracked")
        self.assertEqual(response.status_code, 200, response.text)
        session = response.json()
        self.assertEqual(session["status"], "ready", session.get("error"))
        delivery = session["deliveries"][0]
        self.assertEqual(delivery["pitch"]["length"], "good")
        self.assertGreater(delivery["pace_kph"], 40)
        self.assertLess(delivery["pace_kph"], 180)
        self.assertEqual(delivery["tracking"]["confidence"] in {"ok", "low"}, True)

    def test_track_asks_for_a_calibration(self):
        session = self.client.post("/api/demo/session").json()
        response = self.client.post(f"/api/sessions/{session['id']}/deliveries/d1/track")
        self.assertEqual(response.status_code, 400)

    def test_drill_and_bowl(self):
        session = self.client.post("/api/demo/tracked").json()
        marked = self.client.post(
            f"/api/sessions/{session['id']}/deliveries/d1/pitch",
            json={"x": 0.7, "y": 0.75},
        )
        self.assertEqual(marked.json()["pitch"]["length"], "full")
        outcome = self.client.post(
            f"/api/sessions/{session['id']}/deliveries/d1/outcome",
            json={"outcome": "missed"},
        )
        self.assertEqual(outcome.status_code, 200)
        drill = self.client.post("/api/drill", json={"session_id": session["id"], "count": 6})
        self.assertEqual(drill.status_code, 200)
        self.assertEqual(len(drill.json()["items"]), 6)
        self.assertIn("full", drill.json()["reason"])
        bowled = self.client.post("/api/machine/bowl")
        self.assertEqual(bowled.status_code, 200)
        self.assertEqual(bowled.json()["via"], "simulator")
        machine = self.client.get("/api/machine").json()
        self.assertEqual(len(machine["items"]), 5)
        self.assertEqual(len(machine["bowled"]), 1)

    def test_radar_reading_is_stored(self):
        saved = self.client.post("/api/radar", json={"pace_kph": 141})
        self.assertEqual(saved.json()["pace_kph"], 141)
        self.assertEqual(self.client.get("/api/radar").json()["pace_kph"], 141)


if __name__ == "__main__":
    unittest.main()
