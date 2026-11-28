"""Score a bowling assessment from what the cameras measured.

An assessment is a short spell at the nets, twelve balls is plenty, bowled
with the cameras running. VANSH scores four things it measures on every ball:

- length: balls that pitched on a good length for the discipline
- line: balls that pitched in the off-stump channel (the camera behind the
  bowler measures it)
- pace: how fast the faster half of the balls were, on an age-group scale
  (pace bowling only)
- consistency: how tightly the balls grouped, in length and in line

Only the cameras' own readings count. A pitch mark or a pace the coach typed
in is left out, so a certificate says what the cameras saw and nothing more.
The rules live here, in one place, so the score, the certificate and the
public page that explains the score never disagree.
"""

from __future__ import annotations

from statistics import mean, pstdev

CAMERA_PACE = {"side", "side, corrected for line", "behind"}
MIN_MEASURED = 6  # balls with a camera length before VANSH will certify
MIN_LINE = 4  # balls with a camera line before line is scored
MIN_PACE = 4  # balls with a camera pace before pace is scored
MAX_BALLS = 36  # an assessment is a spell, not a whole session
STRENGTH = 75  # a part scoring this or more is a strength
WORK_ON = 80  # the weakest part, when below this, is the next thing to work on

DISCIPLINES = {
    "pace": {
        "name": "Pace bowling",
        "length": (6.0, 8.0),
        "length_near": (5.0, 9.0),
        "length_words": "a good length, 6 to 8 m from the batter's stumps",
        "length_short": "6–8 m from the stumps",
        "line": (-0.42, 0.0),
        "line_near": (-0.70, 0.15),
        "line_words": "the off-stump channel, from 30 cm outside off stump to middle stump",
        "line_short": "off-stump channel",
        "weights": {"length": 0.35, "line": 0.30, "pace": 0.20, "consistency": 0.15},
    },
    "spin": {
        "name": "Spin bowling",
        "length": (3.5, 6.0),
        "length_near": (2.5, 7.0),
        "length_words": "a spinner's length, 3.5 to 6 m from the batter's stumps",
        "length_short": "3.5–6 m from the stumps",
        "line": (-0.50, 0.12),
        "line_near": (-0.80, 0.40),
        "line_words": "from 40 cm outside off stump to leg stump",
        "line_short": "off stump to leg stump",
        "weights": {"length": 0.40, "line": 0.35, "consistency": 0.25},
    },
}

# Pace is scored on the faster half of the balls: the slow end of the scale
# scores 0 and the fast end 100.
AGE_GROUPS = {
    "u12": ("Under 12", 55.0, 95.0),
    "u14": ("Under 14", 65.0, 110.0),
    "u16": ("Under 16", 75.0, 125.0),
    "u19": ("Under 19", 85.0, 135.0),
    "open": ("Open", 90.0, 145.0),
}

# Standard deviation that scores 100 and standard deviation that scores 0.
LENGTH_SPREAD_M = (0.5, 2.5)
LINE_SPREAD_M = (0.06, 0.35)

LEVELS = ((85, 5, "Elite"), (70, 4, "Advanced"), (55, 3, "Club"), (40, 2, "Developing"), (0, 1, "Starter"))

PART_NAMES = {"length": "Length", "line": "Line", "pace": "Pace", "consistency": "Consistency"}


