"""Pitch geometry for a calibrated camera.

World frame, in metres: the origin is the base of middle stump at the
bowler's end, y runs down the pitch to the batter's stumps at 20.12, x is
across the pitch and positive on the leg side of a right-handed batter, and
z is up.

A side camera is calibrated on the stumps at both ends. That fixes a
homography from the picture to the vertical plane through middle stump, so a
ball near that plane reads as (y, z): how far down the pitch and how high.

A camera behind the bowler is calibrated on the four places where the popping
creases meet the return creases. That fixes a homography from the picture to
the ground, so the bounce reads as (x, y). The same homography also gives the
camera's focal length and position, which is enough to fit a ball's flight in
three dimensions.
"""

from __future__ import annotations

import math

import numpy as np

STUMP_TO_STUMP_M = 20.12
STUMP_HEIGHT_M = 0.711
POPPING_M = 1.22
RETURN_HALF_M = 1.32
STUMPS_HALF_M = 0.1143
BALL_RADIUS_M = 0.036
RELEASE_HEIGHT_M = 2.1
RELEASE_Y_M = 1.0
GRAVITY = 9.81
# Air drag on a cricket ball, as deceleration per metre per (m/s): 0.5 rho Cd A / m.
DRAG_PER_M = 0.5 * 1.2 * 0.45 * math.pi * 0.036**2 / 0.160

# Ground points for the camera behind the bowler, in click order.
CREASE_CORNERS = (
    (-RETURN_HALF_M, POPPING_M),
    (RETURN_HALF_M, POPPING_M),
    (RETURN_HALF_M, STUMP_TO_STUMP_M - POPPING_M),
    (-RETURN_HALF_M, STUMP_TO_STUMP_M - POPPING_M),
)

# Points on the vertical plane through middle stump for the side camera: (y, z).
SIDE_POINTS = {
    "bowler_base": (0.0, 0.0),
    "bowler_top": (0.0, STUMP_HEIGHT_M),
    "batter_base": (STUMP_TO_STUMP_M, 0.0),
    "batter_top": (STUMP_TO_STUMP_M, STUMP_HEIGHT_M),
}


def homography(src, dst) -> np.ndarray:
    """Least-squares homography mapping src points onto dst points (four or more)."""
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    if src.shape != dst.shape or src.shape[0] < 4 or src.shape[1] != 2:
        raise ValueError("A calibration needs at least four matching points.")
    ts, src_n = _normalise(src)
    td, dst_n = _normalise(dst)
    rows = []
    for (x, y), (u, v) in zip(src_n, dst_n):
        rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, singular, vt = np.linalg.svd(np.asarray(rows))
    if singular[-2] < 1e-9:
        raise ValueError("The calibration points do not define a plane. Spread them out.")
    h = vt[-1].reshape(3, 3)
    h = np.linalg.inv(td) @ h @ ts
    if abs(h[2, 2]) < 1e-12:
        raise ValueError("The calibration points do not define a plane. Spread them out.")
    h = h / h[2, 2]
    _check_side(h, src, dst)
    return h


