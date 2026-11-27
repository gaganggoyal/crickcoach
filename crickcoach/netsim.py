"""A virtual cricket net with known answers.

The coach is tested against clips rendered here: a ball with gravity, air
drag, swing and a bounce; a bowler who runs in, bowls over the top and follows
through; a batter at the far end; and a CCTV camera with a real lens, a
shutter that smears the ball, sensor noise, and H.264 compression. Each clip
comes with the truth: release pace, where the ball pitched, and whether it
would have hit the stumps.

World frame, in metres. The origin is the base of middle stump at the
bowler's end. y runs down the pitch to the batter's stumps at y = 20.12.
x runs across the pitch and is positive on the leg side of a right-handed
batter, so the off side is negative x. z is up.
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

PITCH_LENGTH = 20.12
POPPING = 1.22
RETURN_HALF = 1.32
PITCH_HALF = 1.525
STUMP_HEIGHT = 0.711
STUMP_RADIUS = 0.019
STUMP_XS = (-0.0953, 0.0, 0.0953)
STUMPS_HALF = 0.1143
BALL_RADIUS = 0.036
BALL_MASS = 0.160
AIR_DENSITY = 1.2
DRAG_COEFFICIENT = 0.45
GRAVITY = 9.81

BALL_COLOURS = {
    "red": (150, 26, 30),
    "white": (236, 236, 228),
    "pink": (232, 118, 160),
}


# --------------------------------------------------------------------------
# Camera


@dataclass
class Camera:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    rotation: np.ndarray  # world to camera, rows are right, down, forward
    centre: np.ndarray

    def project(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pixels and depth for an (n, 3) array of world points."""
        points = np.atleast_2d(np.asarray(points, dtype=float))
        cam = (points - self.centre) @ self.rotation.T
        depth = cam[:, 2]
        safe = np.where(np.abs(depth) < 1e-6, 1e-6, depth)
        u = self.fx * cam[:, 0] / safe + self.cx
        v = self.fy * cam[:, 1] / safe + self.cy
        return np.stack([u, v], axis=1), depth

    def normalised(self, point: tuple[float, float, float]) -> dict:
        """A clicked point as the browser sends it: 0..1 of the frame."""
        uv, _ = self.project(np.array([point]))
        return {"x": float(uv[0, 0] / self.width), "y": float(uv[0, 1] / self.height)}

    def rays(self, scale: int = 1) -> np.ndarray:
        """World direction of the ray through every pixel, (h, w, 3)."""
        h, w = self.height * scale, self.width * scale
        us = (np.arange(w) + 0.5) / scale - 0.5
        vs = (np.arange(h) + 0.5) / scale - 0.5
        uu, vv = np.meshgrid(us, vs)
        local = np.stack([(uu - self.cx) / self.fx, (vv - self.cy) / self.fy, np.ones_like(uu)], axis=-1)
        return local @ self.rotation


def look_at(position, target, *, width: int = 1280, height: int = 720, hfov_deg: float = 60.0) -> Camera:
    centre = np.asarray(position, dtype=float)
    forward = np.asarray(target, dtype=float) - centre
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    fx = (width / 2) / math.tan(math.radians(hfov_deg) / 2)
    return Camera(
        width=width,
        height=height,
        fx=fx,
        fy=fx,
        cx=width / 2 - 0.5,
        cy=height / 2 - 0.5,
        rotation=np.stack([right, down, forward]),
        centre=centre,
    )


def side_camera(*, width: int = 1280, height: int = 720, distance: float = 12.0, mount: float = 2.6,
                along: float = 10.06, hfov_deg: float = 92.0, leg_side: bool = True) -> Camera:
    """A square-on camera beside the net, the usual CCTV spot."""
    x = distance if leg_side else -distance
    return look_at((x, along, mount), (0.0, along, 0.7), width=width, height=height, hfov_deg=hfov_deg)


