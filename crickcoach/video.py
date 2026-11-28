"""Read camera files, cut them into deliveries, and record CCTV.

Every read goes through the ffmpeg binary that ships with imageio-ffmpeg.
Frames are streamed from the pipe, so a long net session never sits in
memory, and each frame keeps the timestamp the camera gave it.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

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
    codec: str = ""


_CODEC = re.compile(r"Video:\s*([A-Za-z0-9_]+)")
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
    codec = _CODEC.search(stream)
    return VideoInfo(
        width=int(size.group(1)),
        height=int(size.group(2)),
        fps=float(fps_match.group(1)) if fps_match else 25.0,
        duration=duration,
        codec=codec.group(1).lower() if codec else "",
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
        process.stderr.close()
    if code != 0 and count == 0:
        detail = b"".join(errors).decode("utf-8", "replace")[-400:].strip()
        raise RuntimeError(detail or "ffmpeg could not read the video.")
    if count == 0:
        raise RuntimeError("The video has no frames.")
    return times, energy, count / MOTION_FPS


# --------------------------------------------------------------------------
# CCTV.


def camera_input(url: str, *, live: bool = False) -> list[str]:
    """ffmpeg arguments that open one camera.

    RTSP goes over TCP, which survives a busy network, with a ten-second
    timeout. A video file can stand in for a camera; with live=True it plays
    in real time, round and round, the way a camera sends pictures.
    """
    scheme = urlsplit(url).scheme.lower()
    if scheme in {"rtsp", "rtsps"}:
        return ["-rtsp_transport", "tcp", "-timeout", "10000000", "-i", url]
    if scheme in {"http", "https", "rtmp", "srt", "udp"}:
        return ["-i", url]
    path = url[len("file://"):] if url.startswith("file://") else url
    return (["-re", "-stream_loop", "-1"] if live else []) + ["-i", path]


def _video_codec(copy: bool, take_seconds: int | None = None) -> list[str]:
    if copy:
        return ["-c:v", "copy"]
    args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    if take_seconds:
        args += ["-force_key_frames", f"expr:gte(t,n_forced*{take_seconds})"]
    return args


def record_rtsp(url: str, dest: Path, seconds: int) -> None:
    record_cameras({"side": (url, dest)}, seconds)


def record_cameras(cameras: dict[str, tuple[str, Path]], seconds: int, *, limit: int = 90,
                   copy: bool = True) -> dict[str, str]:
    """Record every camera at the same time, so both see the same balls.

    copy=True keeps the camera's own H.264 as it is, which costs almost no
    processor, so a small computer at the nets can record two cameras.

    Returns an error message per camera that failed. Raises if all failed.
    """
    if seconds < 3 or seconds > limit:
        raise RuntimeError(f"Record between 3 and {limit} seconds.")
    processes = {}
    for name, (url, dest) in cameras.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        command = [
            ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *camera_input(url, live=True),
            "-t", str(seconds), "-map", "0:v:0", "-an", *_video_codec(copy), "-movflags", "+faststart", str(dest),
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
            failures[name] = _ffmpeg_error(stderr) or "The camera recording failed."
    if failures and len(failures) == len(cameras):
        raise RuntimeError(" ".join(failures.values()))
    return failures


def segment_command(url: str, folder: Path, stem: str, take_seconds: int, *, copy: bool = True) -> list[str]:
    """Record one camera without a break, cut into takes of about take_seconds.

    Each take starts at time zero and is listed in <stem>.csv once it is
    complete. The cut falls on the camera's next keyframe, so set the camera
    to send one every second.
    """
    return [
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *camera_input(url, live=True),
        "-map", "0:v:0", "-an", *_video_codec(copy, take_seconds),
        "-f", "segment", "-segment_time", str(take_seconds), "-reset_timestamps", "1",
        "-segment_format", "mp4", "-segment_format_options", "movflags=+faststart",
        "-segment_list", str(folder / f"{stem}.csv"), "-segment_list_type", "csv",
        str(folder / f"{stem}_%05d.mp4"),
    ]


def transcode_h264(src: Path, dest: Path, *, max_width: int = 1920) -> None:
    """Re-encode a video as H.264, which every browser plays. Phones and some cameras send H.265."""
    command = [
        ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-map", "0:v:0", "-an", "-vf", f"scale='min({max_width},iw)':-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(dest),
    ]
    result = subprocess.run(command, capture_output=True, timeout=3600, check=False)
    if result.returncode != 0 or not dest.exists() or dest.stat().st_size < 800:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"Could not convert the video: {_ffmpeg_error(result.stderr)}")


_SHOWINFO = re.compile(r"pts_time:\s*(-?[0-9.]+).*?iskey:\s*(\d)")


def check_camera(url: str, *, seconds: float = 5.0, snapshot: Path | None = None) -> dict:
    """Read a few seconds from a camera and say plainly whether VANSH can use it."""
    with tempfile.TemporaryDirectory(prefix="vansh-check-") as folder:
        sample = Path(folder) / "sample.mp4"
        command = [
            ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *camera_input(url),
            "-t", f"{seconds:g}", "-map", "0:v:0", "-an", "-c:v", "copy", "-movflags", "+faststart", str(sample),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=seconds + 30, check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "The camera did not answer within half a minute."}
        if result.returncode != 0 or not sample.exists() or sample.stat().st_size < 800:
            return {"ok": False, "error": _ffmpeg_error(result.stderr) or "The camera sent no video."}
        info = probe(sample)
        shown = subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-i", str(sample), "-map", "0:v:0", "-vf", "showinfo", "-f", "null", "-"],
            capture_output=True, timeout=120, check=False,
        )
        frames = [(float(t), key == "1") for t, key in _SHOWINFO.findall(shown.stderr.decode("utf-8", "replace"))]
        # A copied sample can carry a stray frame from past the cut; judge only the window asked for.
        frames = [frame for frame in frames if frame[0] <= seconds - 0.25] or frames
        if snapshot is not None:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{min(1.0, info.duration / 2):.2f}",
                 "-i", str(sample), "-frames:v", "1", "-q:v", "3", str(snapshot)],
                capture_output=True, timeout=60, check=False,
            )
        size = sample.stat().st_size
    return describe_camera(info, frames, size)


def describe_camera(info: VideoInfo, frames: list[tuple[float, bool]], size_bytes: int) -> dict:
    """Turn what a camera sent into numbers and plain advice."""
    times = sorted(t for t, _ in frames)
    span = times[-1] - times[0] if len(times) > 1 else 0.0
    measured = (len(times) - 1) / span if span > 0 else info.fps
    steps = [b - a for a, b in zip(times, times[1:]) if b > a]
    typical = float(np.median(steps)) if steps else 1.0 / max(info.fps, 1.0)
    missing = sum(max(0, round(step / typical) - 1) for step in steps if step > 1.5 * typical)
    keys = [t for t, key in frames if key]
    key_gap = float(np.median(np.diff(keys))) if len(keys) > 1 else None
    duration = span + typical if span else info.duration
    kbps = size_bytes * 8 / max(duration, 0.1) / 1000
    report = {
        "ok": True,
        "codec": info.codec or "unknown",
        "width": info.width,
        "height": info.height,
        "fps": round(measured, 1),
        "fps_camera": round(info.fps, 2),
        "keyframe_s": round(key_gap, 2) if key_gap else None,
        "missing_frames": int(missing),
        "kbps": int(round(kbps)),
        "warnings": [],
    }
    warn = report["warnings"].append
    if info.codec != "h264":
        warn(f"The camera sends {info.codec.upper() or 'an unusual format'}. VANSH converts it so browsers can play it, "
             "which costs time on the server. Choose H.264 in the camera's video settings if it offers it.")
    if measured < 23.5:
        warn(f"Only {measured:.0f} frames a second. Set the camera to 25 or more; 50 is better.")
    elif measured < 45:
        warn(f"{measured:.0f} frames a second works. 50 or 60, if the camera offers it, measures pace more closely.")
    if info.width < 1280:
        warn(f"The picture is only {info.width}×{info.height}. Use the camera's main stream at 1080p or 720p, "
             "not the sub-stream.")
    if key_gap is None or key_gap > 2.2:
        gap_text = f"every {key_gap:.0f} s" if key_gap else "less than once in the sample"
        warn(f"A keyframe arrives {gap_text}. Set the I-frame interval to the frame rate (one a second) "
             "and turn off H.264+, H.265+ or any 'smart codec'.")
    if times and missing > 0.02 * len(times):
        warn(f"{missing} frames went missing. Use a cable rather than Wi-Fi, or lower the camera's bitrate.")
    if kbps > 12000:
        warn(f"The camera sends {kbps / 1000:.0f} Mbit/s. Each camera needs that much upload speed at the nets; "
             "4 to 8 Mbit/s is plenty for VANSH.")
    return report


_CREDENTIALS = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)


def redact(text: str) -> str:
    """Hide the user name and password in any camera address inside text."""
    return _CREDENTIALS.sub(r"\1***@", text or "")


def _ffmpeg_error(stderr: bytes | None) -> str:
    text = redact((stderr or b"").decode("utf-8", "replace").strip())
    lines = [line for line in text.splitlines() if line.strip()]
    message = " ".join(lines[-3:])[-400:]
    lowered = text.lower()
    if "401" in lowered or "unauthorized" in lowered:
        return "The camera refused the user name or password. " + message
    if "404" in lowered or "not found" in lowered:
        return "The camera has no stream at that path. Check the address for your camera's brand. " + message
    if "connection refused" in lowered or "timed out" in lowered or "no route" in lowered:
        return "Nothing answered at that address. Check the camera's IP address and that RTSP is turned on. " + message
    return message


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
