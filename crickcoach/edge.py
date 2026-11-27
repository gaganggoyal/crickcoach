"""The bridge at the nets: the part of VANSH that lives next to the cameras.

The coach screen runs on a server (vansh.best), but the CCTV cameras and the
bowling machine sit on the club's own network. This program runs on a laptop
or a small computer at the nets and joins the two.

    # once: check that both cameras can be read, and how well
    python -m crickcoach.edge check

    # all the time: wait for the coach to press Start on the website, then
    # record both cameras without a break and send a take every 90 seconds
    python -m crickcoach.edge run

    # bowl the balls queued on the coach screen on the machine at the nets
    python -m crickcoach.edge machine --machine 192.168.1.50:8520 --armed

Settings come from flags, from the environment, or from an env file
(vansh-edge.env in this folder or in ~/.vansh-edge/):

    VANSH_SERVER=https://vansh.best
    VANSH_COACH_KEY=...
    VANSH_SIDE_URL=rtsp://user:password@192.168.1.20:554/...
    VANSH_BEHIND_URL=rtsp://user:password@192.168.1.21:554/...

A bowling machine throws a hard ball at a person. The bridge will not bowl
unless it is started with --armed, it counts down before every ball, and by
default it waits for Enter at the nets before each one. --auto drops the
Enter, never the countdown.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

from crickcoach import __version__
from crickcoach.machine_link import send
from crickcoach.video import check_camera, record_cameras, redact, segment_command

CAMERAS = ("side", "behind")
HOME = Path(os.environ.get("VANSH_EDGE_DIR") or Path.home() / ".vansh-edge")
ENV_NAME = "vansh-edge.env"
GRACE_S = 15.0  # how long a take waits for the other camera's matching take


class KeyRejected(RuntimeError):
    pass


# --------------------------------------------------------------------------
# settings


def load_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines. Blank lines and # comments are skipped; quotes are optional."""
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def find_env_file(given: str | None) -> Path | None:
    if given:
        return Path(given).expanduser()
    for candidate in (Path.cwd() / ENV_NAME, HOME / ENV_NAME):
        if candidate.exists():
            return candidate
    return None


