"""
ffmpeg.py
=========

Thin, defensive wrapper around the ``ffmpeg`` CLI binary.

FFmpeg is used for exactly three things in this project:

1. Extracting a single still-frame cover image from the source video --
   by timestamp, by exact frame index, or automatically (picking a
   representative, non-blurry frame).
2. Optionally trimming the source video before it is embedded.
3. Optionally stripping the audio track from the embedded video (a
   container-level remux with the video stream copied verbatim -- the
   video codec/quality is never touched).

No other use of ffmpeg is permitted (in particular, it is never used to
write Motion Photo metadata -- that is handled entirely in pure Python
elsewhere in this package).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .utils import get_logger

logger = get_logger()


class FFmpegError(RuntimeError):
    """Raised when the ffmpeg subprocess fails or is unavailable."""


def ensure_ffmpeg_available(ffmpeg_binary: str = "ffmpeg") -> str:
    """
    Verify that the configured ffmpeg binary exists on ``PATH``.

    Args:
        ffmpeg_binary: Name or path of the ffmpeg executable.

    Returns:
        The resolved absolute path to the ffmpeg executable.

    Raises:
        FFmpegError: If ffmpeg cannot be located.
    """
    resolved = shutil.which(ffmpeg_binary)
    if resolved is None:
        raise FFmpegError(
            f"ffmpeg executable '{ffmpeg_binary}' was not found on PATH. "
            "Install ffmpeg and ensure it is accessible."
        )
    return resolved


def extract_cover_frame(
    video_path: Path,
    output_path: Path,
    timestamp: str = "0.0",
    frame_index: int | None = None,
    auto: bool = False,
    auto_sample_frames: int = 30,
    ffmpeg_binary: str = "ffmpeg",
) -> Path:
    """
    Extract a single still frame from ``video_path`` as a JPEG cover image.

    Three mutually exclusive selection modes are supported, checked in
    this order of precedence:

    1. ``auto=True`` -- ffmpeg's ``thumbnail`` filter scans the first
       ``auto_sample_frames`` frames and picks the most representative
       one (in practice, this tends to avoid blurry/transitional frames
       near a scene cut, which timestamp ``0.0`` can otherwise land on).
    2. ``frame_index`` is not ``None`` -- selects that exact 0-indexed
       frame using ffmpeg's ``select`` filter, which is robust to
       variable frame rate and does not depend on keyframe placement the
       way timestamp-based seeking (``-ss``) can.
    3. Otherwise, ``timestamp`` -- ffmpeg-compatible timestamp-based
       seeking (the original behavior).

    Uses ``-bitexact`` to suppress ffmpeg's default JPEG COM marker
    (containing the libavcodec version string, e.g. ``"Lavc60.31.102"``).
    A byte-level comparison against a genuine Xiaomi-generated Motion
    Photo confirmed the real file has no COM marker at all, so this
    avoids introducing one that would not otherwise be there.

    Args:
        video_path: Path to the source video.
        output_path: Path where the extracted JPEG cover should be written.
        timestamp: ffmpeg-compatible timestamp (e.g. ``"0.0"``, ``"00:00:01"``)
            at which to sample the frame. Ignored if ``frame_index`` is
            set or ``auto`` is True.
        frame_index: 0-indexed exact frame number to extract. Ignored if
            ``auto`` is True.
        auto: If True, automatically pick a representative frame instead
            of using ``timestamp``/``frame_index``.
        auto_sample_frames: Number of leading frames ffmpeg's
            ``thumbnail`` filter should consider when ``auto=True``.
        ffmpeg_binary: Name or path of the ffmpeg executable.

    Returns:
        The ``output_path`` that was written.

    Raises:
        FFmpegError: If ffmpeg is unavailable or exits with a non-zero
            status, or if the expected output file was not produced.
        ValueError: If ``frame_index`` is negative.
    """
    resolved_ffmpeg = ensure_ffmpeg_available(ffmpeg_binary)

    if not video_path.is_file():
        raise FFmpegError(f"Input video does not exist: {video_path}")
    if frame_index is not None and frame_index < 0:
        raise ValueError("frame_index must be >= 0")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    command = [resolved_ffmpeg, "-y"]

    if auto:
        command += [
            "-i",
            str(video_path),
            "-vf",
            f"thumbnail={max(int(auto_sample_frames), 1)}",
            "-frames:v",
            "1",
        ]
    elif frame_index is not None:
        command += [
            "-i",
            str(video_path),
            "-vf",
            f"select=eq(n\\,{frame_index})",
            "-vsync",
            "vfr",
            "-frames:v",
            "1",
        ]
    else:
        command += ["-ss", str(timestamp), "-i", str(video_path), "-frames:v", "1"]

    command += ["-q:v", "2", "-bitexact", str(output_path)]

    logger.debug("Running ffmpeg cover extraction: %s", " ".join(command))
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise FFmpegError(
            f"ffmpeg failed extracting cover frame from {video_path} "
            f"(exit code {result.returncode}):\n{stderr[-2000:]}"
        )

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise FFmpegError(
            f"ffmpeg reported success but produced no cover image at {output_path}. "
            + (
                f"frame_index={frame_index} may be beyond the video's frame count."
                if frame_index is not None
                else ""
            )
        )

    logger.debug("Extracted cover frame to %s", output_path)
    return output_path


def trim_video(
    video_path: Path,
    output_path: Path,
    start: str | None,
    end: str | None,
    ffmpeg_binary: str = "ffmpeg",
) -> Path:
    """
    Trim ``video_path`` to the ``[start, end]`` window using stream copy.

    Stream copy (``-c copy``) is used so the codec, timestamps, and
    metadata of the video are preserved untouched, per the project's
    requirement that the embedded MP4 must not be re-encoded or altered
    beyond trimming.

    Args:
        video_path: Path to the source video.
        output_path: Path to write the trimmed video to.
        start: Optional ffmpeg-compatible start time (``-ss``). ``None``
            means "from the beginning".
        end: Optional ffmpeg-compatible end time (``-to``). ``None`` means
            "to the end".
        ffmpeg_binary: Name or path of the ffmpeg executable.

    Returns:
        The ``output_path`` that was written.

    Raises:
        FFmpegError: If ffmpeg is unavailable, fails, or produces no output.
    """
    resolved_ffmpeg = ensure_ffmpeg_available(ffmpeg_binary)

    if not video_path.is_file():
        raise FFmpegError(f"Input video does not exist: {video_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    command = [resolved_ffmpeg, "-y"]
    if start is not None:
        command += ["-ss", str(start)]
    command += ["-i", str(video_path)]
    if end is not None:
        command += ["-to", str(end)]
    command += ["-c", "copy", str(output_path)]

    logger.debug("Running ffmpeg trim: %s", " ".join(command))
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise FFmpegError(
            f"ffmpeg failed trimming {video_path} "
            f"(exit code {result.returncode}):\n{stderr[-2000:]}"
        )

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise FFmpegError(f"ffmpeg reported success but produced no trimmed video at {output_path}")

    logger.debug("Trimmed video written to %s", output_path)
    return output_path


def remux_strip_audio(
    video_path: Path,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
) -> Path:
    """
    Remux ``video_path`` into a new file with the audio track removed.

    Uses ``-c:v copy -an``: the video stream is copied verbatim (no
    re-encoding, no quality loss, no timestamp changes) and the audio
    stream is simply dropped from the container. This does change the
    resulting file's bytes relative to the original (it is a genuinely
    different container), but the video content itself is untouched.

    Args:
        video_path: Path to the source video.
        output_path: Path to write the audio-free video to.
        ffmpeg_binary: Name or path of the ffmpeg executable.

    Returns:
        The ``output_path`` that was written.

    Raises:
        FFmpegError: If ffmpeg is unavailable, fails, or produces no output.
    """
    resolved_ffmpeg = ensure_ffmpeg_available(ffmpeg_binary)

    if not video_path.is_file():
        raise FFmpegError(f"Input video does not exist: {video_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        resolved_ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-c:v",
        "copy",
        "-an",
        str(output_path),
    ]

    logger.debug("Running ffmpeg audio strip: %s", " ".join(command))
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise FFmpegError(
            f"ffmpeg failed stripping audio from {video_path} "
            f"(exit code {result.returncode}):\n{stderr[-2000:]}"
        )

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise FFmpegError(
            f"ffmpeg reported success but produced no audio-free video at {output_path}"
        )

    logger.debug("Audio-free video written to %s", output_path)
    return output_path


def probe_video_info(video_path: Path, ffmpeg_binary: str = "ffmpeg") -> dict[str, float | bool]:
    """
    Probe basic video properties (duration, frame rate, has-audio) via ffprobe.

    ``ffprobe`` is expected to live alongside the configured ffmpeg
    binary (the standard ffmpeg distribution layout). This is a
    best-effort convenience used for input validation (e.g. warning if a
    requested ``--cover-frame`` is likely out of range); failures here
    are never fatal to the conversion itself.

    Args:
        video_path: Path to the video file.
        ffmpeg_binary: Name or path of the ffmpeg executable, used to
            locate the sibling ffprobe binary.

    Returns:
        A dict with keys ``"duration_seconds"`` (float or ``None``),
        ``"fps"`` (float or ``None``), and ``"has_audio"`` (bool).
        Missing/unparseable values are ``None`` rather than raising.
    """
    ffprobe_binary = (
        ffmpeg_binary.replace("ffmpeg", "ffprobe") if "ffmpeg" in ffmpeg_binary else "ffprobe"
    )
    resolved_ffprobe = shutil.which(ffprobe_binary) or shutil.which("ffprobe")
    result: dict[str, float | bool] = {"duration_seconds": None, "fps": None, "has_audio": False}

    if resolved_ffprobe is None or not video_path.is_file():
        return result

    command = [
        resolved_ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,avg_frame_rate",
        "-of",
        "default=noprint_wrappers=1",
        str(video_path),
    ]
    try:
        proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        output = proc.stdout.decode("utf-8", errors="replace")
    except OSError:
        return result

    for line in output.splitlines():
        if line.startswith("duration="):
            try:
                result["duration_seconds"] = float(line.split("=", 1)[1])
            except ValueError:
                pass
        elif line.startswith("codec_type=video") or line == "codec_type=video":
            pass
        elif line.startswith("codec_type=audio"):
            result["has_audio"] = True
        elif line.startswith("avg_frame_rate="):
            raw = line.split("=", 1)[1]
            if "/" in raw:
                num, _, den = raw.partition("/")
                try:
                    denom = float(den)
                    if denom > 0:
                        result["fps"] = float(num) / denom
                except ValueError:
                    pass

    return result
