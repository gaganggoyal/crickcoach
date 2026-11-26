"""HTTP API and coach screen for a net session."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from crickcoach.cues import coach, parse_frames
from crickcoach.pitch import LENGTHS, LINES, describe_pitch
from crickcoach.samples import CATALOG, sample_payload
from crickcoach.store import Store, check_delivery_id
from crickcoach.video import cut_video, record_rtsp, synthesize_two_deliveries

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).resolve().parent / "web"
MAX_BYTES = 250 * 1024 * 1024
VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
}


class LiveBody(BaseModel):
    role: str
    frames: list[dict]


class SnapshotBody(BaseModel):
    name: str = "Live ball"
    role: str
    frames: list[dict]


class PoseBody(BaseModel):
    frames: list[dict]


class PitchBody(BaseModel):
    x: float
    y: float


class PaceBody(BaseModel):
    pace_kph: float | None = None


class MachineBody(BaseModel):
    pace_kph: float | None = None
    line: str
    length: str
    note: str = ""
    session_id: str | None = None
    delivery_id: str | None = None


class CameraBody(BaseModel):
    name: str = "Camera take"
    role: str = "bowling"
    side_url: str
    behind_url: str | None = None
    seconds: int = Field(default=20, ge=3, le=90)


def create_app(data_dir: Path | None = None) -> FastAPI:
    store = Store(data_dir or Path(os.environ.get("CRICKCOACH_DATA", ROOT / "data")))
    app = FastAPI(title="CrickCoach", version="0.1.0")
    app.state.store = store

    @app.get("/api/health")
    def health() -> dict:
        ffmpeg_ok = True
        detail = ""
        try:
            from crickcoach.video import ffmpeg_exe

            ffmpeg_exe()
        except Exception as exc:  # pragma: no cover - environment failure
            ffmpeg_ok = False
            detail = str(exc)
        return {"ok": True, "ffmpeg": ffmpeg_ok, "detail": detail}

    @app.get("/api/samples")
    def list_samples() -> dict:
        return {
            "samples": [
                {"id": key, "title": item["title"], "role": item["role"], "blurb": item["blurb"]}
                for key, item in CATALOG.items()
            ]
        }

    @app.get("/api/samples/{name}")
    def get_sample(name: str) -> dict:
        payload = sample_payload(name)
        if payload is None:
            raise HTTPException(404, "Unknown sample.")
        frames = parse_frames(payload["frames"])
        payload["cues"] = [cue.to_dict() for cue in coach(payload["role"], frames)]
        return payload

    @app.post("/api/live/cues")
    def live_cues(body: LiveBody) -> dict:
        role = _role(body.role)
        frames = parse_frames(body.frames)
        return {"role": role, "cues": [cue.to_dict() for cue in coach(role, frames)]}

    @app.post("/api/snapshots")
    def snapshot(body: SnapshotBody) -> dict:
        role = _role(body.role)
        frames = parse_frames(body.frames)
        if len(frames) < 2:
            raise HTTPException(400, "Hold the pose for a moment, then save.")
        cues = [cue.to_dict() for cue in coach(role, frames)]
        session = store.create_session(name=body.name, role=role, status="ready")
        session["deliveries"] = [
            _delivery(1, frames[0].t, frames[-1].t, cues=cues, camera="live")
        ]
        return store.save(session)

    @app.get("/api/sessions")
    def list_sessions() -> dict:
        return {"sessions": store.list_sessions()}

    @app.post("/api/sessions")
    async def create_session(
        name: str = Form("Net session"),
        role: str = Form("bowling"),
        side: UploadFile = File(...),
        behind: UploadFile | None = File(None),
    ) -> dict:
        role = _role(role)
        session = store.create_session(name=name, role=role, status="processing")
        try:
            side_name = await _save_upload(side, store.folder(session["id"]), "side")
            session["cameras"]["side"] = side_name
            if behind is not None and behind.filename:
                session["cameras"]["behind"] = await _save_upload(
                    behind, store.folder(session["id"]), "behind"
                )
            segments = cut_video(store.folder(session["id"]) / side_name)
            session["deliveries"] = [
                _delivery(index, segment.t0, segment.t1, note=segment.note)
                for index, segment in enumerate(segments, start=1)
            ]
            session["status"] = "ready"
        except HTTPException:
            store.delete(session["id"])
            raise
        except Exception as exc:
            session["status"] = "error"
            session["error"] = str(exc)
        return store.save(session)

    @app.post("/api/cameras")
    def record_cameras(body: CameraBody) -> dict:
        role = _role(body.role)
        side_url = _rtsp(body.side_url)
        behind_url = _rtsp(body.behind_url) if body.behind_url else None
        session = store.create_session(name=body.name, role=role, status="recording")
        session["cameras"]["side"] = "side.mp4"
        if behind_url:
            session["cameras"]["behind"] = "behind.mp4"
        store.save(session)
        thread = threading.Thread(
            target=_record_job,
            args=(store, session["id"], side_url, behind_url, body.seconds),
            daemon=True,
        )
        thread.start()
        return session

    @app.post("/api/demo/session")
    def demo_session() -> dict:
        session = store.create_session(name="Sample net clip", role="bowling", status="processing")
        path = store.folder(session["id"]) / "side.mp4"
        try:
            synthesize_two_deliveries(path)
            session["cameras"]["side"] = "side.mp4"
            segments = cut_video(path)
            session["deliveries"] = [
                _delivery(index, segment.t0, segment.t1, note=segment.note)
                for index, segment in enumerate(segments, start=1)
            ]
            session["status"] = "ready"
        except Exception as exc:
            session["status"] = "error"
            session["error"] = str(exc)
        return store.save(session)

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        return _session_or_404(store, session_id)

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str) -> dict:
        try:
            store.delete(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found.") from None
        return {"ok": True}

    @app.get("/api/sessions/{session_id}/media/{camera}")
    def media(session_id: str, camera: str) -> FileResponse:
        if camera not in {"side", "behind"}:
            raise HTTPException(404, "Unknown camera.")
        session = _session_or_404(store, session_id)
        filename = session["cameras"].get(camera)
        if not filename:
            raise HTTPException(404, "That camera has no video.")
        path = store.folder(session_id) / filename
        if not path.exists():
            raise HTTPException(404, "The video file is missing.")
        media_type = VIDEO_TYPES.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=media_type, filename=filename)

    @app.post("/api/sessions/{session_id}/deliveries/{delivery_id}/pose")
    def add_pose(session_id: str, delivery_id: str, body: PoseBody) -> dict:
        session = _session_or_404(store, session_id)
        delivery = _delivery_or_404(session, delivery_id)
        frames = parse_frames(body.frames)
        if len(frames) < 2:
            raise HTTPException(400, "Need at least two pose frames.")
        delivery["cues"] = [cue.to_dict() for cue in coach(session["role"], frames)]
        delivery["pose_note"] = ""
        if any(cue["code"] == "nobody" for cue in delivery["cues"]):
            delivery["pose_note"] = "No player was found in these frames."
        store.save(session)
        return delivery

    @app.post("/api/sessions/{session_id}/deliveries/{delivery_id}/pitch")
    def mark_pitch(session_id: str, delivery_id: str, body: PitchBody) -> dict:
        session = _session_or_404(store, session_id)
        delivery = _delivery_or_404(session, delivery_id)
        delivery["pitch"] = describe_pitch(body.x, body.y)
        store.save(session)
        return delivery

    @app.post("/api/sessions/{session_id}/deliveries/{delivery_id}/pace")
    def mark_pace(session_id: str, delivery_id: str, body: PaceBody) -> dict:
        session = _session_or_404(store, session_id)
        delivery = _delivery_or_404(session, delivery_id)
        delivery["pace_kph"] = _pace(body.pace_kph)
        store.save(session)
        return delivery

    @app.get("/api/machine")
    def machine() -> dict:
        return {"items": store.machine_items(), "lines": list(LINES), "lengths": list(LENGTHS)}

    @app.post("/api/machine")
    def queue_machine(body: MachineBody) -> dict:
        line = body.line.strip().lower()
        length = body.length.strip().lower()
        if line not in LINES or length not in LENGTHS:
            raise HTTPException(400, "Choose a line and a length from the lists.")
        item = store.queue_ball(
            {
                "pace_kph": _pace(body.pace_kph),
                "line": line,
                "length": length,
                "note": body.note.strip()[:200],
                "session_id": body.session_id,
                "delivery_id": body.delivery_id,
            }
        )
        return item

    @app.delete("/api/machine/{item_id}")
    def unqueue(item_id: str) -> dict:
        try:
            store.remove_ball(item_id)
        except KeyError:
            raise HTTPException(404, "That ball is not in the queue.") from None
        return {"ok": True}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    app.mount("/assets", StaticFiles(directory=WEB), name="assets")
    return app


def _record_job(store: Store, session_id: str, side_url: str, behind_url: str | None, seconds: int) -> None:
    session = store.get(session_id)
    folder = store.folder(session_id)
    try:
        record_rtsp(side_url, folder / "side.mp4", seconds)
        if behind_url:
            record_rtsp(behind_url, folder / "behind.mp4", seconds)
        session["status"] = "processing"
        store.save(session)
        segments = cut_video(folder / "side.mp4")
        session = store.get(session_id)
        session["deliveries"] = [
            _delivery(index, segment.t0, segment.t1, note=segment.note)
            for index, segment in enumerate(segments, start=1)
        ]
        session["status"] = "ready"
        session["error"] = ""
    except Exception as exc:
        session = store.get(session_id)
        session["status"] = "error"
        session["error"] = str(exc)
    store.save(session)


def _delivery(
    index: int,
    t0: float,
    t1: float,
    *,
    cues: list | None = None,
    note: str = "",
    camera: str = "side",
) -> dict:
    return {
        "id": f"d{index}",
        "index": index,
        "t0": t0,
        "t1": t1,
        "camera": camera,
        "cut_note": note,
        "cues": cues or [],
        "pitch": None,
        "pace_kph": None,
        "pose_note": "",
    }


def _role(role: str) -> str:
    role = (role or "").strip().lower()
    if role not in {"bowling", "batting"}:
        raise HTTPException(400, "Role must be bowling or batting.")
    return role


def _rtsp(url: str) -> str:
    cleaned = (url or "").strip()
    parts = urlsplit(cleaned)
    if parts.scheme not in {"rtsp", "rtsps"} or not parts.hostname:
        raise HTTPException(400, "Camera address must start with rtsp:// and include a host.")
    return cleaned


def _pace(value: float | None) -> float | None:
    if value is None:
        return None
    if value < 40 or value > 180:
        raise HTTPException(400, "Pace must be between 40 and 180 km/h.")
    return round(float(value), 1)


def _session_or_404(store: Store, session_id: str) -> dict:
    try:
        return store.get(session_id)
    except KeyError:
        raise HTTPException(404, "Session not found.") from None


def _delivery_or_404(session: dict, delivery_id: str) -> dict:
    try:
        check_delivery_id(delivery_id)
    except KeyError:
        raise HTTPException(404, "Delivery not found.") from None
    for delivery in session.get("deliveries") or []:
        if delivery["id"] == delivery_id:
            return delivery
    raise HTTPException(404, "Delivery not found.")


async def _save_upload(upload: UploadFile, folder: Path, stem: str) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in VIDEO_TYPES:
        suffix = ".mp4"
    content_type = (upload.content_type or "").lower()
    if content_type.startswith("image/"):
        raise HTTPException(400, "Upload a video from the side camera.")
    destination = folder / f"{stem}{suffix}"
    total = 0
    try:
        with destination.open("wb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise HTTPException(413, "Video is over 250 MB.")
                handle.write(chunk)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    if total < 32:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, "That file is empty.")
    return destination.name


app = create_app()