def _client(server: str, key: str | None) -> httpx.Client:
    headers = {"User-Agent": f"vansh-edge/{__version__}"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return httpx.Client(base_url=server.rstrip("/"), headers=headers, timeout=httpx.Timeout(30.0, read=60.0))


def _cameras(args: argparse.Namespace) -> dict[str, str]:
    return {name: url for name, url in (("side", args.side), ("behind", args.behind)) if url}


# --------------------------------------------------------------------------
# check


def check(args: argparse.Namespace, client: httpx.Client | None = None, out=print) -> int:
    cameras = _cameras(args)
    if not cameras:
        out("No cameras given. Set VANSH_SIDE_URL and VANSH_BEHIND_URL, or pass --side and --behind.")
        return 2
    folder = Path(args.out)
    reports = {}
    for name, url in cameras.items():
        out(f"Reading the {name} camera ({redact(url)}) for {args.seconds:g} s…")
        report = check_camera(url, seconds=args.seconds, snapshot=folder / f"{name}.jpg")
        reports[name] = report
        if not report["ok"]:
            out(f"  ✗ {report['error']}")
            continue
        keyframes = f"a keyframe every {report['keyframe_s']:g} s" if report.get("keyframe_s") else "few keyframes"
        out(f"  ✓ {report['width']}×{report['height']} {report['codec'].upper()}, {report['fps']:g} frames a second, "
            f"{report['kbps'] / 1000:.1f} Mbit/s, {keyframes}.")
        for warning in report["warnings"]:
            out(f"  • {warning}")
        out(f"  Picture saved to {folder / (name + '.jpg')}")
    good = [report for report in reports.values() if report["ok"]]
    if good:
        upload = sum(report["kbps"] for report in good) / 1000
        out(f"Upload needed to keep up while recording: about {upload:.0f} Mbit/s.")
    out("VANSH cannot read the shutter speed from the stream. Set it to 1/500 s in each camera's exposure settings.")
    if client is not None:
        try:
            response = client.post("/api/nets/check", json={"cameras": reports})
            if response.status_code == 200:
                out("The check is on the coach screen too.")
        except httpx.HTTPError:
            pass
    return 0 if all(report["ok"] for report in reports.values()) else 1


# --------------------------------------------------------------------------
# run: the always-on bridge


class Uploader(threading.Thread):
    """Sends finished takes to the server, oldest first, and keeps trying."""

    def __init__(self, client: httpx.Client, queue: Path, out=print):
        super().__init__(daemon=True, name="vansh-upload")
        self.client = client
        self.queue = queue
        self.out = out
        self.halt = threading.Event()
        self.nudge = threading.Event()
        self.sent = 0
        self.error = ""
        self.current: str | None = None
        self.rejected = False

    def entries(self) -> list[Path]:
        if not self.queue.exists():
            return []
        return sorted(path for path in self.queue.iterdir() if (path / "meta.json").exists())

    def waiting_bytes(self) -> int:
        return sum(item.stat().st_size for entry in self.entries() for item in entry.iterdir() if item.is_file())

    def stop(self) -> None:
        self.halt.set()
        self.nudge.set()

    def run(self) -> None:
        delay = 2.0
        while not self.halt.is_set():
            entries = self.entries()
            if not entries or self.rejected:
                self.nudge.wait(2.0)
                self.nudge.clear()
                continue
            if self.send(entries[0]):
                delay = 2.0
                continue
            self.halt.wait(delay)
            delay = min(delay * 2, 60.0)

    def send(self, entry: Path) -> bool:
        meta = json.loads((entry / "meta.json").read_text())
        recording = meta["recording"]
        label = f"{recording['name']} · take {meta['take']}"
        self.current = label
        handles = []
        try:
            files = {}
            for camera in CAMERAS:
                path = entry / f"{camera}.mp4"
                if path.exists():
                    handle = path.open("rb")
                    handles.append(handle)
                    files[camera] = (path.name, handle, "video/mp4")
            data = {"name": label, "role": recording["role"], "batter": recording["batter"],
                    "series_id": recording["id"], "series_name": recording["name"], "take": str(meta["take"])}
            response = self.client.post("/api/sessions", data=data, files=files,
                                        timeout=httpx.Timeout(60.0, read=300.0, write=900.0))
        except httpx.HTTPError as exc:
            self.error = f"Could not send {label} ({type(exc).__name__}). Trying again."
            return False
        finally:
            for handle in handles:
                handle.close()
            self.current = None
        if response.status_code == 200:
            shutil.rmtree(entry, ignore_errors=True)
            self.sent += 1
            self.error = ""
            self.out(f"Sent {label}.")
            return True
        detail = _detail(response)
        if response.status_code == 401:
            self.rejected = True
            self.error = "The server refused the coach key. Check VANSH_COACH_KEY."
        elif response.status_code == 507:
            self.error = "The server is full. Delete old sessions on the coach screen; takes wait here meanwhile."
        elif response.status_code in (400, 413, 422):
            failed = self.queue.parent / "failed"
            failed.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(failed / entry.name))
            self.error = f"The server refused {label}: {detail}"
            self.out(self.error)
            return True
        else:
            self.error = f"Could not send {label} (HTTP {response.status_code}). Trying again."
        return False


