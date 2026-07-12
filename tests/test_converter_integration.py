"""
Integration tests exercising the full conversion pipeline against a real,
ffmpeg-generated MP4. Skipped automatically if ffmpeg is unavailable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from conftest import requires_ffmpeg
from PIL import Image

from v2mp.config import ConversionConfig
from v2mp.converter import ConversionError, convert_video_to_motion_photo
from v2mp.jpeg import split_motion_photo
from v2mp.validator import validate_motion_photo_file


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@requires_ffmpeg
def test_full_conversion_produces_valid_motion_photo(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    cfg = ConversionConfig(overwrite=True)

    result = convert_video_to_motion_photo(sample_video, output_path, cfg)

    assert result.output_path == output_path
    assert output_path.is_file()
    assert result.validation.is_valid


@requires_ffmpeg
def test_generated_jpeg_opens_with_pillow(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    convert_video_to_motion_photo(sample_video, output_path, ConversionConfig(overwrite=True))

    with Image.open(output_path) as img:
        img.load()
        assert img.format == "JPEG"
        assert img.size[0] > 0 and img.size[1] > 0


@requires_ffmpeg
def test_extracted_mp4_is_byte_identical_to_source(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    convert_video_to_motion_photo(sample_video, output_path, ConversionConfig(overwrite=True))

    combined = output_path.read_bytes()
    _, mp4_part = split_motion_photo(combined)

    assert _sha256(mp4_part) == _sha256(sample_video.read_bytes())


@requires_ffmpeg
def test_motion_photo_metadata_present_and_correct(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    convert_video_to_motion_photo(sample_video, output_path, ConversionConfig(overwrite=True))

    report = validate_motion_photo_file(output_path)
    assert report.is_valid
    assert report.checks["xmp_namespaces_correct"] is True
    assert report.checks["motion_photo_flag_set"] is True
    assert report.checks["video_length_field_matches"] is True


@requires_ffmpeg
def test_video_length_matches_declared_xmp_value(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    result = convert_video_to_motion_photo(
        sample_video, output_path, ConversionConfig(overwrite=True)
    )

    combined = output_path.read_bytes()
    _, mp4_part = split_motion_photo(combined)
    assert len(mp4_part) == result.video_size_bytes
    assert len(mp4_part) == sample_video.stat().st_size


@requires_ffmpeg
def test_output_remains_readable_after_write(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    convert_video_to_motion_photo(sample_video, output_path, ConversionConfig(overwrite=True))

    # Re-read from disk (not from the in-memory result) to be sure the
    # file itself -- not just in-process state -- is valid.
    reloaded = validate_motion_photo_file(output_path)
    assert reloaded.is_valid


@requires_ffmpeg
def test_disable_xiaomi_flag_omits_app4_segment(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    cfg = ConversionConfig(overwrite=True, enable_xiaomi=False)
    convert_video_to_motion_photo(sample_video, output_path, cfg)

    jpeg_part, _ = split_motion_photo(output_path.read_bytes())
    assert b"XIAOMI_CUSTOMIZE" not in jpeg_part


@requires_ffmpeg
def test_enable_xiaomi_default_includes_app4_segment(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    convert_video_to_motion_photo(sample_video, output_path, ConversionConfig(overwrite=True))

    jpeg_part, _ = split_motion_photo(output_path.read_bytes())
    assert b"XIAOMI_CUSTOMIZE" in jpeg_part


@requires_ffmpeg
def test_missing_input_raises_conversion_error(tmp_path: Path) -> None:
    with pytest.raises(ConversionError):
        convert_video_to_motion_photo(
            tmp_path / "does_not_exist.mp4", tmp_path / "out.jpg", ConversionConfig()
        )


@requires_ffmpeg
def test_overwrite_false_generates_unique_name(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    cfg_first = ConversionConfig(overwrite=True)
    convert_video_to_motion_photo(sample_video, output_path, cfg_first)

    cfg_second = ConversionConfig(overwrite=False)
    result = convert_video_to_motion_photo(sample_video, output_path, cfg_second)

    assert result.output_path != output_path
    assert result.output_path.exists()


@requires_ffmpeg
def test_keep_cover_retains_intermediate_jpeg(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    cfg = ConversionConfig(overwrite=True, keep_cover=True)
    result = convert_video_to_motion_photo(sample_video, output_path, cfg)

    assert result.cover_path is not None
    assert result.cover_path.is_file()


@requires_ffmpeg
def test_cover_frame_selection(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    cfg = ConversionConfig(overwrite=True, cover_frame=3)
    result = convert_video_to_motion_photo(sample_video, output_path, cfg)
    assert result.validation.is_valid


@requires_ffmpeg
def test_cover_auto_selection(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    cfg = ConversionConfig(overwrite=True, cover_auto=True)
    result = convert_video_to_motion_photo(sample_video, output_path, cfg)
    assert result.validation.is_valid


@requires_ffmpeg
def test_cover_frame_out_of_range_raises(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    cfg = ConversionConfig(overwrite=True, cover_frame=999999)
    with pytest.raises(ConversionError):
        convert_video_to_motion_photo(sample_video, output_path, cfg)


@requires_ffmpeg
def test_strip_audio_removes_audio_stream(sample_video: Path, tmp_path: Path) -> None:
    import subprocess

    output_path = tmp_path / "output.jpg"
    cfg = ConversionConfig(overwrite=True, strip_audio=True)
    result = convert_video_to_motion_photo(sample_video, output_path, cfg)
    assert result.validation.is_valid

    combined = output_path.read_bytes()
    _, mp4_part = split_motion_photo(combined)
    extracted = tmp_path / "extracted.mp4"
    extracted.write_bytes(mp4_part)

    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", str(extracted)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert b"audio" not in proc.stdout


@requires_ffmpeg
def test_keep_audio_by_default(sample_video: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "output.jpg"
    cfg = ConversionConfig(overwrite=True)  # strip_audio defaults to False
    result = convert_video_to_motion_photo(sample_video, output_path, cfg)

    combined = output_path.read_bytes()
    _, mp4_part = split_motion_photo(combined)
    assert len(mp4_part) == sample_video.stat().st_size  # unchanged, audio intact
    assert result.validation.is_valid


@requires_ffmpeg
def test_preview_cover_frame_does_not_build_full_motion_photo(
    sample_video: Path, tmp_path: Path
) -> None:
    from v2mp.converter import preview_cover_frame

    preview_path = tmp_path / "preview.jpg"
    result_path = preview_cover_frame(sample_video, preview_path, ConversionConfig(cover_frame=2))

    assert result_path == preview_path
    assert preview_path.is_file()
    # Must be a plain JPEG with no appended video (i.e. not a Motion Photo).
    _, trailing = split_motion_photo(preview_path.read_bytes())
    assert trailing == b""


@requires_ffmpeg
def test_extract_video_from_motion_photo_round_trip(sample_video: Path, tmp_path: Path) -> None:
    from v2mp.converter import extract_video_from_motion_photo

    output_path = tmp_path / "output.jpg"
    convert_video_to_motion_photo(sample_video, output_path, ConversionConfig(overwrite=True))

    extracted_path = tmp_path / "extracted.mp4"
    result_path = extract_video_from_motion_photo(output_path, extracted_path)

    assert result_path == extracted_path
    assert extracted_path.read_bytes() == sample_video.read_bytes()


def test_extract_video_from_non_motion_photo_raises(tmp_path: Path) -> None:
    from v2mp.converter import extract_video_from_motion_photo

    plain_jpeg = tmp_path / "plain.jpg"
    plain_jpeg.write_bytes(b"\xff\xd8\xff\xe0\x00\x04ab\xff\xd9")  # valid but no video

    with pytest.raises(ConversionError):
        extract_video_from_motion_photo(plain_jpeg, tmp_path / "out.mp4")


def test_extract_video_from_missing_file_raises(tmp_path: Path) -> None:
    from v2mp.converter import extract_video_from_motion_photo

    with pytest.raises(ConversionError):
        extract_video_from_motion_photo(tmp_path / "missing.jpg", tmp_path / "out.mp4")
