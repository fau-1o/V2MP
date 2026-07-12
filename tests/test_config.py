"""Unit tests for v2mp.config.ConversionConfig validation."""

from __future__ import annotations

import pytest

from v2mp.config import ConversionConfig


def test_default_config_is_valid() -> None:
    cfg = ConversionConfig()
    assert cfg.jobs == 1
    assert cfg.cover_frame is None
    assert cfg.cover_auto is False
    assert cfg.strip_audio is False
    assert cfg.dry_run is False


def test_jobs_must_be_positive() -> None:
    with pytest.raises(ValueError):
        ConversionConfig(jobs=0)
    with pytest.raises(ValueError):
        ConversionConfig(jobs=-1)


def test_jobs_greater_than_one_is_valid() -> None:
    cfg = ConversionConfig(jobs=8)
    assert cfg.jobs == 8


def test_cover_frame_must_be_non_negative() -> None:
    with pytest.raises(ValueError):
        ConversionConfig(cover_frame=-1)


def test_cover_frame_zero_is_valid() -> None:
    cfg = ConversionConfig(cover_frame=0)
    assert cfg.cover_frame == 0


def test_video_extensions_includes_defaults_and_extras() -> None:
    cfg = ConversionConfig(extra_video_extensions=frozenset({".webm"}))
    exts = cfg.video_extensions()
    assert ".mp4" in exts
    assert ".webm" in exts