class Bridge:
    """Records the cameras while the coach's recording runs, and sends every take."""

    def __init__(self, client: httpx.Client, cameras: dict[str, str], home: Path, *, take_seconds: int = 90,
                 max_gb: float = 20.0, copy: bool = True, poll_wait: float = 5.0, check_seconds: float = 3.0,
                 out=print):
        if not cameras:
            raise ValueError("The bridge needs at least one camera.")
        self.client = client
        self.cameras = dict(cameras)
        self.primary = "side" if "side" in self.cameras else "behind"
        self.home = Path(home)
        self.queue = self.home / "queue"
        self.work = self.home / "recording"
        self.take_seconds = int(take_seconds)
        self.max_bytes = int(max_gb * 1024**3)
        self.copy = copy
        self.poll_wait = poll_wait
        self.check_seconds = check_seconds
        self.out = out
        self.version = -1
        self.recording: dict | None = None
        self.part: Path | None = None
        self.launches: list[dict] = []  # one per ffmpeg process: camera, stem, wall-clock start, process
        self.used: set[str] = set()
        self.takes = 0
        self.camera_info: dict[str, dict] = {}
        self.error = ""
        self.server_error = ""
        self.last_report = 0.0
        self.uploader = Uploader(client, self.queue, out)

    # -- the loop -------------------------------------------------------------

    def run(self, halt: threading.Event) -> int:
        self.queue.mkdir(parents=True, exist_ok=True)
        self.work.mkdir(parents=True, exist_ok=True)
        self.rescue_leftovers()
        if self.check_seconds:
            self.look_at_cameras()
        self.uploader.start()
        self.out(f"VANSH bridge {__version__} is waiting for the coach to press Start. Ctrl+C stops it.")
        code = 0
        try:
            while not halt.is_set():
                try:
                    self.apply(self.poll())
                    self.server_error = ""
                except KeyRejected:
                    self.out("The server refused the coach key. Check VANSH_COACH_KEY.")
                    code = 1
                    break
                except httpx.HTTPError as exc:
                    self.server_error = (f"The server is not reachable ({type(exc).__name__}). "
                                         "Recording carries on; takes wait here.")
                    halt.wait(3.0)
                self.supervise()
                self.collect()
                self.report()
        finally:
            if self.recording:
                self.stop_recording()
            self.report(force=True)
            self.uploader.stop()
        return code

    def poll(self) -> dict | None:
        response = self.client.get("/api/nets/poll", params={"since": self.version, "wait": self.poll_wait},
                                   timeout=httpx.Timeout(30.0, read=self.poll_wait + 30.0))
        if response.status_code == 401:
            raise KeyRejected()
        response.raise_for_status()
        data = response.json()
        self.version = int(data.get("version", self.version))
        return data.get("recording")

    def apply(self, recording: dict | None) -> None:
        wanted = recording if recording and recording.get("state") in ("starting", "recording") else None
        if self.recording and (wanted is None or wanted["id"] != self.recording["id"]):
            self.stop_recording()
        if wanted and not self.recording:
            self.start_recording(wanted)

    # -- recording -------------------------------------------------------------

    def start_recording(self, recording: dict) -> None:
        if self.queue_full():
            self.error = "The bridge's disk is full of takes waiting to upload. Not recording."
            return
        self.recording = {key: recording[key] for key in ("id", "name", "role", "batter")}
        self.takes = max(int(recording.get("takes") or 0), self.queued_takes(recording["id"]))
        self.part = self.work / recording["id"] / f"part-{int(time.time() * 1000)}"
        self.part.mkdir(parents=True, exist_ok=True)
        self.launches = []
        for camera, url in self.cameras.items():
            self.launch(camera, url)
        self.out(f"Recording “{recording['name']}”: {', '.join(self.cameras)} camera(s), a take every {self.take_seconds} s.")
        self.report(force=True)

    def launch(self, camera: str, url: str) -> None:
        stem = f"{camera}-{len([item for item in self.launches if item['camera'] == camera]) + 1}"
        with (self.part / f"{stem}.log").open("ab") as log:
            process = subprocess.Popen(segment_command(url, self.part, stem, self.take_seconds, copy=self.copy),
                                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        self.launches.append({"camera": camera, "stem": stem, "started": time.time(), "process": process,
                              "restarted": False})

    def stop_recording(self) -> None:
        name = self.recording["name"] if self.recording else ""
        for launch in self.launches:
            _finish(launch["process"])
        self.collect(final=True)
        if self.part is not None:
            shutil.rmtree(self.part, ignore_errors=True)
            try:
                self.part.parent.rmdir()
            except OSError:
                pass
        self.recording = None
        self.part = None
        self.launches = []
        self.uploader.nudge.set()
        self.out(f"Stopped recording “{name}”.")
        self.report(force=True)

    def supervise(self) -> None:
        """A camera that drops out is started again; the other carries on."""
        if not self.recording:
            return
        for launch in list(self.launches):
            process = launch["process"]
            if process.poll() is None or launch["restarted"]:
                continue
            launch["restarted"] = True
            reason = _tail(self.part / f"{launch['stem']}.log") or f"ffmpeg stopped with code {process.returncode}"
            self.error = f"The {launch['camera']} camera stopped: {redact(reason)}"
            self.out(self.error)
            if time.time() - launch["started"] > 10:
                self.launch(launch["camera"], self.cameras[launch["camera"]])
        if self.queue_full():
            self.error = "The bridge's disk is full of takes waiting to upload. Recording paused."
            self.out(self.error)
            for launch in self.launches:
                _finish(launch["process"])
            self.collect(final=True)
            self.launches = []

    # -- takes -----------------------------------------------------------------

    def finished_segments(self) -> dict[str, list[dict]]:
        """Completed takes from each camera that are not yet queued, with wall-clock times."""
        found: dict[str, list[dict]] = {camera: [] for camera in self.cameras}
        if not self.part:
            return found
        for launch in self.launches:
            listing = self.part / f"{launch['stem']}.csv"
            if not listing.exists():
                continue
            for line in listing.read_text().splitlines():
                parts = line.split(",")
                if len(parts) < 3:
                    continue
                path = self.part / parts[0]
                if str(path) in self.used or not path.exists():
                    continue
                try:
                    start, end = float(parts[1]), float(parts[2])
                except ValueError:
                    continue
                found[launch["camera"]].append({"path": path, "start": launch["started"] + start,
                                                "end": launch["started"] + end, "launch": launch})
        for segments in found.values():
            segments.sort(key=lambda item: item["start"])
        return found

    def collect(self, final: bool = False) -> None:
        """Pair each camera's finished takes by time and queue them for upload."""
        if not self.recording:
            return
        segments = self.finished_segments()
        others = [camera for camera in self.cameras if camera != self.primary]
        now = time.time()
        taken: set[str] = set()
        for lead in segments.get(self.primary, []):
            take = {self.primary: lead}
            waiting = False
            for camera in others:
                pool = [item for item in segments.get(camera, []) if str(item["path"]) not in taken]
                match = _best_overlap(lead, pool)
                if match is not None:
                    take[camera] = match
                elif not final and self.alive(camera) and now - lead["end"] < GRACE_S:
                    waiting = True
            if waiting:
                break
            taken.update(str(item["path"]) for item in take.values())
            self.enqueue(take)
        # Takes from the other camera that never found a partner still hold balls.
        for camera in others:
            for item in segments.get(camera, []):
                if str(item["path"]) in taken:
                    continue
                if final or not self.alive(self.primary) or now - item["end"] > GRACE_S + self.take_seconds:
                    taken.add(str(item["path"]))
                    self.enqueue({camera: item})

    def enqueue(self, take: dict[str, dict]) -> None:
        self.takes += 1
        entry = self.queue / f"{self.recording['id']}-{self.takes:04d}"
        entry.mkdir(parents=True, exist_ok=True)
        for camera, item in take.items():
            shutil.move(str(item["path"]), str(entry / f"{camera}.mp4"))
            self.used.add(str(item["path"]))
        started = min(item["start"] for item in take.values())
        meta = {"recording": self.recording, "take": self.takes, "cameras": sorted(take),
                "recorded": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started))}
        (entry / "meta.json").write_text(json.dumps(meta, indent=2))
        self.out(f"Take {self.takes} recorded ({' + '.join(sorted(take))}).")
        self.uploader.nudge.set()

    def alive(self, camera: str) -> bool:
        return any(item["camera"] == camera and item["process"].poll() is None for item in self.launches)

    def queued_takes(self, recording_id: str) -> int:
        highest = 0
        for folder in (self.queue, self.queue.parent / "failed"):
            if folder.exists():
                for entry in folder.glob(f"{recording_id}-*"):
                    try:
                        highest = max(highest, int(entry.name.rsplit("-", 1)[1]))
                    except ValueError:
                        continue
        return highest

    def queue_full(self) -> bool:
        return self.uploader.waiting_bytes() > self.max_bytes

    def rescue_leftovers(self) -> None:
        """After a power cut, finished takes from the last recording are still worth sending."""
        for listing in self.work.glob("*/part-*/*.csv"):
            part = listing.parent
            recording_id = part.parent.name
            entries = [line.split(",")[0] for line in listing.read_text().splitlines() if line.count(",") >= 2]
            camera = listing.stem.split("-")[0]
            for name in entries:
                path = part / name
                if not path.exists():
                    continue
                take = self.queued_takes(recording_id) + 1
                entry = self.queue / f"{recording_id}-{take:04d}"
                entry.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(entry / f"{camera}.mp4"))
                meta = {"recording": {"id": recording_id, "name": "Recovered take", "role": "bowling", "batter": "right"},
                        "take": take, "cameras": [camera], "recorded": None}
                (entry / "meta.json").write_text(json.dumps(meta, indent=2))
        for folder in self.work.glob("*"):
            shutil.rmtree(folder, ignore_errors=True)

    # -- telling the coach screen ---------------------------------------------

    def look_at_cameras(self) -> None:
        for camera, url in self.cameras.items():
            report = check_camera(url, seconds=self.check_seconds)
            self.camera_info[camera] = report
            if report["ok"]:
                self.out(f"The {camera} camera: {report['width']}×{report['height']}, {report['fps']:g} frames a second.")
            else:
                self.out(f"The {camera} camera did not answer: {report['error']}")

    def report(self, force: bool = False) -> None:
        if not force and time.time() - self.last_report < 10:
            return
        self.last_report = time.time()
        cameras = {}
        for camera in self.cameras:
            info = {key: value for key, value in self.camera_info.get(camera, {}).items() if key != "warnings"}
            info["recording"] = self.alive(camera)
            cameras[camera] = info
        payload = {
            "state": "recording" if self.recording else "idle",
            "recording_id": self.recording["id"] if self.recording else None,
            "cameras": cameras,
            "queue": len(self.uploader.entries()),
            "queue_mb": round(self.uploader.waiting_bytes() / 1024**2, 1),
            "sent": self.uploader.sent,
            "uploading": self.uploader.current,
            "error": (self.server_error or self.error or self.uploader.error)[:600],
            "host": socket.gethostname()[:80],
            "version": __version__,
            "take_seconds": self.take_seconds,
        }
        try:
            self.client.post("/api/nets/status", json=payload)
        except httpx.HTTPError:
            pass


