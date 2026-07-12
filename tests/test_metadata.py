"""Unit tests for v2mp.metadata."""

from __future__ import annotations

import json

import pytest

from v2mp.config import JPEG_APP1, JPEG_APP4, XIAOMI_IDENTIFIER, XMP_IDENTIFIER
from v2mp.metadata import build_exif_segment, build_xiaomi_segment, build_xmp_segment
from v2mp.xmp import build_motion_photo_xmp


def test_build_exif_segment_has_correct_marker_and_identifier() -> None:
    segment = build_exif_segment()
    assert segment[0] == 0xFF
    assert segment[1] == JPEG_APP1
    length = int.from_bytes(segment[2:4], "big")
    assert length == len(segment) - 2
    assert segment[4:10] == b"Exif\x00\x00"


def test_build_exif_segment_with_orientation() -> None:
    segment = build_exif_segment(orientation=6)
    assert segment[1] == JPEG_APP1


def test_build_exif_segment_rejects_bad_orientation() -> None:
    with pytest.raises(ValueError):
        build_exif_segment(orientation=9)
    with pytest.raises(ValueError):
        build_exif_segment(orientation=0)


def test_build_xiaomi_segment_default_payload() -> None:
    from v2mp.metadata import XIAOMI_HEADER_VERSION_BYTES

    segment = build_xiaomi_segment()
    assert segment[0] == 0xFF
    assert segment[1] == JPEG_APP4
    payload = segment[4:]
    assert payload.startswith(XIAOMI_IDENTIFIER)
    after_identifier = payload[len(XIAOMI_IDENTIFIER) :]
    assert after_identifier.startswith(XIAOMI_HEADER_VERSION_BYTES)
    json_part = after_identifier[len(XIAOMI_HEADER_VERSION_BYTES) :]
    parsed = json.loads(json_part.decode("utf-8"))
    assert parsed == {"9a01": "1", "8897": "1", "version": "32"}


def test_build_xiaomi_segment_matches_confirmed_real_bytes() -> None:
    """
    Regression test: locks in the byte-exact APP4 payload confirmed via a
    full (untruncated) hex dump of a genuine Xiaomi Motion Photo sample --
    ``XIAOMI_CUSTOMIZE\\x00`` + ``\\x01\\x01`` + the exact JSON body, 61
    bytes total for the complete segment (marker + length + identifier +
    version bytes + JSON), matching the real file precisely.
    """
    segment = build_xiaomi_segment()
    assert len(segment) == 61
    expected_payload = b'XIAOMI_CUSTOMIZE\x00\x01\x01{"9a01":"1","8897":"1","version":"32"}'
    assert segment[4:] == expected_payload


def test_build_xiaomi_segment_custom_payload() -> None:
    from v2mp.metadata import XIAOMI_HEADER_VERSION_BYTES

    custom = {"foo": "bar"}
    segment = build_xiaomi_segment(custom)
    payload = segment[4:]
    after_identifier = payload[len(XIAOMI_IDENTIFIER) :]
    json_part = after_identifier[len(XIAOMI_HEADER_VERSION_BYTES) :]
    assert json.loads(json_part.decode("utf-8")) == custom


def test_build_xmp_segment_wraps_identifier_and_packet() -> None:
    xmp_packet = build_motion_photo_xmp(video_length_bytes=1000)
    segment = build_xmp_segment(xmp_packet)
    assert segment[1] == JPEG_APP1
    payload = segment[4:]
    assert payload.startswith(XMP_IDENTIFIER)
    assert xmp_packet in payload


def test_segment_length_field_matches_actual_size() -> None:
    xmp_packet = build_motion_photo_xmp(video_length_bytes=555)
    segment = build_xmp_segment(xmp_packet)
    declared_len = int.from_bytes(segment[2:4], "big")
    assert declared_len == len(segment) - 2


def test_build_icc_profile_segment_default() -> None:
    from v2mp.config import JPEG_APP2
    from v2mp.metadata import _ICC_IDENTIFIER, build_icc_profile_segment

    segments = build_icc_profile_segment()
    assert len(segments) >= 1
    segment = segments[0]
    assert segment[0] == 0xFF
    assert segment[1] == JPEG_APP2
    payload = segment[4:]
    assert payload.startswith(_ICC_IDENTIFIER)
    # chunk sequence number (1) and total chunk count follow the identifier
    seq, total = payload[len(_ICC_IDENTIFIER)], payload[len(_ICC_IDENTIFIER) + 1]
    assert seq == 1
    assert total == len(segments)


