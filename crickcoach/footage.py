"""Raw test reels shipped with VANSH.

Each reel is rendered by crickcoach.netsim: a real-sized ball with drag and a
bounce, a bowler who runs in and a batter who presses forward, CCTV shutter
blur, noise and H.264. The manifest holds the calibration clicks a coach
would make and the truth each reel must give back.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOOTAGE = ROOT / "footage"
MANIFEST_PATH = FOOTAGE / "manifest.json"


def manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError("footage/manifest.json is missing.")
    return json.loads(MANIFEST_PATH.read_text())


def clip(name: str) -> dict:
    data = manifest()["clips"][name]
    path = FOOTAGE / data["file"]
    if not path.exists():
        raise FileNotFoundError(f"Raw footage {data['file']} is missing.")
    return {**data, "path": path}


def copy_clip(name: str, dest: Path) -> dict:
    item = clip(name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(item["path"], dest)
    return item


def export_footage(folder: Path | None = None) -> dict:
    """Render the reels and write the manifest. Run when the reels need to change."""
    from crickcoach import netsim

    folder = folder or FOOTAGE
    folder.mkdir(parents=True, exist_ok=True)
    side = netsim.side_camera()
    behind = netsim.behind_camera()
    clips: dict[str, dict] = {}

    session = [
        (3.0, netsim.Delivery(128, -0.25, netsim.PITCH_LENGTH - 7.2, ball="white", swing=0.4)),
        (16.0, netsim.Delivery(134, 0.02, netsim.PITCH_LENGTH - 1.6, ball="red", swing=-0.5)),
    ]
    rendered = netsim.render(folder / "side_two_balls.mp4", side, session, duration=20.0, fps=25, exposure=1 / 250, crf=27, seed=4)
    clips["side_two_balls"] = _entry(
        "side_two_balls.mp4", "side", "Two balls with a walk back between them, side-on.", rendered,
        expect={"deliveries": 2},
    )

    good = netsim.Delivery(131, -0.22, netsim.PITCH_LENGTH - 7.0, ball="red", swing=0.3)
    rendered = netsim.one_ball(folder / "side_good_length.mp4", side, good, fps=25, exposure=1 / 250, crf=26, seed=5)
    clips["side_good_length"] = _entry(
        "side_good_length.mp4", "side", "One red ball on a good length, side-on.", rendered,
        expect=_truth_expect(rendered.truths[0], line=False),
    )

    outside = netsim.Delivery(124, -0.32, netsim.PITCH_LENGTH - 7.4, ball="white", swing=-0.4)
    rendered = netsim.one_ball(folder / "behind_off_good.mp4", behind, outside, fps=50, exposure=1 / 500, crf=26, seed=6)
    clips["behind_off_good"] = _entry(
        "behind_off_good.mp4", "behind", "One white ball, good length outside off, from behind the bowler.", rendered,
        expect=_truth_expect(rendered.truths[0], line=True),
    )

    # One ball, both cameras. The camera behind started 0.8 s later.
    pair = netsim.Delivery(138, 0.05, netsim.PITCH_LENGTH - 5.0, ball="white", swing=0.2)
    side_clip = netsim.render(folder / "pair_side.mp4", side, [(1.6, pair)], duration=3.6, fps=25, exposure=1 / 250, crf=26, seed=7)
    behind_clip = netsim.render(folder / "pair_behind.mp4", behind, [(0.8, pair)], duration=3.0, fps=50, exposure=1 / 500, crf=26, seed=8)
    truth = side_clip.truths[0]
    clips["pair_side"] = _entry("pair_side.mp4", "side", "One ball, side camera.", side_clip,
                                expect=_truth_expect(truth, line=False))
    clips["pair_behind"] = _entry("pair_behind.mp4", "behind", "The same ball from behind, started 0.8 s later.",
                                  behind_clip, expect={**_truth_expect(behind_clip.truths[0], line=True), "clock_offset": -0.8})
    payload = {"coach": "VANSH", "origin": "https://vansh.best", "clips": clips}
    (folder / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def _entry(file: str, camera: str, note: str, rendered, *, expect: dict) -> dict:
    return {
        "file": file,
        "camera": camera,
        "note": note,
        "fps": rendered.fps,
        "duration": rendered.duration,
        "calibration": rendered.calibration(camera, jitter_px=1.0, seed=1),
        "truth": [truth.to_dict() for truth in rendered.truths],
        "expect": expect,
    }


def _truth_expect(truth, *, line: bool) -> dict:
    from crickcoach.pitch import describe

    named = describe(truth.length_m, truth.bounce_x if line else None)
    expect = {
        "length": named["length"],
        "length_m": round(truth.length_m, 2),
        "length_tolerance_m": 0.6,
        "pace_kph": round(truth.pace_kph, 1),
        "pace_tolerance_kph": 12.0,
    }
    if line:
        expect.update(line=named["line"], offset_m=round(truth.bounce_x, 3), offset_tolerance_m=0.15)
    return expect
