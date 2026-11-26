"""Coaching cues from a short pose sequence.

Landmarks follow MediaPipe Pose: 33 points, x and y in 0..1 of the frame,
y growing downward. Angles are measured in the picture, so they describe the
action when the camera is side-on for bowling or in front for batting.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

NAMES = {
    "nose": 0,
    "left_ear": 7,
    "right_ear": 8,
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_wrist": 15,
    "right_wrist": 16,
    "left_hip": 23,
    "right_hip": 24,
    "left_knee": 25,
    "right_knee": 26,
    "left_ankle": 27,
    "right_ankle": 28,
}

VIS = 0.45
SEVERITY_RANK = {"fix": 0, "watch": 1, "good": 2}


@dataclass
class Point:
    x: float
    y: float
    visibility: float = 1.0


@dataclass
class PoseFrame:
    t: float
    points: list[Point]


@dataclass
class Cue:
    code: str
    severity: str
    title: str
    text: str
    measure: str

    def to_dict(self) -> dict:
        return asdict(self)


def parse_frames(raw: list) -> list[PoseFrame]:
    frames: list[PoseFrame] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        incoming = item.get("points") or []
        points = [Point(0.0, 0.0, 0.0) for _ in range(33)]
        for i, value in enumerate(incoming[:33]):
            if value is None:
                continue
            if isinstance(value, dict):
                points[i] = Point(
                    float(value.get("x", 0.0)),
                    float(value.get("y", 0.0)),
                    float(value.get("visibility", 1.0)),
                )
            elif isinstance(value, (list, tuple)) and len(value) >= 2:
                vis = float(value[2]) if len(value) > 2 else 1.0
                points[i] = Point(float(value[0]), float(value[1]), vis)
        frames.append(PoseFrame(t=float(item.get("t", 0.0)), points=points))
    frames.sort(key=lambda frame: frame.t)
    return frames


def coach(role: str, frames: list[PoseFrame]) -> list[Cue]:
    if role not in {"bowling", "batting"}:
        role = "bowling"
    if not _body_visible(frames):
        return [
            Cue(
                code="nobody",
                severity="watch",
                title="Player",
                text="No player is readable in this view. Frame the whole body and try again.",
                measure="",
            )
        ]
    if role == "batting":
        return _batting(frames)
    return _bowling(frames)


def _body_visible(frames: list[PoseFrame]) -> bool:
    hits = 0
    for frame in frames:
        if _pt(frame, "nose") and (_pt(frame, "left_shoulder") or _pt(frame, "right_shoulder")):
            hits += 1
    return hits >= 1


def _pt(frame: PoseFrame, name: str) -> Point | None:
    point = frame.points[NAMES[name]]
    if point.visibility < VIS:
        return None
    return point


def angle_deg(a: Point, b: Point, c: Point) -> float | None:
    """Angle at b, in degrees. A straight line is 180."""
    bax, bay = a.x - b.x, a.y - b.y
    bcx, bcy = c.x - b.x, c.y - b.y
    na = math.hypot(bax, bay)
    nc = math.hypot(bcx, bcy)
    if na < 1e-6 or nc < 1e-6:
        return None
    cos = (bax * bcx + bay * bcy) / (na * nc)
    cos = max(-1.0, min(1.0, cos))
    return math.degrees(math.acos(cos))


def _pick(cues: list[tuple[int, Cue]]) -> list[Cue]:
    ordered = sorted(cues, key=lambda item: (SEVERITY_RANK[item[1].severity], item[0]))
    chosen = [cue for _, cue in ordered[:3]]
    if len(chosen) < 3:
        chosen.append(
            Cue(
                code="view",
                severity="watch",
                title="Camera",
                text="Part of the body is missing. For bowling, stand side-on and keep the whole action in frame. For batting, use a front or open-side view.",
                measure="",
            )
        )
    return chosen[:3]


def _bowling(frames: list[PoseFrame]) -> list[Cue]:
    side = _highest_wrist(frames)
    if side is None:
        return _pick([])
    release = _release_frame(frames, side)
    if release is None:
        return _pick([])
    cues: list[tuple[int, Cue]] = []
    arm = _arm_cue(release, side)
    if arm:
        cues.append((0, arm))
    drift = _head_drift(frames, release.t)
    if drift is not None:
        cues.append((1, _head_cue(drift)))
    knee = _front_knee_cue(frames, release)
    if knee:
        cues.append((2, knee))
    height = _release_height_cue(release, side)
    if height:
        cues.append((3, height))
    return _pick(cues)


def _highest_wrist(frames: list[PoseFrame]) -> str | None:
    best_side = None
    best_y = 2.0
    for side in ("left", "right"):
        for frame in frames:
            wrist = _pt(frame, f"{side}_wrist")
            if wrist and wrist.y < best_y:
                best_y = wrist.y
                best_side = side
    return best_side


def _release_frame(frames: list[PoseFrame], side: str) -> PoseFrame | None:
    best = None
    best_y = 2.0
    for frame in frames:
        wrist = _pt(frame, f"{side}_wrist")
        if wrist and wrist.y < best_y:
            best_y = wrist.y
            best = frame
    return best


def _arm_cue(release: PoseFrame, side: str) -> Cue | None:
    shoulder = _pt(release, f"{side}_shoulder")
    elbow = _pt(release, f"{side}_elbow")
    wrist = _pt(release, f"{side}_wrist")
    if not shoulder or not elbow or not wrist:
        return None
    degrees = angle_deg(shoulder, elbow, wrist)
    if degrees is None:
        return None
    measure = f"Elbow {degrees:.0f}°"
    if degrees < 155:
        return Cue(
            code="arm",
            severity="fix",
            title="Bowling arm",
            text="The bowling arm is bending before release. A pace action wants the arm close to straight as the ball leaves the hand.",
            measure=measure,
        )
    if degrees < 165:
        return Cue(
            code="arm",
            severity="watch",
            title="Bowling arm",
            text="There is some bend in the bowling arm at release. Watch the replay and check the arm is straightening into the ball.",
            measure=measure,
        )
    return Cue(
        code="arm",
        severity="good",
        title="Bowling arm",
        text="The bowling arm is straight at release.",
        measure=measure,
    )


def _head_drift(frames: list[PoseFrame], release_t: float) -> float | None:
    xs = []
    for frame in frames:
        if frame.t < release_t - 0.40 or frame.t > release_t + 0.05:
            continue
        nose = _pt(frame, "nose")
        if nose:
            xs.append(nose.x)
    if len(xs) < 2:
        return None
    mean = sum(xs) / len(xs)
    var = sum((value - mean) ** 2 for value in xs) / len(xs)
    return math.sqrt(var)


def _head_cue(drift: float) -> Cue:
    percent = drift * 100
    measure = f"Drift {percent:.0f}% of the frame"
    if drift > 0.045:
        return Cue(
            code="head",
            severity="fix",
            title="Head",
            text="The head is moving off line through release. Keep it still from the gather until the ball has gone.",
            measure=measure,
        )
    if drift > 0.025:
        return Cue(
            code="head",
            severity="watch",
            title="Head",
            text="The head shifts a little through release. Quiet it so the eyes stay on the target.",
            measure=measure,
        )
    return Cue(
        code="head",
        severity="good",
        title="Head",
        text="The head stays steady through release.",
        measure=measure,
    )


def _hip_x(frame: PoseFrame) -> float | None:
    left = _pt(frame, "left_hip")
    right = _pt(frame, "right_hip")
    if left and right:
        return (left.x + right.x) / 2
    if left or right:
        found = left or right
        return found.x
    return None


def _front_knee_cue(frames: list[PoseFrame], release: PoseFrame) -> Cue | None:
    start_x = _hip_x(frames[0])
    end_x = _hip_x(release)
    if start_x is None or end_x is None or abs(end_x - start_x) < 0.015:
        return None
    direction = 1.0 if end_x > start_x else -1.0
    front = None
    best = -1e9
    for side in ("left", "right"):
        ankle = _pt(release, f"{side}_ankle")
        if ankle is None:
            continue
        score = ankle.x * direction
        if score > best:
            best = score
            front = side
    if front is None:
        return None
    hip = _pt(release, f"{front}_hip")
    knee = _pt(release, f"{front}_knee")
    ankle = _pt(release, f"{front}_ankle")
    if not hip or not knee or not ankle:
        return None
    degrees = angle_deg(hip, knee, ankle)
    if degrees is None:
        return None
    measure = f"Front knee {degrees:.0f}°"
    if degrees < 145:
        return Cue(
            code="knee",
            severity="fix",
            title="Front knee",
            text="The front knee collapses at release. Brace it so the action delivers into a firm front leg.",
            measure=measure,
        )
    if degrees < 160:
        return Cue(
            code="knee",
            severity="watch",
            title="Front knee",
            text="The front knee is soft at release. Feel it brace as the ball leaves the hand.",
            measure=measure,
        )
    return Cue(
        code="knee",
        severity="good",
        title="Front knee",
        text="The front knee braces under the action.",
        measure=measure,
    )


def _release_height_cue(release: PoseFrame, side: str) -> Cue | None:
    wrist = _pt(release, f"{side}_wrist")
    shoulder = _pt(release, f"{side}_shoulder")
    nose = _pt(release, "nose")
    if not wrist or not shoulder:
        return None
    if wrist.y <= shoulder.y:
        if nose and wrist.y <= nose.y:
            where = "up by the eyes"
        else:
            where = "above the shoulder"
        return Cue(
            code="release",
            severity="good",
            title="Release",
            text=f"The release is {where}.",
            measure="High release",
        )
    return Cue(
        code="release",
        severity="watch",
        title="Release",
        text="The release is below the shoulder. That suits some spin. For pace, take the hand higher.",
        measure="Below the shoulder",
    )


def _batting(frames: list[PoseFrame]) -> list[Cue]:
    setup = _stance_frames(frames)
    cues: list[tuple[int, Cue]] = []
    knee = _batting_knee(setup)
    if knee:
        cues.append((0, knee))
    head = _batting_head(setup)
    if head:
        cues.append((1, head))
    hands = _batting_hands(setup)
    if hands:
        cues.append((2, hands))
    backlift = _backlift_cue(frames)
    if backlift:
        cues.append((3, backlift))
    width = _stance_width(setup)
    if width:
        cues.append((4, width))
    return _pick(cues)


def _stance_frames(frames: list[PoseFrame]) -> list[PoseFrame]:
    span = _wrist_span(frames)
    if span is not None and span > 0.10 and len(frames) >= 4:
        return frames[: max(2, len(frames) // 3)]
    return frames


def _wrist_span(frames: list[PoseFrame]) -> float | None:
    highs = []
    for frame in frames:
        values = []
        for side in ("left", "right"):
            wrist = _pt(frame, f"{side}_wrist")
            if wrist:
                values.append(wrist.y)
        if values:
            highs.append(min(values))
    if len(highs) < 2:
        return None
    return max(highs) - min(highs)


def _robust(frames: list[PoseFrame], name: str) -> Point | None:
    xs = []
    ys = []
    for frame in frames:
        point = _pt(frame, name)
        if point:
            xs.append(point.x)
            ys.append(point.y)
    if not xs:
        return None
    xs.sort()
    ys.sort()
    mid = len(xs) // 2
    return Point(xs[mid], ys[mid], 1.0)


def _batting_knee(frames: list[PoseFrame]) -> Cue | None:
    angles = []
    for side in ("left", "right"):
        hip = _robust(frames, f"{side}_hip")
        knee = _robust(frames, f"{side}_knee")
        ankle = _robust(frames, f"{side}_ankle")
        if hip and knee and ankle:
            degrees = angle_deg(hip, knee, ankle)
            if degrees is not None:
                angles.append(degrees)
    if not angles:
        return None
    degrees = sum(angles) / len(angles)
    measure = f"Knees {degrees:.0f}°"
    if degrees > 172:
        return Cue(
            code="knee",
            severity="fix",
            title="Knees",
            text="The knees are locked. Sit into the stance so you can move.",
            measure=measure,
        )
    if degrees < 130:
        return Cue(
            code="knee",
            severity="watch",
            title="Knees",
            text="The stance is very deep. Stay low enough to move, high enough to step.",
            measure=measure,
        )
    if 140 <= degrees <= 168:
        return Cue(
            code="knee",
            severity="good",
            title="Knees",
            text="The knees are bent and ready.",
            measure=measure,
        )
    return Cue(
        code="knee",
        severity="watch",
        title="Knees",
        text="The knee bend is a little off a ready stance. Check the replay and sit into the shot.",
        measure=measure,
    )


def _batting_head(frames: list[PoseFrame]) -> Cue | None:
    nose = _robust(frames, "nose")
    left = _robust(frames, "left_ankle")
    right = _robust(frames, "right_ankle")
    if not nose or not left or not right:
        return None
    mid = (left.x + right.x) / 2
    drift = abs(nose.x - mid)
    measure = f"Head {drift * 100:.0f}% of the frame from the base"
    if drift > 0.08:
        return Cue(
            code="head",
            severity="fix",
            title="Head",
            text="The head is outside the base. Bring it back over the feet.",
            measure=measure,
        )
    return Cue(
        code="head",
        severity="good",
        title="Head",
        text="The head is over the base.",
        measure=measure,
    )


def _batting_hands(frames: list[PoseFrame]) -> Cue | None:
    wrists = [p for p in (_robust(frames, "left_wrist"), _robust(frames, "right_wrist")) if p]
    hips = [p for p in (_robust(frames, "left_hip"), _robust(frames, "right_hip")) if p]
    shoulders = [p for p in (_robust(frames, "left_shoulder"), _robust(frames, "right_shoulder")) if p]
    if not wrists or not hips:
        return None
    wrist_y = sum(p.y for p in wrists) / len(wrists)
    hip_y = sum(p.y for p in hips) / len(hips)
    if wrist_y > hip_y:
        return Cue(
            code="hands",
            severity="fix",
            title="Hands",
            text="The hands have dropped below the hips. Set them up by the thigh so the bat can come through.",
            measure="Hands below the hips",
        )
    if shoulders:
        shoulder_y = sum(p.y for p in shoulders) / len(shoulders)
        if wrist_y < shoulder_y + 0.12:
            return Cue(
                code="hands",
                severity="good",
                title="Hands",
                text="The hands are up and ready.",
                measure="Hands by the chest",
            )
    return Cue(
        code="hands",
        severity="watch",
        title="Hands",
        text="The hands sit a little low. Lift them so the bat starts under the eyes.",
        measure="Hands low",
    )


def _backlift_cue(frames: list[PoseFrame]) -> Cue | None:
    span = _wrist_span(frames)
    if span is None or span < 0.10:
        return None
    highest = None
    for frame in frames:
        for side in ("left", "right"):
            wrist = _pt(frame, f"{side}_wrist")
            if wrist and (highest is None or wrist.y < highest):
                highest = wrist.y
    shoulders = []
    for frame in frames:
        for side in ("left", "right"):
            shoulder = _pt(frame, f"{side}_shoulder")
            if shoulder:
                shoulders.append(shoulder.y)
    if highest is None or not shoulders:
        return None
    shoulder_y = sorted(shoulders)[len(shoulders) // 2]
    if highest < shoulder_y - 0.01:
        return Cue(
            code="backlift",
            severity="good",
            title="Backlift",
            text="The backlift clears the shoulder.",
            measure="Hands above the shoulder",
        )
    return Cue(
        code="backlift",
        severity="watch",
        title="Backlift",
        text="The backlift is short. Take the hands up before the bat comes down.",
        measure="Hands stay below the shoulder",
    )


def _stance_width(frames: list[PoseFrame]) -> Cue | None:
    left = _robust(frames, "left_ankle")
    right = _robust(frames, "right_ankle")
    if not left or not right:
        return None
    width = abs(left.x - right.x)
    measure = f"Base {width * 100:.0f}% of the frame"
    if width < 0.08:
        return Cue(
            code="base",
            severity="watch",
            title="Base",
            text="The stance is narrow. Widen the feet to about shoulder width.",
            measure=measure,
        )
    if width > 0.40:
        return Cue(
            code="base",
            severity="watch",
            title="Base",
            text="The stance is very wide. Bring the feet in so the first step is easy.",
            measure=measure,
        )
    return Cue(
        code="base",
        severity="good",
        title="Base",
        text="The base is a workable width.",
        measure=measure,
    )