def _best_overlap(lead: dict, candidates: list[dict]) -> dict | None:
    best, best_overlap = None, 0.0
    for item in candidates:
        overlap = min(lead["end"], item["end"]) - max(lead["start"], item["start"])
        if overlap > best_overlap:
            best, best_overlap = item, overlap
    if best is None or best_overlap < 0.4 * min(lead["end"] - lead["start"], best["end"] - best["start"]):
        return None
    return best


def _finish(process: subprocess.Popen) -> None:
    """Ask ffmpeg to close its last take properly, then make sure it has gone."""
    if process.poll() is not None:
        return
    try:
        process.stdin.write(b"q")
        process.stdin.flush()
        process.stdin.close()
    except (BrokenPipeError, OSError, ValueError):
        pass
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _tail(path: Path) -> str:
    try:
        lines = [line for line in path.read_text(errors="replace").splitlines() if line.strip()]
    except OSError:
        return ""
    return " ".join(lines[-2:])[-300:]


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail") or response.text)[:300]
    except ValueError:
        return response.text[:300]


def run(args: argparse.Namespace) -> int:
    cameras = _cameras(args)
    if not cameras:
        print("No cameras given. Set VANSH_SIDE_URL and VANSH_BEHIND_URL, or pass --side and --behind.", file=sys.stderr)
        return 2
    bridge = Bridge(_client(args.server, args.key), cameras, Path(args.home).expanduser(),
                    take_seconds=args.take_seconds, max_gb=args.max_gb, copy=not args.reencode)
    halt = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: halt.set())
    try:
        return bridge.run(halt)
    except KeyboardInterrupt:
        halt.set()
        return 0


