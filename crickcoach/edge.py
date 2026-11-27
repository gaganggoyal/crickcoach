"""The edge bridge: the part of VANSH that lives at the nets.

The coach screen can run on a server (vansh.best), but the CCTV cameras and
the bowling machine are on the club's own network. This program runs on a
laptop or a small computer at the nets and joins the two:

    # record both cameras for a minute and send the take to the coach screen
    python -m crickcoach.edge record --server https://vansh.best \\
        --side rtsp://192.168.1.20/stream --behind rtsp://192.168.1.21/stream --seconds 60

    # keep recording takes until stopped
    python -m crickcoach.edge record ... --loop

    # bowl the balls queued on the coach screen on the machine at the nets
    python -m crickcoach.edge machine --server https://vansh.best --machine 192.168.1.50:8520 --armed

The coach key comes from --key or VANSH_COACH_KEY. Run the server with
VANSH_MACHINE=edge so "Bowl next" sends balls here instead of bowling them.

A bowling machine throws a hard ball at a person. The bridge will not bowl
unless it is started with --armed, it counts down before every ball, and by
default it waits for Enter at the nets before each one. --auto drops the
Enter, never the countdown.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx

from crickcoach.machine_link import send
from crickcoach.video import record_cameras


def _client(server: str, key: str | None) -> httpx.Client:
    headers = {"User-Agent": "vansh-edge"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return httpx.Client(base_url=server.rstrip("/"), headers=headers, timeout=httpx.Timeout(30.0, read=60.0))


def record(args: argparse.Namespace) -> int:
    client = _client(args.server, args.key)
    take = 0
    while True:
        take += 1
        with tempfile.TemporaryDirectory(prefix="vansh-take-") as folder:
            folder_path = Path(folder)
            cameras = {"side": (args.side, folder_path / "side.mp4")}
            if args.behind:
                cameras["behind"] = (args.behind, folder_path / "behind.mp4")
            print(f"Recording take {take} for {args.seconds} s…", flush=True)
            try:
                failures = record_cameras(cameras, args.seconds, limit=600)
            except RuntimeError as exc:
                print(f"Recording failed: {exc}", file=sys.stderr)
                if not args.loop:
                    return 1
                time.sleep(5)
                continue
            for camera, reason in failures.items():
                print(f"The {camera} camera failed: {reason}", file=sys.stderr)
            name = f"{args.name} {take}" if args.loop else args.name
            session = upload(client, name=name, role=args.role, batter=args.batter,
                             side=None if "side" in failures else cameras["side"][1],
                             behind=None if "behind" in failures or not args.behind else cameras["behind"][1])
            print(f"Sent. Session {session['id']} is being tracked at {args.server.rstrip('/')}/", flush=True)
        if not args.loop:
            return 0


def upload(client: httpx.Client, *, name: str, role: str, batter: str, side: Path | None, behind: Path | None) -> dict:
    files = {}
    handles = []
    try:
        for camera, path in (("side", side), ("behind", behind)):
            if path is not None and path.exists():
                handle = path.open("rb")
                handles.append(handle)
                files[camera] = (path.name, handle, "video/mp4")
        if not files:
            raise RuntimeError("No camera recorded anything.")
        response = client.post("/api/sessions", data={"name": name, "role": role, "batter": batter}, files=files)
    finally:
        for handle in handles:
            handle.close()
    if response.status_code == 401:
        raise SystemExit("The server wants the coach key. Pass --key or set VANSH_COACH_KEY.")
    response.raise_for_status()
    return response.json()


def machine(args: argparse.Namespace) -> int:
    if not args.armed:
        print("The machine bridge only bowls when started with --armed. Check the net is clear first.", file=sys.stderr)
        return 2
    client = _client(args.server, args.key)
    print(f"Armed. Waiting for balls from {args.server}. Ctrl+C stops the bridge.", flush=True)
    while True:
        try:
            response = client.get("/api/machine/requests", params={"wait": 20})
        except httpx.HTTPError as exc:
            print(f"Server not reachable ({exc}). Trying again.", file=sys.stderr)
            time.sleep(3)
            continue
        if response.status_code == 401:
            print("The server wants the coach key. Pass --key or set VANSH_COACH_KEY.", file=sys.stderr)
            return 1
        response.raise_for_status()
        for item in response.json().get("items", []):
            bowl_one(client, item, endpoint=args.machine, auto=args.auto, countdown=args.countdown)


def bowl_one(client: httpx.Client, item: dict, *, endpoint: str, auto: bool, countdown: int,
             ask=input, out=print) -> bool:
    words = f"{item['length']} length on {item['line']}"
    if item.get("pace_kph"):
        words += f" at {item['pace_kph']:.0f} km/h"
    out(f"Next ball: {words}.")
    if not auto:
        answer = ask("Press Enter to bowl it, or type s to skip: ")
        if answer.strip().lower().startswith("s"):
            client.post(f"/api/machine/requests/{item['id']}", json={"ok": False, "error": "Skipped at the nets."})
            out("Skipped. The ball goes back to the front of the queue.")
            return False
    for remaining in range(countdown, 0, -1):
        out(f"\a{remaining}…")
        time.sleep(1)
    from crickcoach.machine_link import command_for

    try:
        send(endpoint, command_for(item))
    except RuntimeError as exc:
        client.post(f"/api/machine/requests/{item['id']}", json={"ok": False, "error": str(exc)})
        out(f"The machine did not take it: {exc}")
        return False
    client.post(f"/api/machine/requests/{item['id']}", json={"ok": True})
    out("Bowled.")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m crickcoach.edge", description=__doc__.split("\n\n")[0])
    parser.add_argument("--server", default=os.environ.get("VANSH_SERVER", "https://vansh.best"))
    parser.add_argument("--key", default=os.environ.get("VANSH_COACH_KEY"))
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="record the CCTV cameras and send each take")
    rec.add_argument("--side", required=True, help="rtsp:// address of the side-on camera")
    rec.add_argument("--behind", help="rtsp:// address of the camera behind the bowler")
    rec.add_argument("--seconds", type=int, default=120, help="length of each take, up to 600")
    rec.add_argument("--name", default="Net take")
    rec.add_argument("--role", default="bowling", choices=["bowling", "batting"])
    rec.add_argument("--batter", default="right", choices=["right", "left"])
    rec.add_argument("--loop", action="store_true", help="keep recording takes until stopped")
    mac = sub.add_parser("machine", help="bowl the coach's queue on the machine at the nets")
    mac.add_argument("--machine", required=True, help="host:port of the bowling machine")
    mac.add_argument("--armed", action="store_true", help="required: the net is clear and the machine may bowl")
    mac.add_argument("--auto", action="store_true", help="bowl after the countdown without waiting for Enter")
    mac.add_argument("--countdown", type=int, default=3)
    args = parser.parse_args(argv)
    if args.command == "record":
        args.seconds = max(3, min(args.seconds, 600))
        return record(args)
    args.countdown = max(2, args.countdown)
    return machine(args)


if __name__ == "__main__":
    raise SystemExit(main())
