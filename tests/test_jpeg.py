"""Unit tests for v2mp.jpeg."""

from __future__ import annotations

import pytest

from v2mp.jpeg import (
    JpegFormatError,
    assemble_motion_photo,
    find_eoi_index,
    split_motion_photo,
    strip_soi,
    validate_soi,
)


def test_validate_soi_accepts_valid_jpeg(minimal_jpeg_bytes: bytes) -> None:
    validate_soi(minimal_jpeg_bytes)  # should not raise


def test_validate_soi_rejects_bad_header() -> None:
    with pytest.raises(JpegFormatError):
        validate_soi(b"not a jpeg")


def test_find_eoi_index_locates_correct_marker(minimal_jpeg_bytes: bytes) -> None:
    idx = find_eoi_index(minimal_jpeg_bytes)
    assert minimal_jpeg_bytes[idx : idx + 2] == b"\xff\xd9"
    # Ensure it's truly the last two bytes of a clean, single JPEG.
    assert idx == len(minimal_jpeg_bytes) - 2


def test_find_eoi_index_ignores_trailing_data_with_fake_marker(
    minimal_jpeg_bytes: bytes, minimal_mp4_bytes: bytes
) -> None:
    """
    The core regression test: trailing binary data containing an
    accidental FFD9 byte sequence must NOT confuse EOI detection.
    """
    combined = minimal_jpeg_bytes + minimal_mp4_bytes
    idx = find_eoi_index(combined)
    # The true EOI must be found within the original JPEG's own bytes,
    # not somewhere inside the appended (fake) MP4 data.
    assert idx < len(minimal_jpeg_bytes)
    assert combined[idx : idx + 2] == b"\xff\xd9"


def test_strip_soi_returns_body_through_eoi(minimal_jpeg_bytes: bytes) -> None:
    body = strip_soi(minimal_jpeg_bytes)
    assert body == minimal_jpeg_bytes[2:]
    assert body.endswith(b"\xff\xd9")


def test_assemble_motion_photo_round_trip(
    minimal_jpeg_bytes: bytes, minimal_mp4_bytes: bytes
) -> None:
    header_segment = bytes([0xFF, 0xE1, 0x00, 0x04, 0x00, 0x00])  # trivial dummy APP1
    combined = assemble_motion_photo(minimal_jpeg_bytes, minimal_mp4_bytes, [header_segment])

    assert combined.startswith(b"\xff\xd8")
    assert header_segment in combined[:20]

    jpeg_part, mp4_part = split_motion_photo(combined)
    assert mp4_part == minimal_mp4_bytes
    assert jpeg_part.startswith(b"\xff\xd8")
    assert jpeg_part.endswith(b"\xff\xd9")


def test_split_motion_photo_exact_sizes(
    minimal_jpeg_bytes: bytes, minimal_mp4_bytes: bytes
) -> None:
    combined = assemble_motion_photo(minimal_jpeg_bytes, minimal_mp4_bytes, [])
    jpeg_part, mp4_part = split_motion_photo(combined)
    assert len(jpeg_part) + len(mp4_part) == len(combined)
    assert len(mp4_part) == len(minimal_mp4_bytes)


def test_find_eoi_index_raises_without_eoi() -> None:
    with pytest.raises(JpegFormatError):
        find_eoi_index(b"\xff\xd8\xff\xe0\x00\x04\x00\x00")


def test_iter_markers_lists_all_segments(minimal_jpeg_bytes: bytes) -> None:
    from v2mp.jpeg import iter_markers

    markers = iter_markers(minimal_jpeg_bytes)
    assert markers[0].marker == "SOI"
    assert markers[0].offset == 0
    assert markers[-1].marker == "EOI"
    # Total of all segment lengths should equal the JPEG's own size
    # (iter_markers stops at EOI, ignoring any trailing appended data).
    assert markers[-1].offset + markers[-1].length == len(minimal_jpeg_bytes)


def test_iter_markers_ignores_trailing_data(
    minimal_jpeg_bytes: bytes, minimal_mp4_bytes: bytes
) -> None:
    from v2mp.jpeg import iter_markers

    combined = minimal_jpeg_bytes + minimal_mp4_bytes
    markers = iter_markers(combined)
    assert markers[-1].marker == "EOI"
    # EOI must be located within the original JPEG's bytes, not the
    # appended (fake) MP4 data that also contains a coincidental FFD9.
    assert markers[-1].offset < len(minimal_jpeg_bytes)


def test_iter_markers_rejects_invalid_jpeg() -> None:
    from v2mp.jpeg import iter_markers

    with pytest.raises(JpegFormatError):
        iter_markers(b"not a jpeg")
