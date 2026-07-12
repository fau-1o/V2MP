"""Unit tests for v2mp.xmp."""

from __future__ import annotations

import pytest

from v2mp.config import NS_CONTAINER, NS_CONTAINER_ITEM, NS_GCAMERA
from v2mp.xmp import (
    build_motion_photo_xmp,
    has_container_item_element,
    has_motion_photo_flag,
    parse_video_length_from_xmp,
    required_namespaces_present,
)


def test_build_motion_photo_xmp_contains_namespaces() -> None:
    xmp = build_motion_photo_xmp(video_length_bytes=12345)
    assert NS_GCAMERA.encode() in xmp
    assert NS_CONTAINER.encode() in xmp
    assert NS_CONTAINER_ITEM.encode() in xmp


def test_build_motion_photo_xmp_namespace_placement_matches_real_sample() -> None:
    """
    Regression test: a full-body hex dump of a genuine sample confirms
    ``xmlns:GCamera``/``xmlns:Container``/``xmlns:Item`` are declared on
    ``<rdf:Description>`` itself, NOT hoisted onto the root
    ``<x:xmpmeta>`` element (which is XML-equivalent but not what the
    real device writes).
    """
    xmp = build_motion_photo_xmp(video_length_bytes=20612938)

    # The namespace declarations must appear on rdf:Description...
    description_start = xmp.find(b"<rdf:Description")
    description_end = xmp.find(b">", description_start)
    description_tag = xmp[description_start:description_end]
    assert b'xmlns:GCamera="' + NS_GCAMERA.encode() + b'"' in description_tag
    assert b'xmlns:Container="' + NS_CONTAINER.encode() + b'"' in description_tag
    assert b'xmlns:Item="' + NS_CONTAINER_ITEM.encode() + b'"' in description_tag

    # ...and must NOT appear on the root x:xmpmeta element.
    xmpmeta_start = xmp.find(b"<x:xmpmeta")
    xmpmeta_end = xmp.find(b">", xmpmeta_start)
    xmpmeta_tag = xmp[xmpmeta_start:xmpmeta_end]
    assert b"xmlns:GCamera" not in xmpmeta_tag
    assert b"xmlns:Container" not in xmpmeta_tag
    assert b"xmlns:Item" not in xmpmeta_tag


def test_build_motion_photo_xmp_rejects_non_positive_length() -> None:
    with pytest.raises(ValueError):
        build_motion_photo_xmp(video_length_bytes=0)
    with pytest.raises(ValueError):
        build_motion_photo_xmp(video_length_bytes=-5)


def test_parse_video_length_from_xmp_round_trip() -> None:
    xmp = build_motion_photo_xmp(video_length_bytes=987654)
    assert parse_video_length_from_xmp(xmp) == 987654


def test_has_motion_photo_flag_true_for_generated_xmp() -> None:
    xmp = build_motion_photo_xmp(video_length_bytes=100)
    assert has_motion_photo_flag(xmp) is True


def test_has_motion_photo_flag_false_for_unrelated_xml() -> None:
    assert has_motion_photo_flag(b"<x:xmpmeta><rdf:RDF></rdf:RDF></x:xmpmeta>") is False


def test_required_namespaces_present() -> None:
    xmp = build_motion_photo_xmp(video_length_bytes=100)
    assert required_namespaces_present(xmp) is True
    assert required_namespaces_present(b"no namespaces here") is False


def test_presentation_timestamp_written() -> None:
    xmp = build_motion_photo_xmp(video_length_bytes=100, presentation_timestamp_us=42000)
    assert b"42000" in xmp


def test_container_item_is_element_not_item_item() -> None:
    """
    Regression test for the Container:Item vs Item:Item structural bug.

    Google/Xiaomi gallery apps require the container entries to be
    <Container:Item> elements (element in the Container namespace,
    attributes in the Item namespace) -- NOT <Item:Item> elements. Both
    are XML-well-formed, but only the former is recognized by strict
    gallery-app XMP walkers.
    """
    xmp = build_motion_photo_xmp(video_length_bytes=100)
    assert has_container_item_element(xmp) is True
    # The element opening tag must literally read "Container:Item".
    assert b"<Container:Item" in xmp
    assert b"<Item:Item" not in xmp


def test_xmptk_and_parse_type_resource_present() -> None:
    xmp = build_motion_photo_xmp(video_length_bytes=100)
    assert b'x:xmptk="Adobe XMP Core 5.1.0-jc003"' in xmp
    assert b'rdf:parseType="Resource"' in xmp


def test_no_xpacket_wrapper() -> None:
    """
    Regression test: a byte-level diff against a genuine sample showed the
    real XMP packet starts directly with <x:xmpmeta -- no <?xpacket?>
    processing-instruction wrapper. An earlier version of this function
    added that wrapper.
    """
    xmp = build_motion_photo_xmp(video_length_bytes=100)
    assert xmp.startswith(b"<x:xmpmeta")
    assert b"<?xpacket" not in xmp


def test_xmp_matches_confirmed_real_byte_length() -> None:
    """
    Regression test: locks in the exact byte length confirmed via a full
    (untruncated) text dump of a genuine Xiaomi Motion Photo sample's
    APP1-XMP segment content (1050 bytes for Item:Length="20612938",
    the value in the real sample -- combined with the 30-byte Adobe
    identifier and 2-byte length field, this reproduces the real
    segment's confirmed total size of 1084 bytes).
    """
    xmp = build_motion_photo_xmp(video_length_bytes=20612938)
    assert len(xmp) == 1050
    assert xmp.startswith(b'<x:xmpmeta xmlns:x="adobe:ns:meta/"')
    assert xmp.endswith(b"</x:xmpmeta>")
