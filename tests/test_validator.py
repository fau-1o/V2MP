"""Unit tests for v2mp.validator using synthetic (non-ffmpeg) data."""

from __future__ import annotations

from pathlib import Path

from v2mp.jpeg import assemble_motion_photo
from v2mp.metadata import build_xmp_segment
from v2mp.validator import validate_motion_photo_file
from v2mp.xmp import build_motion_photo_xmp


def _build_synthetic_motion_photo(
    tmp_path: Path,
    minimal_jpeg_bytes: bytes,
    minimal_mp4_bytes: bytes,
    corrupt_length: bool = False,
) -> Path:
    declared_length = len(minimal_mp4_bytes) + (100 if corrupt_length else 0)
    xmp_packet = build_motion_photo_xmp(video_length_bytes=declared_length)
    xmp_segment = build_xmp_segment(xmp_packet)

    combined = assemble_motion_photo(minimal_jpeg_bytes, minimal_mp4_bytes, [xmp_segment])
    path = tmp_path / "synthetic.jpg"
    path.write_bytes(combined)
    return path


def test_validator_passes_for_well_formed_file(
    tmp_path: Path, minimal_jpeg_bytes: bytes, minimal_mp4_bytes: bytes
) -> None:
    path = _build_synthetic_motion_photo(tmp_path, minimal_jpeg_bytes, minimal_mp4_bytes)
    report = validate_motion_photo_file(path)
    assert report.is_valid, report.summary()


def test_validator_fails_when_length_field_wrong(
    tmp_path: Path, minimal_jpeg_bytes: bytes, minimal_mp4_bytes: bytes
) -> None:
    path = _build_synthetic_motion_photo(
        tmp_path, minimal_jpeg_bytes, minimal_mp4_bytes, corrupt_length=True
    )
    report = validate_motion_photo_file(path)
    assert not report.is_valid
    assert report.checks["video_length_field_matches"] is False


def test_validator_fails_for_non_jpeg_file(tmp_path: Path) -> None:
    path = tmp_path / "not_a_jpeg.jpg"
    path.write_bytes(b"this is not a jpeg at all")
    report = validate_motion_photo_file(path)
    assert not report.is_valid
    assert report.checks["jpeg_starts_with_soi"] is False


def test_validator_fails_for_jpeg_without_video(tmp_path: Path, minimal_jpeg_bytes: bytes) -> None:
    path = tmp_path / "no_video.jpg"
    path.write_bytes(minimal_jpeg_bytes)
    report = validate_motion_photo_file(path)
    assert report.checks["mp4_payload_present"] is False
    assert not report.is_valid
