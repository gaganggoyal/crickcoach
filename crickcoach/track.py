"""Find the ball in one delivery.

Each frame is compared with the median of frames a little before and after
it, in brightness and in red-difference colour, so a red ball on green turf
shows up as clearly as a white ball. Big moving shapes (the bowler, the
batter) are masked out with a margin around them. The small blobs left are
linked by ball physics: fast, heading down the pitch, and smooth apart from
one bounce. A long slow chain (a batter shuffling) is never a ball.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

CHROMA_WEIGHT = 1.5
MAX_GAP_S = 0.17  # an established track may coast this long through a dropout
SHORT_GAP_S = 0.09  # a young track may not
MIN_TRACK = 4


@dataclass
class Blob:
    frame: int
    u: float
    v: float
    area: int
    strength: float


@dataclass
class Scene:
    """What the tracker knows about the camera, in pixels of the decoded frames."""

    width: int
    height: int
    direction: tuple[float, float]  # unit vector the ball travels in the picture
    pitch_px: float  # picture distance from the bowler's end to the batter's end
    ball_px: float  # largest expected ball diameter
    speed_px: tuple[float, float]  # slowest and fastest picture speed, px per second
    roi: np.ndarray | None = None  # polygon the ball can appear in
    margin_px: float = 8.0  # blobs this close to a moving body are part of it
    strict_direction: bool = True  # side-on, the ball always moves down the pitch in the picture
    background_gap_s: float = 0.04  # the ball must have moved off itself by then


def detect(stream, scene: Scene) -> list[list[Blob]]:
    """Ball candidates in every frame of a FrameStream.

    The background of a frame is the median of four frames at least
    background_gap_s away, so the ball has moved off its own spot. From behind
    the bowler a ball barely moves in the picture, so that gap is longer.
    """
    roi_mask, (x0, y0, x1, y1) = _roi(scene)
    fps = getattr(getattr(stream, "source", None), "fps", 25.0) or 25.0
    gap = max(2, int(math.ceil(scene.background_gap_s * fps - 1e-9)))
    look = gap + 2
    window: deque = deque(maxlen=2 * look + 1)
    per_frame: dict[int, list[Blob]] = {}
    seen = 0
    for frame in stream:
        window.append((frame.index, frame.y[y0:y1, x0:x1].copy(), frame.v[y0:y1, x0:x1].copy()))
        seen = frame.index + 1
        target = frame.index - look
        if target >= 0:
            per_frame[target] = _blobs_for(window, target, scene, roi_mask, x0, y0, gap)
    for target in range(max(0, seen - look), seen):
        per_frame[target] = _blobs_for(window, target, scene, roi_mask, x0, y0, gap)
    return [per_frame.get(index, []) for index in range(seen)]


def _roi(scene: Scene) -> tuple[np.ndarray | None, tuple[int, int, int, int]]:
    if scene.roi is None or len(scene.roi) < 3:
        return None, (0, 0, scene.width, scene.height)
    polygon = np.round(np.asarray(scene.roi, dtype=float)).astype(np.int32)
    x0 = int(max(0, polygon[:, 0].min()))
    x1 = int(min(scene.width, polygon[:, 0].max() + 1))
    y0 = int(max(0, polygon[:, 1].min()))
    y1 = int(min(scene.height, polygon[:, 1].max() + 1))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None, (0, 0, scene.width, scene.height)
    mask = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
    cv2.fillPoly(mask, [polygon - np.array([x0, y0])], 1)
    return mask, (x0, y0, x1, y1)


def _blobs_for(window, target: int, scene: Scene, roi_mask, x0: int, y0: int, gap: int = 2) -> list[Blob]:
    frames = {index: (y, v) for index, y, v in window}
    if target not in frames:
        return []
    others = sorted((index for index in frames if abs(index - target) >= gap), key=lambda index: abs(index - target))[:4]
    if len(others) < 3:
        # A median of two frames would print a ghost of the ball where it is in them.
        return []
    y_now, v_now = frames[target]
    bg_y = _median([frames[index][0] for index in others])
    bg_v = _median([frames[index][1] for index in others])
    score = cv2.absdiff(y_now, bg_y).astype(np.float32)
    score += CHROMA_WEIGHT * cv2.absdiff(v_now, bg_v).astype(np.float32)
    if roi_mask is not None:
        score *= roi_mask
    sample = score[::3, ::3]
    if roi_mask is not None:
        sample = sample[roi_mask[::3, ::3] > 0]
    if sample.size == 0:
        return []
    middle = float(np.median(sample))
    spread = 1.4826 * float(np.median(np.abs(sample - middle))) + 0.5
    threshold = max(middle + 6.0 * spread, 10.0)
    mask = (score > threshold).astype(np.uint8)
    if not mask.any():
        return []
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return []
    ball = scene.ball_px
    streak = scene.speed_px[1] / 25.0
    max_area = 1.6 * (ball + 3) * (ball + 3 + streak)
    areas = stats[:, cv2.CC_STAT_AREA]
    big = np.nonzero(areas[1:] > max_area)[0] + 1
    near_big = None
    margin = float(scene.margin_px)
    if big.size:
        # Distance from every pixel to the nearest big mover (a body, a bat).
        lookup = np.ones(count, dtype=np.uint8)
        lookup[big] = 0
        near_big = cv2.distanceTransform(lookup[labels], cv2.DIST_L2, 3)
    found = []
    for label in range(1, count):
        area = int(areas[label])
        if area < 2 or area > max_area:
            continue
        left, top, w, h = (int(stats[label, k]) for k in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
        region = labels[top : top + h, left : left + w] == label
        if near_big is not None and near_big[top : top + h, left : left + w][region].min() <= margin:
            continue
        weights = score[top : top + h, left : left + w] * region
        total = float(weights.sum())
        if total <= 0:
            continue
        rows, cols = np.mgrid[top : top + h, left : left + w]
        u = float((weights * cols).sum() / total) + x0
        v = float((weights * rows).sum() / total) + y0
        found.append(Blob(target, u, v, area, total / max(int(region.sum()), 1)))
    found.sort(key=lambda blob: -blob.strength)
    return found[:25]


def _median(stack: list[np.ndarray]) -> np.ndarray:
    if len(stack) >= 4:
        a, b, c, d = stack[:4]
        low = cv2.max(cv2.min(a, b), cv2.min(c, d))
        high = cv2.min(cv2.max(a, b), cv2.max(c, d))
        return cv2.addWeighted(low, 0.5, high, 0.5, 0)
    if len(stack) == 3:
        a, b, c = stack
        return cv2.max(cv2.min(a, b), cv2.min(cv2.max(a, b), c))
    return cv2.addWeighted(stack[0], 0.5, stack[1], 0.5, 0)


# --------------------------------------------------------------------------
# Linking blobs into one flight.


def best_track(per_frame: list[list[Blob]], times: list[float], scene: Scene) -> list[Blob]:
    tracks = candidate_tracks(per_frame, times, scene, keep=1)
    return tracks[0] if tracks else []


def candidate_tracks(per_frame: list[list[Blob]], times: list[float], scene: Scene, *, keep: int = 5) -> list[list[Blob]]:
    """The few most ball-like tracks, best first. The caller checks them against physics."""
    direction = np.asarray(scene.direction, dtype=float)
    direction = direction / (np.linalg.norm(direction) or 1.0)
    slow, fast = scene.speed_px
    seeds = []
    for index in range(len(per_frame)):
        for gap in (1, 2):
            other = index + gap
            if other >= len(per_frame):
                continue
            dt = times[other] - times[index]
            if dt <= 0:
                continue
            for a in per_frame[index]:
                for b in per_frame[other]:
                    du, dv = b.u - a.u, b.v - a.v
                    speed = math.hypot(du, dv) / dt
                    if not slow <= speed <= fast:
                        continue
                    if scene.strict_direction and (du * direction[0] + dv * direction[1]) / (speed * dt) < 0.5:
                        continue
                    seeds.append((a, b))
    seeds.sort(key=lambda pair: -(pair[0].strength + pair[1].strength))
    scored: list[tuple[float, list[Blob]]] = []
    seen_keys: set[tuple[int, ...]] = set()
    covered: dict[int, int] = {}
    for a, b in seeds[:400]:
        if covered.get(id(a), 0) >= 2 and covered.get(id(b), 0) >= 2:
            continue
        for forward_first in (True, False):
            if forward_first:
                track, used = _grow(per_frame, times, [a, b], scene, direction, forward=True, allow_bounce=True)
                track, _ = _grow(per_frame, times, track, scene, direction, forward=False, allow_bounce=not used)
            else:
                track, used = _grow(per_frame, times, [a, b], scene, direction, forward=False, allow_bounce=True)
                track, _ = _grow(per_frame, times, track, scene, direction, forward=True, allow_bounce=not used)
            track = trim(track, times, scene.ball_px)
            if len(track) < MIN_TRACK:
                continue
            key = tuple(sorted(id(blob) for blob in track))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            score = _score(track, times, scene, direction)
            if score <= -1e8:
                continue
            scored.append((score, track))
            for blob in track:
                covered[id(blob)] = covered.get(id(blob), 0) + 1
    scored.sort(key=lambda item: -item[0])
    chosen: list[list[Blob]] = []
    for _, track in scored:
        ids = {id(blob) for blob in track}
        if any(len(ids & {id(blob) for blob in other}) > 0.6 * min(len(ids), len(other)) for other in chosen):
            continue
        chosen.append(track)
        if len(chosen) >= keep:
            break
    return chosen


def _grow(per_frame, times, track: list[Blob], scene: Scene, direction: np.ndarray, *, forward: bool,
          allow_bounce: bool = True) -> tuple[list[Blob], bool]:
    """Extend a track one frame at a time, allowing one bounce.

    The points on each side of the bounce are separate segments: prediction
    only ever uses the segment the walk is in.
    """
    track = list(track) if forward else list(reversed(track))
    bounced = not allow_bounce
    used_bounce = False
    segment = 0  # index in track where the current segment starts
    sign = 1.0 if forward else -1.0
    step = 1 if forward else -1
    stalls = 0
    index = track[-1].frame + step
    while 0 <= index < len(per_frame):
        last = track[-1]
        dt = abs(times[index] - times[last.frame])
        current = track[segment:]
        allowed = MAX_GAP_S if len(current) >= 3 else SHORT_GAP_S
        if dt > allowed + 1e-6:
            break
        choice = None
        if len(current) >= 2:
            predicted, velocity = _predict(current, times, times[index])
            speed_prev = math.hypot(*velocity)
            reach = speed_prev * dt
            gate = max(3.0, 0.3 * reach) + scene.ball_px
            best_distance = gate
            for blob in per_frame[index]:
                distance = math.hypot(blob.u - predicted[0], blob.v - predicted[1])
                if distance >= best_distance:
                    continue
                if not _steady(last, blob, velocity, dt, sign, scene):
                    continue
                if stalls >= 2 and _is_stall(last, blob, dt):
                    continue
                choice, best_distance = blob, distance
            if choice is None and not bounced and len(track) >= 3:
                choice = _bounce_step(per_frame[index], last, velocity, dt, sign, direction, reach, scene)
                if choice is not None:
                    bounced = True
                    used_bounce = True
                    segment = len(track)
        else:
            # First step of a new segment, just after the bounce: no velocity yet.
            previous = track[-2] if len(track) >= 2 else last
            jump = math.hypot(last.u - previous.u, last.v - previous.v)
            radius = 1.6 * jump + scene.ball_px + 4.0
            best_rank = None
            for blob in per_frame[index]:
                distance = math.hypot(blob.u - last.u, blob.v - last.v)
                if distance > radius or distance / max(dt, 1e-6) > scene.speed_px[1]:
                    continue
                if scene.strict_direction:
                    progress = sign * ((blob.u - last.u) * direction[0] + (blob.v - last.v) * direction[1])
                    if progress <= 0 or progress / max(dt, 1e-6) < scene.speed_px[0]:
                        continue
                rank = (blob.strength, -distance)
                if best_rank is None or rank > best_rank:
                    choice, best_rank = blob, rank
        if choice is not None:
            stalls = stalls + 1 if _is_stall(last, choice, dt) else 0
            track.append(choice)
        index += step
    return (track if forward else list(reversed(track))), used_bounce


def _is_stall(last: Blob, blob: Blob, dt: float) -> bool:
    return dt > 0 and math.hypot(blob.u - last.u, blob.v - last.v) < 3.0


def _steady(last: Blob, blob: Blob, velocity, dt: float, sign: float, scene: Scene) -> bool:
    """A ball keeps its speed and heading between bounces."""
    if dt <= 0:
        return False
    du, dv = (blob.u - last.u) / dt, (blob.v - last.v) / dt
    speed = math.hypot(du, dv)
    before = math.hypot(*velocity)
    if before * dt < 3.0 or speed * dt < 3.0:
        # Barely moving in the picture (a ball coming down the camera's line of sight).
        return speed <= max(2.5 * before, 3.0 / dt + scene.ball_px / dt)
    if not 0.4 * before <= speed <= 2.5 * before:
        return False
    # velocity points forward in time; in a backward walk the step runs against it.
    cos = sign * (du * velocity[0] + dv * velocity[1]) / (speed * before)
    return cos >= math.cos(math.radians(55))


def _bounce_step(blobs: list[Blob], last: Blob, velocity, dt: float, sign: float, direction: np.ndarray,
                 reach: float, scene: Scene) -> Blob | None:
    """The bounce: the path turns, and the picture speed may change a lot.

    Side-on, the ball still moves down the pitch through the bounce. From
    behind the bowler it can stall in the picture as it comes down and jump
    away after pitching, or move down the frame before the bounce and up it
    after, so there only distance and speed limits apply.
    """
    before = math.hypot(*velocity)
    radius = max(2.5 * reach, 3.0 * scene.ball_px) + scene.ball_px + 6.0
    choice, best = None, None
    for blob in blobs:
        du, dv = blob.u - last.u, blob.v - last.v
        distance = math.hypot(du, dv)
        if distance > radius or distance < 0.5:
            continue
        speed = distance / dt
        if speed > max(12.0 * before, scene.speed_px[1]):
            continue
        across = abs(-du * direction[1] + dv * direction[0])
        if scene.strict_direction:
            progress = sign * (du * direction[0] + dv * direction[1])
            if progress <= 0.5 or across > 0.6 * progress + scene.ball_px + 4.0:
                continue
        rank = (blob.strength, -distance)
        if best is None or rank > best:
            choice, best = blob, rank
    return choice


def _predict(track: list[Blob], times, t: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """Where the ball should be at time t, and its velocity forward in time."""
    last = track[-1]
    if len(track) == 1:
        return (last.u, last.v), (0.0, 0.0)
    a, b = track[-2], track[-1]
    dt = times[b.frame] - times[a.frame]
    if dt == 0:
        return (b.u, b.v), (0.0, 0.0)
    vel = ((b.u - a.u) / dt, (b.v - a.v) / dt)
    line = (b.u + vel[0] * (t - times[b.frame]), b.v + vel[1] * (t - times[b.frame]))
    if len(track) >= 3:
        c = track[-3]
        ts = np.array([times[c.frame], times[a.frame], times[b.frame]])
        if len(set(ts.tolist())) == 3:
            us = np.polyfit(ts, [c.u, a.u, b.u], 2)
            vs = np.polyfit(ts, [c.v, a.v, b.v], 2)
            curve = (float(np.polyval(us, t)), float(np.polyval(vs, t)))
            # Lean on the straight line: three points over-react to noise.
            return ((curve[0] + 2 * line[0]) / 3, (curve[1] + 2 * line[1]) / 3), vel
    return line, vel


def trim(track: list[Blob], times, ball_px: float = 4.0) -> list[Blob]:
    """Drop the blobs that do not sit on one smooth flight with one bounce."""
    track = sorted(track, key=lambda blob: blob.frame)
    limit_floor = max(2.0, 0.25 * ball_px)
    while len(track) > MIN_TRACK:
        residual = _kinked_residual(track, times)
        if residual is None:
            break
        worst = int(np.argmax(residual))
        limit = max(limit_floor, 3.0 * float(np.median(residual)))
        limit = min(limit, 4.0 * limit_floor)
        if residual[worst] <= limit:
            break
        del track[worst]
    return track


def _kinked_residual(track: list[Blob], times) -> np.ndarray | None:
    t = np.array([times[blob.frame] for blob in track])
    u = np.array([blob.u for blob in track])
    v = np.array([blob.v for blob in track])
    if len(t) < 5:
        return None
    best = None
    for k in range(2, len(t) - 1):
        for tk in (t[k - 1], (t[k - 1] + t[k]) / 2):
            tau = t - tk
            pre = tau <= 0
            rows = np.zeros((len(t), 5))
            rows[:, 0] = 1
            rows[pre, 1] = tau[pre]
            rows[pre, 2] = tau[pre] ** 2
            rows[~pre, 3] = tau[~pre]
            rows[~pre, 4] = tau[~pre] ** 2
            total = np.zeros(len(t))
            for values in (u, v):
                solution, *_ = np.linalg.lstsq(rows, values, rcond=None)
                total += (values - rows @ solution) ** 2
            cost = total.sum()
            if best is None or cost < best[0]:
                best = (cost, np.sqrt(total))
    # A flight with no bounce in view is one curve.
    rows = np.stack([np.ones_like(t), t - t[0], (t - t[0]) ** 2], axis=1)
    total = np.zeros(len(t))
    for values in (u, v):
        solution, *_ = np.linalg.lstsq(rows, values, rcond=None)
        total += (values - rows @ solution) ** 2
    if best is None or total.sum() <= best[0]:
        best = (total.sum(), np.sqrt(total))
    return best[1]


def _score(track: list[Blob], times, scene: Scene, direction: np.ndarray) -> float:
    first, last = track[0], track[-1]
    progress = (last.u - first.u) * direction[0] + (last.v - first.v) * direction[1]
    if progress <= 0:
        return -1e9
    span = times[last.frame] - times[first.frame]
    if span <= 0:
        return -1e9
    if progress / span < scene.speed_px[0]:
        return -1e9
    if progress < 0.15 * scene.pitch_px:
        return -1e9
    residual = _kinked_residual(track, times)
    rms = float(np.sqrt(np.mean(residual**2))) if residual is not None else 0.0
    gaps = sum(max(0, b.frame - a.frame - 1) for a, b in zip(track, track[1:]))
    return len(track) + 2.0 * min(progress / max(scene.pitch_px, 1.0), 1.0) - 0.6 * rms - 0.3 * gaps


def track_frames(frames: list[bytes], width: int, height: int) -> list[dict]:
    """Track a bright or dark ball in raw grey frames with no calibration.

    Used by tests and by footage that arrives without a calibration.
    """
    if len(frames) < 4 or width < 2 or height < 2:
        return []

    class _Raw:
        def __iter__(self):
            for index, raw in enumerate(frames):
                y = np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
                yield _RawFrame(index, y, np.full_like(y, 128))

    scene = Scene(
        width=width,
        height=height,
        direction=(1.0, 0.0),
        pitch_px=float(width),
        ball_px=max(3.0, width / 60),
        speed_px=(0.5, width * 60.0),
    )
    per_frame = detect(_Raw(), scene)
    times = [index / 25.0 for index in range(len(per_frame))]
    directions = []
    for direction in ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)):
        scene.direction = direction
        track = best_track(per_frame, times, scene)
        directions.append((len(track), track))
    track = max(directions, key=lambda item: item[0])[1]
    return [{"frame": blob.frame, "x": blob.u / width, "y": blob.v / height} for blob in track]


@dataclass
class _RawFrame:
    index: int
    y: np.ndarray
    v: np.ndarray
