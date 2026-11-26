"""Names for a place on the pitch.

x is 0 on the off side and 1 on the leg side, drawn for a right-hand batter.
y is 0 at the bowler's end and 1 at the batter's end. A yorker is near the
batter. A bouncer pitches much further up the pitch.
"""

from __future__ import annotations

LINES = ("wide off", "off", "middle", "leg", "wide leg")
LENGTHS = ("yorker", "full", "good", "short", "bouncer")


def describe_pitch(x: float, y: float) -> dict:
    x = _clamp(x)
    y = _clamp(y)
    line = _line(x)
    length = _length(y)
    return {
        "x": round(x, 4),
        "y": round(y, 4),
        "line": line,
        "length": length,
        "text": f"{length} on {line}",
    }


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _line(x: float) -> str:
    if x < 0.18:
        return "wide off"
    if x < 0.40:
        return "off"
    if x < 0.60:
        return "middle"
    if x < 0.82:
        return "leg"
    return "wide leg"


def _length(y: float) -> str:
    if y >= 0.86:
        return "yorker"
    if y >= 0.68:
        return "full"
    if y >= 0.46:
        return "good"
    if y >= 0.24:
        return "short"
    return "bouncer"
