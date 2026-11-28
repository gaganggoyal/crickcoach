import json
import os
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from crickcoach.app import create_app
from crickcoach.footage import FOOTAGE, clip
from crickcoach.video import ffmpeg_exe, probe


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


class RecordingAtTheNetsTests(unittest.TestCase):
    """The coach presses Start; the bridge at the nets records and sends takes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, **body) -> dict:
        response = self.client.post("/api/nets/start", json={"name": "Nets", **body})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["recording"]

    def test_a_recording_runs_from_start_to_stop_through_the_bridge(self):
        self.assertFalse(self.client.get("/api/nets").json()["online"])
        recording = self.start(role="batting", batter="left")
        self.assertEqual((recording["state"], recording["role"], recording["batter"]), ("starting", "batting", "left"))
        self.assertEqual(self.client.post("/api/nets/start", json={}).status_code, 409)
        polled = self.client.get("/api/nets/poll", params={"since": -1}).json()
        self.assertEqual(polled["recording"]["id"], recording["id"])
        started = time.time()
        self.client.get("/api/nets/poll", params={"since": polled["version"], "wait": 0.6})
        self.assertGreaterEqual(time.time() - started, 0.5)
        said = self.client.post("/api/nets/status", json={"state": "recording", "recording_id": recording["id"]}).json()
        self.assertEqual(said["recording"]["state"], "recording")
        self.assertTrue(self.client.get("/api/nets").json()["online"])
        self.assertEqual(self.client.post("/api/nets/stop").json()["recording"]["state"], "stopping")
        said = self.client.post("/api/nets/status", json={"state": "idle"}).json()
        self.assertEqual(said["recording"]["state"], "stopped")
        self.assertEqual([item["id"] for item in self.client.get("/api/nets").json()["recent"]], [recording["id"]])
        self.assertEqual(self.client.post("/api/nets/stop").status_code, 409)

    def test_a_quiet_bridge_cannot_hold_a_recording_open(self):
        self.start()
        self.assertEqual(self.client.post("/api/nets/stop").json()["recording"]["state"], "stopped")
        recording = self.start()
        self.client.post("/api/nets/status", json={"state": "recording", "recording_id": recording["id"]})
        self.assertEqual(self.client.post("/api/nets/stop").json()["recording"]["state"], "stopping")
        self.assertEqual(self.client.post("/api/nets/stop").json()["recording"]["state"], "stopped")

    def test_what_the_bridge_says_is_cleaned_before_the_coach_sees_it(self):
        self.client.post("/api/nets/status", json={
            "state": "idle", "error": "rtsp://admin:hunter2@10.0.0.9/live failed",
            "cameras": {"side": {"ok": True, "width": 1920, "fps": "25", "script": "<b>x</b>",
                                 "warnings": ["see rtsp://u:p@cam/x"]}, "garage": {"ok": True}},
        })
        edge = self.client.get("/api/nets").json()["edge"]
        self.assertEqual(set(edge["cameras"]), {"side"})
        self.assertEqual(edge["cameras"]["side"]["fps"], 25.0)
        self.assertNotIn("script", edge["cameras"]["side"])
        self.assertNotIn("hunter2", json.dumps(edge))
        self.assertNotIn("u:p@", json.dumps(edge))
        checked = self.client.post("/api/nets/check", json={"cameras": {"behind": {"ok": False, "error": "no answer"}}})
        self.assertEqual(checked.json()["check"]["cameras"]["behind"]["error"], "no answer")

    def test_takes_join_their_recording_and_drill_together(self):
        recording = self.start()
        with (FOOTAGE / "side_good_length.mp4").open("rb") as handle:
            bad = self.client.post("/api/sessions", data={"series_id": "nope", "take": "1"},
                                   files={"side": ("side.mp4", handle, "video/mp4")})
        self.assertEqual(bad.status_code, 400)
        demo = wait_ready(self.client, self.client.post("/api/demo/tracked").json()["id"])
        self.client.post(f"/api/sessions/{demo['id']}/calibration/side", json=clip("side_good_length")["calibration"])
        with (FOOTAGE / "side_good_length.mp4").open("rb") as handle:
            take = self.client.post("/api/sessions", data={
                "name": "Nets · take 1", "series_id": recording["id"], "series_name": "Nets", "take": "1",
            }, files={"side": ("side.mp4", handle, "video/mp4")}).json()
        self.assertEqual(take["series"], {"id": recording["id"], "name": "Nets", "take": 1})
        self.assertEqual(self.client.get("/api/nets").json()["recording"]["takes"], 1)
        take = wait_ready(self.client, take["id"])
        self.assertEqual(take["deliveries"][0]["pitch"]["length"], "good")
        listed = [item for item in self.client.get("/api/sessions").json()["sessions"] if item["series"]]
        self.assertEqual(listed[0]["series"]["take"], 1)
        self.client.post(f"/api/sessions/{take['id']}/deliveries/d1/outcome", json={"outcome": "edged"})
        drill = self.client.post("/api/drill", json={"series_id": recording["id"], "count": 3}).json()
        self.assertEqual([item["length"] for item in drill["items"]], ["good"] * 3)
        self.assertEqual(self.client.post("/api/drill", json={"series_id": "r00000000"}).status_code, 404)

    def test_the_oldest_video_makes_room_and_the_measurements_stay(self):
        store = self.client.app.state.store
        ids = []
        for days in (9, 5, 0):
            session = store.create_session(name=f"{days} days ago", role="bowling", status="ready")
            session["created"] = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
            session["cameras"]["side"] = "side.mp4"
            session["deliveries"] = [{"id": "d1", "index": 1, "t0": 0, "t1": 1, "pitch": None}]
            store.save(session)
            (store.folder(session["id"]) / "side.mp4").write_bytes(b"x" * 100_000)
            ids.append(session["id"])
        freed = store.free_video(150_000)
        self.assertEqual(freed, ids[:2])
        oldest = self.client.get(f"/api/sessions/{ids[0]}").json()
        self.assertTrue(oldest["video_removed"])
        self.assertEqual(len(oldest["deliveries"]), 1)
        self.assertFalse(self.client.get(f"/api/sessions/{ids[2]}").json()["video_removed"])
        self.assertEqual(self.client.get(f"/api/sessions/{ids[0]}/media/side").status_code, 404)

    def test_h265_from_a_phone_is_converted_so_browsers_can_play_it(self):
        hevc = Path(self.tmp.name) / "phone.mp4"
        made = subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(FOOTAGE / "side_good_length.mp4"),
                               "-c:v", "libx265", "-crf", "18", "-x265-params", "log-level=error", "-tag:v", "hvc1",
                               str(hevc)], capture_output=True)
        if made.returncode != 0:
            self.skipTest("this ffmpeg cannot write H.265")
        self.assertEqual(probe(hevc).codec, "hevc")
        with hevc.open("rb") as handle:
            session = self.client.post("/api/sessions", files={"side": ("phone.mov", handle, "video/quicktime")}).json()
        session = wait_ready(self.client, session["id"])
        self.assertEqual(session["cameras"]["side"], "side.mp4")
        stored = Path(self.tmp.name) / "sessions" / session["id"] / "side.mp4"
        self.assertEqual(probe(stored).codec, "h264")
        self.assertFalse((stored.parent / "side.mov").exists())

    def test_the_setup_guide_is_public_and_locked_down(self):
        page = self.client.get("/setup/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Where the cameras go", page.text)
        self.assertIn("script-src 'self'", page.headers["content-security-policy"])
        self.assertEqual(self.client.get("/setup", follow_redirects=False).status_code, 307)
        self.assertEqual(self.client.get("/setup/img/top-view.svg").status_code, 200)


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

    def test_the_nets_need_the_key(self):
        self.assertEqual(self.client.get("/api/nets").status_code, 401)
        self.assertEqual(self.client.post("/api/nets/start", json={}).status_code, 401)
        self.assertEqual(self.client.post("/api/nets/status", json={}).status_code, 401)
        bridge = {"Authorization": "Bearer nets-at-six"}
        self.assertEqual(self.client.get("/api/nets/poll", headers=bridge).status_code, 200)
        self.assertEqual(self.client.get("/setup/").status_code, 200)

    def test_the_server_does_not_reach_into_camera_networks(self):
        self.client.post("/api/login", json={"key": "nets-at-six"})
        response = self.client.post("/api/cameras", json={"side_url": "rtsp://203.0.113.9/stream"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("edge recorder", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
