"""Find the deliveries in a long net recording.

With a calibrated camera VANSH looks for the balls themselves: a fast small
object that flies down the pitch, pitches once, and fits gravity. Each flight
becomes one delivery window, padded so the bowler's action is in it.

Without a calibration it falls back to motion: a run-up and delivery is the
biggest burst of movement in a net, bigger than a walk back to the mark, so
the peaks of motion mark the balls.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np

from crickcoach.segment import Segment

BEFORE_S = 1.6  # the run-up and delivery stride, before the ball is first seen
AFTER_S = 1.0  # the shot and follow-through, after the ball is last seen
CHUNK_S = 6.0
OVERLAP_S = 2.0
MIN_GAP_S = 3.0


def find_deliveries(path: Path, calibration: dict | None = None, *,
                    progress: Callable[[float], None] | None = None) -> tuple[list[Segment], str]:
    """Delivery windows and how they were found ("flights" or "motion")."""
    from crickcoach.video import probe

    info = probe(path)
    duration = info.duration or 0.0
    if calibration and calibration.get("kind") in {"side", "behind"}:
        flights = find_flights(path, calibration, duration=duration, progress=progress)
        if flights:
            segments = []
            for flight in flights:
                start = max(0.0, flight["t_first"] - BEFORE_S)
                stop = min(duration or flight["t_last"] + AFTER_S, flight["t_last"] + AFTER_S)
                segments.append(Segment(round(start, 3), round(stop, 3), "Found by the ball's flight."))
            return _merge(segments), "flights"
    from crickcoach.video import motion_series

    times, energy, total = motion_series(path)
    return motion_bursts(times, energy, duration=total), "motion"


def find_flights(path: Path, calibration: dict, *, duration: float,
                 progress: Callable[[float], None] | None = None, max_width: int = 1280) -> list[dict]:
    """Every ball flight in the recording, scanned in overlapping pieces."""
    from crickcoach.flight import measure, scene_for
    from crickcoach.track import candidate_tracks, detect
    from crickcoach.video import FrameStream

    found: list[dict] = []
    start = 0.0
    if duration <= 0:
        duration = CHUNK_S
    while start < duration - 0.2:
        stop = min(duration, start + CHUNK_S)
        stream = FrameStream(path, start, stop, max_width=max_width)
        try:
            scene = scene_for(calibration, stream.width, stream.height)
        except ValueError:
            return []
        per_frame = detect(stream, scene)
        times = stream.times
        for track in candidate_tracks(per_frame, times, scene, keep=4):
            points = [{"t": times[b.frame], "x": b.u / stream.width, "y": b.v / stream.height} for b in track]
            measured = measure(points, calibration, stream.width, stream.height)
            if not _looks_like_a_ball(measured, len(points)):
                continue
            measured["points"] = [{"t": round(p["t"], 4), "x": round(p["x"], 4), "y": round(p["y"], 4)} for p in points]
            found.append({
                "t_first": points[0]["t"],
                "t_last": points[-1]["t"],
                "bounce_t": measured.get("bounce_t"),
                "score": len(points) + (10 if measured.get("confidence") == "ok" else 0),
                "measured": measured,
            })
        if progress:
            progress(min(1.0, stop / duration))
        if stop >= duration:
            break
        start = stop - OVERLAP_S
    return _dedupe(found)


def _looks_like_a_ball(measured: dict, count: int) -> bool:
    """A flight that obeys gravity and sits on a smooth curve is a ball, even if unsure where it pitched."""
    if measured.get("confidence") == "none" or count < 6:
        return False
    fit = measured.get("fit") or {}
    rms = fit.get("rms_px")
    if measured.get("camera") == "side":
        if rms is not None and rms > 3.0:
            return False
        gravity = fit.get("gravity_error")
        if gravity is not None and gravity > 5.0:
            return False
        return True
    if fit.get("three_d"):
        return True
    return count >= 9 and (rms is None or rms <= 4.0)


def _dedupe(flights: list[dict]) -> list[dict]:
    """One flight per ball: overlapping finds from neighbouring pieces are the same ball."""
    flights = sorted(flights, key=lambda item: -item["score"])
    kept: list[dict] = []
    for flight in flights:
        clash = False
        for other in kept:
            if flight["t_first"] < other["t_last"] + MIN_GAP_S and other["t_first"] < flight["t_last"] + MIN_GAP_S:
                clash = True
                break
        if not clash:
            kept.append(flight)
    return sorted(kept, key=lambda item: item["t_first"])


def _merge(segments: list[Segment]) -> list[Segment]:
    segments = sorted(segments, key=lambda seg: seg.t0)
    merged: list[Segment] = []
    for segment in segments:
        if merged and segment.t0 < merged[-1].t1 - 0.5:
            merged[-1] = Segment(merged[-1].t0, max(merged[-1].t1, segment.t1), merged[-1].note)
        else:
            merged.append(segment)
    return merged


def motion_bursts(times: list[float], energy: list[float], *, duration: float) -> list[Segment]:
    """Peaks of movement, at least five seconds apart, as delivery windows."""
    if len(times) < 5:
        return [Segment(0.0, round(max(duration, 0.0), 3), "Kept as one delivery. The clip is very short.")]
    values = np.asarray(energy, dtype=float)
    dt = float(np.median(np.diff(times))) if len(times) > 1 else 0.1
    width = max(1, int(round(0.6 / max(dt, 1e-3))))
    smooth = np.convolve(values, np.ones(width) / width, mode="same")
    middle = float(np.median(smooth))
    spread = 1.4826 * float(np.median(np.abs(smooth - middle))) + 1e-6
    threshold = middle + 2.5 * spread
    if float(smooth.max()) < middle + 0.02 and float(smooth.max()) - middle < 4 * spread:
        return [Segment(0.0, round(duration, 3), "Kept as one delivery. Nothing stood out from the motion.")]
    separation = max(1, int(round(5.0 / max(dt, 1e-3))))
    order = np.argsort(-smooth)
    peaks: list[int] = []
    for index in order:
        if smooth[index] < threshold:
            break
        if all(abs(index - other) >= separation for other in peaks):
            peaks.append(int(index))
    if not peaks:
        return [Segment(0.0, round(duration, 3), "Kept as one delivery. Nothing stood out from the motion.")]
    segments = []
    for index in sorted(peaks):
        moment = times[index]
        segments.append(Segment(round(max(0.0, moment - 2.5), 3), round(min(duration, moment + 2.0), 3),
                                "Found by motion. Calibrate the camera to find balls by their flight."))
    return _merge(segments)


def match_offset(side_bounces: list[float], behind_bounces: list[float], *, max_lag_s: float = 20.0,
                 tolerance_s: float = 0.12) -> tuple[float, int]:
    """Seconds to add to a side-camera time to reach the same moment on the camera behind.

    Every bounce is a sharp moment both cameras see, so the offset that lines
    up the most bounces is the clock difference. Returns (offset, matches).
    """
    side_bounces = sorted(t for t in side_bounces if t is not None)
    behind_bounces = sorted(t for t in behind_bounces if t is not None)
    best = (0.0, 0, float("inf"))
    for a in side_bounces:
        for b in behind_bounces:
            offset = b - a
            if abs(offset) > max_lag_s:
                continue
            gaps = []
            for x in side_bounces:
                nearest = min(behind_bounces, key=lambda y: abs(y - x - offset))
                gap = abs(nearest - x - offset)
                if gap <= tolerance_s:
                    gaps.append(nearest - x)
            if not gaps:
                continue
            refined = float(sorted(gaps)[len(gaps) // 2])
            spread = sum(abs(g - refined) for g in gaps) / len(gaps)
            if len(gaps) > best[1] or (len(gaps) == best[1] and spread < best[2]):
                best = (refined, len(gaps), spread)
    return round(best[0], 3), best[1]