def behind_camera(*, width: int = 1280, height: int = 720, back: float = 8.0, mount: float = 4.5,
                  hfov_deg: float = 40.0, offset: float = 0.0) -> Camera:
    """High behind the bowler's stumps, looking down the pitch."""
    return look_at((offset, -back, mount), (0.0, 15.0, 0.0), width=width, height=height, hfov_deg=hfov_deg)


def side_clicks(camera: Camera, *, jitter_px: float = 0.0, seed: int = 0) -> dict:
    """The four clicks the coach makes for a side camera: base and top of each set of stumps."""
    rng = np.random.default_rng(seed)
    points = {
        "bowler_base": (0.0, 0.0, 0.0),
        "bowler_top": (0.0, 0.0, STUMP_HEIGHT),
        "batter_base": (0.0, PITCH_LENGTH, 0.0),
        "batter_top": (0.0, PITCH_LENGTH, STUMP_HEIGHT),
    }
    return {key: _jitter(camera.normalised(point), camera, jitter_px, rng) for key, point in points.items()}


def behind_clicks(camera: Camera, *, jitter_px: float = 0.0, seed: int = 0) -> list[dict]:
    """Where each popping crease meets the return creases. Off then leg, bowler's end first."""
    rng = np.random.default_rng(seed)
    points = [
        (-RETURN_HALF, POPPING, 0.0),
        (RETURN_HALF, POPPING, 0.0),
        (RETURN_HALF, PITCH_LENGTH - POPPING, 0.0),
        (-RETURN_HALF, PITCH_LENGTH - POPPING, 0.0),
    ]
    return [_jitter(camera.normalised(point), camera, jitter_px, rng) for point in points]


def _jitter(point: dict, camera: Camera, jitter_px: float, rng) -> dict:
    if jitter_px <= 0:
        return point
    return {
        "x": point["x"] + rng.normal(0, jitter_px) / camera.width,
        "y": point["y"] + rng.normal(0, jitter_px) / camera.height,
    }


# --------------------------------------------------------------------------
# Ball flight


@dataclass
class Delivery:
    pace_kph: float
    bounce_x: float  # metres from middle stump, positive is leg side
    bounce_y: float  # metres from the bowler's stumps
    swing: float = 0.0  # lateral acceleration in the air, m/s^2
    seam_deg: float = 0.0  # change of direction off the pitch
    ball: str = "red"
    release_x: float = -0.28
    release_y: float = 1.0
    release_z: float = 2.18


@dataclass
class Truth:
    pace_kph: float
    release_t: float
    release: tuple[float, float, float]
    bounce_t: float
    bounce_x: float
    bounce_y: float
    length_m: float  # from the batter's stumps
    at_stumps_t: float | None
    at_stumps_x: float | None
    at_stumps_z: float | None
    hits_stumps: bool
    ball: str
    path: np.ndarray = field(repr=False)  # columns t, x, y, z

    def to_dict(self) -> dict:
        return {
            "pace_kph": round(self.pace_kph, 2),
            "release_t": round(self.release_t, 4),
            "release": [round(value, 3) for value in self.release],
            "bounce_t": round(self.bounce_t, 4),
            "bounce_x": round(self.bounce_x, 4),
            "bounce_y": round(self.bounce_y, 4),
            "length_m": round(self.length_m, 3),
            "at_stumps_x": None if self.at_stumps_x is None else round(self.at_stumps_x, 4),
            "at_stumps_z": None if self.at_stumps_z is None else round(self.at_stumps_z, 4),
            "hits_stumps": self.hits_stumps,
            "ball": self.ball,
        }

    def position(self, t: float | np.ndarray) -> np.ndarray:
        t = np.atleast_1d(np.asarray(t, dtype=float))
        cols = [np.interp(t, self.path[:, 0], self.path[:, index]) for index in (1, 2, 3)]
        return np.stack(cols, axis=1)


_K = 0.5 * AIR_DENSITY * DRAG_COEFFICIENT * math.pi * BALL_RADIUS**2 / BALL_MASS
_DT = 0.0005


