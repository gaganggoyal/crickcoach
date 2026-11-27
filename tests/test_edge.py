import io
import json
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from crickcoach import edge
from crickcoach.app import create_app
from crickcoach.footage import FOOTAGE, clip
from crickcoach.machine_link import serve


class FakeClient:
    def __init__(self):
        self.posts = []

    def post(self, url, json=None, **kwargs):
        self.posts.append((url, json))


class EdgeBridgeTests(unittest.TestCase):
    item = {"id": "abcd1234", "line": "off stump", "length": "good", "pace_kph": 124, "length_m": 7.0,
            "offset_m": -0.1, "batter": "right"}

    def test_the_bridge_refuses_to_bowl_unless_armed(self):
        code = edge.main(["--server", "http://127.0.0.1:9", "machine", "--machine", "127.0.0.1:9"])
        self.assertEqual(code, 2)

    def test_a_ball_is_bowled_after_enter_and_a_countdown(self):
        log = []
        stop = threading.Event()
        thread = threading.Thread(target=serve, args=("127.0.0.1", 18766, stop, log), daemon=True)
        thread.start()
        time.sleep(0.15)
        client = FakeClient()
        said = []
        try:
            with mock.patch("crickcoach.edge.time.sleep"):
                bowled = edge.bowl_one(client, self.item, endpoint="127.0.0.1:18766", auto=False, countdown=3,
                                       ask=lambda prompt: "", out=said.append)
        finally:
            stop.set()
            thread.join(1)
        self.assertTrue(bowled)
        self.assertEqual(log[0]["id"], "abcd1234")
        self.assertEqual(client.posts[-1], ("/api/machine/requests/abcd1234", {"ok": True}))
        self.assertEqual(sum(1 for line in said if line.endswith("…")), 3)

    def test_skipping_at_the_nets_sends_the_ball_back(self):
        client = FakeClient()
        bowled = edge.bowl_one(client, self.item, endpoint="127.0.0.1:9", auto=False, countdown=2,
                               ask=lambda prompt: "s", out=lambda line: None)
        self.assertFalse(bowled)
        self.assertFalse(client.posts[-1][1]["ok"])

    def test_a_machine_that_does_not_answer_is_reported(self):
        client = FakeClient()
        with mock.patch("crickcoach.edge.time.sleep"):
            bowled = edge.bowl_one(client, self.item, endpoint="127.0.0.1:9", auto=True, countdown=2,
                                   ask=lambda prompt: "", out=lambda line: None)
        self.assertFalse(bowled)
        self.assertIn("did not answer", client.posts[-1][1]["error"])



class SettingsTests(unittest.TestCase):
    def test_the_env_file_is_read_with_quotes_comments_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vansh-edge.env"
            path.write_text('# cameras\nexport VANSH_SERVER="https://vansh.best"\nVANSH_SIDE_URL=rtsp://u:p@cam/1?a=b\n\n'
                            "VANSH_TAKE_SECONDS='60'\nnot a setting\n")
            self.assertEqual(edge.load_env_file(path), {
                "VANSH_SERVER": "https://vansh.best", "VANSH_SIDE_URL": "rtsp://u:p@cam/1?a=b", "VANSH_TAKE_SECONDS": "60",
            })

    def test_check_reads_a_camera_named_in_the_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nets.env"
            path.write_text(f"VANSH_SIDE_URL={FOOTAGE / 'side_two_balls.mp4'}\n")
            said = io.StringIO()
            with mock.patch.dict("os.environ", {}, clear=False), redirect_stdout(said):
                code = edge.main(["--env-file", str(path), "check", "--seconds", "3", "--no-report", "--out", tmp])
            self.assertEqual(code, 0, said.getvalue())
            self.assertIn("1280×720 H264, 25 frames a second", said.getvalue())
            self.assertTrue((Path(tmp) / "side.jpg").exists())

    def test_a_camera_that_does_not_answer_is_explained(self):
        said = io.StringIO()
        with redirect_stdout(said):
            code = edge.main(["check", "--side", "rtsp://admin:secret@127.0.0.1:9/x", "--seconds", "2", "--no-report",
                              "--out", tempfile.gettempdir()])
        self.assertEqual(code, 1)
        self.assertIn("Nothing answered at that address", said.getvalue())
        self.assertNotIn("secret", said.getvalue())


