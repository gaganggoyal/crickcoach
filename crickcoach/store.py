"""Sessions, the lane calibration and the bowling-machine queue, as JSON on disk.

Every change to a session goes through update(), which holds that session's
lock while it reads, changes and writes, so a background tracking job and a
coach clicking outcomes never overwrite each other.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SESSION_ID = re.compile(r"^[a-f0-9]{12}$")
_DELIVERY_ID = re.compile(r"^d[0-9]{1,4}$")
_BALL_ID = re.compile(r"^[a-f0-9]{8}$")
_CERTIFICATE_ID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{8}$")
ACADEMY_FIELDS = ("name", "city", "coach", "role")
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
NETS_EMPTY = {"version": 0, "recording": None, "recent": [], "edge": None, "check": None}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.sessions = self.root / "sessions"
        self.machine_dir = self.root / "machine"
        self.machine_path = self.machine_dir / "queue.json"
        self.spool_path = self.machine_dir / "spool.jsonl"
        self.bowled_path = self.machine_dir / "bowled.json"
        self.requests_path = self.machine_dir / "requests.json"
        self.lane_path = self.root / "lane.json"
        self.nets_path = self.root / "nets.json"
        self.certificates_dir = self.root / "certificates"
        self.academy_path = self.root / "academy.json"
        self.logo_path = self.root / "academy-logo.png"
        self.lock = threading.RLock()
        self._session_locks: dict[str, threading.Lock] = {}
        self.sessions.mkdir(parents=True, exist_ok=True)
        self.machine_dir.mkdir(parents=True, exist_ok=True)

    # -- sessions ---------------------------------------------------------

    def _session_lock(self, session_id: str) -> threading.Lock:
        with self.lock:
            return self._session_locks.setdefault(session_id, threading.Lock())

    def create_session(self, *, name: str, role: str, status: str = "processing") -> dict:
        session = {
            "id": uuid.uuid4().hex[:12],
            "name": (name or "").strip()[:80] or "Net session",
            "role": role,
            "created": now_iso(),
            "status": status,
            "error": "",
            "batter": "right",
            "cameras": {"side": None, "behind": None},
            "calibration": self.get_lane(),
            "clock_offset": 0.0,
            "found_by": None,
            "job": None,
            "series": None,
            "video_removed": None,
            "deliveries": [],
        }
        folder = self.sessions / session["id"]
        folder.mkdir(parents=True, exist_ok=True)
        _write_json(folder / "session.json", session)
        return session

    def folder(self, session_id: str) -> Path:
        _check_session_id(session_id)
        return self.sessions / session_id

    def get(self, session_id: str) -> dict:
        path = self.folder(session_id) / "session.json"
        if not path.exists():
            raise KeyError(session_id)
        return upgrade(json.loads(path.read_text()))

    def save(self, session: dict) -> dict:
        _check_session_id(session["id"])
        with self._session_lock(session["id"]):
            _write_json(self.folder(session["id"]) / "session.json", session)
        return session

    def update(self, session_id: str, change: Callable[[dict], Any]) -> tuple[dict, Any]:
        """Read, change and write one session under its lock. Returns (session, what change returned)."""
        with self._session_lock(session_id):
            session = self.get(session_id)
            result = change(session)
            _write_json(self.folder(session_id) / "session.json", session)
        return session, result

    def list_sessions(self) -> list[dict]:
        found = []
        if not self.sessions.exists():
            return found
        for folder in self.sessions.iterdir():
            path = folder / "session.json"
            if path.exists():
                try:
                    found.append(_summary(json.loads(path.read_text())))
                except (json.JSONDecodeError, KeyError):
                    continue
        found.sort(key=lambda item: item["created"], reverse=True)
        return found

    def all_deliveries(self) -> list[dict]:
        found = []
        for summary in self.list_sessions():
            try:
                session = self.get(summary["id"])
            except KeyError:
                continue
            for delivery in session.get("deliveries") or []:
                found.append({**delivery, "session_id": session["id"], "batter": session.get("batter", "right"),
                              "series": session.get("series")})
        return found

    def delete(self, session_id: str) -> None:
        folder = self.folder(session_id)
        if not folder.exists():
            raise KeyError(session_id)
        with self._session_lock(session_id):
            shutil.rmtree(folder)

    def free_video(self, target_bytes: int, *, protect_s: float = 2 * 86400) -> list[str]:
        """Remove the oldest sessions' video until sessions fit in target_bytes.

        Like a CCTV recorder, VANSH keeps the newest video and lets the oldest
        go. The measurements, outcomes and cues stay. Sessions from the last
        two days, and sessions being worked on, are never touched.
        """
        freed: list[str] = []
        total = self.disk_bytes()
        cutoff = time.time() - protect_s
        for summary in sorted(self.list_sessions(), key=lambda item: item["created"]):
            if total <= target_bytes:
                break
            try:
                created = datetime.fromisoformat(summary["created"]).timestamp()
            except ValueError:
                continue
            if created > cutoff or summary.get("job"):
                continue
            folder = self.folder(summary["id"])
            videos = [path for path in folder.iterdir() if path.suffix.lower() in VIDEO_SUFFIXES]
            if not videos:
                continue
            size = sum(path.stat().st_size for path in videos)

            def change(session: dict, videos=videos) -> None:
                for path in videos:
                    path.unlink(missing_ok=True)
                session["video_removed"] = now_iso()

            try:
                self.update(summary["id"], change)
            except KeyError:
                continue
            total -= size
            freed.append(summary["id"])
        return freed

    def disk_bytes(self) -> int:
        total = 0
        for path in self.sessions.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    # -- the lane -----------------------------------------------------------

    def get_lane(self) -> dict:
        if not self.lane_path.exists():
            return {"side": None, "behind": None}
        saved = json.loads(self.lane_path.read_text())
        return {"side": saved.get("side"), "behind": saved.get("behind")}

    def save_lane_camera(self, camera: str, calibration: dict) -> dict:
        with self.lock:
            lane = self.get_lane()
            lane[camera] = calibration
            _write_json(self.lane_path, lane)
            return lane

    # -- the machine --------------------------------------------------------

    def machine_items(self) -> list[dict]:
        return _read_items(self.machine_path)

    def queue_ball(self, item: dict) -> dict:
        stored = {
            "id": uuid.uuid4().hex[:8],
            "created": now_iso(),
            "pace_kph": item.get("pace_kph"),
            "line": item["line"],
            "length": item["length"],
            "length_m": item.get("length_m"),
            "offset_m": item.get("offset_m"),
            "batter": item.get("batter") or "right",
            "note": item.get("note") or "",
            "session_id": item.get("session_id"),
            "delivery_id": item.get("delivery_id"),
        }
        with self.lock:
            items = self.machine_items()
            items.append(stored)
            _write_json(self.machine_path, {"items": items})
            with self.spool_path.open("a") as spool:
                spool.write(json.dumps(stored) + "\n")
        return stored

    def remove_ball(self, item_id: str) -> None:
        if not _BALL_ID.fullmatch(item_id):
            raise KeyError(item_id)
        with self.lock:
            items = self.machine_items()
            kept = [item for item in items if item["id"] != item_id]
            if len(kept) == len(items):
                raise KeyError(item_id)
            _write_json(self.machine_path, {"items": kept})

    def take_next_ball(self) -> dict:
        with self.lock:
            items = self.machine_items()
            if not items:
                raise KeyError("empty")
            nxt = items.pop(0)
            _write_json(self.machine_path, {"items": items})
            return nxt

    def put_front(self, item: dict) -> None:
        with self.lock:
            items = self.machine_items()
            items.insert(0, item)
            _write_json(self.machine_path, {"items": items})

    def bowled_items(self) -> list[dict]:
        return _read_items(self.bowled_path)

    def add_bowled(self, record: dict) -> dict:
        with self.lock:
            items = self.bowled_items()
            items.append(record)
            _write_json(self.bowled_path, {"items": items[-80:]})
        return record

    def requests(self) -> list[dict]:
        return _read_items(self.requests_path)

    def add_request(self, item: dict) -> dict:
        with self.lock:
            items = self.requests()
            items.append({**item, "requested": now_iso()})
            _write_json(self.requests_path, {"items": items})
        return item

    def finish_request(self, item_id: str) -> dict:
        if not _BALL_ID.fullmatch(item_id):
            raise KeyError(item_id)
        with self.lock:
            items = self.requests()
            for index, item in enumerate(items):
                if item["id"] == item_id:
                    items.pop(index)
                    _write_json(self.requests_path, {"items": items})
                    return item
        raise KeyError(item_id)

    # -- the nets: a recording at the nets and the bridge that makes it -----

    def nets(self) -> dict:
        if not self.nets_path.exists():
            return json.loads(json.dumps(NETS_EMPTY))
        try:
            saved = json.loads(self.nets_path.read_text())
        except json.JSONDecodeError:
            saved = {}
        return {**json.loads(json.dumps(NETS_EMPTY)), **saved}

    def update_nets(self, change: Callable[[dict], Any]) -> tuple[dict, Any]:
        with self.lock:
            nets = self.nets()
            result = change(nets)
            _write_json(self.nets_path, nets)
        return nets, result

    # -- the academy and its certificates -------------------------------------

    def academy(self) -> dict:
        saved: dict = {}
        if self.academy_path.exists():
            try:
                saved = json.loads(self.academy_path.read_text())
            except json.JSONDecodeError:
                saved = {}
        academy = {field: str(saved.get(field) or "") for field in ACADEMY_FIELDS}
        academy["logo"] = self.logo_path.exists()
        return academy

    def save_academy(self, academy: dict) -> dict:
        with self.lock:
            _write_json(self.academy_path, {field: academy.get(field) or "" for field in ACADEMY_FIELDS})
        return self.academy()

    def save_logo(self, data: bytes | None) -> None:
        with self.lock:
            if data is None:
                self.logo_path.unlink(missing_ok=True)
                return
            temporary = self.logo_path.with_name(f".logo.{uuid.uuid4().hex[:6]}.tmp")
            temporary.write_bytes(data)
            temporary.replace(self.logo_path)

    def certificate_folder(self, code: str) -> Path:
        if not _CERTIFICATE_ID.fullmatch(code or ""):
            raise KeyError(code)
        return self.certificates_dir / code

    def certificate(self, code: str) -> dict:
        path = self.certificate_folder(code) / "certificate.json"
        if not path.exists():
            raise KeyError(code)
        return json.loads(path.read_text())

    def add_certificate(self, record: dict, new_code: Callable[[], str]) -> dict:
        """Store a new certificate under a code no other certificate has."""
        with self.lock:
            for _ in range(20):
                code = new_code()
                folder = self.certificate_folder(code)
                if not folder.exists():
                    break
            else:  # pragma: no cover - 20 clashes in a space of 10^12
                raise RuntimeError("Could not find a free certificate ID.")
            folder.mkdir(parents=True)
            record = {**record, "id": code}
            _write_json(folder / "certificate.json", record)
        return record

    def update_certificate(self, code: str, change: Callable[[dict], Any]) -> dict:
        with self.lock:
            record = self.certificate(code)
            change(record)
            _write_json(self.certificate_folder(code) / "certificate.json", record)
        return record

    def list_certificates(self) -> list[dict]:
        found = []
        if not self.certificates_dir.exists():
            return found
        for folder in self.certificates_dir.iterdir():
            path = folder / "certificate.json"
            if folder.is_dir() and path.exists():
                try:
                    found.append(json.loads(path.read_text()))
                except json.JSONDecodeError:
                    continue
        found.sort(key=lambda item: item.get("issued", ""), reverse=True)
        return found

    def delete_certificate(self, code: str) -> None:
        folder = self.certificate_folder(code)
        with self.lock:
            if not folder.exists():
                raise KeyError(code)
            shutil.rmtree(folder)

    # -- secrets ------------------------------------------------------------

    def secret(self) -> bytes:
        path = self.root / "secret.key"
        with self.lock:
            if not path.exists():
                path.write_text(secrets.token_hex(32))
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            return bytes.fromhex(path.read_text().strip())


def upgrade(session: dict) -> dict:
    """Read sessions written by the first version of VANSH.

    That version kept pitch marks as 0..1 of a 3.05 m by 20.12 m pitch, with
    y = 0 at the bowler's end, and called a ball on the stumps "hit".
    """
    from crickcoach.pitch import describe

    session.setdefault("batter", "right")
    session.setdefault("clock_offset", 0.0)
    session.setdefault("job", None)
    session.setdefault("series", None)
    session.setdefault("video_removed", None)
    for delivery in session.get("deliveries") or []:
        pitch = delivery.get("pitch")
        if pitch and "length_m" not in pitch and pitch.get("y") is not None:
            length = (1.0 - float(pitch["y"])) * 20.12
            offset = None if pitch.get("x") is None else (float(pitch["x"]) - 0.5) * 3.05
            delivery["pitch"] = describe(length, offset, session["batter"])
        if delivery.get("outcome") == "hit":
            delivery["outcome"] = "bowled"
        delivery.setdefault("tracks", {})
        delivery.setdefault("stumps", None)
        delivery.setdefault("confidence", None)
    return session


def check_delivery_id(delivery_id: str) -> str:
    if not _DELIVERY_ID.fullmatch(delivery_id):
        raise KeyError(delivery_id)
    return delivery_id


def _check_session_id(session_id: str) -> None:
    if not _SESSION_ID.fullmatch(session_id or ""):
        raise KeyError(session_id)


def _summary(session: dict) -> dict:
    deliveries = session.get("deliveries") or []
    return {
        "id": session["id"],
        "name": session["name"],
        "role": session["role"],
        "created": session["created"],
        "status": session["status"],
        "error": session.get("error") or "",
        "deliveries": len(deliveries),
        "tracked": sum(1 for delivery in deliveries if delivery.get("pitch")),
        "job": session.get("job"),
        "batter": session.get("batter", "right"),
        "series": session.get("series"),
        "video_removed": session.get("video_removed"),
        "cameras": {
            "side": bool(session.get("cameras", {}).get("side")),
            "behind": bool(session.get("cameras", {}).get("behind")),
        },
    }


def _read_items(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return list(json.loads(path.read_text()).get("items", []))
    except json.JSONDecodeError:
        return []


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:6]}.tmp")
    temporary.write_text(json.dumps(payload, indent=2))
    temporary.replace(path)
