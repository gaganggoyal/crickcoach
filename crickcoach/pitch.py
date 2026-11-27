"""Names for where a ball pitched.

Length is metres from the batter's stumps, banded the way ball-tracking pitch
maps band it for pace bowling: yorker under 2 m, full 2 to 6 m, good 6 to 8 m,
short of a length 8 to 10 m, and short (bouncer territory) beyond 10 m.

Line is metres across from middle stump, positive on the leg side of a
right-handed batter. The stumps are 22.86 cm wide, so a ball whose centre is
within 15 cm of middle stump is on the stumps (the ball is 7.2 cm across).
For a left-handed batter the coach mirrors the line; the machine does too.
"""

from __future__ import annotations

LENGTHS = ("yorker", "full", "good", "short", "bouncer")
LINES = ("wide off", "outside off", "off stump", "middle", "leg stump", "down leg", "wide leg")

LENGTH_BANDS = ((2.0, "yorker"), (6.0, "full"), (8.0, "good"), (10.0, "short"))
LINE_BANDS = (
    (-0.60, "wide off"),
    (-0.15, "outside off"),
    (-0.05, "off stump"),
    (0.05, "middle"),
    (0.15, "leg stump"),
    (0.60, "down leg"),
)

# Where the machine aims for each name.
LENGTH_TARGET_M = {"yorker": 1.0, "full": 4.0, "good": 7.0, "short": 9.0, "bouncer": 11.5}
LINE_TARGET_M = {
    "wide off": -0.80,
    "outside off": -0.30,
    "off stump": -0.10,
    "middle": 0.0,
    "leg stump": 0.10,
    "down leg": 0.35,
    "wide leg": 0.80,
}

LENGTH_WORDS = {
    "yorker": "Yorker",
    "full": "Full",
    "good": "Good length",
    "short": "Short of a length",
    "bouncer": "Short",
}

# Old names written by the first version of VANSH.
_OLD_LINES = {"off": "outside off", "leg": "down leg"}


def length_name(length_m: float) -> str:
    for limit, name in LENGTH_BANDS:
        if length_m < limit:
            return name
    return "bouncer"


def line_name(offset_m: float) -> str:
    for limit, name in LINE_BANDS:
        if offset_m < limit:
            return name
    return "wide leg"


def describe(length_m: float | None, offset_m: float | None, batter: str = "right") -> dict | None:
    """The pitch mark the app stores and draws.

    offset_m stays where the ball really pitched (positive towards the leg
    side of a right-hander). The line is named for the batter on strike, so a
    left-hander's off side is the other side.
    """
    if length_m is None:
        return None
    length_m = max(-1.0, min(22.0, float(length_m)))
    length = length_name(length_m)
    line = None
    if offset_m is not None:
        line = line_name(-offset_m if batter == "left" else offset_m)
    words = f"{LENGTH_WORDS[length]}, {length_m:.1f} m"
    if line:
        words += f", {line}"
    else:
        words += ". Line not seen"
    return {
        "length_m": round(length_m, 2),
        "offset_m": None if offset_m is None else round(float(offset_m), 3),
        "length": length,
        "line": line,
        "text": words,
    }


def clean_line(line: str) -> str:
    line = (line or "").strip().lower()
    return _OLD_LINES.get(line, line)


def target_for(line: str, length: str) -> dict:
    """Metres the machine aims at for a named line and length."""
    return {"length_m": LENGTH_TARGET_M[length], "offset_m": LINE_TARGET_M[clean_line(line)]}