class TakeTests(unittest.TestCase):
    def test_takes_pair_by_overlap_in_time(self):
        lead = {"start": 100.0, "end": 190.0}
        late = {"start": 100.6, "end": 190.9}
        before = {"start": 10.0, "end": 100.5}
        self.assertIs(edge._best_overlap(lead, [before, late]), late)
        self.assertIsNone(edge._best_overlap(lead, [before]))

    def test_a_take_the_server_refuses_is_set_aside(self):
        class Refusing:
            def post(self, url, **kwargs):
                import httpx
                return httpx.Response(400, json={"detail": "That file is empty."})

        with tempfile.TemporaryDirectory() as tmp:
            queue = Path(tmp) / "queue"
            entry = queue / "r12345678-0001"
            entry.mkdir(parents=True)
            (entry / "side.mp4").write_bytes(b"x" * 100)
            (entry / "meta.json").write_text(json.dumps({
                "recording": {"id": "r12345678", "name": "Nets", "role": "bowling", "batter": "right"}, "take": 1}))
            uploader = edge.Uploader(Refusing(), queue, out=lambda line: None)
            self.assertTrue(uploader.send(entry))
            self.assertFalse(entry.exists())
            self.assertTrue((Path(tmp) / "failed" / "r12345678-0001" / "side.mp4").exists())
            self.assertIn("That file is empty", uploader.error)


class BridgeTests(unittest.TestCase):
    """The whole loop: Start on the coach screen, takes from two cameras, Stop."""

    def test_takes_from_both_cameras_reach_the_coach_while_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app(root / "server")
            coach, link = TestClient(app), TestClient(app)
            demo = coach.post("/api/demo/tracked").json()["id"]
            for camera in ("side", "behind"):
                saved = coach.post(f"/api/sessions/{demo}/calibration/{camera}", json=clip(f"pair_{camera}")["calibration"])
                self.assertEqual(saved.status_code, 200, saved.text)
            cameras = {"side": str(FOOTAGE / "pair_side.mp4"), "behind": str(FOOTAGE / "pair_behind.mp4")}
            bridge = edge.Bridge(link, cameras, root / "edge", take_seconds=3, poll_wait=0.5, check_seconds=0,
                                 out=lambda line: None)
            halt = threading.Event()
            thread = threading.Thread(target=bridge.run, args=(halt,), daemon=True)
            thread.start()
            try:
                recording = coach.post("/api/nets/start", json={"name": "Test nets"}).json()["recording"]
                takes = self.wait_for(coach, lambda: len(self.takes(coach, recording["id"])) >= 2, 40)
                self.assertEqual(coach.get("/api/nets").json()["recording"]["state"], "recording")
                coach.post("/api/nets/stop")
                self.wait_for(coach, lambda: coach.get("/api/nets").json()["recording"]["state"] == "stopped"
                              and coach.get("/api/nets").json()["edge"]["queue"] == 0, 40)
            finally:
                halt.set()
                thread.join(30)
            takes = self.takes(coach, recording["id"])
            numbers = sorted(take["series"]["take"] for take in takes)
            self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
            # The stand-in reels loop at different lengths, so later takes drift apart and may go alone;
            # nothing is dropped, and the first take holds both cameras.
            self.assertTrue(all(take["cameras"]["side"] or take["cameras"]["behind"] for take in takes))
            first = next(take for take in takes if take["series"]["take"] == 1)
            self.assertTrue(first["cameras"]["side"] and first["cameras"]["behind"])
            first = self.wait_for(coach, lambda: self.ready(coach, first["id"]), 60)
            pitch = first["deliveries"][0]["pitch"]
            self.assertEqual(pitch["length"], "full")
            self.assertIsNotNone(pitch["offset_m"], "the camera behind should give the line")
            self.assertAlmostEqual(first["clock_offset"], -0.8, delta=0.1)
            self.assertFalse(any((root / "edge" / "recording").iterdir()))

    @staticmethod
    def takes(coach, recording_id):
        return [item for item in coach.get("/api/sessions").json()["sessions"]
                if (item.get("series") or {}).get("id") == recording_id]

    @staticmethod
    def ready(coach, session_id):
        session = coach.get(f"/api/sessions/{session_id}").json()
        return session if session["status"] in {"ready", "error"} and not session["job"] else None

    @staticmethod
    def wait_for(coach, test, seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            result = test()
            if result:
                return result
            time.sleep(0.3)
        raise AssertionError("timed out")


if __name__ == "__main__":
    unittest.main()
