"""HTTP API and coach screen for a net session."""

from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from crickcoach import NAME, ORIGIN, __version__
from crickcoach.auth import COOKIE, DAYS, Auth
from crickcoach.cues import coach, parse_frames
from crickcoach.drill import drill_plan
from crickcoach.footage import copy_clip
from crickcoach.geometry import ground_plane, side_plane
from crickcoach.machine_link import MachineLink, command_for, listen_radar
from crickcoach.pitch import LENGTHS, LINES, clean_line, describe, target_for
from crickcoach.process import Busy, recover, refresh, submit, track_one
from crickcoach.samples import CATALOG, sample_payload
from crickcoach.store import Store, check_delivery_id, now_iso

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).resolve().parent / "web"
MAX_BYTES = 600 * 1024 * 1024
VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
}
OUTCOMES = ("left", "defended", "attacked", "missed", "edged", "bowled")
PUBLIC_API = {"/api/health", "/api/me", "/api/login", "/api/logout", "/api/samples", "/api/live/cues"}
SIDE_KEYS = ("bowler_base", "bowler_top", "batter_base", "batter_top")


class LiveBody(BaseModel):
    role: str
    frames: list[dict] = Field(max_length=240)


class SnapshotBody(BaseModel):
    name: str = "Live ball"
    role: str
    frames: list[dict] = Field(max_length=240)


class PoseBody(BaseModel):
    frames: list[dict] = Field(max_length=240)


class PitchBody(BaseModel):
    length_m: float | None = None
    offset_m: float | None = None
    clear: bool = False


class PaceBody(BaseModel):
    pace_kph: float | None = None
    source: str = "manual"


class MachineBody(BaseModel):
    pace_kph: float | None = None
    line: str
    length: str
    note: str = ""
    batter: str = "right"
    session_id: str | None = None
    delivery_id: str | None = None


class CameraBody(BaseModel):
    name: str = "Camera take"
    role: str = "bowling"
    side_url: str
    behind_url: str | None = None
    seconds: int = Field(default=20, ge=3, le=90)


class CalibrationBody(BaseModel):
    kind: str
    bowler_base: dict | None = None
    bowler_top: dict | None = None
    batter_base: dict | None = None
    batter_top: dict | None = None
    corners: list[dict] | None = None
    distance_m: float | None = Field(default=None, ge=2, le=60)


class OutcomeBody(BaseModel):
    outcome: str | None = None


class RadarBody(BaseModel):
    pace_kph: float


class DrillBody(BaseModel):
    session_id: str | None = None
    count: int = Field(default=6, ge=1, le=18)


class LoginBody(BaseModel):
    key: str = Field(max_length=200)


class SessionPatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    batter: str | None = None
    clock_offset: float | None = Field(default=None, ge=-60, le=60)


class DeliveryBody(BaseModel):
    t0: float = Field(ge=0)
    t1: float = Field(ge=0)


class DoneBody(BaseModel):
    ok: bool = True
    error: str = ""


