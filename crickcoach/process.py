"""Turn uploaded or recorded camera files into tracked deliveries.

The work runs on two background workers, so the coach screen stays live and
a small server is never asked to decode four videos at once. Each step writes
its progress into the session, which the screen polls.
"""

from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from crickcoach.deliveries import find_deliveries, find_flights, match_offset
from crickcoach.flight import analyse_clip, combine
from crickcoach.segment import Segment
from crickcoach.store import Store, now_iso

WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vansh-job")
HEAVY = threading.BoundedSemaphore(2)  # video decoding at once, jobs and single tracks together
CAMERAS = ("side", "behind")
MATCH_S = 0.6  # flights on the two cameras this close (after the clock offset) are one ball


def submit(store: Store, session_id: str, kind: str) -> None:
    def set_job(session: dict) -> None:
        session["job"] = {"kind": kind, "progress": 0.0, "message": _START[kind], "started": now_iso()}
        session["status"] = "processing"

    store.update(session_id, set_job)
    WORKERS.submit(_run, store, session_id, kind)


_START = {
    "find": "Finding the balls in the video…",
    "track": "Tracking every ball again…",
}


def _run(store: Store, session_id: str, kind: str) -> None:
    try:
        with HEAVY:
            if kind == "find":
                find_and_track(store, session_id)
            else:
                retrack(store, session_id)
        store.update(session_id, _finished)
    except KeyError:
        return  # the session was deleted while the job ran
    except Exception as exc:  # a failed job is shown on the session
        detail = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()

        def failed(session: dict) -> None:
            session["status"] = "error"
            session["error"] = detail[:400]
            session["job"] = None

        try:
            store.update(session_id, failed)
        except KeyError:
            return


def _finished(session: dict) -> None:
    session["status"] = "ready"
    session["job"] = None


def _progress(store: Store, session_id: str, share: float, message: str) -> None:
    def change(session: dict) -> None:
        job = session.get("job") or {}
        job.update(progress=round(max(0.0, min(1.0, share)), 3), message=message)
        session["job"] = job

    store.update(session_id, change)


def find_and_track(store: Store, session_id: str) -> None:
    """Find every ball on each calibrated camera, line the clocks up, and build the deliveries."""
    session = store.get(session_id)
    folder = store.folder(session_id)
    calibration = session.get("calibration") or {}
    files = {camera: folder / name for camera, name in (session.get("cameras") or {}).items() if name}
    flights: dict[str, list[dict]] = {}
    plan = [camera for camera in CAMERAS if camera in files and calibration.get(camera)]
    for position, camera in enumerate(plan):
        def report(share: float, camera=camera, position=position) -> None:
            _progress(store, session_id, (position + share) / max(len(plan), 1),
                      f"Looking for ball flights on the {camera} camera…")

        from crickcoach.video import probe

        duration = probe(files[camera]).duration
        flights[camera] = find_flights(files[camera], calibration[camera], duration=duration, progress=report)
    offset = session.get("clock_offset") or 0.0
    matched_by = "kept"
    if flights.get("side") and flights.get("behind"):
        found, matches = match_offset([f["bounce_t"] for f in flights["side"]], [f["bounce_t"] for f in flights["behind"]])
        if matches >= 1:
            offset = found
            matched_by = f"{matches} bounces"
    deliveries = _deliveries_from_flights(flights, offset, session.get("batter", "right"))
    found_by = "flights"
    if not deliveries:
        main = files.get("side") or files.get("behind")
        if main is None:
            raise RuntimeError("The session has no video.")
        segments, found_by = find_deliveries(main, None)
        camera = "side" if files.get("side") else "behind"
        deliveries = [_blank(index, seg, camera) for index, seg in enumerate(segments, start=1)]

    def save(session: dict) -> None:
        session["deliveries"] = _keep_coach_marks(session.get("deliveries") or [], deliveries)
        session["clock_offset"] = offset
        session["clock_note"] = matched_by
        session["found_by"] = found_by
        session["error"] = ""

    store.update(session_id, save)


def _deliveries_from_flights(flights: dict[str, list[dict]], offset: float, batter: str) -> list[dict]:
    """One delivery per ball. Times are on the side camera's clock."""
    balls: list[dict] = []
    for flight in flights.get("side") or []:
        balls.append({"t_first": flight["t_first"], "t_last": flight["t_last"], "bounce_t": flight["bounce_t"],
                      "side": flight["measured"], "behind": None})
    for flight in flights.get("behind") or []:
        on_side = (flight["bounce_t"] or flight["t_first"]) - offset
        twin = None
        for ball in balls:
            if ball["bounce_t"] is not None and abs(ball["bounce_t"] - on_side) <= MATCH_S:
                twin = ball
                break
        if twin is not None:
            twin["behind"] = flight["measured"]
        else:
            balls.append({"t_first": flight["t_first"] - offset, "t_last": flight["t_last"] - offset,
                          "bounce_t": on_side, "side": None, "behind": flight["measured"]})
    balls.sort(key=lambda ball: ball["t_first"])
    deliveries = []
    for index, ball in enumerate(balls, start=1):
        segment = Segment(round(max(0.0, ball["t_first"] - 1.6), 3), round(ball["t_last"] + 1.0, 3), "Found by the ball's flight.")
        delivery = _blank(index, segment, "side" if ball["side"] else "behind")
        for camera in CAMERAS:
            if ball[camera]:
                apply_tracking(delivery, ball[camera], batter)
        deliveries.append(delivery)
    return deliveries


