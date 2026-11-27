"""Turn a ball track and a camera calibration into pace, length, and line."""

from __future__ import annotations

import math
from pathlib import Path

from bench.grok_v1.geometry import (
    RELEASE_HEIGHT_M,
    STUMP_HEIGHT_M,
    STUMP_TO_STUMP_M,
    along,
    apply_h,
    height_above,
    homography,
    path_pace_kph,
)
from bench.grok_v1.pitch import describe_pitch
from bench.grok_v1.track import track_frames

PITCH_CORNERS = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


def analyse_clip(path: Path, t0: float, t1: float, calibration: dict) -> dict:
    from bench.grok_v1.video import decode_gray

    frames, width, height, fps = decode_gray(path, t0, min(t1, t0 + 8))
    raw = track_frames(frames, width, height)
    points = [{"t": round(t0 + item["frame"] / fps, 4), "x": item["x"], "y": item["y"]} for item in raw]
    measured = measure(points, calibration)
    measured["points"] = [{"t": point["t"], "x": round(point["x"], 4), "y": round(point["y"], 4)} for point in points]
    return measured


def combine(side: dict | None, behind: dict | None) -> dict:
    """One pitch and one pace from the two cameras.

    The side camera owns length and pace. The camera behind the bowler owns
    the line. A coach's own mark is left alone by the caller.
    """
    side_pitch = _usable_pitch(side)
    behind_pitch = _usable_pitch(behind)
    length = (side_pitch or {}).get("length") or (behind_pitch or {}).get("length")
    length_y = None
    if side_pitch and side_pitch.get("length"):
        length_y = side_pitch.get("y")
    elif behind_pitch:
        length_y = behind_pitch.get("y")
    line = (behind_pitch or {}).get("line") or (side_pitch or {}).get("line")
    line_x = None
    if behind_pitch and behind_pitch.get("line"):
        line_x = behind_pitch.get("x")
    elif side_pitch:
        line_x = side_pitch.get("x")
    pitch = None
    if length or line:
        if line and length:
            text = f"{length} on {line}"
        elif length:
            text = f"{length} length. The side camera does not show the line."
        else:
            text = f"On {line}. Length needs the side camera."
        pitch = {"x": line_x, "y": length_y, "line": line, "length": length, "text": text}
    pace = None
    pace_source = None
    if side and side.get("pace_kph"):
        pace = side["pace_kph"]
        pace_source = "side"
    elif behind and behind.get("pace_kph"):
        pace = behind["pace_kph"]
        pace_source = "behind"
    return {
        "pace_kph": pace,
        "pace_source": pace_source,
        "pitch": pitch,
        "hit_stumps": bool((side or {}).get("hit_stumps") or (behind or {}).get("hit_stumps")),
    }


def _usable_pitch(measured: dict | None) -> dict | None:
    if not measured or measured.get("confidence") == "none":
        return None
    pitch = measured.get("pitch")
    return pitch or None


def measure(points: list[dict], calibration: dict) -> dict:
    kind = calibration.get("kind")
    if len(points) < 5:
        return _empty(kind or "side", "No ball stood out from the picture.")
    if kind == "behind":
        return _behind(points, calibration)
    if kind == "side":
        return _side(points, calibration)
    return _empty("side", "Calibrate this camera before tracking the ball.")


def _side(points: list[dict], calibration: dict) -> dict:
    bowling = calibration["bowling"]
    batting = calibration["batting"]
    try:
        places = [along(point, bowling, batting) for point in points]
        heights = [height_above(point, bowling, batting) for point in points]
    except ValueError as exc:
        return _empty("side", str(exc))
    bounce = min(range(len(heights)), key=lambda index: heights[index])
    release = max(range(bounce + 1), key=lambda index: heights[index]) if bounce else 0
    length = _clamp(places[bounce])
    named = describe_pitch(0.5, length)
    pitch = {
        "x": None,
        "y": named["y"],
        "line": None,
        "length": named["length"],
        "text": f"{named['length']} length. The side camera does not show the line.",
    }
    stump = calibration.get("stump_top")
    scale = None
    if stump:
        stump_height = height_above(stump, bowling, batting)
        if stump_height > 0.02:
            scale = STUMP_HEIGHT_M / stump_height
    release_height = heights[release] * scale if scale else 0.0
    ground = abs(places[bounce] - places[release]) * STUMP_TO_STUMP_M
    seconds = points[bounce]["t"] - points[release]["t"]
    pace = path_pace_kph(ground, release_height, seconds)
    hit = _side_hit(places, heights, scale)
    confidence = "ok"
    notes = []
    if bounce >= len(points) - 2:
        confidence = "low"
        notes.append("The ball was still falling at the end of the clip, so the pitch mark is the last sight of it.")
    if scale is None:
        notes.append("Pace is the ground distance over time. Click the top of the stumps to include the release height.")
    else:
        notes.append("Pace includes the height above the pitch, using the stumps as a 71 cm ruler.")
    if pace is None:
        confidence = "low"
        notes.append("The timed flight was too short, or the pace fell outside 40 to 180 km/h.")
    return {
        "camera": "side",
        "confidence": confidence,
        "note": " ".join(notes),
        "pace_kph": pace,
        "pitch": pitch,
        "hit_stumps": hit,
        "bounce_t": points[bounce]["t"],
        "points": [],
    }


