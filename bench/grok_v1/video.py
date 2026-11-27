"""Read camera files and cut them into deliveries.

Motion is measured with ffmpeg: the picture is scaled down to a gray
thumbnail stream, and each frame is compared with the one before it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from bench.grok_v1.segment import Segment, segment_motion

FRAME_W = 160
FRAME_H = 90
FPS = 10


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def cut_video(path: Path) -> list[Segment]:
    times, energy, duration = motion_series(path)
    return segment_motion(times, energy, duration=duration)


def motion_series(path: Path) -> tuple[list[float], list[float], float]:
    command = [
        ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-an",
        "-vf",
        f"fps={FPS},scale={FRAME_W}:{FRAME_H},format=gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=180, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Reading the video took too long.") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace")[-400:].strip()
        raise RuntimeError(detail or "ffmpeg could not read the video.")
    frame_size = FRAME_W * FRAME_H
    raw = result.stdout
    frame_count = len(raw) // frame_size
    if frame_count == 0:
        raise RuntimeError("The video has no frames.")
    duration = frame_count / FPS
    times: list[float] = []
    energy: list[float] = []
    previous = None
    for index in range(frame_count):
        frame = raw[index * frame_size : (index + 1) * frame_size]
        if previous is not None:
            total = 0
            count = 0
            for offset in range(0, frame_size, 16):
                total += abs(frame[offset] - previous[offset])
                count += 1
            energy.append(total / count / 255)
            times.append(index / FPS)
        previous = frame
    return times, energy, duration


def record_rtsp(url: str, dest: Path, seconds: int) -> None:
    if seconds < 3 or seconds > 90:
        raise RuntimeError("Record between 3 and 90 seconds.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_exe(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        url,
        "-t",
        str(seconds),
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=seconds + 40, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("The camera did not finish recording in time.") from exc
    if result.returncode != 0 or not dest.exists() or dest.stat().st_size < 800:
        detail = result.stderr.decode("utf-8", "replace")[-400:].strip()
        raise RuntimeError(detail or "The camera recording failed.")


def video_size(path: Path) -> tuple[int, int]:
    command = [ffmpeg_exe(), "-hide_banner", "-i", str(path)]
    result = subprocess.run(command, capture_output=True, timeout=20, check=False)
    text = result.stderr.decode("utf-8", "replace")
    matches = re.findall(r"(\d{3,5})x(\d{3,5})", text)
    if not matches:
        raise RuntimeError("Could not read the video size.")
    width, height = max(((int(a), int(b)) for a, b in matches), key=lambda item: item[0] * item[1])
    return width, height


def decode_gray(path: Path, t0: float, t1: float, *, fps: int = 50, max_side: int = 320):
    width, height = video_size(path)
    scale = max_side / max(width, height)
    out_w = max(2, int(width * scale) // 2 * 2)
    out_h = max(2, int(height * scale) // 2 * 2)
    duration = max(0.05, t1 - t0)
    command = [
        ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{t0:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(path),
        "-an",
        "-vf",
        f"fps={fps},scale={out_w}:{out_h},format=gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Reading the delivery took too long.") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace")[-400:].strip()
        raise RuntimeError(detail or "ffmpeg could not read the delivery.")
    frame_size = out_w * out_h
    raw = result.stdout
    frames = [raw[index : index + frame_size] for index in range(0, len(raw) - frame_size + 1, frame_size)]
    return frames, out_w, out_h, fps


def synthesize_two_deliveries(path: Path) -> None:
    """Write a short clip with two bursts of motion and quiet gaps between them."""
    width, height = FRAME_W, FRAME_H
    frame_count = 8 * FPS
    frames = []
    for index in range(frame_count):
        moment = index / FPS
        buffer = bytearray(width * height)
        burst = None
        if 1.0 <= moment <= 2.3:
            burst = moment - 1.0
        elif 5.0 <= moment <= 6.4:
            burst = moment - 5.0
        if burst is not None:
            center_x = int(30 + burst * 70)
            center_y = int(50 - burst * 12)
            for y in range(center_y - 12, center_y + 12):
                if not 0 <= y < height:
                    continue
                row = y * width
                for x in range(center_x - 12, center_x + 12):
                    if 0 <= x < width:
                        buffer[row + x] = 240
        frames.append(bytes(buffer))
    encode_gray(frames, width, height, FPS, path)


def write_tracked_sample(path: Path) -> dict:
    """A side-on clip of one ball pitching on a good length.

    The creases and the stump top are the calibration that matches the drawing.
    """
    width, height, fps = 320, 180, 50
    frames = []
    for index in range(fps):
        buffer = bytearray([28]) * (width * height)
        for x in range(30, 290):
            y = 140 + int((x - 40) * 10 / 240)
            if 0 <= y < height:
                buffer[y * width + x] = 70
        center_x, center_y = _sample_ball(index)
        for y in range(center_y - 4, center_y + 4):
            if not 0 <= y < height:
                continue
            row = y * width
            for x in range(center_x - 4, center_x + 4):
                if 0 <= x < width:
                    buffer[row + x] = 250
        frames.append(bytes(buffer))
    encode_gray(frames, width, height, fps, path)
    return {
        "duration": 1.0,
        "calibration": {
            "kind": "side",
            "bowling": {"x": 40 / width, "y": 140 / height},
            "batting": {"x": 280 / width, "y": 150 / height},
            "stump_top": {"x": 280 / width, "y": 110 / height},
        },
    }


def write_behind_sample(path: Path) -> dict:
    """A behind-the-bowler clip. The ball kinks on a good length, off stump."""
    width, height, fps = 320, 180, 50
    frames = []
    for index in range(fps):
        buffer = bytearray([22]) * (width * height)
        for corner in ((0.2, 0.8), (0.8, 0.8), (0.8, 0.2), (0.2, 0.2)):
            _paint(buffer, width, height, int(corner[0] * width), int(corner[1] * height), 80, 2)
        center_x, center_y = _behind_ball(index, width, height)
        _paint(buffer, width, height, center_x, center_y, 250, 4)
        frames.append(bytes(buffer))
    encode_gray(frames, width, height, fps, path)
    return {
        "duration": 1.0,
        "calibration": {
            "kind": "behind",
            "corners": [
                {"x": 0.2, "y": 0.8},
                {"x": 0.8, "y": 0.8},
                {"x": 0.8, "y": 0.2},
                {"x": 0.2, "y": 0.2},
            ],
        },
    }


def _behind_ball(index: int, width: int, height: int) -> tuple[int, int]:
    moment = index / 50
    if moment <= 0.4:
        phase = moment / 0.4
        x = 0.20 + 0.18 * phase
        y = 0.80 - 0.33 * phase
    else:
        phase = min((moment - 0.4) / 0.35, 1)
        x = 0.38 + 0.24 * phase
        y = 0.47 - 0.07 * phase
    return int(x * width), int(y * height)


def _paint(buffer: bytearray, width: int, height: int, center_x: int, center_y: int, value: int, radius: int) -> None:
    for y in range(center_y - radius, center_y + radius):
        if not 0 <= y < height:
            continue
        row = y * width
        for x in range(center_x - radius, center_x + radius):
            if 0 <= x < width:
                buffer[row + x] = value


def _sample_ball(index: int) -> tuple[int, int]:
    if index <= 24:
        phase = index / 24
        return int(50 + (160 - 50) * phase), int(40 + (145 - 40) * phase)
    phase = min((index - 24) / 10, 1)
    return int(160 + 30 * phase), int(145 - 28 * phase)


def encode_gray(frames: list[bytes], width: int, height: int, fps: int, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_exe(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(path),
    ]
    result = subprocess.run(command, input=b"".join(frames), capture_output=True, timeout=60, check=False)
    if result.returncode != 0 or not path.exists():
        detail = result.stderr.decode("utf-8", "replace")[-400:].strip()
        raise RuntimeError(detail or "Could not write the sample video.")
