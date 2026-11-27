"""Raw test reels shipped with VANSH.

These are the pictures the coach is tested against: a side camera with two
deliveries, a side camera with one measured ball, and a behind-the-bowler
camera with one measured ball. The manifest records the calibration and the
result each reel must produce.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOOTAGE = ROOT / "footage"
MANIFEST_PATH = FOOTAGE / "manifest.json"

CLIPS = {
    "side_two_balls": {
        "file": "side_two_balls.mp4",
        "camera": "side",
        "note": "Two deliveries with a quiet gap between them.",
        "expect": {"deliveries": 2},
    },
    "side_good_length": {
        "file": "side_good_length.mp4",
        "camera": "side",
        "note": "One side-on ball pitching on a good length.",
        "expect": {"length": "good", "pace_min": 40, "pace_max": 180, "hit_stumps": False},
    },
    "behind_off_good": {
        "file": "behind_off_good.mp4",
        "camera": "behind",
        "note": "One ball from behind the bowler, good length on off stump.",
        "expect": {"line": "off", "length": "good", "hit_stumps": False},
    },
}


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
    """Write the raw reels and the manifest. Used when the pictures change."""
    from crickcoach.video import synthesize_two_deliveries, write_behind_sample, write_tracked_sample

    folder = folder or FOOTAGE
    folder.mkdir(parents=True, exist_ok=True)
    synthesize_two_deliveries(folder / CLIPS["side_two_balls"]["file"])
    side = write_tracked_sample(folder / CLIPS["side_good_length"]["file"])
    behind = write_behind_sample(folder / CLIPS["behind_off_good"]["file"])
    payload = {
        "coach": "VANSH",
        "origin": "https://vansh.best",
        "clips": {
            "side_two_balls": dict(CLIPS["side_two_balls"]),
            "side_good_length": {
                **CLIPS["side_good_length"],
                "duration": side["duration"],
                "calibration": side["calibration"],
            },
            "behind_off_good": {
                **CLIPS["behind_off_good"],
                "duration": behind["duration"],
                "calibration": behind["calibration"],
            },
        },
    }
    (folder / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n")
    return payload