def create_app(data_dir: Path | None = None) -> FastAPI:
    store = Store(data_dir or Path(_setting("VANSH_DATA", "CRICKCOACH_DATA", default=str(ROOT / "data"))))
    recover(store)
    auth = Auth(_setting("VANSH_COACH_KEY"), store.secret())
    app = FastAPI(title=NAME, version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.auth = auth
    app.state.machine = MachineLink(_setting("VANSH_MACHINE", "CRICKCOACH_MACHINE"))
    app.state.radar = {"pace_kph": None, "at": None}
    app.state.wake = threading.Condition()
    max_bytes = int(float(_setting("VANSH_MAX_GB", default="20")) * 1024**3)
    allow_rtsp = _flag("VANSH_ALLOW_RTSP", default=not auth.required)
    rtsp_private = _flag("VANSH_RTSP_PRIVATE", default=not auth.required)
    radar_port = _setting("VANSH_RADAR_PORT", "CRICKCOACH_RADAR_PORT")
    if radar_port and radar_port.isdigit():
        threading.Thread(
            target=listen_radar,
            args=(int(radar_port), app.state.radar, threading.Event()),
            daemon=True,
        ).start()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and auth.required and not _public(path) and not _signed_in(request, auth):
            return JSONResponse({"detail": "Sign in as the coach first."}, status_code=401)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        if path == "/" or path.endswith(".html"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'wasm-unsafe-eval'; "
                "connect-src 'self' https://cdn.jsdelivr.net https://storage.googleapis.com; "
                "img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
                "worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            )
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # -- who is here ------------------------------------------------------

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
        return {
            "ok": True,
            "name": NAME,
            "origin": ORIGIN,
            "version": __version__,
            "ffmpeg": ffmpeg_ok,
            "detail": detail,
            "machine": app.state.machine.mode,
            "auth_required": auth.required,
        }

    @app.get("/api/me")
    def me(request: Request) -> dict:
        return {"coach": _signed_in(request, auth), "auth_required": auth.required, "rtsp": allow_rtsp}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request) -> Response:
        if not auth.required:
            return JSONResponse({"coach": True})
        client = request.client.host if request.client else "unknown"
        if not auth.allow_attempt(client):
            raise HTTPException(429, "Too many tries. Wait ten minutes.")
        if not auth.check_key(body.key):
            time.sleep(0.4)
            raise HTTPException(401, "That is not the coach key.")
        auth.forget(client)
        response = JSONResponse({"coach": True})
        response.set_cookie(
            COOKIE,
            auth.token(),
            max_age=DAYS * 86400,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
        )
        return response

    @app.post("/api/logout")
    def logout() -> Response:
        response = JSONResponse({"coach": False})
        response.delete_cookie(COOKIE, path="/")
        return response

    # -- the lane: samples and live cues are open to anyone -------------------

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

    # -- sessions -------------------------------------------------------------

    @app.post("/api/snapshots")
    def snapshot(body: SnapshotBody) -> dict:
        role = _role(body.role)
        frames = parse_frames(body.frames)
        if len(frames) < 2:
            raise HTTPException(400, "Hold the pose for a moment, then save.")
        cues = [cue.to_dict() for cue in coach(role, frames)]
        session = store.create_session(name=body.name, role=role, status="ready")
        delivery = _blank_delivery(1, frames[0].t, frames[-1].t, camera="live")
        delivery["cues"] = cues
        session["deliveries"] = [delivery]
        return store.save(session)

    @app.get("/api/sessions")
    def list_sessions() -> dict:
        return {"sessions": store.list_sessions(), "disk_gb": round(store.disk_bytes() / 1024**3, 2)}

    @app.post("/api/sessions")
    async def create_session(
        name: str = Form("Net session"),
        role: str = Form("bowling"),
        batter: str = Form("right"),
        side: UploadFile | None = File(None),
        behind: UploadFile | None = File(None),
    ) -> dict:
        role = _role(role)
        if (side is None or not side.filename) and (behind is None or not behind.filename):
            raise HTTPException(400, "Upload a video from at least one camera.")
        if store.disk_bytes() > max_bytes:
            raise HTTPException(507, "The server is full. Delete old sessions first.")
        session = store.create_session(name=name, role=role, status="processing")
        try:
            for camera, upload in (("side", side), ("behind", behind)):
                if upload is not None and upload.filename:
                    session["cameras"][camera] = await _save_upload(upload, store.folder(session["id"]), camera)
        except HTTPException:
            store.delete(session["id"])
            raise
        session["batter"] = _batter(batter)
        store.save(session)
        submit(store, session["id"], "find")
        return store.get(session["id"])

    @app.post("/api/cameras")
    def record_cameras(body: CameraBody) -> dict:
        if not allow_rtsp:
            raise HTTPException(
                403,
                "This server does not reach into camera networks. Run the edge recorder at the nets "
                "(python -m crickcoach.edge) to send recordings here.",
            )
        role = _role(body.role)
        side_url = _rtsp(body.side_url, rtsp_private)
        behind_url = _rtsp(body.behind_url, rtsp_private) if body.behind_url else None
        session = store.create_session(name=body.name, role=role, status="recording")
        session["cameras"]["side"] = "side.mp4"
        if behind_url:
            session["cameras"]["behind"] = "behind.mp4"
        store.save(session)
        threading.Thread(
            target=_record_job,
            args=(store, session["id"], side_url, behind_url, body.seconds),
            daemon=True,
        ).start()
        return session

    @app.post("/api/demo/{name}")
    def demo(name: str) -> dict:
        reels = {
            "session": ("side_two_balls", "VANSH raw side session", ("side",)),
            "tracked": ("side_good_length", "VANSH raw side ball", ("side",)),
            "behind": ("behind_off_good", "VANSH raw behind ball", ("behind",)),
            "pair": ("pair", "VANSH raw two-camera ball", ("side", "behind")),
        }
        if name not in reels:
            raise HTTPException(404, "Unknown reel.")
        clip_name, title, cameras = reels[name]
        session = store.create_session(name=title, role="bowling", status="processing")
        try:
            for camera in cameras:
                item = copy_clip(f"{clip_name}_{camera}" if clip_name == "pair" else clip_name,
                                 store.folder(session["id"]) / f"{camera}.mp4")
                session["cameras"][camera] = f"{camera}.mp4"
                session["calibration"][camera] = item.get("calibration")
        except FileNotFoundError as exc:
            store.delete(session["id"])
            raise HTTPException(404, str(exc)) from exc
        store.save(session)
        submit(store, session["id"], "find")
        return store.get(session["id"])

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        return _session_or_404(store, session_id)

    @app.patch("/api/sessions/{session_id}")
    def patch_session(session_id: str, body: SessionPatch) -> dict:
        def change(session: dict) -> None:
            if body.name is not None:
                session["name"] = body.name.strip() or session["name"]
            if body.batter is not None:
                session["batter"] = _batter(body.batter)
                for delivery in session.get("deliveries") or []:
                    if delivery.get("pitch_source") == "manual" and delivery.get("pitch"):
                        pitch = delivery["pitch"]
                        delivery["pitch"] = describe(pitch["length_m"], pitch.get("offset_m"), session["batter"])
                    refresh(delivery, session["batter"])
            if body.clock_offset is not None:
                session["clock_offset"] = round(body.clock_offset, 3)
                session["clock_note"] = "set by the coach"

        return _update_or_404(store, session_id, change)[0]

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
        return FileResponse(path, media_type=media_type)

    @app.post("/api/sessions/{session_id}/calibration/{camera}")
    def calibrate(session_id: str, camera: str, body: CalibrationBody) -> dict:
        if camera not in {"side", "behind"}:
            raise HTTPException(404, "Unknown camera.")
        saved = _calibration(camera, body)

        def change(session: dict) -> None:
            session.setdefault("calibration", {"side": None, "behind": None})
            session["calibration"][camera] = saved

        session, _ = _update_or_404(store, session_id, change)
        store.save_lane_camera(camera, saved)
        return session

    @app.post("/api/sessions/{session_id}/find")
    def find_again(session_id: str) -> dict:
        session = _session_or_404(store, session_id)
        if session.get("job"):
            raise HTTPException(409, "This session is already being worked on.")
        submit(store, session_id, "find")
        return store.get(session_id)

    @app.post("/api/sessions/{session_id}/track")
    def track_all(session_id: str) -> dict:
        session = _session_or_404(store, session_id)
        if session.get("job"):
            raise HTTPException(409, "This session is already being worked on.")
        if not session.get("deliveries"):
            raise HTTPException(400, "Find the balls first.")
        submit(store, session_id, "track")
        return store.get(session_id)

    @app.get("/api/lane")
    def lane() -> dict:
        return store.get_lane()

    # -- one ball ---------------------------------------------------------------

    @app.post("/api/sessions/{session_id}/deliveries")
    def add_delivery(session_id: str, body: DeliveryBody) -> dict:
        if body.t1 <= body.t0 or body.t1 - body.t0 > 20:
            raise HTTPException(400, "A ball window runs forwards and lasts under 20 seconds.")

        def change(session: dict) -> dict:
            deliveries = session.setdefault("deliveries", [])
            index = max((item["index"] for item in deliveries), default=0) + 1
            camera = "side" if (session.get("cameras") or {}).get("side") else "behind"
            delivery = _blank_delivery(index, body.t0, body.t1, camera=camera)
            delivery["cut_note"] = "Marked by the coach."
            deliveries.append(delivery)
            deliveries.sort(key=lambda item: item["t0"])
            return delivery

        return _update_or_404(store, session_id, change)[1]

    @app.delete("/api/sessions/{session_id}/deliveries/{delivery_id}")
    def remove_delivery(session_id: str, delivery_id: str) -> dict:
        _check_delivery(delivery_id)

        def change(session: dict) -> None:
            before = len(session.get("deliveries") or [])
            session["deliveries"] = [item for item in session.get("deliveries") or [] if item["id"] != delivery_id]
            if len(session["deliveries"]) == before:
                raise KeyError(delivery_id)

        return _update_or_404(store, session_id, change)[0]

    @app.post("/api/sessions/{session_id}/deliveries/{delivery_id}/pose")
    def add_pose(session_id: str, delivery_id: str, body: PoseBody) -> dict:
        frames = parse_frames(body.frames)
        if len(frames) < 2:
            raise HTTPException(400, "Need at least two pose frames.")

        def change(session: dict) -> dict:
            delivery = _find_delivery(session, delivery_id)
            delivery["cues"] = [cue.to_dict() for cue in coach(session["role"], frames)]
            delivery["pose_note"] = ""
            if any(cue["code"] == "nobody" for cue in delivery["cues"]):
                delivery["pose_note"] = "No player was found in these frames."
            return delivery

        return _update_or_404(store, session_id, change)[1]

    @app.post("/api/sessions/{session_id}/deliveries/{delivery_id}/pitch")
    def mark_pitch(session_id: str, delivery_id: str, body: PitchBody) -> dict:
        if not body.clear:
            if body.length_m is None or not -1 <= body.length_m <= 21:
                raise HTTPException(400, "Mark a length between the stumps.")
            if body.offset_m is not None and not -3 <= body.offset_m <= 3:
                raise HTTPException(400, "Mark a line on the pitch.")

        def change(session: dict) -> dict:
            delivery = _find_delivery(session, delivery_id)
            if body.clear:
                delivery["pitch_source"] = None
                refresh(delivery, session.get("batter", "right"))
            else:
                delivery["pitch"] = describe(body.length_m, body.offset_m, session.get("batter", "right"))
                delivery["pitch_source"] = "manual"
                delivery["confidence"] = "ok"
            return delivery

        return _update_or_404(store, session_id, change)[1]

    @app.post("/api/sessions/{session_id}/deliveries/{delivery_id}/pace")
    def mark_pace(session_id: str, delivery_id: str, body: PaceBody) -> dict:
        pace = _pace(body.pace_kph)
        source = "radar" if body.source == "radar" else "manual"

        def change(session: dict) -> dict:
            delivery = _find_delivery(session, delivery_id)
            if pace is None:
                delivery["pace_source"] = None
                refresh(delivery, session.get("batter", "right"))
            else:
                delivery["pace_kph"] = pace
                delivery["pace_source"] = source
            return delivery

        return _update_or_404(store, session_id, change)[1]

    @app.post("/api/sessions/{session_id}/deliveries/{delivery_id}/track")
    def track_delivery(session_id: str, delivery_id: str, camera: str = "side") -> dict:
        if camera not in {"side", "behind"}:
            raise HTTPException(400, "Camera must be side or behind.")
        _check_delivery(delivery_id)
        session = _session_or_404(store, session_id)
        _find_delivery_or_404(session, delivery_id)
        if not (session.get("calibration") or {}).get(camera):
            raise HTTPException(400, "Calibrate this camera first.")
        if not (session.get("cameras") or {}).get(camera):
            raise HTTPException(404, "That camera has no video.")
        try:
            return track_one(store, session_id, delivery_id, camera)
        except Busy as exc:
            raise HTTPException(503, str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except KeyError:
            raise HTTPException(404, "Delivery not found.") from None

    @app.post("/api/sessions/{session_id}/deliveries/{delivery_id}/outcome")
    def mark_outcome(session_id: str, delivery_id: str, body: OutcomeBody) -> dict:
        outcome = (body.outcome or "").strip().lower() or None
        if outcome == "hit":
            outcome = "bowled"
        if outcome is not None and outcome not in OUTCOMES:
            raise HTTPException(400, "Outcome must be left, defended, attacked, missed, edged, or bowled.")

        def change(session: dict) -> dict:
            delivery = _find_delivery(session, delivery_id)
            delivery["outcome"] = outcome
            return delivery

        return _update_or_404(store, session_id, change)[1]

    # -- radar, progress and the machine ----------------------------------------

    @app.get("/api/radar")
    def radar() -> dict:
        return dict(app.state.radar)

    @app.post("/api/radar")
    def set_radar(body: RadarBody) -> dict:
        app.state.radar.update(pace_kph=_pace(body.pace_kph), at=now_iso())
        return dict(app.state.radar)

    @app.get("/api/progress")
    def progress() -> dict:
        deliveries = store.all_deliveries()
        plan = drill_plan(deliveries, count=6)
        paces = [delivery["pace_kph"] for delivery in deliveries if delivery.get("pace_kph")]
        counts: dict[str, int] = {}
        lengths: dict[str, int] = {}
        for delivery in deliveries:
            outcome = delivery.get("outcome")
            if outcome:
                counts[outcome] = counts.get(outcome, 0) + 1
            length = (delivery.get("pitch") or {}).get("length")
            if length:
                lengths[length] = lengths.get(length, 0) + 1
        return {
            "balls": len(deliveries),
            "outcomes": counts,
            "lengths": lengths,
            "mean_pace_kph": round(sum(paces) / len(paces), 1) if paces else None,
            "top_pace_kph": max(paces) if paces else None,
            "next_drill": plan["reason"],
        }

    @app.post("/api/drill")
    def make_drill(body: DrillBody) -> dict:
        batter = "right"
        if body.session_id:
            session = _session_or_404(store, body.session_id)
            deliveries = session.get("deliveries") or []
            batter = session.get("batter", "right")
        else:
            deliveries = store.all_deliveries()
        plan = drill_plan(deliveries, count=body.count)
        queued = [
            store.queue_ball({**ball, "batter": batter, "session_id": body.session_id, "delivery_id": None})
            for ball in plan["balls"]
        ]
        _wake(app)
        return {"reason": plan["reason"], "items": queued}

    @app.get("/api/machine")
    def machine() -> dict:
        return {
            "items": store.machine_items(),
            "bowled": store.bowled_items(),
            "requests": store.requests(),
            "mode": app.state.machine.mode,
            "radar": dict(app.state.radar),
            "lines": list(LINES),
            "lengths": list(LENGTHS),
        }

    @app.post("/api/machine")
    def queue_machine(body: MachineBody) -> dict:
        line = clean_line(body.line)
        length = body.length.strip().lower()
        if line not in LINES or length not in LENGTHS:
            raise HTTPException(400, "Choose a line and a length from the lists.")
        item = store.queue_ball(
            {
                "pace_kph": _pace(body.pace_kph),
                "line": line,
                "length": length,
                **target_for(line, length),
                "batter": _batter(body.batter),
                "note": body.note.strip()[:200],
                "session_id": body.session_id,
                "delivery_id": body.delivery_id,
            }
        )
        return item

    @app.post("/api/machine/bowl")
    def bowl_next() -> dict:
        return _bowl_one(app, store)

    @app.post("/api/machine/bowl-all")
    def bowl_all() -> dict:
        bowled = []
        while store.machine_items() and len(bowled) < 18:
            bowled.append(_bowl_one(app, store))
        return {"bowled": bowled}

    @app.delete("/api/machine/{item_id}")
    def unqueue(item_id: str) -> dict:
        try:
            store.remove_ball(item_id)
        except KeyError:
            raise HTTPException(404, "That ball is not in the queue.") from None
        return {"ok": True}

    @app.get("/api/machine/requests")
    def machine_requests(wait: float = 0.0) -> dict:
        """For the edge bridge at the nets: balls waiting to be bowled. wait long-polls."""
        deadline = time.time() + max(0.0, min(wait, 25.0))
        with app.state.wake:
            while not store.requests() and time.time() < deadline:
                app.state.wake.wait(timeout=max(0.0, deadline - time.time()))
        return {"items": store.requests(), "mode": app.state.machine.mode}

    @app.post("/api/machine/requests/{item_id}")
    def machine_done(item_id: str, body: DoneBody) -> dict:
        try:
            item = store.finish_request(item_id)
        except KeyError:
            raise HTTPException(404, "No such request.") from None
        if not body.ok:
            store.put_front({k: v for k, v in item.items() if k != "requested"})
            return {"ok": False, "error": body.error[:300]}
        record = {**command_for(item), "via": "edge", "bowled_at": now_iso()}
        store.add_bowled(record)
        return record

    # -- the page -----------------------------------------------------------------

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    app.mount("/assets", StaticFiles(directory=WEB), name="assets")
    return app


# ------------------------------------------------------------------------------
# helpers


def _setting(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _flag(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _public(path: str) -> bool:
    return path in PUBLIC_API or path.startswith("/api/samples/")


def _signed_in(request: Request, auth: Auth) -> bool:
    if not auth.required:
        return True
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer ") and auth.check_key(header[7:]):
        return True
    return auth.valid(request.cookies.get(COOKIE))


def _wake(app: FastAPI) -> None:
    with app.state.wake:
        app.state.wake.notify_all()


def _record_job(store: Store, session_id: str, side_url: str, behind_url: str | None, seconds: int) -> None:
    from crickcoach.video import record_cameras

    folder = store.folder(session_id)
    cameras = {"side": (side_url, folder / "side.mp4")}
    if behind_url:
        cameras["behind"] = (behind_url, folder / "behind.mp4")
    try:
        failures = record_cameras(cameras, seconds)
    except Exception as exc:
        message = str(exc)[:400]

        def failed(session: dict) -> None:
            session["status"] = "error"
            session["error"] = message

        store.update(session_id, failed)
        return

    def recorded(session: dict) -> None:
        for camera in failures:
            session["cameras"][camera] = None
        if failures:
            session["error"] = " ".join(f"{camera}: {text}" for camera, text in failures.items())[:400]

    store.update(session_id, recorded)
    submit(store, session_id, "find")


def _blank_delivery(index: int, t0: float, t1: float, *, camera: str = "side") -> dict:
    return {
        "id": f"d{index}",
        "index": index,
        "t0": round(float(t0), 3),
        "t1": round(float(t1), 3),
        "camera": camera,
        "cut_note": "",
        "cues": [],
        "pose_note": "",
        "tracks": {},
        "tracking": None,
        "pitch": None,
        "pitch_source": None,
        "pace_kph": None,
        "pace_source": None,
        "stumps": None,
        "confidence": None,
        "outcome": None,
    }


def _bowl_one(app: FastAPI, store: Store) -> dict:
    machine: MachineLink = app.state.machine
    try:
        item = store.take_next_ball()
    except KeyError:
        raise HTTPException(400, "The queue is empty.") from None
    if machine.is_edge:
        store.add_request(item)
        _wake(app)
        return {**command_for(item), "via": "edge", "status": "sent to the nets"}
    try:
        record = machine.bowl(item)
    except RuntimeError as exc:
        store.put_front(item)
        raise HTTPException(502, str(exc)) from exc
    record["bowled_at"] = now_iso()
    return store.add_bowled(record)


def _calibration(camera: str, body: CalibrationBody) -> dict:
    if camera == "side":
        saved = {"kind": "side"}
        for key in SIDE_KEYS:
            value = getattr(body, key)
            if not value:
                raise HTTPException(400, "Click the base and the top of the stumps at both ends.")
            saved[key] = _point(value, key.replace("_", " "))
        if body.distance_m:
            saved["distance_m"] = round(float(body.distance_m), 2)
        try:
            side_plane(saved, 1000, 1000)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return saved
    corners = body.corners or []
    if len(corners) != 4:
        raise HTTPException(400, "Click the four crease corners.")
    saved = {"kind": "behind", "corners": [_point(corner, "a crease corner") for corner in corners]}
    try:
        ground_plane(saved, 1000, 1000)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return saved


def _point(value: dict, name: str) -> dict:
    try:
        x = float(value["x"])
        y = float(value["y"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, f"Need a click for {name}.") from None
    if not 0 <= x <= 1 or not 0 <= y <= 1:
        raise HTTPException(400, f"The click for {name} is outside the picture.")
    return {"x": round(x, 5), "y": round(y, 5)}


def _role(role: str) -> str:
    role = (role or "").strip().lower()
    if role not in {"bowling", "batting"}:
        raise HTTPException(400, "Role must be bowling or batting.")
    return role


def _batter(value: str | None) -> str:
    return "left" if (value or "").strip().lower() == "left" else "right"


def _rtsp(url: str, allow_private: bool) -> str:
    cleaned = (url or "").strip()
    parts = urlsplit(cleaned)
    if parts.scheme not in {"rtsp", "rtsps"} or not parts.hostname:
        raise HTTPException(400, "Camera address must start with rtsp:// and include a host.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 554, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise HTTPException(400, "That camera address does not resolve.") from None
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
            raise HTTPException(400, "That camera address points at this server.")
        if address.is_private and not allow_private:
            raise HTTPException(400, "This server does not reach into private networks.")
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


def _update_or_404(store: Store, session_id: str, change):
    try:
        return store.update(session_id, change)
    except KeyError:
        raise HTTPException(404, "Not found.") from None


def _check_delivery(delivery_id: str) -> None:
    try:
        check_delivery_id(delivery_id)
    except KeyError:
        raise HTTPException(404, "Delivery not found.") from None


def _find_delivery(session: dict, delivery_id: str) -> dict:
    check_delivery_id(delivery_id)
    for delivery in session.get("deliveries") or []:
        if delivery["id"] == delivery_id:
            return delivery
    raise KeyError(delivery_id)


def _find_delivery_or_404(session: dict, delivery_id: str) -> dict:
    try:
        return _find_delivery(session, delivery_id)
    except KeyError:
        raise HTTPException(404, "Delivery not found.") from None


async def _save_upload(upload: UploadFile, folder: Path, stem: str) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in VIDEO_TYPES:
        suffix = ".mp4"
    content_type = (upload.content_type or "").lower()
    if content_type.startswith("image/"):
        raise HTTPException(400, "Upload a video, not a picture.")
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
                    raise HTTPException(413, "Video is over 600 MB. Send shorter takes.")
                handle.write(chunk)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    if total < 32:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, "That file is empty.")
    return destination.name


app = create_app()
