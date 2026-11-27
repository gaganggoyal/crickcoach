"""Score ball trackers against clips with known answers.

    python -m bench.run make  --out /tmp/vansh-bench --n 12
    python -m bench.run score --set /tmp/vansh-bench --tracker grok --window oracle
    python -m bench.run score --set /tmp/vansh-bench --tracker vansh --window app

Clips come from crickcoach.netsim: a real-sized ball with drag, swing and a
bounce, a bowler and a batter who move, CCTV shutter blur, noise, and H.264.
"oracle" hands the tracker a window that starts at release. "app" uses the
window the app itself would cut, which is what a coach actually gets.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path

import numpy as np

from crickcoach import netsim

SCENARIOS = {
    "side-red-25fps-1/250": dict(camera="side", ball="red", fps=25, exposure=1 / 250),
    "side-white-25fps-1/250": dict(camera="side", ball="white", fps=25, exposure=1 / 250),
    "side-red-25fps-1/60": dict(camera="side", ball="red", fps=25, exposure=1 / 60),
    "side-red-50fps-1/500": dict(camera="side", ball="red", fps=50, exposure=1 / 500),
    "behind-red-25fps-1/250": dict(camera="behind", ball="red", fps=25, exposure=1 / 250),
    "behind-white-50fps-1/500": dict(camera="behind", ball="white", fps=50, exposure=1 / 500),
}

RELEASE_T = 1.2
DURATION = 3.0
CLICK_JITTER_PX = 1.5

LENGTH_BANDS = ((2.0, "yorker"), (6.0, "full"), (8.0, "good"), (10.0, "short"), (1e9, "bouncer"))


def zone(length_m: float) -> str:
    """Length from the batter's stumps, named the way ball-tracking pitch maps name it."""
    for limit, name in LENGTH_BANDS:
        if length_m < limit:
            return name
    return "bouncer"


def _camera(kind: str) -> netsim.Camera:
    return netsim.side_camera() if kind == "side" else netsim.behind_camera()


def grok_calibration(camera: netsim.Camera, kind: str, seed: int) -> dict:
    rng = np.random.default_rng(seed)

    def click(point):
        return netsim._jitter(camera.normalised(point), camera, CLICK_JITTER_PX, rng)

    if kind == "side":
        return {
            "kind": "side",
            "bowling": click((0.0, 0.0, 0.0)),
            "batting": click((0.0, netsim.PITCH_LENGTH, 0.0)),
            "stump_top": click((-0.0953, netsim.PITCH_LENGTH, netsim.STUMP_HEIGHT)),
        }
    half = netsim.PITCH_HALF
    return {
        "kind": "behind",
        "corners": [
            click((-half, 0.0, 0.0)),
            click((half, 0.0, 0.0)),
            click((half, netsim.PITCH_LENGTH, 0.0)),
            click((-half, netsim.PITCH_LENGTH, 0.0)),
        ],
    }