def _accel(vel: np.ndarray, swing: float) -> np.ndarray:
    speed = float(np.linalg.norm(vel))
    a = -_K * speed * vel
    a[2] -= GRAVITY
    a[0] += swing
    return a


def _fly(p, v, swing: float, seam_deg: float, *, until_y: float = PITCH_LENGTH + 1.5, max_t: float = 3.0,
         dt: float = _DT):
    """Integrate one flight with midpoint steps. Returns the path and the first bounce (t, x, y)."""
    p = np.array(p, dtype=float)
    v = np.array(v, dtype=float)
    rows = [(0.0, *p)]
    bounce = None
    t = 0.0
    while t < max_t and p[1] < until_y:
        lateral = swing if bounce is None else 0.0
        a1 = _accel(v, lateral)
        a2 = _accel(v + a1 * dt / 2, lateral)
        before = p.copy()
        p = p + (v + a1 * dt / 2) * dt
        v = v + a2 * dt
        t += dt
        if p[2] <= BALL_RADIUS and v[2] < 0:
            share = (before[2] - BALL_RADIUS) / max(before[2] - p[2], 1e-12)
            share = min(max(share, 0.0), 1.0)
            contact = before + share * (p - before)
            contact[2] = BALL_RADIUS
            when = t - dt + share * dt
            if bounce is None:
                bounce = (when, float(contact[0]), float(contact[1]))
                # The pitch takes much of the downward speed and some of the pace,
                # and the seam can send the ball off at a new angle.
                horizontal = math.hypot(v[0], v[1]) * 0.86
                heading = math.atan2(v[0], v[1]) + math.radians(seam_deg)
                v = np.array([horizontal * math.sin(heading), horizontal * math.cos(heading), -0.52 * v[2]])
            else:
                v = np.array([v[0] * 0.8, v[1] * 0.8, -0.4 * v[2]])
            p = contact
            rows.append((when, *p))
            continue
        rows.append((t, *p))
    return np.array(rows), bounce


def simulate(delivery: Delivery, release_t: float = 0.0) -> Truth:
    """Aim the ball at the pitching point and fly it. Returns the truth."""
    release = np.array([delivery.release_x, delivery.release_y, delivery.release_z])
    speed = delivery.pace_kph / 3.6
    target = np.array([delivery.bounce_x, delivery.bounce_y])
    azimuth = math.atan2(target[0] - release[0], target[1] - release[1])
    elevation = -math.radians(6)
    for _ in range(3):
        low, high = -math.radians(35), math.radians(12)
        for _ in range(22):
            elevation = (low + high) / 2
            v = speed * np.array([
                math.cos(elevation) * math.sin(azimuth),
                math.cos(elevation) * math.cos(azimuth),
                math.sin(elevation),
            ])
            _, bounce = _fly(release, v, delivery.swing, 0.0, until_y=delivery.bounce_y + 3, max_t=2.0, dt=0.002)
            reached = bounce[2] if bounce else 1e9
            if reached < target[1]:
                low = elevation
            else:
                high = elevation
        if bounce is None:
            break
        miss = target[0] - bounce[1]
        azimuth += miss / max(target[1] - release[1], 1.0)
    v = speed * np.array([
        math.cos(elevation) * math.sin(azimuth),
        math.cos(elevation) * math.cos(azimuth),
        math.sin(elevation),
    ])
    path, bounce = _fly(release, v, delivery.swing, delivery.seam_deg)
    if bounce is None:
        raise ValueError("That delivery never pitched.")
    path[:, 0] += release_t
    at_t = at_x = at_z = None
    crossing = np.nonzero(path[:, 2] >= PITCH_LENGTH)[0]
    if crossing.size:
        index = int(crossing[0])
        before, after = path[index - 1], path[index]
        share = (PITCH_LENGTH - before[2]) / max(after[2] - before[2], 1e-9)
        at = before + share * (after - before)
        at_t, at_x, at_z = float(at[0]), float(at[1]), float(at[3])
    hits = bool(
        at_x is not None
        and abs(at_x) <= STUMPS_HALF + BALL_RADIUS
        and at_z <= STUMP_HEIGHT + BALL_RADIUS
    )
    return Truth(
        pace_kph=delivery.pace_kph,
        release_t=release_t,
        release=tuple(float(value) for value in release),
        bounce_t=float(bounce[0] + release_t),
        bounce_x=float(bounce[1]),
        bounce_y=float(bounce[2]),
        length_m=float(PITCH_LENGTH - bounce[2]),
        at_stumps_t=at_t,
        at_stumps_x=at_x,
        at_stumps_z=at_z,
        hits_stumps=hits,
        ball=delivery.ball,
        path=path,
    )