def _behind(points: list[dict], calibration: dict) -> dict:
    corners = calibration.get("corners") or []
    if len(corners) != 4:
        return _empty("behind", "Click the four corners of the pitch for the behind-the-bowler camera.")
    try:
        matrix = homography([(point["x"], point["y"]) for point in corners], PITCH_CORNERS)
    except ValueError as exc:
        return _empty("behind", str(exc))
    bounce = _bounce_index(points)
    try:
        line, length = apply_h(matrix, points[bounce]["x"], points[bounce]["y"])
    except ValueError as exc:
        return _empty("behind", str(exc))
    pitch = describe_pitch(line, length)
    seconds = points[bounce]["t"] - points[0]["t"]
    ground = max(pitch["y"], 0) * STUMP_TO_STUMP_M
    pace = path_pace_kph(ground, RELEASE_HEIGHT_M, seconds)
    hit = _behind_hit(points, matrix)
    turn = _turn_degrees(points, bounce)
    confidence = "ok" if turn >= 25 else "low"
    note = "Line and length come from the bounce. Pace assumes a 2 m release, because this camera does not see height."
    if confidence == "low":
        note += " The bounce was a weak kink, so check the mark."
    if pace is None:
        confidence = "low"
        note += " The timed flight was too short, or the pace fell outside 40 to 180 km/h."
    return {
        "camera": "behind",
        "confidence": confidence,
        "note": note,
        "pace_kph": pace,
        "pitch": pitch,
        "hit_stumps": hit,
        "bounce_t": points[bounce]["t"],
        "points": [],
    }


def _bounce_index(points: list[dict]) -> int:
    best_index = 0
    best_turn = -1.0
    for index in range(1, len(points) - 1):
        turn = _corner(points[index - 1], points[index], points[index + 1])
        if turn > best_turn:
            best_turn = turn
            best_index = index
    return best_index


def _turn_degrees(points: list[dict], index: int) -> float:
    if index <= 0 or index >= len(points) - 1:
        return 0.0
    return _corner(points[index - 1], points[index], points[index + 1])


def _corner(before: dict, here: dict, after: dict) -> float:
    ax = here["x"] - before["x"]
    ay = here["y"] - before["y"]
    bx = after["x"] - here["x"]
    by = after["y"] - here["y"]
    na = math.hypot(ax, ay)
    nb = math.hypot(bx, by)
    if na < 1e-6 or nb < 1e-6:
        return 0.0
    cos = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
    return math.degrees(math.acos(cos))


def _side_hit(places: list[float], heights: list[float], scale: float | None) -> bool:
    for place, height in zip(places, heights):
        if place < 0.97:
            continue
        if scale is None:
            if height <= 0.08:
                return True
        elif height * scale <= STUMP_HEIGHT_M + 0.05:
            return True
    return False


def _behind_hit(points: list[dict], matrix: list[list[float]]) -> bool:
    for point in points[-4:]:
        try:
            line, length = apply_h(matrix, point["x"], point["y"])
        except ValueError:
            continue
        if length >= 0.97 and 0.42 <= line <= 0.58:
            return True
    return False


def _empty(camera: str, note: str) -> dict:
    return {
        "camera": camera,
        "confidence": "none",
        "note": note,
        "pace_kph": None,
        "pitch": None,
        "hit_stumps": False,
        "bounce_t": None,
        "points": [],
    }


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