def _blank(index: int, segment: Segment, camera: str) -> dict:
    return {
        "id": f"d{index}",
        "index": index,
        "t0": segment.t0,
        "t1": segment.t1,
        "camera": camera,
        "cut_note": segment.note,
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


def _keep_coach_marks(old: list[dict], new: list[dict]) -> list[dict]:
    """A re-run keeps outcomes, cues and the coach's own marks on balls that line up in time."""
    for delivery in new:
        for previous in old:
            if abs(previous.get("t0", -99) - delivery["t0"]) < 1.0:
                for key in ("outcome", "cues", "pose_note"):
                    if previous.get(key):
                        delivery[key] = previous[key]
                if previous.get("pitch_source") == "manual":
                    delivery["pitch"], delivery["pitch_source"] = previous["pitch"], "manual"
                if previous.get("pace_source") in {"manual", "radar"}:
                    delivery["pace_kph"], delivery["pace_source"] = previous["pace_kph"], previous["pace_source"]
                break
    return new


def retrack(store: Store, session_id: str) -> None:
    """Track every delivery again on every calibrated camera."""
    session = store.get(session_id)
    deliveries = session.get("deliveries") or []
    total = max(1, len(deliveries) * len(CAMERAS))
    done = 0
    for delivery in deliveries:
        for camera in CAMERAS:
            done += 1
            if not (session.get("cameras") or {}).get(camera) or not (session.get("calibration") or {}).get(camera):
                continue
            _progress(store, session_id, done / total, f"Tracking ball {delivery['index']} on the {camera} camera…")
            measured = measure_delivery(store, session, delivery, camera)

            def save(session: dict, delivery_id=delivery["id"], measured=measured) -> None:
                for item in session.get("deliveries") or []:
                    if item["id"] == delivery_id:
                        apply_tracking(item, measured, session.get("batter", "right"))

            store.update(session_id, save)


def measure_delivery(store: Store, session: dict, delivery: dict, camera: str) -> dict:
    """Track one ball on one camera. The camera behind uses the side camera's timing when it has it."""
    path = store.folder(session["id"]) / session["cameras"][camera]
    calibration = session["calibration"][camera]
    t0, t1 = float(delivery["t0"]), float(delivery["t1"])
    offset = float(session.get("clock_offset") or 0.0)
    if camera == "behind":
        side = (delivery.get("tracks") or {}).get("side") or {}
        if side.get("bounce_t") and side.get("confidence") != "none":
            bounce = float(side["bounce_t"]) + offset
            return analyse_clip(path, max(0.0, bounce - 0.75), bounce + 0.8, calibration)
        return analyse_clip(path, max(0.0, t0 + offset), t1 + offset, calibration)
    return analyse_clip(path, t0, t1, calibration)


class Busy(RuntimeError):
    pass


def track_one(store: Store, session_id: str, delivery_id: str, camera: str) -> dict:
    session = store.get(session_id)
    delivery = next((item for item in session["deliveries"] if item["id"] == delivery_id), None)
    if delivery is None:
        raise KeyError(delivery_id)
    if not HEAVY.acquire(timeout=90):
        raise Busy("VANSH is busy with another video. Try again in a minute.")
    try:
        measured = measure_delivery(store, session, delivery, camera)
    finally:
        HEAVY.release()

    def save(session: dict) -> dict:
        for item in session.get("deliveries") or []:
            if item["id"] == delivery_id:
                apply_tracking(item, measured, session.get("batter", "right"))
                return item
        raise KeyError(delivery_id)

    _, updated = store.update(session_id, save)
    return updated


def apply_tracking(delivery: dict, measured: dict, batter: str = "right") -> None:
    """Fold one camera's reading into the delivery. The coach's own marks win."""
    camera = measured.get("camera") or "side"
    tracks = delivery.setdefault("tracks", {})
    tracks[camera] = measured
    delivery["tracking"] = measured
    refresh(delivery, batter)


def refresh(delivery: dict, batter: str = "right") -> None:
    tracks = delivery.get("tracks") or {}
    merged = combine(tracks.get("side"), tracks.get("behind"), batter)
    if delivery.get("pace_source") not in {"manual", "radar"}:
        delivery["pace_kph"] = merged.get("pace_kph")
        delivery["pace_source"] = merged.get("pace_source")
    if delivery.get("pitch_source") != "manual":
        delivery["pitch"] = merged.get("pitch")
        delivery["pitch_source"] = "cameras" if merged.get("pitch") else None
        delivery["confidence"] = merged.get("confidence")
    delivery["stumps"] = merged.get("stumps")


def path_for(store: Store, session: dict, camera: str) -> Path | None:
    name = (session.get("cameras") or {}).get(camera)
    return store.folder(session["id"]) / name if name else None


def recover(store: Store) -> int:
    """After a restart, no job is running. Say so on any session that was mid-job."""
    count = 0
    for summary in store.list_sessions():
        if not summary.get("job") and summary["status"] not in {"processing", "recording"}:
            continue

        def interrupted(session: dict) -> None:
            session["job"] = None
            session["status"] = "error"
            session["error"] = "VANSH restarted while this session was being worked on. Press Find the balls again."

        try:
            store.update(summary["id"], interrupted)
            count += 1
        except KeyError:
            continue
    return count