def random_delivery(rng: np.random.Generator, *, ball: str | None = None, pace: tuple[float, float] = (95, 145)) -> Delivery:
    """A plausible pace delivery: any length from yorker to bouncer, any line near the stumps."""
    length = float(rng.uniform(0.6, 13.0))
    return Delivery(
        pace_kph=float(rng.uniform(*pace)),
        bounce_x=float(rng.normal(-0.12, 0.28)),
        bounce_y=float(PITCH_LENGTH - length),
        swing=float(rng.normal(0, 0.7)),
        seam_deg=float(rng.normal(0, 1.2)),
        ball=ball or str(rng.choice(["red", "red", "white"])),
        release_x=float(rng.uniform(-0.45, -0.15)),
        release_y=float(rng.uniform(0.7, 1.4)),
        release_z=float(rng.uniform(2.0, 2.3)),
    )


# --------------------------------------------------------------------------
# People


def _smooth(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


RUN_UP = 2.0  # seconds of running before release
WALK_SPEED = 2.0


def _mark(delivery: Delivery) -> tuple[float, float]:
    """Where the bowler stands before the run-up."""
    return delivery.release_x - 0.2, delivery.release_y - 0.08 - 2.25 - 6.5 * (RUN_UP - 0.5)


def _follow_end(delivery: Delivery) -> tuple[float, float]:
    return delivery.release_x - 0.2 - 1.3, delivery.release_y - 0.08 + 1.875


def bowler_pose(t: float, releases: list[tuple[float, Delivery]]) -> tuple[list, tuple | None]:
    """Capsules (start, end, radius, colour) for the bowler, and the ball while it is in the hand."""
    if not releases:
        return [], None
    ordered = sorted(releases, key=lambda item: item[0])
    index = 0
    for position, (release_t, _) in enumerate(ordered):
        if t >= release_t - RUN_UP:
            index = position
    release_t, delivery = ordered[index]
    tau = t - release_t
    run_x = delivery.release_x - 0.2
    y0 = delivery.release_y - 0.08
    facing = 1.0
    moving = True
    if tau < -RUN_UP:
        x, y = _mark(delivery)
        speed, travelled, moving = 0.0, 0.0, False
    elif tau <= 0:
        if tau >= -0.5:
            y = y0 + 2.5 * tau - 4.0 * tau * tau
            speed = 2.5 + 8 * -tau
        else:
            y = y0 - 2.25 + 6.5 * (tau + 0.5)
            speed = 6.5
        x = run_x
        travelled = y
    elif tau <= 1.5:
        y = y0 + 2.5 * tau - (2.5 / 3.0) * tau * tau
        x = run_x - 1.3 * _smooth(tau / 1.5)
        speed = 2.5 * (1 - tau / 1.5)
        travelled = y
    else:
        start = np.array(_follow_end(delivery))
        target = np.array(_mark(ordered[index + 1][1] if index + 1 < len(ordered) else delivery))
        span = float(np.linalg.norm(target - start)) / WALK_SPEED
        share = min((tau - 1.5) / max(span, 1e-6), 1.0)
        x, y = start + (target - start) * share
        moving = share < 1.0
        speed = WALK_SPEED if moving else 0.0
        travelled = (tau - 1.5) * WALK_SPEED
        facing = -1.0 if moving else 1.0
    walking = speed < 3.0
    stride = 1.5 if walking else 2.1
    phase = 2 * math.pi * travelled / stride
    swing_amp = 0.0 if not moving or speed < 0.2 else (0.32 if walking else 0.55)
    hip = np.array([x, y, delivery.release_z - 1.14])
    neck = hip + np.array([0.0, 0.08 * facing, 0.5])
    head = neck + np.array([0.0, 0.03 * facing, 0.2])
    white = (232, 232, 226)
    skin = (176, 128, 100)
    shoe = (40, 40, 40)
    capsules = [
        (hip, neck, 0.16, white),
        (head, head, 0.11, skin),
    ]
    for side, sign in (("left", -1.0), ("right", 1.0)):
        leg_phase = phase + (0 if side == "left" else math.pi)
        thigh = swing_amp * math.sin(leg_phase)
        knee_bend = 0.25 + (0.5 * swing_amp) * (1 + math.cos(leg_phase))
        hip_j = hip + np.array([0.1 * sign, 0.0, -0.05])
        knee = hip_j + 0.46 * np.array([0.0, math.sin(thigh) * facing, -math.cos(thigh)])
        ankle = knee + 0.46 * np.array([0.0, math.sin(thigh - knee_bend) * facing, -math.cos(thigh - knee_bend)])
        capsules.append((hip_j, knee, 0.08, white))
        capsules.append((knee, ankle, 0.06, white))
        capsules.append((ankle, ankle + np.array([0, 0.12 * facing, 0]), 0.045, shoe))
    shoulder_l = neck + np.array([-0.2, 0.0, -0.04])
    shoulder_r = neck + np.array([0.2, 0.0, -0.04])
    arm_l = math.sin(phase) * swing_amp * 0.8
    elbow_l = shoulder_l + 0.3 * np.array([0.0, math.sin(arm_l) * facing, -math.cos(arm_l)])
    wrist_l = elbow_l + 0.28 * np.array([0.0, math.sin(arm_l + 0.4) * facing, -math.cos(arm_l + 0.4)])
    capsules.append((shoulder_l, elbow_l, 0.05, white))
    capsules.append((elbow_l, wrist_l, 0.04, skin))
    in_hand = None
    if -0.45 <= tau <= 0.25:
        # The bowling arm comes over the top: straight up at release.
        alpha = math.radians(-180 + 1200 * (tau + 0.15)) if tau >= -0.15 else math.radians(-180)
        alpha = min(alpha, math.radians(205))
        direction = np.array([0.0, math.sin(alpha), math.cos(alpha)])
        elbow_r = shoulder_r + 0.32 * direction
        wrist_r = elbow_r + 0.36 * direction
        if tau < 0:
            in_hand = tuple(wrist_r)
    else:
        arm_r = -math.sin(phase) * swing_amp * 0.8
        elbow_r = shoulder_r + 0.3 * np.array([0.0, math.sin(arm_r) * facing, -math.cos(arm_r)])
        wrist_r = elbow_r + 0.28 * np.array([0.0, math.sin(arm_r - 0.4) * facing, -math.cos(arm_r - 0.4)])
        if -RUN_UP <= tau < -0.45:
            in_hand = tuple(wrist_r)
    capsules.append((shoulder_r, elbow_r, 0.05, white))
    capsules.append((elbow_r, wrist_r, 0.04, skin))
    return capsules, in_hand


def batter_pose(t: float, releases: list[tuple[float, Delivery]]) -> list:
    """A right-handed batter at the far end who presses forward as the ball arrives."""
    arrive = None
    for release_t, delivery in releases:
        flight = PITCH_LENGTH / max(delivery.pace_kph / 3.6, 1.0)
        if release_t - 0.6 <= t <= release_t + flight + 1.2:
            arrive = release_t + flight * 0.85
            break
    press = 0.0 if arrive is None else _smooth((t - (arrive - 0.35)) / 0.3) * (1 - _smooth((t - (arrive + 0.6)) / 0.5))
    white = (232, 232, 226)
    skin = (170, 120, 92)
    pad = (240, 240, 235)
    bat = (214, 190, 140)
    helmet = (30, 40, 80)
    base_y = 19.35 - 0.45 * press
    hip = np.array([0.32, base_y + 0.15, 0.92 - 0.08 * press])
    neck = hip + np.array([-0.05, -0.08, 0.48])
    head = neck + np.array([-0.03, -0.06, 0.2])
    front_foot = np.array([0.22, base_y - 0.35 - 0.3 * press, 0.04])
    back_foot = np.array([0.40, base_y + 0.45, 0.04])
    front_knee = (hip + front_foot) / 2 + np.array([-0.05, -0.12, 0.05])
    back_knee = (hip + back_foot) / 2 + np.array([0.0, 0.05, 0.05])
    hands = hip + np.array([-0.25, -0.05 - 0.2 * press, 0.05 + 0.25 * (1 - press)])
    toe = np.array([0.05, base_y - 0.35 - 0.45 * press, 0.06 + 0.25 * (1 - press)])
    return [
        (hip, neck, 0.17, white),
        (head, head, 0.12, helmet),
        (hip, front_knee, 0.08, pad),
        (front_knee, front_foot, 0.08, pad),
        (hip, back_knee, 0.08, pad),
        (back_knee, back_foot, 0.08, pad),
        (neck + np.array([0.0, 0.0, -0.05]), hands, 0.05, white),
        (hands, hands + np.array([0.02, 0.02, -0.02]), 0.05, skin),
        (hands, toe, 0.055, bat),
    ]


# --------------------------------------------------------------------------
# Rendering


@dataclass
class Look:
    turf: tuple[int, int, int] = (72, 118, 74)  # synthetic net mat
    grass: tuple[int, int, int] = (64, 104, 56)
    backdrop: tuple[int, int, int] = (96, 110, 96)
    sky: tuple[int, int, int] = (196, 208, 220)
    noise: float = 3.0
    flicker: float = 0.01
    netting: bool = False


def _background(camera: Camera, look: Look, seed: int) -> np.ndarray:
    scale = 2
    rays = camera.rays(scale)
    rng = np.random.default_rng(seed)
    h, w = rays.shape[:2]
    image = np.empty((h, w, 3), dtype=np.float32)
    down = rays[..., 2] < -1e-6
    dist = np.where(down, -camera.centre[2] / np.where(down, rays[..., 2], -1), 0.0)
    gx = camera.centre[0] + rays[..., 0] * dist
    gy = camera.centre[1] + rays[..., 1] * dist
    ground = down & (dist < 90)
    elevation = np.degrees(np.arctan2(rays[..., 2], np.hypot(rays[..., 0], rays[..., 1])))
    sky_mix = np.clip((elevation - 1.0) / 12.0, 0, 1)[..., None]
    image[:] = np.array(look.backdrop, dtype=np.float32) * (1 - sky_mix) + np.array(look.sky, dtype=np.float32) * sky_mix
    on_pitch = ground & (np.abs(gx) <= PITCH_HALF + 0.6) & (gy > -3.0) & (gy < PITCH_LENGTH + 3.0)
    grass = ground & ~on_pitch
    image[grass] = look.grass
    image[on_pitch] = look.turf
    # Painted creases, 5 cm wide.
    half = 0.025
    lines = np.zeros(gx.shape, dtype=bool)
    for end in (0.0, PITCH_LENGTH):
        lines |= (np.abs(gy - end) < half) & (np.abs(gx) <= RETURN_HALF)
    for crease in (POPPING, PITCH_LENGTH - POPPING):
        lines |= (np.abs(gy - crease) < half) & (np.abs(gx) <= 1.83)
    for sign in (-1, 1):
        near = np.abs(gx - sign * RETURN_HALF) < half
        lines |= near & (gy > -1.22) & (gy < POPPING)
        lines |= near & (gy > PITCH_LENGTH - POPPING) & (gy < PITCH_LENGTH + 1.22)
    image[ground & lines] = (238, 238, 232)
    texture = rng.normal(0, 5.0, size=(h // 4 + 1, w // 4 + 1)).astype(np.float32)
    texture = np.kron(texture, np.ones((4, 4), dtype=np.float32))[:h, :w]
    image += (texture * ground)[..., None]
    image += rng.normal(0, 2.0, size=image.shape).astype(np.float32)
    image = image.reshape(h // scale, scale, w // scale, scale, 3).mean(axis=(1, 3))
    for end in (0.0, PITCH_LENGTH):
        for sx in STUMP_XS:
            _capsule(image, camera, np.array([sx, end, 0.0]), np.array([sx, end, STUMP_HEIGHT]), STUMP_RADIUS, (222, 204, 162))
    if look.netting:
        yy, xx = np.mgrid[0 : image.shape[0], 0 : image.shape[1]]
        mesh = ((xx % 38) < 2) | ((yy % 38) < 2)
        image[mesh] *= 0.62
    return image


def _capsule(image: np.ndarray, camera: Camera, a, b, radius: float, colour, alpha: float = 1.0) -> None:
    uv, depth = camera.project(np.stack([a, b]))
    if np.any(depth <= 0.05):
        return
    r0 = camera.fx * radius / depth[0]
    r1 = camera.fx * radius / depth[1]
    pad = max(r0, r1) + 2
    x0 = int(max(0, math.floor(min(uv[:, 0]) - pad)))
    x1 = int(min(image.shape[1], math.ceil(max(uv[:, 0]) + pad)))
    y0 = int(max(0, math.floor(min(uv[:, 1]) - pad)))
    y1 = int(min(image.shape[0], math.ceil(max(uv[:, 1]) + pad)))
    if x1 <= x0 or y1 <= y0:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    d = uv[1] - uv[0]
    length2 = float(d @ d)
    if length2 < 1e-9:
        share = np.zeros_like(xx)
    else:
        share = np.clip(((xx - uv[0, 0]) * d[0] + (yy - uv[0, 1]) * d[1]) / length2, 0, 1)
    px = uv[0, 0] + share * d[0]
    py = uv[0, 1] + share * d[1]
    dist = np.hypot(xx - px, yy - py)
    rad = r0 + share * (r1 - r0)
    cover = np.clip(rad - dist + 0.5, 0, 1)[..., None] * alpha
    patch = image[y0:y1, x0:x1]
    patch *= 1 - cover
    patch += cover * np.array(colour, dtype=np.float32)


def _ball(image: np.ndarray, camera: Camera, positions: np.ndarray, colour) -> None:
    """Draw the ball smeared across the shutter interval."""
    uv, depth = camera.project(positions)
    keep = depth > 0.2
    if not np.any(keep):
        return
    uv, depth = uv[keep], depth[keep]
    radius = camera.fx * BALL_RADIUS / depth
    pad = float(radius.max()) + 2
    x0 = int(max(0, math.floor(uv[:, 0].min() - pad)))
    x1 = int(min(image.shape[1], math.ceil(uv[:, 0].max() + pad)))
    y0 = int(max(0, math.floor(uv[:, 1].min() - pad)))
    y1 = int(min(image.shape[0], math.ceil(uv[:, 1].max() + pad)))
    if x1 <= x0 or y1 <= y0:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    cover = np.zeros(xx.shape, dtype=np.float32)
    for (u, v), r in zip(uv, radius):
        cover += np.clip(r - np.hypot(xx - u, yy - v) + 0.5, 0, 1)
    cover = (cover / len(uv))[..., None]
    patch = image[y0:y1, x0:x1]
    patch *= 1 - cover
    patch += cover * np.array(colour, dtype=np.float32)


@dataclass
class Clip:
    path: Path
    camera: Camera
    fps: float
    duration: float
    truths: list[Truth]
    exposure: float

    def calibration(self, kind: str, *, jitter_px: float = 0.0, seed: int = 0) -> dict:
        if kind == "side":
            return {"kind": "side", **side_clicks(self.camera, jitter_px=jitter_px, seed=seed)}
        return {"kind": "behind", "corners": behind_clicks(self.camera, jitter_px=jitter_px, seed=seed)}


def render(path: Path, camera: Camera, deliveries: list[tuple[float, Delivery]], *, duration: float,
           fps: float = 25.0, exposure: float = 1 / 250, look: Look | None = None, crf: int = 26,
           seed: int = 0, ffmpeg: str | None = None) -> Clip:
    """Render a net session to an H.264 file and return the truth for every ball."""
    look = look or Look()
    rng = np.random.default_rng(seed)
    truths = [simulate(delivery, release_t) for release_t, delivery in deliveries]
    background = _background(camera, look, seed)
    h, w = camera.height, camera.width
    bank = [rng.normal(0, look.noise, size=(h, w, 1)).astype(np.float32) for _ in range(6)]
    if ffmpeg is None:
        from crickcoach.video import ffmpeg_exe

        ffmpeg = ffmpeg_exe()
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-g", str(int(round(fps * 2))),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    frames = int(round(duration * fps))
    try:
        for index in range(frames):
            t = index / fps
            frame = background.copy()
            items = []
            bowler, in_hand = bowler_pose(t + exposure / 2, deliveries)
            items.append(("people", bowler))
            items.append(("people", batter_pose(t + exposure / 2, deliveries)))
            for truth in truths:
                end = truth.path[-1, 0]
                if t + exposure < truth.release_t or t > end:
                    continue
                times = np.linspace(max(t, truth.release_t), min(t + exposure, end), 12)
                items.append(("ball", (truth.position(times), BALL_COLOURS[truth.ball])))
            if in_hand is not None:
                ball_colour = BALL_COLOURS[min(deliveries, key=lambda item: abs(item[0] - t))[1].ball]
                items.append(("ball", (np.array([in_hand]), ball_colour)))
            ordered = []
            for kind, payload in items:
                if kind == "people":
                    centre = np.mean([np.asarray(c[0]) for c in payload], axis=0)
                else:
                    centre = payload[0].mean(axis=0)
                _, depth = camera.project(np.array([centre]))
                ordered.append((float(depth[0]), kind, payload))
            ordered.sort(key=lambda item: -item[0])
            for _, kind, payload in ordered:
                if kind == "people":
                    for a, b, radius, colour in payload:
                        _capsule(frame, camera, np.asarray(a), np.asarray(b), radius, colour)
                else:
                    _ball(frame, camera, payload[0], payload[1])
            frame *= 1 + rng.normal(0, look.flicker)
            frame += bank[index % len(bank)]
            np.clip(frame, 0, 255, out=frame)
            process.stdin.write(frame.astype(np.uint8).tobytes())
        process.stdin.close()
        error = process.stderr.read()
        code = process.wait(timeout=120)
    except BaseException:
        process.kill()
        raise
    if code != 0:
        raise RuntimeError(error.decode("utf-8", "replace")[-400:] or "ffmpeg failed")
    return Clip(path=path, camera=camera, fps=fps, duration=duration, truths=truths, exposure=exposure)


def one_ball(path: Path, camera: Camera, delivery: Delivery, *, fps: float = 25.0, exposure: float = 1 / 250,
             look: Look | None = None, crf: int = 26, seed: int = 0, release_t: float = 1.2,
             duration: float = 3.0) -> Clip:
    return render(path, camera, [(release_t, delivery)], duration=duration, fps=fps, exposure=exposure,
                  look=look, crf=crf, seed=seed)
