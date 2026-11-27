"""Read camera files, cut them into deliveries, and record CCTV.

Every read goes through the ffmpeg binary that ships with imageio-ffmpeg.
Frames are streamed from the pipe, so a long net session never sits in
memory, and each frame keeps the timestamp the camera gave it.
"""

from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from crickcoach.segment import Segment, segment_motion

MOTION_W = 160
MOTION_H = 90
MOTION_FPS = 10


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


@dataclass
class VideoInfo:
    width: int
    height: int
    fps: float
    duration: float


_STREAM = re.compile(r"Stream #.*?Video:.*?(\d{2,5})x(\d{2,5})")
_FPS = re.compile(r"(\d+(?:\.\d+)?) fps")
_DURATION = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")


def probe(path: Path) -> VideoInfo:
    result = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, timeout=30, check=False
    )
    text = result.stderr.decode("utf-8", "replace")
    stream = None
    for line in text.splitlines():
        if "Video:" in line:
            stream = line
            break
    if stream is None:
        raise RuntimeError("That file has no video stream.")
    size = _STREAM.search(stream)
    if not size:
        raise RuntimeError("Could not read the video size.")
    fps_match = _FPS.search(stream)
    duration_match = _DURATION.search(text)
    duration = 0.0
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return VideoInfo(
        width=int(size.group(1)),
        height=int(size.group(2)),
        fps=float(fps_match.group(1)) if fps_match else 25.0,
        duration=duration,
    )


def video_size(path: Path) -> tuple[int, int]:
    info = probe(path)
    return info.width, info.height


@dataclass
class Frame:
    index: int
    y: np.ndarray  # luma, (h, w) uint8
    v: np.ndarray  # red-difference chroma, (h, w) uint8, upsampled from 4:2:0


