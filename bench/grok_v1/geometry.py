"""Pitch geometry for one calibrated camera.

A side camera gives length and pace. A camera behind the bowler gives line
and length. Angles in the picture are not joint angles. y in an image grows
downward. On the pitch, x is 0 on the off side and 1 on the leg side, and y
is 0 at the bowler's end and 1 at the batter's end.
"""

from __future__ import annotations

import math

STUMP_TO_STUMP_M = 20.12
STUMP_HEIGHT_M = 0.711
RELEASE_HEIGHT_M = 2.0


def homography(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> list[list[float]]:
    """Map four source points onto four destination points."""
    if len(src) != 4 or len(dst) != 4:
        raise ValueError("A pitch calibration needs four corners.")
    rows: list[list[float]] = []
    targets: list[float] = []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([x, y, 1, 0, 0, 0, -x * u, -y * u])
        targets.append(u)
        rows.append([0, 0, 0, x, y, 1, -x * v, -y * v])
        targets.append(v)
    solved = _solve(rows, targets)
    return [
        [solved[0], solved[1], solved[2]],
        [solved[3], solved[4], solved[5]],
        [solved[6], solved[7], 1.0],
    ]


def apply_h(matrix: list[list[float]], x: float, y: float) -> tuple[float, float]:
    denominator = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
    if abs(denominator) < 1e-9:
        raise ValueError("That point is off the calibrated pitch.")
    return (
        (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]) / denominator,
        (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]) / denominator,
    )


def along(point: dict, start: dict, end: dict) -> float:
    """0 at start and 1 at end, measured along the ground line."""
    gx = end["x"] - start["x"]
    gy = end["y"] - start["y"]
    length = gx * gx + gy * gy
    if length < 1e-8:
        raise ValueError("The two creases are on top of each other.")
    return ((point["x"] - start["x"]) * gx + (point["y"] - start["y"]) * gy) / length


def height_above(point: dict, start: dict, end: dict) -> float:
    """How far the point sits above the ground line, in fractions of the frame.

    Positive means the point is higher in the picture than the pitch.
    """
    gx = end["x"] - start["x"]
    gy = end["y"] - start["y"]
    if abs(gx) < 1e-6 and abs(gy) < 1e-6:
        raise ValueError("The two creases are on top of each other.")
    if abs(gx) >= abs(gy):
        line_y = start["y"] + (point["x"] - start["x"]) * gy / gx
        return line_y - point["y"]
    line_x = start["x"] + (point["y"] - start["y"]) * gx / gy
    # A steep ground line: "above" is the side toward the top of the frame.
    return start["x"] - point["x"] if gy >= 0 else point["x"] - line_x


def _solve(rows: list[list[float]], targets: list[float]) -> list[float]:
    size = len(targets)
    table = [row[:] + [targets[index]] for index, row in enumerate(rows)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(table[row][column]))
        if abs(table[pivot][column]) < 1e-12:
            raise ValueError("The calibration points do not define a pitch. Spread the four corners.")
        table[column], table[pivot] = table[pivot], table[column]
        scale = table[column][column]
        for entry in range(column, size + 1):
            table[column][entry] /= scale
        for row in range(size):
            if row == column:
                continue
            factor = table[row][column]
            if factor == 0:
                continue
            for entry in range(column, size + 1):
                table[row][entry] -= factor * table[column][entry]
    return [table[index][size] for index in range(size)]


def path_pace_kph(ground_m: float, height_m: float, seconds: float) -> float | None:
    if seconds <= 0.05 or ground_m <= 0:
        return None
    pace = math.hypot(ground_m, height_m) / seconds * 3.6
    if pace < 40 or pace > 180:
        return None
    return round(pace, 1)
