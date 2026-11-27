"""Split a net recording into one window per delivery.

The cut is based on motion energy, not on the ball. A delivery is a burst of
movement with a quieter gap before the next one. A clip with no gap stays as
a single delivery so the coach can still review it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Segment:
    t0: float
    t1: float
    note: str = ""


def segment_motion(
    times: list[float],
    energy: list[float],
    *,
    duration: float | None = None,
) -> list[Segment]:
    end = duration if duration is not None else (times[-1] if times else 0.0)
    end = max(0.0, float(end))
    if end <= 0 and not times:
        return []
    if len(times) < 2 or len(energy) < 2:
        return [_whole(end, "Kept as one delivery. There was not enough motion to separate balls.")]

    count = min(len(times), len(energy))
    times = [float(value) for value in times[:count]]
    energy = [float(value) for value in energy[:count]]
    dt = _median_dt(times)
    window = max(1, min(15, int(round(0.30 / dt)))) if dt > 0 else 1
    smooth = _moving_average(energy, window)
    baseline = _percentile(smooth, 25)
    peak = _percentile(smooth, 95)
    spread = peak - baseline
    if spread < 0.02:
        return [_whole(end, "Kept as one delivery. The motion never dropped enough to separate balls.")]

    threshold = baseline + 0.40 * spread
    regions = _regions(times, smooth, threshold)
    regions = _merge_gaps(regions, 0.80)
    regions = [[a, b] for a, b in regions if b - a >= 0.25]
    regions = _split_long(regions, times, smooth, baseline, spread)
    if not regions:
        return [_whole(end, "Kept as one delivery. No separate bowling action stood out from the clip.")]

    padded = []
    for start, stop in regions:
        padded.append([max(0.0, start - 0.70), min(end, stop + 1.10)])
    padded = _merge_gaps(padded, 0.05)
    notes = ""
    segments = []
    for start, stop in padded:
        if stop - start > 5.5:
            note = "This passage is long. Scrub it if it holds more than one ball."
        else:
            note = notes
        segments.append(Segment(round(start, 3), round(max(start, stop), 3), note))
    return segments


def _whole(end: float, note: str) -> Segment:
    return Segment(0.0, round(max(end, 0.0), 3), note)


def _median_dt(times: list[float]) -> float:
    gaps = [times[i] - times[i - 1] for i in range(1, len(times)) if times[i] > times[i - 1]]
    if not gaps:
        return 0.1
    gaps.sort()
    return gaps[len(gaps) // 2]


def _moving_average(values: list[float], window: int) -> list[float]:
    if window <= 1:
        return list(values)
    half = window // 2
    smoothed = []
    for index in range(len(values)):
        start = max(0, index - half)
        stop = min(len(values), index + half + 1)
        smoothed.append(sum(values[start:stop]) / (stop - start))
    return smoothed


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100
    index = int(position)
    fraction = position - index
    if index + 1 >= len(ordered):
        return ordered[-1]
    return ordered[index] * (1 - fraction) + ordered[index + 1] * fraction


def _regions(times: list[float], energy: list[float], threshold: float) -> list[list[float]]:
    found: list[list[float]] = []
    start = None
    for index, (moment, value) in enumerate(zip(times, energy)):
        active = value >= threshold
        if active and start is None:
            start = moment
        if not active and start is not None:
            found.append([start, times[index - 1]])
            start = None
    if start is not None:
        found.append([start, times[-1]])
    return found


def _merge_gaps(regions: list[list[float]], gap: float) -> list[list[float]]:
    if not regions:
        return []
    ordered = sorted(regions)
    merged = [ordered[0][:]]
    for start, stop in ordered[1:]:
        if start <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], stop)
        else:
            merged.append([start, stop])
    return merged


def _split_long(
    regions: list[list[float]],
    times: list[float],
    smooth: list[float],
    baseline: float,
    spread: float,
) -> list[list[float]]:
    valley_line = baseline + 0.70 * spread
    output: list[list[float]] = []
    for start, stop in regions:
        if stop - start <= 5.0:
            output.append([start, stop])
            continue
        mid_a = start + 0.25 * (stop - start)
        mid_b = start + 0.75 * (stop - start)
        valley_at = None
        valley = 1e9
        for moment, value in zip(times, smooth):
            if mid_a <= moment <= mid_b and value < valley:
                valley = value
                valley_at = moment
        can_split = (
            valley_at is not None
            and valley < valley_line
            and valley_at - start > 0.4
            and stop - valley_at > 0.4
        )
        if not can_split:
            output.append([start, stop])
            continue
        output.extend(
            _split_long([[start, valley_at], [valley_at, stop]], times, smooth, baseline, spread)
        )
    return output