def test_build_icc_profile_segment_custom_bytes() -> None:
    from v2mp.metadata import build_icc_profile_segment

    fake_icc = b"\x00" * 100
    segments = build_icc_profile_segment(fake_icc)
    assert len(segments) == 1
    assert fake_icc in segments[0]


def test_exif_segment_uses_short_type_for_dimensions() -> None:
    """
    Regression test: a byte-level diff against two independent genuine
    Xiaomi samples showed ImageWidth/ImageLength encoded as TIFF type 3
    (SHORT), not type 4 (LONG). An earlier version of this function
    produced LONG-typed entries (piexif's uncustomized default).
    """
    import piexif

    segment = build_exif_segment(image_width=1920, image_height=1080)
    exif_dict = piexif.load(segment[4:])
    assert exif_dict["0th"][piexif.ImageIFD.ImageWidth] == 1920
    assert exif_dict["0th"][piexif.ImageIFD.ImageLength] == 1080

    # Confirm the on-the-wire TIFF type byte for each entry is 3 (SHORT),
    # not 4 (LONG) -- piexif.load() doesn't expose the raw type, so walk
    # the IFD0 entries directly.
    import struct

    exif_bytes = segment[4:]
    tiff = exif_bytes[6:]  # after "Exif\x00\x00"
    ifd0_offset = struct.unpack(">I", tiff[4:8])[0]
    count = struct.unpack(">H", tiff[ifd0_offset : ifd0_offset + 2])[0]
    pos = ifd0_offset + 2
    found_types = {}
    for _ in range(count):
        tag, typ = struct.unpack(">HH", tiff[pos : pos + 4])
        found_types[tag] = typ
        pos += 12
    assert found_types[piexif.ImageIFD.ImageWidth] == 3
    assert found_types[piexif.ImageIFD.ImageLength] == 3


def test_exif_segment_includes_confirmed_vendor_tags() -> None:
    """
    Regression test: a full flattened tag dump of a genuine Xiaomi sample
    showed Exif SubIFD tags 0x8897=1, 0x9A01=1, and LightSource=0. An
    earlier version of this function instead wrote fabricated ColorSpace/
    ExifVersion/PixelXDimension/PixelYDimension tags that do not appear
    in genuine samples at all.
    """
    import piexif

    segment = build_exif_segment(image_width=1920, image_height=1080)
    exif_dict = piexif.load(segment[4:])

    assert exif_dict["Exif"][0x9A01] == 1
    assert exif_dict["Exif"][0x8897] == 1
    assert exif_dict["Exif"][piexif.ExifIFD.LightSource] == 0

    # Fabricated tags from the earlier version must NOT be present.
    assert piexif.ExifIFD.ColorSpace not in exif_dict["Exif"]
    assert piexif.ExifIFD.ExifVersion not in exif_dict["Exif"]
    assert piexif.ExifIFD.PixelXDimension not in exif_dict["Exif"]
    assert piexif.ExifIFD.PixelYDimension not in exif_dict["Exif"]


def test_xiaomi_header_version_bytes_present_before_json() -> None:
    """
    Regression test: a byte-level diff against a genuine sample showed
    the real APP4 payload is IDENTIFIER + b"\\x01\\x01" + JSON, while an
    earlier version of this function wrote IDENTIFIER + JSON directly
    (silently dropping those two bytes).
    """
    segment = build_xiaomi_segment()
    payload = segment[4:]
    assert payload[len(XIAOMI_IDENTIFIER) : len(XIAOMI_IDENTIFIER) + 2] == b"\x01\x01"
    # The byte immediately after the two version bytes must be the JSON
    # opening brace, not (e.g.) another stray byte.
    assert payload[len(XIAOMI_IDENTIFIER) + 2 : len(XIAOMI_IDENTIFIER) + 3] == b"{"


def test_build_jfif_segment_matches_confirmed_real_bytes() -> None:
    """
    Regression test: locks in the byte-exact APP0/JFIF segment confirmed
    via hex dump of a genuine Xiaomi Motion Photo sample (18 bytes total).
    """
    from v2mp.metadata import build_jfif_segment

    segment = build_jfif_segment()
    assert len(segment) == 18
    assert segment == bytes.fromhex("ffe000104a46494600010100000100010000")