# --------------------------------------------------------------------------
# record: one take by hand


def record(args: argparse.Namespace) -> int:
    cameras_given = _cameras(args)
    if "side" not in cameras_given and "behind" not in cameras_given:
        print("No cameras given. Pass --side and --behind, or set VANSH_SIDE_URL and VANSH_BEHIND_URL.", file=sys.stderr)
        return 2
    client = _client(args.server, args.key)
    take = 0
    while True:
        take += 1
        with tempfile.TemporaryDirectory(prefix="vansh-take-") as folder:
            folder_path = Path(folder)
            cameras = {name: (url, folder_path / f"{name}.mp4") for name, url in cameras_given.items()}
            print(f"Recording take {take} for {args.seconds} s…", flush=True)
            try:
                failures = record_cameras(cameras, args.seconds, limit=600, copy=not args.reencode)
            except RuntimeError as exc:
                print(f"Recording failed: {redact(str(exc))}", file=sys.stderr)
                if not args.loop:
                    return 1
                time.sleep(5)
                continue
            for camera, reason in failures.items():
                print(f"The {camera} camera failed: {redact(reason)}", file=sys.stderr)
            name = f"{args.name} {take}" if args.loop else args.name
            session = upload(client, name=name, role=args.role, batter=args.batter,
                             side=None if "side" in failures or "side" not in cameras else cameras["side"][1],
                             behind=None if "behind" in failures or "behind" not in cameras else cameras["behind"][1])
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
        response = client.post("/api/sessions", data={"name": name, "role": role, "batter": batter}, files=files,
                               timeout=httpx.Timeout(60.0, read=300.0, write=900.0))
    finally:
        for handle in handles:
            handle.close()
    if response.status_code == 401:
        raise SystemExit("The server wants the coach key. Pass --key or set VANSH_COACH_KEY.")
    response.raise_for_status()
    return response.json()


