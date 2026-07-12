"""Unit tests for v2mp.cli argument parsing (no ffmpeg required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from v2mp.cli import _config_from_args, build_arg_parser


def _parse(args: list[str]):
    parser = build_arg_parser()
    return parser.parse_args(args)


def test_cover_selection_flags_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        _parse(["input.mp4", "--cover-timestamp", "1.0", "--cover-frame", "5"])
    with pytest.raises(SystemExit):
        _parse(["input.mp4", "--cover-frame", "5", "--cover-auto"])


def test_cover_frame_parsed_as_int() -> None:
    args = _parse(["input.mp4", "--cover-frame", "42"])
    cfg = _config_from_args(args)
    assert cfg.cover_frame == 42
    assert cfg.cover_auto is False


def test_cover_auto_flag() -> None:
    args = _parse(["input.mp4", "--cover-auto"])
    cfg = _config_from_args(args)
    assert cfg.cover_auto is True
    assert cfg.cover_frame is None


def test_no_audio_flag_maps_to_strip_audio() -> None:
    args = _parse(["input.mp4", "--no-audio"])
    cfg = _config_from_args(args)
    assert cfg.strip_audio is True


def test_audio_kept_by_default() -> None:
    args = _parse(["input.mp4"])
    cfg = _config_from_args(args)
    assert cfg.strip_audio is False


def test_jobs_flag_parsed() -> None:
    args = _parse(["folder/", "--jobs", "4"])
    cfg = _config_from_args(args)
    assert cfg.jobs == 4


def test_dry_run_flag() -> None:
    args = _parse(["folder/", "--dry-run"])
    cfg = _config_from_args(args)
    assert cfg.dry_run is True


def test_preview_cover_extract_video_inspect_are_optional_paths() -> None:
    args = _parse(["input.mp4"])
    assert args.preview_cover is None
    assert args.extract_video is None
    assert args.inspect is False

    args = _parse(["input.mp4", "--preview-cover", "preview.jpg"])
    assert args.preview_cover == Path("preview.jpg")

    args = _parse(["photo.jpg", "--extract-video", "out.mp4"])
    assert args.extract_video == Path("out.mp4")

    args = _parse(["photo.jpg", "--inspect"])
    assert args.inspect is True
