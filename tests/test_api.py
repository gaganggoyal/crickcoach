import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from crickcoach.app import create_app
from crickcoach.footage import clip


def wait_ready(client: TestClient, session_id: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        session = client.get(f"/api/sessions/{session_id}").json()
        if session["status"] in {"ready", "error"} and not session.get("job"):
            return session
        time.sleep(0.2)
    raise AssertionError("the session never finished")


class OpenLaneTests(unittest.TestCase):
    """No coach key: the way VANSH runs on a laptop at the nets."""

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

    def test_a_ball_is_tracked_marked_and_drilled(self):
        session = wait_ready(self.client, self.client.post("/api/demo/tracked").json()["id"])
        self.assertEqual(session["status"], "ready", session.get("error"))
        delivery = session["deliveries"][0]
        self.assertEqual(delivery["pitch"]["length"], "good")
        self.assertEqual(delivery["pitch_source"], "cameras")
        self.assertGreater(delivery["pace_kph"], 100)
        base = f"/api/sessions/{session['id']}/deliveries/{delivery['id']}"
        marked = self.client.post(f"{base}/pitch", json={"length_m": 1.2, "offset_m": 0.0}).json()
        self.assertEqual(marked["pitch"]["length"], "yorker")
        self.assertEqual(marked["pitch_source"], "manual")
        # Tracking again keeps the coach's mark.
        again = self.client.post(f"{base}/track?camera=side").json()
        self.assertEqual(again["pitch"]["length"], "yorker")
        cleared = self.client.post(f"{base}/pitch", json={"clear": True}).json()
        self.assertEqual(cleared["pitch"]["length"], "good")
        self.client.post(f"{base}/pitch", json={"length_m": 1.2, "offset_m": 0.0})
        self.assertEqual(self.client.post(f"{base}/outcome", json={"outcome": "bowled"}).status_code, 200)
        self.assertEqual(self.client.post(f"{base}/outcome", json={"outcome": "hit"}).json()["outcome"], "bowled")
        drill = self.client.post("/api/drill", json={"session_id": session["id"], "count": 4}).json()
        self.assertEqual(len(drill["items"]), 4)
        self.assertEqual(drill["items"][0]["length"], "yorker")
        self.assertEqual(drill["items"][0]["length_m"], 1.0)
        bowled = self.client.post("/api/machine/bowl").json()
        self.assertEqual(bowled["via"], "simulator")
        machine = self.client.get("/api/machine").json()
        self.assertEqual(len(machine["items"]), 3)
        self.assertEqual(len(machine["bowled"]), 1)

    def test_pace_from_the_coach_and_the_radar(self):
        session = wait_ready(self.client, self.client.post("/api/demo/tracked").json()["id"])
        base = f"/api/sessions/{session['id']}/deliveries/d1"
        self.assertEqual(self.client.post(f"{base}/pace", json={"pace_kph": 30}).status_code, 400)
        self.assertEqual(self.client.post(f"{base}/pace", json={"pace_kph": 141}).json()["pace_source"], "manual")
        self.client.post("/api/radar", json={"pace_kph": 139})
        self.assertEqual(self.client.get("/api/radar").json()["pace_kph"], 139)
        radar = self.client.post(f"{base}/pace", json={"pace_kph": 139, "source": "radar"}).json()
        self.assertEqual(radar["pace_source"], "radar")
        back = self.client.post(f"{base}/pace", json={"pace_kph": None}).json()
        self.assertEqual(back["pace_source"], "side")

    def test_calibration_checks_the_clicks_and_is_kept_for_the_lane(self):
        session = wait_ready(self.client, self.client.post("/api/demo/tracked").json()["id"])
        bad = {"kind": "side", "bowler_base": {"x": 0.1, "y": 0.5}, "bowler_top": {"x": 0.1, "y": 0.5},
               "batter_base": {"x": 0.1, "y": 0.5}, "batter_top": {"x": 0.1, "y": 0.5}}
        self.assertEqual(self.client.post(f"/api/sessions/{session['id']}/calibration/side", json=bad).status_code, 400)
        good = {**clip("side_good_length")["calibration"], "distance_m": 12.5}
        saved = self.client.post(f"/api/sessions/{session['id']}/calibration/side", json=good)
        self.assertEqual(saved.status_code, 200, saved.text)
        lane = self.client.get("/api/lane").json()
        self.assertEqual(lane["side"]["distance_m"], 12.5)
        snapshot = self.client.post("/api/snapshots", json={
            "name": "Next net", "role": "batting", "frames": self.client.get("/api/samples/batting-clean").json()["frames"],
        }).json()
        self.assertEqual(snapshot["calibration"]["side"]["distance_m"], 12.5)

    def test_a_left_hander_renames_the_line(self):
        session = wait_ready(self.client, self.client.post("/api/demo/behind").json()["id"])
        self.assertEqual(session["deliveries"][0]["pitch"]["line"], "outside off")
        flipped = self.client.patch(f"/api/sessions/{session['id']}", json={"batter": "left"}).json()
        self.assertEqual(flipped["deliveries"][0]["pitch"]["line"], "down leg")

    def test_the_coach_can_mark_and_remove_a_ball(self):
        session = wait_ready(self.client, self.client.post("/api/demo/tracked").json()["id"])
        added = self.client.post(f"/api/sessions/{session['id']}/deliveries", json={"t0": 0.2, "t1": 2.0}).json()
        self.assertEqual(added["cut_note"], "Marked by the coach.")
        self.assertEqual(self.client.post(f"/api/sessions/{session['id']}/deliveries", json={"t0": 2, "t1": 1}).status_code, 400)
        removed = self.client.delete(f"/api/sessions/{session['id']}/deliveries/{added['id']}").json()
        self.assertEqual(len(removed["deliveries"]), 1)

    def test_a_restart_frees_a_session_that_was_mid_job(self):
        session = wait_ready(self.client, self.client.post("/api/demo/tracked").json()["id"])
        store = self.client.app.state.store
        store.update(session["id"], lambda s: s.update(status="processing", job={"kind": "find", "progress": 0.4}))
        TestClient(create_app(Path(self.tmp.name)))
        recovered = self.client.get(f"/api/sessions/{session['id']}").json()
        self.assertEqual(recovered["status"], "error")
        self.assertIsNone(recovered["job"])
        self.assertEqual(self.client.post(f"/api/sessions/{session['id']}/find").status_code, 200)
        wait_ready(self.client, session["id"])

    def test_upload_needs_a_video(self):
        response = self.client.post("/api/sessions", data={"name": "Empty"})
        self.assertEqual(response.status_code, 400)

    def test_camera_addresses_are_checked(self):
        self.assertEqual(self.client.post("/api/cameras", json={"side_url": "file:///etc/passwd"}).status_code, 400)
        self.assertEqual(self.client.post("/api/cameras", json={"side_url": "rtsp://127.0.0.1/x"}).status_code, 400)

    def test_security_headers(self):
        page = self.client.get("/")
        self.assertEqual(page.headers["x-content-type-options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        self.assertEqual(self.client.get("/api/health").headers["cache-control"], "no-store")


class EdgeMachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with mock.patch.dict(os.environ, {"VANSH_MACHINE": "edge"}):
            self.client = TestClient(create_app(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_bowl_waits_for_the_bridge_at_the_nets(self):
        queued = self.client.post("/api/machine", json={"line": "off stump", "length": "good", "pace_kph": 120}).json()
        self.assertEqual(queued["length_m"], 7.0)
        sent = self.client.post("/api/machine/bowl").json()
        self.assertEqual(sent["status"], "sent to the nets")
        waiting = self.client.get("/api/machine/requests").json()["items"]
        self.assertEqual([item["id"] for item in waiting], [queued["id"]])
        done = self.client.post(f"/api/machine/requests/{queued['id']}", json={"ok": True}).json()
        self.assertEqual(done["via"], "edge")
        machine = self.client.get("/api/machine").json()
        self.assertEqual(machine["requests"], [])
        self.assertEqual(len(machine["bowled"]), 1)

    def test_a_refused_ball_goes_back_to_the_front(self):
        first = self.client.post("/api/machine", json={"line": "middle", "length": "full"}).json()
        self.client.post("/api/machine", json={"line": "middle", "length": "good"})
        self.client.post("/api/machine/bowl")
        self.client.post(f"/api/machine/requests/{first['id']}", json={"ok": False, "error": "Net not clear"})
        self.assertEqual(self.client.get("/api/machine").json()["items"][0]["id"], first["id"])


class CoachKeyTests(unittest.TestCase):
    """With VANSH_COACH_KEY set: the way VANSH runs on vansh.best."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        with mock.patch.dict(os.environ, {"VANSH_COACH_KEY": "nets-at-six"}):
            self.client = TestClient(create_app(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_lane_is_open_and_sessions_are_closed(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/samples/bowling-clean").status_code, 200)
        self.assertEqual(self.client.get("/api/sessions").status_code, 401)
        self.assertEqual(self.client.post("/api/demo/tracked").status_code, 401)
        self.assertEqual(self.client.get("/api/machine").status_code, 401)
        self.assertFalse(self.client.get("/api/me").json()["coach"])

    def test_signing_in_opens_the_sessions(self):
        self.assertEqual(self.client.post("/api/login", json={"key": "wrong"}).status_code, 401)
        self.assertEqual(self.client.post("/api/login", json={"key": "nets-at-six"}).status_code, 200)
        self.assertTrue(self.client.get("/api/me").json()["coach"])
        self.assertEqual(self.client.get("/api/sessions").status_code, 200)
        self.client.post("/api/logout")
        self.assertEqual(self.client.get("/api/sessions").status_code, 401)

    def test_a_script_at_the_nets_uses_the_key_as_a_bearer_token(self):
        headers = {"Authorization": "Bearer nets-at-six"}
        self.assertEqual(self.client.get("/api/machine/requests", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/machine/requests", headers={"Authorization": "Bearer no"}).status_code, 401)

    def test_guessing_the_key_is_slowed_down(self):
        codes = [self.client.post("/api/login", json={"key": f"guess-{n}"}).status_code for n in range(10)]
        self.assertIn(429, codes)

    def test_the_server_does_not_reach_into_camera_networks(self):
        self.client.post("/api/login", json={"key": "nets-at-six"})
        response = self.client.post("/api/cameras", json={"side_url": "rtsp://203.0.113.9/stream"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("edge recorder", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
