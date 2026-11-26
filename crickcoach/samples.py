"""Stick-figure deliveries used by the coach screen and the tests.

Coordinates are a side-on bowler and a front-on batter in the same 0..1
frame as MediaPipe, with y growing downward.
"""

from __future__ import annotations

from crickcoach.cues import NAMES


def bowling_clean_frames() -> list[dict]:
    frames = []
    noses = (0.500, 0.502, 0.498, 0.501, 0.499, 0.500)
    for index, phase in enumerate((0.0, 0.2, 0.4, 0.6, 0.8, 1.0)):
        hip = 0.46 + 0.10 * phase
        right_shoulder = (hip + 0.04, 0.32)
        if phase < 1:
            right_elbow = (right_shoulder[0] + 0.05, 0.32 + 0.18 * (1 - phase))
            right_wrist = (right_elbow[0] + 0.02, right_elbow[1] + 0.12)
        else:
            right_elbow = (right_shoulder[0] + 0.08, 0.22)
            right_wrist = (right_shoulder[0] + 0.16, 0.12)
        left_wrist_y = 0.24 + 0.28 * phase
        frames.append(
            _frame(
                round(index * 0.12, 2),
                nose=(noses[index], 0.20),
                left_shoulder=(hip - 0.06, 0.33),
                right_shoulder=right_shoulder,
                left_elbow=(hip - 0.10, 0.30 + 0.08 * phase),
                left_wrist=(hip - 0.12, left_wrist_y),
                right_elbow=right_elbow,
                right_wrist=right_wrist,
                left_hip=(hip - 0.04, 0.52),
                right_hip=(hip + 0.02, 0.52),
                left_knee=(hip - 0.06, 0.68),
                left_ankle=(hip - 0.10, 0.86),
                right_knee=(hip + 0.08, 0.68) if phase < 1 else (hip + 0.08, 0.68),
                right_ankle=(hip + 0.14, 0.86),
            )
        )
    # Straighten the front leg on the release frame only.
    release = frames[-1]
    hip_x = 0.56
    _set(release, "right_hip", hip_x + 0.02, 0.52)
    _set(release, "right_knee", hip_x + 0.08, 0.68)
    _set(release, "right_ankle", hip_x + 0.14, 0.84)
    _set(release, "left_hip", hip_x - 0.04, 0.52)
    _set(release, "left_knee", hip_x - 0.08, 0.70)
    _set(release, "left_ankle", hip_x - 0.12, 0.86)
    return frames


def bowling_fault_frames() -> list[dict]:
    frames = []
    for index, phase in enumerate((0.0, 0.2, 0.4, 0.6, 0.8, 1.0)):
        hip = 0.46 + 0.10 * phase
        right_shoulder = (hip + 0.04, 0.34)
        right_elbow = (right_shoulder[0] + 0.12, 0.30)
        right_wrist = (right_shoulder[0] + 0.02, 0.18 + 0.20 * (1 - phase))
        frames.append(
            _frame(
                round(index * 0.12, 2),
                nose=(0.38 + 0.24 * phase, 0.20),
                left_shoulder=(hip - 0.06, 0.34),
                right_shoulder=right_shoulder,
                left_elbow=(hip - 0.08, 0.42),
                left_wrist=(hip - 0.08, 0.50),
                right_elbow=right_elbow,
                right_wrist=right_wrist,
                left_hip=(hip - 0.04, 0.52),
                right_hip=(hip + 0.02, 0.52),
                left_knee=(hip - 0.06, 0.68),
                left_ankle=(hip - 0.12, 0.86),
                right_knee=(hip + 0.16, 0.64),
                right_ankle=(hip + 0.06, 0.86),
            )
        )
    return frames


def batting_clean_frames() -> list[dict]:
    frames = []
    for index in range(4):
        wobble = (index - 1.5) * 0.002
        frames.append(
            _frame(
                index * 0.1,
                nose=(0.50 + wobble, 0.18),
                left_shoulder=(0.42, 0.32),
                right_shoulder=(0.58, 0.32),
                left_elbow=(0.38, 0.40),
                right_elbow=(0.62, 0.40),
                left_wrist=(0.40, 0.38),
                right_wrist=(0.60, 0.38),
                left_hip=(0.44, 0.52),
                right_hip=(0.56, 0.52),
                left_knee=(0.40, 0.68),
                right_knee=(0.60, 0.68),
                left_ankle=(0.42, 0.86),
                right_ankle=(0.58, 0.86),
            )
        )
    return frames


def batting_fault_frames() -> list[dict]:
    frames = []
    for index in range(4):
        frames.append(
            _frame(
                index * 0.1,
                nose=(0.30, 0.16),
                left_shoulder=(0.44, 0.30),
                right_shoulder=(0.58, 0.30),
                left_elbow=(0.46, 0.48),
                right_elbow=(0.60, 0.48),
                left_wrist=(0.48, 0.74),
                right_wrist=(0.58, 0.74),
                left_hip=(0.46, 0.50),
                right_hip=(0.58, 0.50),
                left_knee=(0.46, 0.66),
                right_knee=(0.58, 0.66),
                left_ankle=(0.46, 0.84),
                right_ankle=(0.58, 0.84),
            )
        )
    return frames


CATALOG = {
    "bowling-clean": {
        "title": "Sample pace action",
        "role": "bowling",
        "blurb": "Side-on, straight arm, steady head, braced front knee.",
        "frames": bowling_clean_frames,
    },
    "bowling-faults": {
        "title": "Sample faults",
        "role": "bowling",
        "blurb": "Bent arm, moving head, collapsing front knee.",
        "frames": bowling_fault_frames,
    },
    "batting-clean": {
        "title": "Sample stance",
        "role": "batting",
        "blurb": "Head over the base, knees bent, hands up.",
        "frames": batting_clean_frames,
    },
    "batting-faults": {
        "title": "Sample batting faults",
        "role": "batting",
        "blurb": "Head outside the base, locked knees, hands dropped.",
        "frames": batting_fault_frames,
    },
}


def sample_payload(name: str) -> dict | None:
    item = CATALOG.get(name)
    if item is None:
        return None
    return {
        "id": name,
        "title": item["title"],
        "role": item["role"],
        "blurb": item["blurb"],
        "frames": item["frames"](),
    }


def _frame(t: float, **named: tuple[float, float]) -> dict:
    points = [{"x": 0.0, "y": 0.0, "visibility": 0.0} for _ in range(33)]
    for name, (x, y) in named.items():
        points[NAMES[name]] = {"x": round(x, 4), "y": round(y, 4), "visibility": 1.0}
    return {"t": t, "points": points}


def _set(frame: dict, name: str, x: float, y: float) -> None:
    frame["points"][NAMES[name]] = {"x": round(x, 4), "y": round(y, 4), "visibility": 1.0}
