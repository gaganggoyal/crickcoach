"""Sessions and the bowling-machine queue, stored as JSON on disk."""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SESSION_ID = re.compile(r"^[a-f0-9]{12}$")
_DELIVERY_ID = re.compile(r"^d[0-9]{1,4}$")


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
        self.lock = threading.Lock()
        self.sessions.mkdir(parents=True, exist_ok=True)
        self.machine_dir.mkdir(parents=True, exist_ok=True)

    def create_session(self, *, name: str, role: str, status: str = "processing") -> dict:
        session = {
            "id": uuid.uuid4().hex[:12],
            "name": name.strip() or "Net session",
            "role": role,
            "created": now_iso(),
            "status": status,
            "error": "",
            "cameras": {"side": None, "behind": None},
            "calibration": {"side": None, "behind": None},
            "deliveries": [],
        }
        with self.lock:
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
        return json.loads(path.read_text())

    def save(self, session: dict) -> dict:
        _check_session_id(session["id"])
        with self.lock:
            _write_json(self.folder(session["id"]) / "session.json", session)
        return session

    def list_sessions(self) -> list[dict]:
        found = []
        if not self.sessions.exists():
            return found
        for folder in self.sessions.iterdir():
            path = folder / "session.json"
            if path.exists():
                session = json.loads(path.read_text())
                found.append(_summary(session))
        found.sort(key=lambda item: item["created"], reverse=True)
        return found

    def delete(self, session_id: str) -> None:
        folder = self.folder(session_id)
        if not folder.exists():
            raise KeyError(session_id)
        for child in folder.iterdir():
            child.unlink()
        folder.rmdir()

    def machine_items(self) -> list[dict]:
        if not self.machine_path.exists():
            return []
        payload = json.loads(self.machine_path.read_text())
        return list(payload.get("items", []))

    def queue_ball(self, item: dict) -> dict:
        stored = {
            "id": uuid.uuid4().hex[:8],
            "created": now_iso(),
            "pace_kph": item.get("pace_kph"),
            "line": item["line"],
            "length": item["length"],
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
        if not re.fullmatch(r"[a-f0-9]{8}", item_id):
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
        if not self.bowled_path.exists():
            return []
        return list(json.loads(self.bowled_path.read_text()).get("items", []))

    def add_bowled(self, record: dict) -> dict:
        with self.lock:
            items = self.bowled_items()
            items.append(record)
            _write_json(self.bowled_path, {"items": items[-80:]})
        return record


def check_delivery_id(delivery_id: str) -> str:
    if not _DELIVERY_ID.fullmatch(delivery_id):
        raise KeyError(delivery_id)
    return delivery_id


def _check_session_id(session_id: str) -> None:
    if not _SESSION_ID.fullmatch(session_id):
        raise KeyError(session_id)


def _summary(session: dict) -> dict:
    return {
        "id": session["id"],
        "name": session["name"],
        "role": session["role"],
        "created": session["created"],
        "status": session["status"],
        "error": session.get("error") or "",
        "deliveries": len(session.get("deliveries") or []),
        "cameras": {
            "side": bool(session.get("cameras", {}).get("side")),
            "behind": bool(session.get("cameras", {}).get("behind")),
        },
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2))
    temporary.replace(path)