def _normalise(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    centre = points.mean(axis=0)
    spread = np.sqrt(((points - centre) ** 2).sum(axis=1)).mean()
    if spread < 1e-12:
        raise ValueError("The calibration points sit on top of each other.")
    scale = math.sqrt(2) / spread
    t = np.array([[scale, 0, -scale * centre[0]], [0, scale, -scale * centre[1]], [0, 0, 1]])
    out = (points - centre) * scale
    return t, out


def _check_side(h: np.ndarray, src: np.ndarray, dst: np.ndarray) -> None:
    """Every calibration point must land on the same side of the horizon."""
    w = h[2, 0] * src[:, 0] + h[2, 1] * src[:, 1] + h[2, 2]
    if not (np.all(w > 0) or np.all(w < 0)):
        raise ValueError("The clicks are in the wrong order. Start again and follow the steps.")


def apply_h(matrix, x: float, y: float) -> tuple[float, float]:
    m = np.asarray(matrix, dtype=float)
    w = m[2, 0] * x + m[2, 1] * y + m[2, 2]
    if abs(w) < 1e-12:
        raise ValueError("That point is off the calibrated plane.")
    return (
        float((m[0, 0] * x + m[0, 1] * y + m[0, 2]) / w),
        float((m[1, 0] * x + m[1, 1] * y + m[1, 2]) / w),
    )


def apply_many(matrix, points: np.ndarray) -> np.ndarray:
    m = np.asarray(matrix, dtype=float)
    pts = np.asarray(points, dtype=float)
    homo = np.c_[pts, np.ones(len(pts))] @ m.T
    w = homo[:, 2:3]
    w = np.where(np.abs(w) < 1e-12, np.nan, w)
    return homo[:, :2] / w


# --------------------------------------------------------------------------
# Calibrations from clicks. Clicks arrive as 0..1 of the frame.


def to_pixels(point: dict, width: int, height: int) -> tuple[float, float]:
    return float(point["x"]) * width, float(point["y"]) * height


def side_plane(calibration: dict, width: int, height: int) -> np.ndarray:
    """Picture (pixels) to the vertical plane through middle stump, (y, z) in metres."""
    src, dst = [], []
    for key, target in SIDE_POINTS.items():
        point = calibration.get(key)
        if point:
            src.append(to_pixels(point, width, height))
            dst.append(target)
    if len(src) < 4:
        raise ValueError("Click the base and the top of the stumps at both ends.")
    return homography(src, dst)


def ground_plane(calibration: dict, width: int, height: int) -> np.ndarray:
    """Picture (pixels) to the ground, (x, y) in metres."""
    corners = calibration.get("corners") or []
    if len(corners) != 4:
        raise ValueError("Click the four crease corners for the camera behind the bowler.")
    src = [to_pixels(point, width, height) for point in corners]
    return homography(src, CREASE_CORNERS)


def pixels_per_metre(calibration: dict, width: int, height: int) -> tuple[float, float]:
    """A rough scale near the bowler's end and near the batter's end, for sizing the ball."""
    if calibration.get("kind") == "side":
        near = _distance(calibration["bowler_base"], calibration["bowler_top"], width, height) / STUMP_HEIGHT_M
        far = _distance(calibration["batter_base"], calibration["batter_top"], width, height) / STUMP_HEIGHT_M
        return near, far
    corners = calibration["corners"]
    near = _distance(corners[0], corners[1], width, height) / (2 * RETURN_HALF_M)
    far = _distance(corners[2], corners[3], width, height) / (2 * RETURN_HALF_M)
    return near, far


def _distance(a: dict, b: dict, width: int, height: int) -> float:
    ax, ay = to_pixels(a, width, height)
    bx, by = to_pixels(b, width, height)
    return math.hypot(ax - bx, ay - by)


# --------------------------------------------------------------------------
# A full camera from the ground homography.


class PinholeCamera:
    def __init__(self, k: np.ndarray, r: np.ndarray, t: np.ndarray):
        self.k = k
        self.r = r
        self.t = t.reshape(3)
        self.centre = -r.T @ self.t

    def project(self, points: np.ndarray) -> np.ndarray:
        pts = np.atleast_2d(points)
        cam = pts @ self.r.T + self.t
        uv = cam @ self.k.T
        return uv[:, :2] / uv[:, 2:3]

    def ray(self, u: float, v: float) -> np.ndarray:
        local = np.linalg.solve(self.k, np.array([u, v, 1.0]))
        direction = self.r.T @ local
        return direction / np.linalg.norm(direction)


def camera_from_ground(image_to_ground: np.ndarray, width: int, height: int) -> PinholeCamera | None:
    """The camera behind the bowler, from its view of the ground (x, y)."""
    return camera_from_plane(image_to_ground, width, height, axes=(0, 1))


def camera_from_side(image_to_plane: np.ndarray, width: int, height: int) -> PinholeCamera | None:
    """The side camera, from its view of the vertical plane through the stumps (y, z)."""
    return camera_from_plane(image_to_plane, width, height, axes=(1, 2))


def camera_from_plane(image_to_plane: np.ndarray, width: int, height: int, *, axes: tuple[int, int]) -> PinholeCamera | None:
    """Focal length and pose from one view of a known plane.

    Assumes square pixels and a lens centred in the frame, which holds for
    CCTV and phone cameras closely enough. axes names the two world axes the
    plane coordinates run along: (0, 1) is the ground, (1, 2) is the vertical
    plane through middle stump.
    """
    try:
        h = np.linalg.inv(np.asarray(image_to_plane, dtype=float))  # plane to pixels
    except np.linalg.LinAlgError:
        return None
    cx, cy = width / 2 - 0.5, height / 2 - 0.5
    shift = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1.0]])
    hp = shift @ h
    h11, h21, h31 = hp[:, 0]
    h12, h22, h32 = hp[:, 1]
    candidates = []
    denominator = h31 * h32
    if abs(denominator) > 1e-15:
        f2 = -(h11 * h12 + h21 * h22) / denominator
        if f2 > 0:
            candidates.append(f2)
    denominator = h32 * h32 - h31 * h31
    if abs(denominator) > 1e-15:
        f2 = (h11 * h11 + h21 * h21 - h12 * h12 - h22 * h22) / denominator
        if f2 > 0:
            candidates.append(f2)
    focal = math.sqrt(float(np.median(candidates))) if candidates else None
    if focal is None or not (0.25 * width < focal < 8 * width):
        focal = (width / 2) / math.tan(math.radians(35))  # a 70 degree lens
    k = np.array([[focal, 0, cx], [0, focal, cy], [0, 0, 1.0]])
    m = np.linalg.solve(k, h)
    scale = 1.0 / np.linalg.norm(m[:, 0])
    first = m[:, 0] * scale
    second = m[:, 1] * scale
    t = m[:, 2] * scale
    if t[2] < 0:
        first, second, t = -first, -second, -t
    columns = [None, None, None]
    columns[axes[0]] = first
    columns[axes[1]] = second
    missing = 3 - axes[0] - axes[1]
    # The third axis completes a right-handed frame: x = y cross z, z = x cross y.
    if missing == 0:
        columns[0] = np.cross(columns[1], columns[2])
    elif missing == 2:
        columns[2] = np.cross(columns[0], columns[1])
    else:
        columns[1] = np.cross(columns[2], columns[0])
    u, _, vt = np.linalg.svd(np.stack(columns, axis=1))
    r = u @ vt
    if np.linalg.det(r) < 0:
        return None
    camera = PinholeCamera(k, r, t)
    if axes == (0, 1) and camera.centre[2] <= 0:
        return None
    return camera


# --------------------------------------------------------------------------
# Ball flight helpers.


def drag_decel(speed: float) -> float:
    return DRAG_PER_M * speed * speed


def release_speed(speed_seen: float, metres_from_release: float) -> float:
    """Undo the air drag between the release and the first sighting."""
    return speed_seen * math.exp(DRAG_PER_M * max(metres_from_release, 0.0))


def path_pace_kph(ground_m: float, height_m: float, seconds: float) -> float | None:
    if seconds <= 0.05 or ground_m <= 0:
        return None
    pace = math.hypot(ground_m, height_m) / seconds * 3.6
    if pace < 40 or pace > 180:
        return None
    return round(pace, 1)
