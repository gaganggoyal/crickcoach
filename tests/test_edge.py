import threading
import time
import unittest
from unittest import mock

from crickcoach import edge
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


if __name__ == "__main__":
    unittest.main()
