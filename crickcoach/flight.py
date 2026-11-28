"""Turn a tracked ball and a camera calibration into pace, line and length.

The side camera owns length and pace: its track is mapped onto the vertical
plane through the stumps, where a ball's height and distance down the pitch
are read in metres. The camera behind the bowler owns the line: the bounce is
the one moment the ball touches the ground, so the ground homography gives
its true place. Both cameras fit the flight with gravity in it, find the
bounce to a fraction of a frame, and say how sure they are.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from crickcoach.geometry import (
    BALL_RADIUS_M,
    DRAG_PER_M,
    GRAVITY,
    RELEASE_HEIGHT_M,
    RELEASE_Y_M,
    STUMP_HEIGHT_M,
    STUMP_TO_STUMP_M,
    STUMPS_HALF_M,
    apply_h,
    apply_many,
    camera_from_ground,
    ground_plane,
    pixels_per_metre,
    release_speed,
    side_plane,
)
from crickcoach.pitch import describe

MIN_POINTS = 5


def analyse_clip(path: Path, t0: float, t1: float, calibration: dict) -> dict:
    from crickcoach.track import candidate_tracks, detect
    from crickcoach.video import FrameStream

    stream = FrameStream(path, t0, min(t1, t0 + 8.0))
    try:
        scene = scene_for(calibration, stream.width, stream.height)
    except ValueError as exc:
        return _empty(calibration.get("kind") or "side", str(exc))
    per_frame = detect(stream, scene)
    times = stream.times
    best = None
    for track in candidate_tracks(per_frame, times, scene, keep=8):
        points = [{"t": times[blob.frame], "x": blob.u / stream.width, "y": blob.v / stream.height} for blob in track]
        measured = measure(points, calibration, stream.width, stream.height)
        quality = _quality(measured, len(points))
        if best is None or quality > best[0]:
            best = (quality, measured, points)
    if best is None:
        measured = _empty(calibration.get("kind") or "side", "No ball flight stood out from the picture. Check the shutter speed and the calibration.")
        points = []
    else:
        _, measured, points = best
    measured["points"] = [{"t": round(p["t"], 4), "x": round(p["x"], 4), "y": round(p["y"], 4)} for p in points]
    measured["frames"] = len(times)
    return measured


def _quality(measured: dict, count: int) -> float:
    """How much a measured flight looks like a real ball. Higher is better.

    A handful of stray blobs can always be fitted with some bounce, so the
    count of points and the closeness of the fit count for more than the label.
    """
    rank = {"none": 0.0, "low": 1.0, "ok": 2.0}[measured.get("confidence", "none")]
    if rank == 0:
        return -1e9
    fit = measured.get("fit") or {}
    rms = fit.get("rms_px")
    gravity = fit.get("gravity_error")
    score = rank * 20.0 + count
    if rms is not None:
        score -= 3.0 * min(rms, 20.0)
    if gravity is not None:
        score -= 0.5 * min(gravity, 30.0)
    if fit.get("three_d"):
        score += 10.0
    return score


def scene_for(calibration: dict, width: int, height: int):
    """What the tracker should expect from this camera: size, speed and region of the ball."""
    from crickcoach.track import Scene

    kind = calibration.get("kind")
    near, far = pixels_per_metre(calibration, width, height)
    ppm_high, ppm_low = max(near, far), max(min(near, far), 1e-3)
    if kind == "side":
        plane = side_plane(calibration, width, height)
        back = np.linalg.inv(plane)
        start = apply_h(back, 0.0, 0.0)
        end = apply_h(back, STUMP_TO_STUMP_M, 0.0)
        box = [(-1.5, -0.5), (STUMP_TO_STUMP_M + 1.5, -0.5), (STUMP_TO_STUMP_M + 1.5, 4.0), (-1.5, 4.0)]
        roi = np.array([apply_h(back, y, z) for y, z in box])
        ball = 0.072 * ppm_high * 1.15
        speed = (0.5 * 12 * ppm_low, 1.4 * 46 * ppm_high)
    elif kind == "behind":
        ground = ground_plane(calibration, width, height)
        back = np.linalg.inv(ground)
        start = apply_h(back, 0.0, 1.22)
        end = apply_h(back, 0.0, STUMP_TO_STUMP_M - 1.22)
        camera = camera_from_ground(ground, width, height)
        roi = None
        ball = 0.072 * ppm_high * 1.1
        speed = (1.5 * ppm_low, 30 * ppm_high)
        if camera is not None:
            bounds = _flight_bounds(camera, width, height)
            if bounds is not None:
                speed, ball = bounds
            corners = np.array([[x, y, z] for x in (-2.2, 2.2) for y in (0.3, STUMP_TO_STUMP_M + 2.0) for z in (0.0, 3.4)])
            cam = corners @ camera.r.T + camera.t
            if np.all(cam[:, 2] > 0.5):
                import cv2

                projected = camera.project(corners).astype(np.float32)
                roi = cv2.convexHull(projected).reshape(-1, 2)
    else:
        raise ValueError("Calibrate this camera before tracking the ball.")
    direction = (end[0] - start[0], end[1] - start[1])
    pitch_px = math.hypot(*direction)
    if pitch_px < 10:
        raise ValueError("The calibration points are too close together.")
    ball = float(max(2.0, ball))
    return Scene(
        width=width,
        height=height,
        direction=(direction[0] / pitch_px, direction[1] / pitch_px),
        pitch_px=pitch_px,
        ball_px=ball,
        speed_px=(float(speed[0]), float(speed[1])),
        roi=roi,
        margin_px=float(max(4.0, 1.6 * ball)),
        strict_direction=kind == "side",
        background_gap_s=0.04 if kind == "side" else 0.12,
    )


def _flight_bounds(camera, width: int, height: int):
    """Picture speed and size of a ball, from flights thrown through this camera.

    Returns ((slowest mean speed / 2, fastest step speed * 1.6), largest ball diameter).
    A ball can nearly stop in the picture for a frame, but over its flight it
    always covers ground, so the floor is a mean over each flight.
    """
    means, peaks, sizes = [], [], []
    for pace in (55.0, 165.0):
        for length in (0.5, 7.0, 13.0):
            v = pace / 3.6
            reach = STUMP_TO_STUMP_M - length - RELEASE_Y_M
            flight = reach / (v * 0.95)
            vz = (-RELEASE_HEIGHT_M + GRAVITY * flight * flight / 2) / flight
            times = np.arange(0.0, flight + 0.6, 0.02)
            pts = []
            for t in times:
                if t <= flight:
                    pts.append((0.0, RELEASE_Y_M + v * t, RELEASE_HEIGHT_M + vz * t - GRAVITY * t * t / 2))
                else:
                    after = t - flight
                    up = 0.5 * abs(vz - GRAVITY * flight)
                    pts.append((0.0, RELEASE_Y_M + reach + 0.86 * v * after, max(0.0, up * after - GRAVITY * after * after / 2)))
            pts = np.array(pts)
            cam = pts @ camera.r.T + camera.t
            if np.any(cam[:, 2] <= 0.2):
                continue
            uv = camera.project(pts)
            inside = (uv[:, 0] >= 0) & (uv[:, 0] < width) & (uv[:, 1] >= 0) & (uv[:, 1] < height)
            both = inside[1:] & inside[:-1]
            if both.sum() < 3:
                continue
            step = np.hypot(*np.diff(uv, axis=0).T) / 0.02
            means.append(float(step[both].mean()))
            peaks.append(float(step[both].max()))
            sizes.extend((2 * camera.k[0, 0] * BALL_RADIUS_M / cam[inside, 2]).tolist())
    if not means or not sizes:
        return None
    return (0.5 * min(means), 1.6 * max(peaks)), max(sizes)


def measure(points: list[dict], calibration: dict, width: int = 1280, height: int = 720) -> dict:
    kind = calibration.get("kind")
    if kind not in {"side", "behind"}:
        return _empty("side", "Calibrate this camera before tracking the ball.")
    if len(points) < MIN_POINTS:
        return _empty(kind, "No ball flight stood out from the picture. Check the shutter speed and the calibration.")
    try:
        if kind == "side":
            return _side(points, calibration, width, height)
        return _behind(points, calibration, width, height)
    except ValueError as exc:
        return _empty(kind, str(exc))


# --------------------------------------------------------------------------
# Fitting a bounce.


def fit_bounce(t: np.ndarray, a: np.ndarray, b: np.ndarray, *, gravity_on: str | None, kink_on: str,
               floor: float | None = None) -> dict | None:
    """Two curves that meet at the bounce.

    a and b are two coordinates of the ball over time. Before and after the
    bounce each is a quadratic in time; both pieces share the bounce point.
    gravity_on names the coordinate that is height ("a" or "b"), whose
    curvature is pulled towards -g/2. kink_on names the coordinate whose
    velocity must turn at the bounce: height must go from falling to rising.
    """
    t = np.asarray(t, dtype=float)
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    keep = np.isfinite(a) & np.isfinite(b)
    t, a, b = t[keep], a[keep], b[keep]
    if len(t) < MIN_POINTS:
        return None
    best = None
    lo, hi = t[0] + 0.004, t[-1] + 0.12
    for tb in np.arange(lo, hi, 0.0015):
        fitted = _fit_at(t, a, b, tb, gravity_on, kink_on)
        if fitted is None:
            continue
        if best is None or fitted["cost"] < best["cost"]:
            best = fitted
    if best is None:
        return None
    # One pass of outlier rejection, then refit around the best bounce.
    residual = best["residual"]
    scale = max(np.median(residual) * 3.0, 0.08)
    good = residual <= scale
    if good.sum() >= MIN_POINTS and good.sum() < len(t):
        t, a, b = t[good], a[good], b[good]
        best = None
        for tb in np.arange(t[0] + 0.004, t[-1] + 0.12, 0.0015):
            fitted = _fit_at(t, a, b, tb, gravity_on, kink_on)
            if fitted is not None and (best is None or fitted["cost"] < best["cost"]):
                best = fitted
        if best is None:
            return None
    best["t"] = t
    best["dropped"] = int((~good).sum()) if good.sum() >= MIN_POINTS else 0
    return best


def _fit_at(t, a, b, tb, gravity_on, kink_on) -> dict | None:
    tau = t - tb
    pre = tau <= 0
    post = ~pre
    n_pre, n_post = int(pre.sum()), int(post.sum())
    if n_pre < 2 or (n_pre == 2 and n_post < 4):
        return None
    one_sided = n_post < 2
    coeffs = {}
    cost = 0.0
    residual = np.zeros(len(t))
    for name, values in (("a", a), ("b", b)):
        rows, target = [], []
        for k in range(len(t)):
            if pre[k]:
                rows.append([1.0, tau[k], tau[k] ** 2, 0.0, 0.0])
            else:
                rows.append([1.0, 0.0, 0.0, tau[k], tau[k] ** 2])
            target.append(values[k])
        weight = 0.0
        if gravity_on == name:
            # Curvature of height is -g/2 in both pieces. Hold it there gently.
            weight = 0.02
            rows.append([0, 0, weight, 0, 0])
            target.append(weight * -GRAVITY / 2)
            rows.append([0, 0, 0, 0, weight])
            target.append(weight * -GRAVITY / 2)
            # At the bounce the ball is on the pitch. A few centimetres of
            # calibration error are allowed; a bounce in mid-air is not.
            rows.append([0.5, 0, 0, 0, 0])
            target.append(0.5 * BALL_RADIUS_M)
        if n_pre == 2:
            # Two sightings before the bounce: hold that piece close to a straight line.
            rows.append([0, 0, 0.3, 0, 0])
            target.append(0.3 * (-GRAVITY / 2 if gravity_on == name else 0.0))
        if one_sided:
            rows.append([0, 0, 0, 1e-3, 0])
            target.append(0.0)
            rows.append([0, 0, 0, 0, 1e-3])
            target.append(0.0)
        matrix = np.asarray(rows)
        solution, *_ = np.linalg.lstsq(matrix, np.asarray(target), rcond=None)
        fitted = matrix[: len(t)] @ solution
        diff = values - fitted
        residual += diff**2
        cost += float((diff**2).sum())
        coeffs[name] = solution
    k = coeffs[kink_on]
    if gravity_on == kink_on:
        if k[1] > 0.3:  # still rising into the bounce
            return None
        if not one_sided and k[3] < 0.3:  # not rising out of it
            return None
    else:
        if not one_sided and abs(k[3]) < abs(k[1]) * 1.05:
            return None
    # Prefer the bounce that leaves points on both sides.
    if one_sided:
        cost *= 1.25
        cost += 1e-4
    return {"tb": float(tb), "coeffs": coeffs, "cost": cost, "residual": np.sqrt(residual),
            "n_pre": n_pre, "n_post": n_post, "one_sided": one_sided}


def _poly(c: np.ndarray, tau: float, after: bool) -> tuple[float, float]:
    """Value and slope of one coordinate tau seconds from the bounce."""
    if after:
        return c[0] + c[3] * tau + c[4] * tau * tau, c[3] + 2 * c[4] * tau
    return c[0] + c[1] * tau + c[2] * tau * tau, c[1] + 2 * c[2] * tau


# --------------------------------------------------------------------------
# The side camera.


def _side(points: list[dict], calibration: dict, width: int, height: int) -> dict:
    plane = side_plane(calibration, width, height)
    pixels = np.array([[p["x"] * width, p["y"] * height] for p in points])
    t = np.array([p["t"] for p in points])
    yz = apply_many(plane, pixels)
    along, up = yz[:, 0], yz[:, 1]
    fit = fit_bounce(t, up, along, gravity_on="a", kink_on="a")
    if fit is None:
        return _empty("side", "The ball was seen, but the flight did not fit a bounce. Check the calibration clicks.")
    tb = fit["tb"]
    z_fit, y_fit = fit["coeffs"]["a"], fit["coeffs"]["b"]
    bounce_y = float(y_fit[0])
    bounce_z = float(z_fit[0])
    notes = []
    confidence = "ok"
    if fit["one_sided"]:
        # No sight of the ball after it pitched: take the moment the falling curve meets the pitch.
        tb, bounce_y = _ground_crossing(z_fit, y_fit, tb)
        confidence = "low"
        notes.append("The ball was lost before it pitched, so the bounce comes from the falling curve.")
    if abs(bounce_z - BALL_RADIUS_M) > 0.35 and not fit["one_sided"]:
        confidence = "low"
        notes.append("The bounce sits off the pitch line. Calibrate again on the stumps.")
    curvature = 2 * z_fit[2]
    if abs(curvature + GRAVITY) > 6 and fit["n_pre"] >= 5:
        confidence = "low"
        notes.append("The ball's drop does not match gravity, so the camera scale is suspect.")
    if len(fit["t"]) < 7:
        confidence = "low"
        notes.append("Only a few sightings of the ball, so treat this as a rough read.")
    t_first = float(fit["t"][0])
    y_first, vy = _poly(y_fit, t_first - tb, after=False)
    _, vz = _poly(z_fit, t_first - tb, after=False)
    pace = _pace(math.hypot(vy, vz), y_first - RELEASE_Y_M)
    if pace is None:
        confidence = "low"
        notes.append("The pace fell outside 40 to 180 km/h, so it was left out.")
    length = STUMP_TO_STUMP_M - bounce_y
    pitch = describe(length, None)
    stumps = _side_stumps(y_fit, z_fit, tb, fit)
    notes.append("Length and pace come from the side camera. It cannot see the line.")
    rms_m = float(math.sqrt(fit["cost"] / max(len(fit["t"]), 1)))
    near, far = pixels_per_metre(calibration, width, height)
    # Which side of the pitch the camera stands on: from the leg side of a
    # right-hander the bowler's stumps are on the left of the picture.
    leg_side = calibration["bowler_base"]["x"] < calibration["batter_base"]["x"]
    return {
        "camera": "side",
        "parallax": {"distance_m": float(calibration.get("distance_m") or 12.0), "sign": 1 if leg_side else -1},
        "confidence": confidence,
        "note": " ".join(notes),
        "pace_kph": pace,
        "pitch": pitch,
        "bounce_t": round(tb, 4),
        "stumps": stumps,
        "hit_stumps": None,
        "fit": {
            "rms_m": round(rms_m, 4),
            "rms_px": round(rms_m * (near + far) / 2, 2),
            "gravity_error": round(abs(curvature + GRAVITY), 2),
            "points_used": int(len(fit["t"])),
        },
        "points": [],
    }


def _ground_crossing(z_fit, y_fit, tb: float) -> tuple[float, float]:
    # Solve z(tau) = ball radius on the falling piece, after the last seen point.
    c0, c1, c2 = z_fit[0] - BALL_RADIUS_M, z_fit[1], z_fit[2]
    roots = np.roots([c2, c1, c0]) if abs(c2) > 1e-9 else np.array([-c0 / c1]) if abs(c1) > 1e-9 else np.array([])
    real = [float(r.real) for r in np.atleast_1d(roots) if abs(r.imag) < 1e-9 and r.real > -0.5]
    tau = min(real, key=abs) if real else 0.0
    y, _ = _poly(y_fit, tau, after=False)
    return tb + tau, float(y)


def _side_stumps(y_fit, z_fit, tb: float, fit: dict) -> dict:
    if fit["one_sided"] or fit["n_post"] < 2:
        return {"height_m": None, "offset_m": None, "would_hit": None}
    c0, c1, c2 = y_fit[0] - STUMP_TO_STUMP_M, y_fit[3], y_fit[4]
    roots = np.roots([c2, c1, c0]) if abs(c2) > 1e-9 else np.array([-c0 / c1])
    taus = [float(r.real) for r in np.atleast_1d(roots) if abs(r.imag) < 1e-9 and 0 < r.real < 0.6]
    if not taus:
        return {"height_m": None, "offset_m": None, "would_hit": None}
    tau = min(taus)
    last_seen = float(fit["t"][-1]) - tb
    if tau - last_seen > 0.25:
        return {"height_m": None, "offset_m": None, "would_hit": None}
    z, _ = _poly(z_fit, tau, after=True)
    return {"height_m": round(float(z), 3), "offset_m": None, "would_hit": None}


# --------------------------------------------------------------------------
# The camera behind the bowler.


def _behind(points: list[dict], calibration: dict, width: int, height: int) -> dict:
    """Line and length from the camera behind the bowler.

    The bounce is the one moment the ball is on the ground, so its place in
    the picture maps exactly through the ground homography. The picture track
    has a sharp kink there; a three-dimensional fit of the flight times that
    kink more finely, and also says whether the ball would go on to hit the
    stumps. The location itself never depends on the fitted camera, so a small
    error in the four clicks stays a small error on the pitch map.
    """
    ground = ground_plane(calibration, width, height)
    pixels = np.array([[p["x"] * width, p["y"] * height] for p in points])
    t = np.array([p["t"] for p in points])
    kink = fit_bounce(t, pixels[:, 1], pixels[:, 0], gravity_on=None, kink_on="a")
    if kink is None:
        return _empty("behind", "The ball was seen, but its path did not show a bounce. Check the four crease clicks.")
    tb = kink["tb"]
    confidence = "ok" if not kink["one_sided"] else "low"
    notes = ["Line and length come from where the ball touched the pitch."]
    camera = camera_from_ground(ground, width, height)
    stumps = {"height_m": None, "offset_m": None, "would_hit": None}
    release = None
    solved = None
    if camera is not None and not kink["one_sided"]:
        start_bounce = apply_h(ground, kink["coeffs"]["b"][0], kink["coeffs"]["a"][0])
        kept = np.isin(t, kink["t"])
        solved = _fit_joint(camera, pixels[kept], t[kept], start_bounce, tb)
    if solved is not None and abs(solved["tb"] - tb) < 0.06:
        tb = solved["tb"]
        if solved.get("at_stumps") is not None:
            x_s, z_s = solved["at_stumps"]
            would = abs(x_s) <= STUMPS_HALF_M + BALL_RADIUS_M and 0 <= z_s <= STUMP_HEIGHT_M + BALL_RADIUS_M
            stumps = {"height_m": round(z_s, 3), "offset_m": round(x_s, 3), "would_hit": bool(would)}
        if solved.get("release"):
            position, _ = solved["release"]
            release = {"height_m": round(float(position[2]), 2), "y_m": round(float(position[1]), 2),
                       "x_m": round(float(position[0]), 3)}
    elif kink["one_sided"]:
        notes.append("The ball was lost before it pitched, so check the mark.")
    # The fit only times the bounce. Where the ball pitched comes from the picture
    # through the ground homography: the fitted camera turns a 1.5 px click
    # error into 15 cm of line, the homography into 2 cm.
    u_b, v_b = _kink_pixel(kink["t"], _pick(kink, t, pixels[:, 0]), _pick(kink, t, pixels[:, 1]), tb,
                           fallback=(kink["coeffs"]["b"][0], kink["coeffs"]["a"][0]))
    bounce = apply_h(ground, u_b, v_b)
    length_m = STUMP_TO_STUMP_M - bounce[1]
    if not -0.5 <= length_m <= STUMP_TO_STUMP_M - 0.7:
        # A "bounce" behind the batter's stumps or behind the bowling crease is not
        # the ball pitching: the flight before the bounce was hidden, usually by
        # the bowler's follow-through, and the kink found is something later.
        return _empty("behind", "The ball was hidden before it pitched, often behind the bowler's follow-through, "
                                "so this camera could not place the bounce.")
    if kink["n_pre"] < 2 or kink["n_post"] < 2:
        confidence = "low"
    residual_px = float(np.sqrt(np.mean(kink["residual"] ** 2)))
    if residual_px > 3.0:
        confidence = "low"
        notes.append("The track is ragged, so check the mark.")
    pitch = describe(STUMP_TO_STUMP_M - bounce[1], bounce[0])
    notes.append("This camera does not time the ball's pace; the side camera or a radar does.")
    return {
        "camera": "behind",
        "confidence": confidence,
        "note": " ".join(notes),
        "pace_kph": None,
        "pitch": pitch,
        "bounce_t": round(tb, 4),
        "stumps": stumps,
        "hit_stumps": stumps["would_hit"],
        "release": release,
        "fit": {
            "rms_px": round(residual_px, 2),
            "three_d": solved is not None,
            "points_used": int(len(kink["t"])),
        },
        "points": [],
    }


def _kink_pixel(t: np.ndarray, u: np.ndarray, v: np.ndarray, tb: float, *, fallback) -> tuple[float, float]:
    """Where the ball was in the picture at the bounce, from the sightings either side of it.

    A short local fit: a curve over the last few sightings before the bounce and
    a line over the first few after it, joined at the bounce.
    """
    before = np.nonzero(t <= tb)[0][-4:]
    after = np.nonzero(t > tb)[0][:3]
    if len(before) < 2 or len(after) < 2:
        return float(fallback[0]), float(fallback[1])
    chosen = np.concatenate([before, after])
    tau = t[chosen] - tb
    pre = tau <= 0
    rows = np.zeros((len(chosen), 4))
    rows[:, 0] = 1.0
    rows[pre, 1] = tau[pre]
    if pre.sum() >= 3:
        rows[pre, 2] = tau[pre] ** 2
    rows[~pre, 3] = tau[~pre]
    if pre.sum() < 3:
        rows = rows[:, [0, 1, 3]]
    out = []
    for values in (u[chosen], v[chosen]):
        solution, *_ = np.linalg.lstsq(rows, values, rcond=None)
        out.append(float(solution[0]))
    return out[0], out[1]


def _pick(kink: dict, t: np.ndarray, values: np.ndarray) -> np.ndarray:
    """The values for the times the bounce fit kept."""
    keep = np.isin(t, kink["t"])
    return values[keep]


def _fit_joint(camera, pixels: np.ndarray, t: np.ndarray, bounce: tuple[float, float], tb: float) -> dict | None:
    """Fit the whole flight in three dimensions.

    Unknowns: where and when the ball pitched, and its velocity into and out
    of the pitch. Gravity and air drag do the rest. The camera comes from the
    ground calibration, so this needs no extra clicks.
    """
    order = np.argsort(t)
    t, pixels = t[order], pixels[order]
    pre_count = int((t <= tb).sum())
    if pre_count < 2 or len(t) - pre_count < 2 or len(t) < 6:
        return None
    first = float(t[0])
    flight_time = max(tb - first + 0.02, 0.12)
    vy = max((bounce[1] - RELEASE_Y_M) / flight_time * 0.95, 8.0)
    vz = -RELEASE_HEIGHT_M / flight_time - GRAVITY * flight_time / 2
    vx = (bounce[0] - (-0.25)) / flight_time
    theta = np.array([bounce[0], bounce[1], tb, vx, vy, vz, vx, vy * 0.85, -0.5 * vz])
    steps = np.array([0.01, 0.01, 0.001, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05])

    def residual(params: np.ndarray, split: int) -> np.ndarray:
        x_b, y_b, t_b = params[:3]
        start = np.array([x_b, y_b, BALL_RADIUS_M])
        before = t[:split] - t_b
        after = t[split:] - t_b
        if np.any(before > 1e-6) or np.any(after < -1e-6):
            return np.full(2 * len(t), 50.0)
        pts = np.vstack([
            _integrate(start, params[3:6], before) if len(before) else np.empty((0, 3)),
            _integrate(start, params[6:9], after) if len(after) else np.empty((0, 3)),
        ])
        cam = pts @ camera.r.T + camera.t
        if np.any(cam[:, 2] <= 0.1):
            return np.full(2 * len(t), 50.0)
        return (camera.project(pts) - pixels).ravel()

    split = pre_count
    current = residual(theta, split)
    for _ in range(3):
        theta, current = _levenberg(lambda params: residual(params, split), theta, steps)
        new_split = int((t <= theta[2]).sum())
        if new_split == split or new_split < 2 or len(t) - new_split < 2:
            break
        split = new_split
    # One pass of outlier rejection: a hand or a pad picked up at either end.
    per_point = np.hypot(current[0::2], current[1::2])
    limit = max(2.5, 3.0 * float(np.median(per_point)))
    keep = per_point <= limit
    if 0 < (~keep).sum() <= max(2, len(t) // 5) and keep.sum() >= 6:
        t, pixels = t[keep], pixels[keep]
        split = int((t <= theta[2]).sum())
        if split >= 2 and len(t) - split >= 2:
            theta, current = _levenberg(lambda params: residual(params, split), theta, steps)
    rms = float(np.sqrt((current**2).mean()))
    if rms > 3.0 or theta[5] >= 0 or theta[8] <= 0 or theta[4] <= 5 or theta[7] <= 3:
        return None
    start = np.array([theta[0], theta[1], BALL_RADIUS_M])
    release = _integrate_until(start, theta[3:6], toward_y=RELEASE_Y_M)
    at_stumps = None
    ahead = _integrate_until(start, theta[6:9], toward_y=STUMP_TO_STUMP_M)
    last_seen = float(t[-1]) - float(theta[2])
    if ahead is not None:
        reach_time = (STUMP_TO_STUMP_M - float(theta[1])) / max(float(theta[7]), 1.0)
        if reach_time - last_seen < 0.3:
            at_stumps = (float(ahead[0][0]), float(ahead[0][2]))
    return {
        "bounce": (float(theta[0]), float(theta[1])),
        "tb": float(theta[2]),
        "release": release,
        "at_stumps": at_stumps,
        "rms_px": rms,
    }


def _levenberg(func, theta: np.ndarray, steps: np.ndarray, iterations: int = 60):
    """Damped Gauss-Newton with a numerical Jacobian. Returns the parameters and residual."""
    theta = theta.astype(float).copy()
    current = func(theta)
    damping = 1e-2
    for _ in range(iterations):
        jac = np.empty((len(current), len(theta)))
        for k in range(len(theta)):
            bumped = theta.copy()
            bumped[k] += steps[k]
            jac[:, k] = (func(bumped) - current) / steps[k]
        normal = jac.T @ jac
        gradient = jac.T @ current
        improved = False
        delta = np.zeros_like(theta)
        while damping < 1e8:
            try:
                delta = -np.linalg.solve(normal + damping * np.diag(np.diag(normal) + 1e-9), gradient)
            except np.linalg.LinAlgError:
                damping *= 10
                continue
            trial = theta + delta
            trial_res = func(trial)
            if (trial_res**2).sum() < (current**2).sum():
                theta, current = trial, trial_res
                damping = max(damping / 3, 1e-7)
                improved = True
                break
            damping *= 4
        if not improved or np.all(np.abs(delta) < steps * 0.05):
            break
    return theta, current


def _accel(v: np.ndarray) -> np.ndarray:
    a = -DRAG_PER_M * np.linalg.norm(v) * v
    a[2] -= GRAVITY
    return a


def _integrate(p0: np.ndarray, v0: np.ndarray, taus: np.ndarray, dt: float = 0.003) -> np.ndarray:
    """Positions at each time offset from (p0, v0). All offsets share one sign."""
    taus = np.asarray(taus, dtype=float)
    if taus.size == 0:
        return np.empty((0, 3))
    sign = -1.0 if taus.min() < 0 else 1.0
    span = float(np.abs(taus).max())
    steps = max(1, int(math.ceil(span / dt)))
    h = sign * span / steps
    path = np.empty((steps + 1, 3))
    p, v = p0.astype(float).copy(), v0.astype(float).copy()
    path[0] = p
    for index in range(steps):
        a1 = _accel(v)
        a2 = _accel(v + a1 * h / 2)
        p = p + (v + a1 * h / 2) * h
        v = v + a2 * h
        path[index + 1] = p
    grid = np.linspace(0.0, span, steps + 1)
    reach = np.abs(taus)
    return np.stack([np.interp(reach, grid, path[:, k]) for k in range(3)], axis=1)


def _integrate_until(p0: np.ndarray, v0: np.ndarray, *, toward_y: float, dt: float = 0.002):
    p, v = p0.copy(), v0.copy()
    h = dt if toward_y > p[1] else -dt
    for _ in range(2000):
        if (h > 0 and p[1] >= toward_y) or (h < 0 and p[1] <= toward_y):
            return p, v
        a1 = _accel(v)
        a2 = _accel(v + a1 * h / 2)
        p = p + (v + a1 * h / 2) * h
        v = v + a2 * h
        if p[2] < -0.5 or p[2] > 8:
            return None
    return None


# --------------------------------------------------------------------------
# Both cameras on one ball.


def combine(side: dict | None, behind: dict | None, batter: str = "right") -> dict:
    """One pitch, one pace and one stumps call from the two cameras.

    The side camera owns length and pace. The camera behind the bowler owns
    the line. A sure reading beats an unsure one. A coach's own mark is left
    alone by the caller.
    """
    side_pitch = _usable_pitch(side)
    behind_pitch = _usable_pitch(behind)
    side_sure = bool(side_pitch) and side.get("confidence") == "ok"
    behind_sure = bool(behind_pitch) and behind.get("confidence") == "ok"
    length_m = None
    length_from = None
    if side_pitch and (side_sure or not behind_sure):
        length_m, length_from = side_pitch["length_m"], side
    elif behind_pitch:
        length_m, length_from = behind_pitch["length_m"], behind
    offset_m = behind_pitch.get("offset_m") if behind_pitch else None
    pitch = describe(length_m, offset_m, batter) if length_m is not None else None
    confidence = None
    if length_from is not None:
        confidence = length_from.get("confidence")
        if offset_m is not None and behind.get("confidence") != "ok":
            confidence = "low"
    pace = None
    pace_source = None
    if side and side.get("confidence") != "none" and side.get("pace_kph"):
        pace, pace_source = side["pace_kph"], "side"
        parallax = side.get("parallax") or {}
        release_x = ((behind or {}).get("release") or {}).get("x_m")
        across = release_x if release_x is not None else offset_m
        if across is not None and parallax.get("distance_m"):
            # The side camera reads the ball on the plane of the stumps. A ball
            # nearer the camera looks faster, one further away looks slower.
            # Pace is the speed at release, so the release line matters most.
            distance = float(parallax["distance_m"])
            pace = round(pace * (distance - parallax.get("sign", 1) * across) / distance, 1)
            pace_source = "side, corrected for line"
    elif behind and behind.get("confidence") != "none" and behind.get("pace_kph"):
        pace, pace_source = behind["pace_kph"], "behind"
    stumps = _combine_stumps((side or {}).get("stumps") or {}, (behind or {}).get("stumps") or {})
    return {
        "pace_kph": pace,
        "pace_source": pace_source,
        "pitch": pitch,
        "confidence": confidence,
        "stumps": stumps,
        "hit_stumps": stumps.get("would_hit"),
    }


def _combine_stumps(side: dict, behind: dict) -> dict:
    height = side.get("height_m") if side.get("height_m") is not None else behind.get("height_m")
    offset = behind.get("offset_m")
    would = None
    if height is not None and offset is not None:
        would = abs(offset) <= STUMPS_HALF_M + BALL_RADIUS_M and height <= STUMP_HEIGHT_M + BALL_RADIUS_M
    return {"height_m": height, "offset_m": offset, "would_hit": would}


def _usable_pitch(measured: dict | None) -> dict | None:
    if not measured or measured.get("confidence") == "none":
        return None
    pitch = measured.get("pitch")
    if not pitch or pitch.get("length_m") is None:
        return None
    return pitch


def _pace(speed_ms: float, metres_from_release: float) -> float | None:
    if not math.isfinite(speed_ms):
        return None
    kph = release_speed(speed_ms, metres_from_release) * 3.6
    if kph < 40 or kph > 180:
        return None
    return round(kph, 1)


def _empty(camera: str, note: str) -> dict:
    return {
        "camera": camera,
        "confidence": "none",
        "note": note,
        "pace_kph": None,
        "pitch": None,
        "bounce_t": None,
        "stumps": {"height_m": None, "offset_m": None, "would_hit": None},
        "hit_stumps": None,
        "points": [],
    }
