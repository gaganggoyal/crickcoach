"""Choose the next set of balls from what the batter found hard.

Each pitch zone (length, and line when a camera saw it) gets a rate: balls
where the batter was beaten, edged or bowled, out of balls whose outcome was
marked. The rate is smoothed, so one miss in one ball does not outweigh three
misses in eight. The drill repeats the hardest zone at the batter's pace.
"""

from __future__ import annotations

from collections import defaultdict

from crickcoach.pitch import LENGTH_TARGET_M, LINE_TARGET_M, clean_line

WEAK = {"missed", "edged", "bowled"}
MARKED = {"left", "defended", "attacked", "missed", "edged", "bowled"}


def drill_plan(deliveries: list[dict], *, count: int = 6) -> dict:
    count = max(1, min(int(count), 18))
    zones: dict[tuple[str, str | None], dict] = defaultdict(lambda: {"weak": 0, "marked": 0, "paces": []})
    for delivery in deliveries:
        pitch = delivery.get("pitch") or {}
        length = pitch.get("length")
        if not length:
            continue
        outcome = _outcome(delivery.get("outcome"))
        line = clean_line(pitch["line"]) if pitch.get("line") else None
        zone = zones[(length, line)]
        if outcome in MARKED:
            zone["marked"] += 1
            if outcome in WEAK:
                zone["weak"] += 1
                if delivery.get("pace_kph"):
                    zone["paces"].append(float(delivery["pace_kph"]))
    ranked = []
    for (length, line), zone in zones.items():
        if zone["weak"] == 0:
            continue
        rate = (zone["weak"] + 1) / (zone["marked"] + 3)
        ranked.append((rate, zone["weak"], length, line, zone))
    if not ranked:
        reason = "Groove: good length, just outside off stump."
        return {"reason": reason, "balls": [_ball("outside off", "good", None, reason) for _ in range(count)]}
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    rate, weak, length, line, zone = ranked[0]
    pace = _median(sorted(zone["paces"]))
    where = f"{length} length on {line}" if line else f"{length} length"
    reason = (
        f"Drill: {where}. The batter was beaten there {weak} of {zone['marked']} marked balls."
        if zone["marked"]
        else f"Drill: {where}."
    )
    aim_line = line or "outside off"
    if not line:
        reason += " No camera saw the line, so the machine aims just outside off."
    return {"reason": reason, "balls": [_ball(aim_line, length, pace, reason) for _ in range(count)]}


def _outcome(value: str | None) -> str | None:
    if value == "hit":  # the first version of VANSH called a ball on the stumps "hit"
        return "bowled"
    return value


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return round(values[mid], 1)
    return round((values[mid - 1] + values[mid]) / 2, 1)


def _ball(line: str, length: str, pace: float | None, note: str) -> dict:
    return {
        "line": line,
        "length": length,
        "pace_kph": pace,
        "note": note,
        "length_m": LENGTH_TARGET_M[length],
        "offset_m": LINE_TARGET_M[line],
    }
