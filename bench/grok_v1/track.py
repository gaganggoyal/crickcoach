"""Find the ball in a short delivery.

The background is the darkest value at each pixel, so a static pitch drops
out and a bright moving ball remains. The track is the long, smooth chain of
those small blobs.
"""

from __future__ import annotations

import math


def track_frames(frames: list[bytes], width: int, height: int) -> list[dict]:
    if len(frames) < 4 or width < 2 or height < 2:
        return []
    background = bytearray(frames[0])
    for frame in frames[1:]:
        for index, value in enumerate(frame):
            if value < background[index]:
                background[index] = value
    per_frame = []
    for frame in frames:
        mask = bytearray(width * height)
        for index, value in enumerate(frame):
            if value - background[index] > 40:
                mask[index] = 1
        per_frame.append(components(mask, width, height))
    return best_track(per_frame)


def components(mask: bytes | bytearray, width: int, height: int) -> list[dict]:
    seen = bytearray(width * height)
    found = []
    for start, on in enumerate(mask):
        if not on or seen[start]:
            continue
        stack = [start]
        seen[start] = 1
        count = 0
        sum_x = 0
        sum_y = 0
        while stack:
            index = stack.pop()
            count += 1
            x = index % width
            y = index // width
            sum_x += x
            sum_y += y
            if x > 0:
                _push(mask, seen, stack, index - 1)
            if x + 1 < width:
                _push(mask, seen, stack, index + 1)
            if y > 0:
                _push(mask, seen, stack, index - width)
            if y + 1 < height:
                _push(mask, seen, stack, index + width)
        if 3 <= count <= 180:
            found.append({"x": (sum_x / count) / width, "y": (sum_y / count) / height, "area": count})
    return found


def best_track(per_frame: list[list[dict]]) -> list[dict]:
    starts = []
    for index, blobs in enumerate(per_frame[:15]):
        for blob in blobs:
            starts.append((index, blob))
    tracks = []
    for index, blob in starts:
        track = _follow(per_frame, index, blob)
        if len(track) >= 6:
            tracks.append(track)
    if not tracks:
        return []
    return max(tracks, key=_score)


def _follow(per_frame: list[list[dict]], start: int, blob: dict) -> list[dict]:
    track = [{"frame": start, "x": blob["x"], "y": blob["y"]}]
    velocity = (0.0, 0.0)
    missed = 0
    index = start + 1
    while index < len(per_frame) and missed < 3:
        predicted_x = track[-1]["x"] + velocity[0]
        predicted_y = track[-1]["y"] + velocity[1]
        limit = 0.18 * (missed + 1)
        chosen = None
        chosen_distance = limit
        for blob in per_frame[index]:
            distance = math.hypot(blob["x"] - predicted_x, blob["y"] - predicted_y)
            if distance < chosen_distance:
                chosen = blob
                chosen_distance = distance
        if chosen is None:
            missed += 1
            index += 1
            continue
        missed = 0
        velocity = (chosen["x"] - track[-1]["x"], chosen["y"] - track[-1]["y"])
        track.append({"frame": index, "x": chosen["x"], "y": chosen["y"]})
        index += 1
    return track


def _score(track: list[dict]) -> tuple[float, float]:
    steps = [
        math.hypot(b["x"] - a["x"], b["y"] - a["y"])
        for a, b in zip(track, track[1:])
    ]
    if not steps:
        return (0, 0)
    mean = sum(steps) / len(steps)
    variance = sum((step - mean) ** 2 for step in steps) / len(steps)
    return (len(track), -variance)


def _push(mask, seen, stack, index: int) -> None:
    if mask[index] and not seen[index]:
        seen[index] = 1
        stack.append(index)