# --------------------------------------------------------------------------
# machine


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


# --------------------------------------------------------------------------
# command line


def build_parser() -> argparse.ArgumentParser:
    env = os.environ.get
    parser = argparse.ArgumentParser(prog="python -m crickcoach.edge", description=__doc__.split("\n\n")[0])
    parser.add_argument("--env-file", help=f"settings file (default: ./{ENV_NAME} or ~/.vansh-edge/{ENV_NAME})")
    parser.add_argument("--server", default=env("VANSH_SERVER", "https://vansh.best"))
    parser.add_argument("--key", default=env("VANSH_COACH_KEY"))
    sub = parser.add_subparsers(dest="command", required=True)

    def cameras(command: argparse.ArgumentParser) -> None:
        command.add_argument("--side", default=env("VANSH_SIDE_URL"), help="address of the side-on camera (rtsp://…)")
        command.add_argument("--behind", default=env("VANSH_BEHIND_URL"), help="address of the camera behind the bowler")

    chk = sub.add_parser("check", help="read each camera for a few seconds and report how well VANSH can use it")
    cameras(chk)
    chk.add_argument("--seconds", type=float, default=5.0)
    chk.add_argument("--out", default="vansh-check", help="folder for a picture from each camera")
    chk.add_argument("--no-report", action="store_true", help="do not send the result to the coach screen")

    srv = sub.add_parser("run", help="record whenever the coach presses Start on the website, and send every take")
    cameras(srv)
    srv.add_argument("--take-seconds", type=int, default=int(env("VANSH_TAKE_SECONDS", "90")),
                     help="length of each take (30 to 600 s)")
    srv.add_argument("--home", default=str(HOME), help="where takes wait to upload")
    srv.add_argument("--max-gb", type=float, default=float(env("VANSH_EDGE_MAX_GB", "20")),
                     help="stop recording when this much is waiting to upload")
    srv.add_argument("--reencode", action="store_true", help="re-encode as H.264 here (needs a faster computer)")

    rec = sub.add_parser("record", help="record the cameras once and send the take")
    cameras(rec)
    rec.add_argument("--seconds", type=int, default=120, help="length of the take, up to 600")
    rec.add_argument("--name", default="Net take")
    rec.add_argument("--role", default="bowling", choices=["bowling", "batting"])
    rec.add_argument("--batter", default="right", choices=["right", "left"])
    rec.add_argument("--loop", action="store_true", help="keep recording takes until stopped")
    rec.add_argument("--reencode", action="store_true", help="re-encode as H.264 here")

    mac = sub.add_parser("machine", help="bowl the coach's queue on the machine at the nets")
    mac.add_argument("--machine", default=env("VANSH_MACHINE_ADDRESS"), required=not env("VANSH_MACHINE_ADDRESS"),
                     help="host:port of the bowling machine")
    mac.add_argument("--armed", action="store_true", help="required: the net is clear and the machine may bowl")
    mac.add_argument("--auto", action="store_true", help="bowl after the countdown without waiting for Enter")
    mac.add_argument("--countdown", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    early = argparse.ArgumentParser(add_help=False)
    early.add_argument("--env-file")
    known, _ = early.parse_known_args(argv)
    env_file = find_env_file(known.env_file)
    if env_file is not None:
        if not env_file.exists():
            print(f"No settings file at {env_file}.", file=sys.stderr)
            return 2
        for key, value in load_env_file(env_file).items():
            os.environ.setdefault(key, value)
    args = build_parser().parse_args(argv)
    if args.command == "check":
        client = None if args.no_report or not args.key else _client(args.server, args.key)
        return check(args, client)
    if args.command == "run":
        args.take_seconds = max(30, min(args.take_seconds, 600))
        return run(args)
    if args.command == "record":
        args.seconds = max(3, min(args.seconds, 600))
        return record(args)
    args.countdown = max(2, args.countdown)
    return machine(args)


if __name__ == "__main__":
    raise SystemExit(main())