class FrameStream:
    """Frames of one stretch of a video, with their real timestamps once the stream ends."""

    def __init__(self, path: Path, t0: float, t1: float, *, max_width: int = 1280):
        info = probe(path)
        scale = min(1.0, max_width / max(info.width, 1))
        self.width = max(2, int(info.width * scale) // 2 * 2)
        self.height = max(2, int(info.height * scale) // 2 * 2)
        self.source = info
        self.t0 = max(0.0, float(t0))
        self.t1 = max(self.t0 + 0.04, float(t1))
        self.times: list[float] = []
        self._stderr: list[str] = []
        self._command = [
            ffmpeg_exe(), "-hide_banner", "-loglevel", "info", "-nostats",
            "-ss", f"{self.t0:.3f}", "-t", f"{self.t1 - self.t0:.3f}", "-i", str(path), "-an",
            "-vf", f"scale={self.width}:{self.height}:flags=area,showinfo",
            "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "yuv420p", "pipe:1",
        ]

    def __iter__(self) -> Iterator[Frame]:
        w, h = self.width, self.height
        frame_bytes = w * h * 3 // 2
        process = subprocess.Popen(self._command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        reader = threading.Thread(target=self._drain, args=(process.stderr,), daemon=True)
        reader.start()
        index = 0
        try:
            while True:
                raw = _read_exact(process.stdout, frame_bytes)
                if raw is None:
                    break
                buffer = np.frombuffer(raw, dtype=np.uint8)
                y = buffer[: w * h].reshape(h, w)
                v = buffer[w * h + (w * h) // 4 :].reshape(h // 2, w // 2)
                v = np.repeat(np.repeat(v, 2, axis=0), 2, axis=1)
                yield Frame(index, y, v)
                index += 1
        finally:
            process.stdout.close()
            code = process.wait(timeout=60)
            reader.join(timeout=5)
        if code != 0 and index == 0:
            detail = "".join(line for line in self._stderr if "showinfo" not in line)[-400:].strip()
            raise RuntimeError(detail or "ffmpeg could not read the video.")
        stamps = [float(match) for line in self._stderr for match in re.findall(r"pts_time:(-?[0-9.]+)", line)]
        if len(stamps) >= index:
            self.times = [self.t0 + stamp for stamp in stamps[:index]]
        else:
            step = 1.0 / (self.source.fps or 25.0)
            self.times = [self.t0 + i * step for i in range(index)]

    def _drain(self, pipe) -> None:
        for line in iter(pipe.readline, b""):
            self._stderr.append(line.decode("utf-8", "replace"))
        pipe.close()


def _read_exact(pipe, size: int) -> bytes | None:
    chunks = []
    remaining = size
    while remaining:
        chunk = pipe.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


# --------------------------------------------------------------------------
# Cutting a session into deliveries.


def cut_video(path: Path) -> list[Segment]:
    times, energy, duration = motion_series(path)
    return segment_motion(times, energy, duration=duration)


def motion_series(path: Path) -> tuple[list[float], list[float], float]:
    """Mean frame-to-frame change of a small grey thumbnail, ten times a second.

    The stream is read in pieces, so an hour of video needs a few megabytes.
    """
    command = [
        ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", str(path), "-an",
        "-vf", f"fps={MOTION_FPS},scale={MOTION_W}:{MOTION_H},format=gray",
        "-f", "rawvideo", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    errors: list[bytes] = []
    drain = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)
    drain.start()
    frame_size = MOTION_W * MOTION_H
    times: list[float] = []
    energy: list[float] = []
    previous = None
    count = 0
    try:
        while True:
            raw = _read_exact(process.stdout, frame_size)
            if raw is None:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
            if previous is not None:
                energy.append(float(np.abs(frame - previous).mean() / 255))
                times.append(count / MOTION_FPS)
            previous = frame
            count += 1
    finally:
        process.stdout.close()
        code = process.wait(timeout=600)
        drain.join(timeout=5)
    if code != 0 and count == 0:
        detail = b"".join(errors).decode("utf-8", "replace")[-400:].strip()
        raise RuntimeError(detail or "ffmpeg could not read the video.")
    if count == 0:
        raise RuntimeError("The video has no frames.")
    return times, energy, count / MOTION_FPS


# --------------------------------------------------------------------------
# CCTV.


def record_rtsp(url: str, dest: Path, seconds: int) -> None:
    record_cameras({"side": (url, dest)}, seconds)


def record_cameras(cameras: dict[str, tuple[str, Path]], seconds: int, *, limit: int = 90) -> dict[str, str]:
    """Record every camera at the same time, so both see the same balls.

    Returns an error message per camera that failed. Raises if all failed.
    """
    if seconds < 3 or seconds > limit:
        raise RuntimeError(f"Record between 3 and {limit} seconds.")
    processes = {}
    for name, (url, dest) in cameras.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        command = [
            ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-rtsp_transport", "tcp", "-timeout", "10000000", "-i", url, "-t", str(seconds), "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(dest),
        ]
        processes[name] = (subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE), dest)
    failures = {}
    for name, (process, dest) in processes.items():
        try:
            _, stderr = process.communicate(timeout=seconds + 40)
        except subprocess.TimeoutExpired:
            process.kill()
            failures[name] = "The camera did not finish recording in time."
            continue
        if process.returncode != 0 or not dest.exists() or dest.stat().st_size < 800:
            failures[name] = stderr.decode("utf-8", "replace")[-400:].strip() or "The camera recording failed."
    if failures and len(failures) == len(cameras):
        raise RuntimeError(" ".join(failures.values()))
    return failures


# --------------------------------------------------------------------------
# Writing clips.


def encode_gray(frames: list[bytes], width: int, height: int, fps: int, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path),
    ]
    result = subprocess.run(command, input=b"".join(frames), capture_output=True, timeout=60, check=False)
    if result.returncode != 0 or not path.exists():
        detail = result.stderr.decode("utf-8", "replace")[-400:].strip()
        raise RuntimeError(detail or "Could not write the sample video.")


def synthesize_two_deliveries(path: Path) -> None:
    """A short clip with two bursts of motion and quiet gaps between them."""
    width, height = MOTION_W, MOTION_H
    frame_count = 8 * MOTION_FPS
    frames = []
    for index in range(frame_count):
        moment = index / MOTION_FPS
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
    encode_gray(frames, width, height, MOTION_FPS, path)
