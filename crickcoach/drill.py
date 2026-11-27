"""Choose the next set of balls from what the batter missed."""

from __future__ import annotations

from collections import Counter

WEAK = {"missed", "edged", "hit"}


def drill_plan(deliveries: list[dict], *, count: int = 6) -> dict:
    count = max(1, min(int(count), 18))
    weak = []
    for delivery in deliveries:
        pitch = delivery.get("pitch") or {}
        if not pitch.get("line") or not pitch.get("length"):
            continue
        outcome = delivery.get("outcome")
        if outcome in WEAK or (delivery.get("tracking") or {}).get("hit_stumps"):
            weak.append(delivery)
    if not weak:
        reason = "Groove: good length, off stump."
        return {"reason": reason, "balls": [_ball("off", "good", None, reason) for _ in range(count)]}
    tally = Counter((delivery["pitch"]["line"], delivery["pitch"]["length"]) for delivery in weak)
    line, length = tally.most_common(1)[0][0]
    paces = sorted(delivery["pace_kph"] for delivery in weak if delivery.get("pace_kph"))
    pace = _median(paces)
    reason = f"Drill: {length} on {line}. That is where the batter was beaten."
    return {"reason": reason, "balls": [_ball(line, length, pace, reason) for _ in range(count)]}


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return round((values[mid - 1] + values[mid]) / 2, 1)


def _ball(line: str, length: str, pace: float | None, note: str) -> dict:
    return {"line": line, "length": length, "pace_kph": pace, "note": note}