def assess(balls: list[dict], *, discipline: str = "pace", age_group: str = "open") -> dict:
    """The score analysis for a spell, in bowling order.

    Each ball is a delivery as VANSH stores it, with the batter on strike
    ("right" or "left") added, so the line is read for that batter.
    """
    if discipline not in DISCIPLINES:
        raise ValueError("Choose pace or spin bowling.")
    if age_group not in AGE_GROUPS:
        raise ValueError("Choose an age group from the list.")
    rules = DISCIPLINES[discipline]
    counted = list(balls[:MAX_BALLS])
    rows, typed = _rows(counted, rules)
    lengths = [row["length_m"] for row in rows if row["length_m"] is not None]
    lines = [row["line_m"] for row in rows if row["line_m"] is not None]
    paces = [row["pace_kph"] for row in rows if row["pace_kph"] is not None]

    parts = [_length_part(lengths, rules), _line_part(lines, rules)]
    if discipline == "pace":
        parts.append(_pace_part(paces, age_group))
    parts.append(_consistency_part(lengths, lines))
    scored = [part for part in parts if part["measured"]]
    total_weight = sum(rules["weights"][part["key"]] for part in scored)
    for part in parts:
        part["weight"] = round(rules["weights"][part["key"]] / total_weight, 3) if part["measured"] and total_weight else 0.0
    score = round(sum(part["score"] * part["weight"] for part in scored)) if scored else 0
    level, level_name = level_for(score)

    fast_half = sorted(paces, reverse=True)[: max(1, (len(paces) + 1) // 2)] if paces else []
    stats = {
        "balls": len(counted),
        "measured": len(lengths),
        "lined": len(lines),
        "paced": len(paces),
        "good_length": sum(1 for row in rows if row["length_points"] == 2),
        "on_line": sum(1 for row in rows if row["line_points"] == 2),
        "top_kph": round(max(paces), 1) if paces else None,
        "fast_half_kph": round(mean(fast_half), 1) if fast_half else None,
        "mean_kph": round(mean(paces), 1) if paces else None,
        "length_spread_m": round(pstdev(lengths), 2) if len(lengths) >= 2 else None,
        "line_spread_m": round(pstdev(lines), 3) if len(lines) >= 2 else None,
        "typed": typed,
        "left_out": max(0, len(balls) - MAX_BALLS),
    }
    why_not = _why_not(stats)
    return {
        "discipline": discipline,
        "discipline_name": rules["name"],
        "age_group": age_group,
        "age_group_name": AGE_GROUPS[age_group][0],
        "score": score,
        "level": level,
        "level_name": level_name,
        "parts": parts,
        "stats": stats,
        "strengths": _strengths(parts),
        "work_on": _work_on(parts, lengths, lines, rules),
        "notes": _notes(stats, parts),
        "balls": rows,
        "ready": not why_not,
        "why_not": why_not,
    }


def level_for(score: float) -> tuple[int, str]:
    for floor, level, name in LEVELS:
        if score >= floor:
            return level, name
    return 1, LEVELS[-1][2]


def points(value: float, target: tuple[float, float], near: tuple[float, float]) -> int:
    """2 on the target, 1 near it, 0 off it."""
    if target[0] <= value <= target[1]:
        return 2
    if near[0] <= value <= near[1]:
        return 1
    return 0


def _rows(balls: list[dict], rules: dict) -> tuple[list[dict], int]:
    rows = []
    typed = 0
    for number, ball in enumerate(balls, start=1):
        pitch = ball.get("pitch") or {}
        row = {"n": number, "length_m": None, "line_m": None, "pace_kph": None, "length_points": None, "line_points": None}
        was_typed = False
        if pitch.get("length_m") is not None:
            if ball.get("pitch_source") == "cameras":
                row["length_m"] = round(float(pitch["length_m"]), 2)
                row["length_points"] = points(row["length_m"], rules["length"], rules["length_near"])
                if pitch.get("offset_m") is not None:
                    offset = float(pitch["offset_m"])
                    # Negative is the off side of the batter on strike.
                    row["line_m"] = round(-offset if ball.get("batter") == "left" else offset, 3)
                    row["line_points"] = points(row["line_m"], rules["line"], rules["line_near"])
            else:
                was_typed = True
        if ball.get("pace_kph"):
            if ball.get("pace_source") in CAMERA_PACE:
                row["pace_kph"] = round(float(ball["pace_kph"]), 1)
            else:
                was_typed = True
        typed += was_typed
        rows.append(row)
    return rows, typed


def _scale(value: float, best: float, worst: float) -> int:
    """100 at best, 0 at worst, straight in between."""
    share = (value - worst) / (best - worst)
    return int(round(100 * min(1.0, max(0.0, share))))


def _length_part(lengths: list[float], rules: dict) -> dict:
    part = {"key": "length", "name": PART_NAMES["length"], "measured": bool(lengths), "score": None, "detail": ""}
    if not lengths:
        part["detail"] = "Not measured: the cameras did not place any ball."
        return part
    gained = sum(points(value, rules["length"], rules["length_near"]) for value in lengths)
    on = sum(1 for value in lengths if points(value, rules["length"], rules["length_near"]) == 2)
    part["score"] = int(round(100 * gained / (2 * len(lengths))))
    part["detail"] = f"{on} of {len(lengths)} on {rules['length_words']}."
    return part


def _line_part(lines: list[float], rules: dict) -> dict:
    part = {"key": "line", "name": PART_NAMES["line"], "measured": len(lines) >= MIN_LINE, "score": None, "detail": ""}
    if not part["measured"]:
        part["detail"] = "Not measured: the camera behind the bowler saw too few balls."
        return part
    gained = sum(points(value, rules["line"], rules["line_near"]) for value in lines)
    on = sum(1 for value in lines if points(value, rules["line"], rules["line_near"]) == 2)
    part["score"] = int(round(100 * gained / (2 * len(lines))))
    part["detail"] = f"{on} of {len(lines)} in {rules['line_words']}."
    return part


def _pace_part(paces: list[float], age_group: str) -> dict:
    name, slow, fast = AGE_GROUPS[age_group]
    part = {"key": "pace", "name": PART_NAMES["pace"], "measured": len(paces) >= MIN_PACE, "score": None, "detail": ""}
    if not part["measured"]:
        part["detail"] = "Not measured: the side camera timed too few balls."
        return part
    faster = sorted(paces, reverse=True)[: max(1, (len(paces) + 1) // 2)]
    speed = mean(faster)
    part["score"] = _scale(speed, fast, slow)
    part["detail"] = (f"The faster half averaged {speed:.0f} km/h, the fastest {max(paces):.0f} km/h "
                      f"({name} scale: {slow:.0f} km/h scores 0, {fast:.0f} km/h scores 100).")
    return part


def _consistency_part(lengths: list[float], lines: list[float]) -> dict:
    part = {"key": "consistency", "name": PART_NAMES["consistency"], "measured": len(lengths) >= 2, "score": None, "detail": ""}
    if not part["measured"]:
        part["detail"] = "Not measured: too few balls to see a spread."
        return part
    length_sd = pstdev(lengths)
    scores = [_scale(length_sd, *LENGTH_SPREAD_M)]
    detail = f"Lengths spread ±{length_sd:.1f} m"
    if len(lines) >= MIN_LINE:
        line_sd = pstdev(lines)
        scores.append(_scale(line_sd, *LINE_SPREAD_M))
        detail += f", lines ±{line_sd * 100:.0f} cm"
    part["score"] = int(round(mean(scores)))
    part["detail"] = detail + "."
    return part


def _why_not(stats: dict) -> str:
    if stats["measured"] < MIN_MEASURED:
        return (f"VANSH needs at least {MIN_MEASURED} balls placed by the cameras to certify a spell, "
                f"and has {stats['measured']}. Calibrate the cameras and press Track every ball.")
    if stats["measured"] * 3 < stats["balls"] * 2:
        return (f"The cameras placed only {stats['measured']} of {stats['balls']} balls. A certificate should not "
                "rest on the balls the cameras happened to see: calibrate, track every ball, then assess again.")
    return ""


def _strengths(parts: list[dict]) -> list[str]:
    good = sorted((part for part in parts if part["measured"] and part["score"] >= STRENGTH),
                  key=lambda part: -part["score"])
    return [f"{part['name']}: {part['detail']}" for part in good[:2]]


def _work_on(parts: list[dict], lengths: list[float], lines: list[float], rules: dict) -> list[str]:
    measured = [part for part in parts if part["measured"]]
    if not measured:
        return []
    weakest = min(measured, key=lambda part: part["score"])
    if weakest["score"] >= WORK_ON:
        return []
    key = weakest["key"]
    if key == "length":
        low, high = rules["length"]
        full = sum(1 for value in lengths if value < low)
        short = sum(1 for value in lengths if value > high)
        middle = (low + high) / 2
        if full >= short:
            return [f"Length: most misses were too full ({full} of {full + short}). "
                    f"Put a cone {middle:.1f} m from the batter's stumps and land the ball on it."]
        return [f"Length: most misses were too short ({short} of {full + short}). "
                f"Put a cone {middle:.1f} m from the batter's stumps and pitch the ball up to it."]
    if key == "line":
        low, high = rules["line"]
        wide = sum(1 for value in lines if value < low)
        leg = sum(1 for value in lines if value > high)
        if wide >= leg:
            return [f"Line: most misses were too wide of off stump ({wide} of {wide + leg}) to make the batter play. "
                    "Bring the line in to off stump."]
        return [f"Line: most misses drifted onto the pads ({leg} of {wide + leg}). "
                "Aim at off stump, with the front arm pulling towards it."]
    if key == "consistency":
        return [f"Consistency: {weakest['detail'][0].lower()}{weakest['detail'][1:-1]}. "
                "Bowl at one target from a marked run-up so the release repeats."]
    return ["Pace has the most room to grow. Work with the coach on run-up rhythm and a braced front leg."]


def _notes(stats: dict, parts: list[dict]) -> list[str]:
    notes = [f"The cameras placed {stats['measured']} of {stats['balls']} balls."]
    missing = [part["name"].lower() for part in parts if not part["measured"]]
    if missing:
        notes.append(f"Not measured, so not scored: {', '.join(missing)}.")
    if stats["typed"]:
        notes.append(f"{stats['typed']} ball{'s' if stats['typed'] != 1 else ''} had a reading typed in by the coach. "
                     "Only the cameras' own readings count.")
    if stats["left_out"]:
        notes.append(f"Only the first {MAX_BALLS} balls count; {stats['left_out']} later balls were left out.")
    return notes