def make(out: Path, n: int, seed: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    index = []
    started = time.time()
    for scenario_index, (name, spec) in enumerate(SCENARIOS.items()):
        rng = np.random.default_rng(seed + 1000 * scenario_index)
        camera = _camera(spec["camera"])
        for k in range(n):
            delivery = netsim.random_delivery(rng, ball=spec["ball"])
            clip_name = f"{name.replace('/', '_')}-{k:02d}.mp4"
            clip = netsim.one_ball(
                out / clip_name,
                camera,
                delivery,
                fps=spec["fps"],
                exposure=spec["exposure"],
                seed=seed + k,
                release_t=RELEASE_T,
                duration=DURATION,
            )
            truth = clip.truths[0]
            index.append(
                {
                    "scenario": name,
                    "file": clip_name,
                    "camera": spec["camera"],
                    "fps": spec["fps"],
                    "exposure": spec["exposure"],
                    "truth": truth.to_dict(),
                    "calibration": clip.calibration(spec["camera"], jitter_px=CLICK_JITTER_PX, seed=seed + k),
                    "grok_calibration": grok_calibration(camera, spec["camera"], seed + k),
                }
            )
        print(f"{name}: {n} clips ({time.time() - started:.0f}s)", flush=True)
    (out / "set.json").write_text(json.dumps({"seed": seed, "clips": index}, indent=1))


# --------------------------------------------------------------------------
# Trackers under test


def run_grok(path: Path, item: dict, window: tuple[float, float]) -> dict:
    from bench.grok_v1.flight import analyse_clip

    measured = analyse_clip(path, window[0], window[1], item["grok_calibration"])
    pitch = measured.get("pitch") or {}
    found = measured.get("confidence") != "none" and pitch.get("y") is not None
    bounce_y = pitch["y"] * netsim.PITCH_LENGTH if found else None
    bounce_x = (pitch["x"] - 0.5) * 2 * netsim.PITCH_HALF if found and pitch.get("x") is not None else None
    return {
        "found": found,
        "bounce_y": bounce_y,
        "bounce_x": bounce_x,
        "pace_kph": measured.get("pace_kph"),
        "label": pitch.get("length"),
        "hits_stumps": measured.get("hit_stumps"),
        "confidence": measured.get("confidence"),
    }


EXACT_CLICKS = False


def exact_calibration(item: dict) -> dict:
    camera = _camera(item["camera"])
    if item["camera"] == "side":
        return {"kind": "side", **netsim.side_clicks(camera)}
    return {"kind": "behind", "corners": netsim.behind_clicks(camera)}


def run_vansh(path: Path, item: dict, window: tuple[float, float]) -> dict:
    from crickcoach.flight import analyse_clip

    calibration = exact_calibration(item) if EXACT_CLICKS else item["calibration"]
    measured = analyse_clip(path, window[0], window[1], calibration)
    pitch = measured.get("pitch") or {}
    found = measured.get("confidence") != "none" and pitch.get("length_m") is not None
    return {
        "found": found,
        "bounce_y": netsim.PITCH_LENGTH - pitch["length_m"] if found else None,
        "bounce_x": pitch.get("offset_m") if found else None,
        "pace_kph": measured.get("pace_kph"),
        "label": pitch.get("length"),
        "hits_stumps": measured.get("hit_stumps"),
        "confidence": measured.get("confidence"),
    }


TRACKERS = {"grok": run_grok, "vansh": run_vansh}


def app_window(tracker: str, path: Path, release_t: float, calibration: dict | None = None) -> tuple[float, float]:
    """The delivery window the app would cut for this clip."""
    if tracker == "grok":
        from bench.grok_v1.video import cut_video

        segments = cut_video(path)
    else:
        from crickcoach.deliveries import find_deliveries

        segments, _ = find_deliveries(path, calibration)
    for segment in segments:
        if segment.t0 <= release_t <= segment.t1:
            return segment.t0, segment.t1
    if segments:
        return segments[0].t0, segments[0].t1
    return 0.0, DURATION


def score(set_dir: Path, tracker: str, window_mode: str, only: str | None = None, suffix: str = "") -> dict:
    data = json.loads((set_dir / "set.json").read_text())
    run = TRACKERS[tracker]
    rows = []
    for item in data["clips"]:
        if only and only not in item["scenario"]:
            continue
        path = set_dir / item["file"]
        truth = item["truth"]
        if window_mode == "oracle":
            window = (truth["release_t"] - 0.1, truth["release_t"] + 1.2)
        else:
            calibration = exact_calibration(item) if EXACT_CLICKS else item["calibration"]
            window = app_window(tracker, path, truth["release_t"], calibration if tracker == "vansh" else None)
        started = time.time()
        try:
            got = run(path, item, window)
            error = ""
        except Exception as exc:  # a crash is a miss, and it is reported
            got = {"found": False}
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.time() - started
        row = {"scenario": item["scenario"], "file": item["file"], "seconds": round(elapsed, 2), "error": error,
               "window": [round(window[0], 3), round(window[1], 3)], "truth": truth, "got": got}
        if got.get("found"):
            row["length_err_m"] = abs(got["bounce_y"] - truth["bounce_y"])
            est_length = netsim.PITCH_LENGTH - got["bounce_y"]
            row["zone_ok"] = zone(est_length) == zone(truth["length_m"])
            row["label_ok"] = got.get("label") == zone(truth["length_m"])
            if got.get("pace_kph"):
                row["pace_err"] = got["pace_kph"] - truth["pace_kph"]
            if got.get("bounce_x") is not None and item["camera"] == "behind":
                row["line_err_cm"] = abs(got["bounce_x"] - truth["bounce_x"]) * 100
        hit = got.get("hits_stumps")
        if hit is not None:
            row["stumps_ok"] = bool(hit) == bool(truth["hits_stumps"])
        rows.append(row)
        print(f"  {item['file']}: found={got.get('found')} "
              f"len_err={row.get('length_err_m', float('nan')):.2f} pace_err={row.get('pace_err', float('nan')):+.1f} "
              f"{elapsed:.1f}s {error}", flush=True)
    out = set_dir / f"result-{tracker}-{window_mode}{suffix}.json"
    out.write_text(json.dumps(rows, indent=1, default=float))
    summary = summarise(rows)
    (set_dir / f"summary-{tracker}-{window_mode}{suffix}.json").write_text(json.dumps(summary, indent=1))
    print_summary(f"{tracker} / {window_mode} window{suffix}", summary)
    return summary


def summarise(rows: list[dict]) -> dict:
    by: dict[str, list[dict]] = {}
    for row in rows:
        by.setdefault(row["scenario"], []).append(row)
    summary = {}
    for name, group in by.items():
        found = [row for row in group if row["got"].get("found")]
        pace = [abs(row["pace_err"]) for row in found if "pace_err" in row]
        summary[name] = {
            "clips": len(group),
            "found": len(found),
            "length_err_median_m": _median([row["length_err_m"] for row in found]),
            "zone_ok": sum(1 for row in found if row.get("zone_ok")),
            "label_ok": sum(1 for row in group if row.get("label_ok")),
            "pace_read": len(pace),
            "pace_err_median_kph": _median(pace),
            "line_err_median_cm": _median([row["line_err_cm"] for row in found if "line_err_cm" in row]),
            "stumps_ok": sum(1 for row in group if row.get("stumps_ok")),
            "stumps_called": sum(1 for row in group if "stumps_ok" in row),
            "seconds_median": _median([row["seconds"] for row in group]),
            "crashes": sum(1 for row in group if row["error"]),
        }
    return summary


def _median(values: list[float]) -> float | None:
    values = [value for value in values if value is not None and not math.isnan(value)]
    return round(statistics.median(values), 3) if values else None


def print_summary(title: str, summary: dict) -> None:
    print(f"\n{title}")
    header = f"{'scenario':26} {'found':>7} {'len err m':>9} {'zone ok':>8} {'label ok':>8} {'pace err':>9} {'line cm':>8} {'stumps':>7} {'s/clip':>7}"
    print(header)
    for name, row in summary.items():
        n = row["clips"]
        print(
            f"{name:26} {row['found']:>3}/{n:<3} {_fmt(row['length_err_median_m']):>9} "
            f"{row['zone_ok']:>3}/{n:<4} {row['label_ok']:>3}/{n:<4} {_fmt(row['pace_err_median_kph']):>9} "
            f"{_fmt(row['line_err_median_cm']):>8} {row['stumps_ok']:>2}/{row['stumps_called']:<4} {_fmt(row['seconds_median']):>7}"
        )


def _fmt(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    make_parser = sub.add_parser("make")
    make_parser.add_argument("--out", type=Path, required=True)
    make_parser.add_argument("--n", type=int, default=12)
    make_parser.add_argument("--seed", type=int, default=7)
    score_parser = sub.add_parser("score")
    score_parser.add_argument("--set", type=Path, required=True)
    score_parser.add_argument("--tracker", choices=sorted(TRACKERS), required=True)
    score_parser.add_argument("--window", choices=["oracle", "app"], default="oracle")
    score_parser.add_argument("--only", default=None)
    score_parser.add_argument("--exact-clicks", action="store_true")
    args = parser.parse_args()
    if args.command == "make":
        make(args.out, args.n, args.seed)
    else:
        global EXACT_CLICKS
        EXACT_CLICKS = args.exact_clicks
        score(args.set, args.tracker, args.window, args.only, suffix="-exact" if args.exact_clicks else "")


if __name__ == "__main__":
    main()
