"""
conftest.py
===========

Shared pytest fixtures for the Motion Photo test suite.

Tests that require a real MP4 (integration-level tests exercising ffmpeg)
are skipped automatically if ffmpeg is not available in the environment,
so the suite remains runnable on machines without it (unit-level tests
for JPEG/XMP/metadata construction do not depend on ffmpeg at all).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


requires_ffmpeg = pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg is not installed in this environment"
)


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Generate a small synthetic MP4 (test pattern + tone) using ffmpeg.

    Session-scoped so it is generated once and reused across all tests
    that need a real video file.
    """
    if not _ffmpeg_available():
        pytest.skip("ffmpeg is not installed in this environment")

    out_dir = tmp_path_factory.mktemp("videos")
    video_path = out_dir / "sample.mp4"
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=160x120:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        "-loglevel",
        "error",
        str(video_path),
    ]
    subprocess.run(command, check=True)
    return video_path


@pytest.fixture()
def minimal_jpeg_bytes() -> bytes:
    """
    A minimal, valid, hand-built baseline JPEG (1x1 white pixel).

    Useful for unit tests of jpeg.py that don't need a real photo.
    Generated once via Pillow and hard-coded here as bytes so tests do
    not depend on Pillow's specific encoder output across versions.
    """
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(255, 0, 0)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def minimal_mp4_bytes() -> bytes:
    """A minimal synthetic byte string that looks like an MP4 ftyp header."""
    # 'ftyp' box: size(4) + 'ftyp'(4) + major_brand 'mp42'(4) + minor_version(4) + compatible_brands
    box = (24).to_bytes(4, "big") + b"ftyp" + b"mp42" + (0).to_bytes(4, "big") + b"mp42isom"
    # Pad with some pseudo-random-looking bytes to simulate mdat/moov content,
    # deliberately including a literal 0xFF 0xD9 sequence to stress-test that
    # EOI detection is not fooled by coincidental marker bytes in trailing data.
    filler = bytes([(i * 37 + 11) % 256 for i in range(500)]) + b"\xff\xd9" + b"\x00" * 100
    return box + filler
