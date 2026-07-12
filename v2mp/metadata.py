"""
metadata.py
===========

Construction of the non-XMP metadata segments embedded in the output JPEG.

Structural details below were confirmed by a byte-level comparison against
a genuine Xiaomi-generated Motion Photo (not just format-level
reverse-engineering):

* Exif APP1: the real sample's IFD0 contains exactly ``ImageWidth`` (tag
  ``0x0100``), ``ImageLength`` (tag ``0x0101``), and an ``ExifIFDPointer``
  (tag ``0x8769``) -- notably NOT ``Software``, resolution tags, or
  ``DateTime`` in IFD0, which an earlier version of this module incorrectly
  added (ballooning the segment well past the real ~120-byte size and away
  from the real structure, not closer to it).
* Xiaomi ``XIAOMI_CUSTOMIZE`` APP4: the real sample's payload is
  ``"XIAOMI_CUSTOMIZE\\x00"`` followed by two additional header bytes
  (``\\x01\\x01``, most likely a major/minor version pair) BEFORE the JSON
  body starts. An earlier version of this module wrote the JSON
  immediately after the null terminator, silently dropping these two
  bytes -- confirmed by a direct hex diff against the real file.
* APP1 XMP: the real sample's XMP does NOT use the ``<?xpacket
  begin=...?> ... <?xpacket end="w"?>`` processing-instruction wrapper --
  the packet begins directly with ``<x:xmpmeta`` right after the
  ``"http://ns.adobe.com/xap/1.0/\\x00"`` identifier. An earlier version
  of this module added that wrapper, which is valid but not what the real
  device writes.

This segment is optional and gated behind
:attr:`ConversionConfig.enable_xiaomi`.

A standard sRGB ICC profile APP2 segment is also generated (via Pillow's
LittleCMS binding), matching the APP2/ICC Profile segment present in
genuine camera/gallery-generated Motion Photos -- many ffmpeg-extracted
video frames carry no color profile at all. Note this is NOT a byte-exact
copy of Xiaomi's own ICC profile (only Xiaomi's profile size and a "no
CMM declared" header have been confirmed from the sample; the full
profile body -- e.g. whether it is sRGB or Display P3 -- has not).

All functions here return fully-formed JPEG segment bytes (marker +
length + identifier + payload), ready to be spliced into a JPEG byte
stream by :mod:`v2mp.jpeg`.
"""

from __future__ import annotations

import json

import piexif

from .config import (
    DEFAULT_XIAOMI_PAYLOAD,
    JPEG_APP1,
    JPEG_APP2,
    JPEG_APP4,
    MAX_SEGMENT_PAYLOAD,
    XIAOMI_IDENTIFIER,
)
from .utils import get_logger

logger = get_logger()

# --------------------------------------------------------------------------- #
# piexif registry patches
# --------------------------------------------------------------------------- #
# piexif hardcodes ImageWidth/ImageLength (tags 0x0100/0x0101) as TIFF type 4
# (LONG) in its internal tag registry, with no way to override the type via
# its high-level dump() API. Two independent byte-level comparisons against
# genuine Xiaomi-generated Motion Photos showed both tags encoded as type 3
# (SHORT) instead. Patching the registry once, at import time, is simpler
# and safer than reimplementing IFD serialization ourselves, and only ever
# *adds* capability -- it doesn't change how any tag piexif already
# understood is interpreted by anything reading Exif data (only how *we*
# write these two tags going forward in this process).
piexif.TAGS["0th"][piexif.ImageIFD.ImageWidth]["type"] = 3
piexif.TAGS["0th"][piexif.ImageIFD.ImageLength]["type"] = 3

#: Xiaomi vendor-specific Exif SubIFD tags, confirmed present (value ``1``
#: each) via a full flattened tag dump of a genuine sample. These share
#: their numeric IDs with two of the keys in the XIAOMI_CUSTOMIZE APP4 JSON
#: payload (see DEFAULT_XIAOMI_PAYLOAD) -- Xiaomi's format apparently
#: stores the same custom fields in both places. piexif has no built-in
#: definition for these (they aren't standard Exif tags), so they're
#: registered here as SHORT, matching the type of neighboring standard
#: tags in the same numeric range (e.g. LightSource).
_XIAOMI_EXIF_TAG_9A01 = 0x9A01
_XIAOMI_EXIF_TAG_8897 = 0x8897
piexif.TAGS["Exif"].setdefault(_XIAOMI_EXIF_TAG_9A01, {"name": "XiaomiTag9A01", "type": 3})
piexif.TAGS["Exif"].setdefault(_XIAOMI_EXIF_TAG_8897, {"name": "XiaomiTag8897", "type": 3})

#: The two bytes observed immediately after the null-terminated
#: "XIAOMI_CUSTOMIZE\\x00" identifier and before the JSON body in a
#: genuine sample -- confirmed via direct hex comparison. Most likely a
#: major/minor version pair for the vendor payload format itself (distinct
#: from the "version" key inside the JSON, which describes something else).
XIAOMI_HEADER_VERSION_BYTES = b"\x01\x01"

#: Standard ICC APP2 segment identifier, per the Adobe/ICC embedding spec.
_ICC_IDENTIFIER = b"ICC_PROFILE\x00"

#: Maximum ICC data bytes per APP2 chunk. The identifier (12 bytes) plus a
#: 2-byte chunk-sequence header must also fit within the 65533-byte
#: segment payload limit, leaving this much room for profile data.
_ICC_MAX_CHUNK_DATA = MAX_SEGMENT_PAYLOAD - len(_ICC_IDENTIFIER) - 2


def _build_app_segment(marker_byte: int, payload: bytes) -> bytes:
    """
    Wrap a payload in a standard JPEG APPn marker segment.

    Args:
        marker_byte: The marker byte for this segment (e.g. 0xE1 for APP1).
        payload: The identifier + data bytes to wrap (NOT including the
            marker or length field).

    Returns:
        The full segment bytes: ``FF <marker> <len_hi> <len_lo> <payload>``.

    Raises:
        ValueError: If the payload is too large to fit in a single JPEG
            segment (the 2-byte length field allows at most 65533 bytes
            of payload, since the length field counts itself).
    """
    length = len(payload) + 2  # length field includes itself
    if length > 0xFFFF:
        raise ValueError(
            f"Segment payload too large ({len(payload)} bytes); "
            f"maximum is {MAX_SEGMENT_PAYLOAD} bytes per JPEG segment "
            "(extended/multi-segment XMP is not implemented)."
        )
    return bytes([0xFF, marker_byte]) + length.to_bytes(2, "big") + payload


def build_exif_segment(
    orientation: int | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
) -> bytes:
    """
    Build an Exif APP1 segment matching the structure confirmed in genuine
    Xiaomi samples.

    Two independent byte-level comparisons against real Xiaomi-generated
    Motion Photos (including a full flattened tag dump, not just a raw
    hex preview) refined this structure twice:

    1. IFD0 contains ``ImageWidth``/``ImageLength`` encoded as TIFF type
       ``3`` (SHORT), not type ``4`` (LONG) -- confirmed identically in
       both samples' raw bytes. ``piexif`` hardcodes these as LONG in its
       internal tag registry, so this module patches that registry entry
       at import time (see below).
    2. The Exif SubIFD contains two vendor-specific tags, ``0x8897`` and
       ``0x9A01`` (both value ``1``) -- the same tag numbers used as JSON
       keys in the ``XIAOMI_CUSTOMIZE`` APP4 segment (see
       :data:`DEFAULT_XIAOMI_PAYLOAD`), plus a standard ``LightSource``
       tag (``0``). A PREVIOUS version of this function instead added
       fabricated ``ColorSpace``, ``ExifVersion``, ``PixelXDimension``,
       and ``PixelYDimension`` tags that do NOT appear in either genuine
       sample at all -- removing them is a correction, not a regression.

    An IFD0 ``Orientation`` value was reported by one comparison, but a
    byte-level walk of that same sample's IFD0 (entry count 4: Width,
    Length, then an entry whose tag bytes are already ``0x8769`` --
    ExifIFDPointer -- immediately after Length) leaves no structural room
    for it there, and ``piexif`` does not synthesize a default when the
    tag is absent. This is therefore NOT added here pending clearer
    evidence of where it actually lives.

    Args:
        orientation: Optional EXIF orientation value (1-8) to carry over
            from the source frame. Omitted entirely if ``None``.
        image_width: Pixel width of the cover image, written to
            ``ImageWidth``. If ``None``, the tag is omitted.
        image_height: Pixel height of the cover image, written to
            ``ImageLength``. If ``None``, the tag is omitted.

    Returns:
        Complete APP1 JPEG segment bytes containing the Exif data.

    Raises:
        ValueError: If ``orientation`` is provided but out of range.
    """
    if orientation is not None and not 1 <= orientation <= 8:
        raise ValueError("EXIF orientation must be between 1 and 8")

    zeroth_ifd: dict[int, object] = {}
    if image_width is not None:
        zeroth_ifd[piexif.ImageIFD.ImageWidth] = int(image_width)
    if image_height is not None:
        zeroth_ifd[piexif.ImageIFD.ImageLength] = int(image_height)
    if orientation is not None:
        zeroth_ifd[piexif.ImageIFD.Orientation] = orientation

    exif_ifd: dict[int, object] = {
        _XIAOMI_EXIF_TAG_9A01: 1,
        _XIAOMI_EXIF_TAG_8897: 1,
        piexif.ExifIFD.LightSource: 0,
    }

    # piexif automatically writes the ExifIFDPointer (tag 0x8769) into
    # IFD0 when the "Exif" sub-dict is non-empty, matching the observed
    # sample without us needing to compute the offset ourselves.
    exif_dict = {"0th": zeroth_ifd, "Exif": exif_ifd, "GPS": {}, "1st": {}, "thumbnail": None}
    exif_bytes = piexif.dump(exif_dict)

    # piexif.dump() already returns a full "Exif\x00\x00" + TIFF payload,
    # but without the surrounding JPEG APP1 marker/length -- add those here.
    return _build_app_segment(JPEG_APP1, exif_bytes)


def build_xmp_segment(xmp_packet: bytes) -> bytes:
    """
    Wrap a pre-built XMP packet in an APP1 JPEG segment with the Adobe identifier.

    Args:
        xmp_packet: The full XMP packet bytes (see
            :func:`v2mp.xmp.build_motion_photo_xmp`).

    Returns:
        Complete APP1 JPEG segment bytes containing the XMP identifier
        followed by the XMP packet.
    """
    from .config import XMP_IDENTIFIER  # local import avoids a cycle at module load

    payload = XMP_IDENTIFIER + xmp_packet
    return _build_app_segment(JPEG_APP1, payload)


def build_xiaomi_segment(payload: dict[str, str] | None = None) -> bytes:
    """
    Build the Xiaomi ``XIAOMI_CUSTOMIZE`` APP4 segment.

    Payload layout, confirmed via byte-level comparison against a genuine
    sample: ``"XIAOMI_CUSTOMIZE\\x00"`` + ``XIAOMI_HEADER_VERSION_BYTES``
    (``\\x01\\x01``) + JSON body. An earlier version of this function
    wrote the JSON immediately after the null terminator, omitting those
    two header bytes.

    Args:
        payload: Mapping of Xiaomi-specific tag names to string values.
            Defaults to :data:`v2mp.config.DEFAULT_XIAOMI_PAYLOAD`,
            which now matches a full (untruncated) hex dump of a genuine
            sample byte-for-byte: exactly the keys ``9a01``, ``8897``,
            ``version`` in that order.

    Returns:
        Complete APP4 JPEG segment bytes.
    """
    data = payload if payload is not None else DEFAULT_XIAOMI_PAYLOAD
    json_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")
    full_payload = XIAOMI_IDENTIFIER + XIAOMI_HEADER_VERSION_BYTES + json_bytes
    return _build_app_segment(JPEG_APP4, full_payload)


def _generate_srgb_icc_profile() -> bytes:
    """
    Generate a standard sRGB ICC profile using Pillow's LittleCMS binding.

    The raw profile LittleCMS produces stamps its own ``"lcms"`` four-
    character code into the ICC header's "preferred CMM type" field
    (bytes 4-7). A genuine Xiaomi sample has this field zeroed out (no
    CMM declared) -- confirmed via byte-level comparison. That field is
    purely informational (ICC spec explicitly allows it to be absent/
    zero) so zeroing it does not affect the profile's validity, but it
    does remove the obvious "this was generated by LittleCMS" signature.

    Returns:
        Raw ICC profile bytes (a compact, valid sRGB profile -- not the
        full ~500+ byte profile Xiaomi embeds, since the actual profile
        body has not been confirmed byte-for-byte, but structurally valid
        and parses correctly as a standard ICC profile).

    Raises:
        RuntimeError: If Pillow was built without LittleCMS support.
    """
    try:
        from PIL import ImageCms
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "Pillow's ImageCms module (LittleCMS) is required to generate "
            "an embedded ICC profile, but is not available."
        ) from exc

    profile = ImageCms.createProfile("sRGB")
    data = bytearray(ImageCms.ImageCmsProfile(profile).tobytes())
    if len(data) >= 8:
        data[4:8] = b"\x00\x00\x00\x00"  # zero the "preferred CMM type" field
    return bytes(data)


def build_icc_profile_segment(icc_bytes: bytes | None = None) -> list[bytes]:
    """
    Build one or more APP2 JPEG segments embedding an ICC color profile.

    Follows the standard multi-chunk ICC embedding convention (identifier
    ``"ICC_PROFILE\\0"`` followed by a 1-based chunk sequence number and
    total chunk count, then a slice of the profile data) used by Adobe
    products and virtually all JPEG encoders/decoders, splitting across
    multiple APP2 segments if the profile does not fit in one.

    Args:
        icc_bytes: Raw ICC profile bytes to embed. If ``None``, a
            standard sRGB profile is generated automatically via
            :func:`_generate_srgb_icc_profile`.

    Returns:
        A list of complete APP2 segment byte strings, in order. Almost
        always a single-element list, since a standard sRGB profile is
        far smaller than one segment's capacity.
    """
    data = icc_bytes if icc_bytes is not None else _generate_srgb_icc_profile()

    chunks = [
        data[i : i + _ICC_MAX_CHUNK_DATA] for i in range(0, len(data), _ICC_MAX_CHUNK_DATA)
    ] or [b""]
    total_chunks = len(chunks)

    segments = []
    for index, chunk in enumerate(chunks, start=1):
        payload = _ICC_IDENTIFIER + bytes([index, total_chunks]) + chunk
        segments.append(_build_app_segment(JPEG_APP2, payload))
    return segments


#: Canonical JFIF APP0 payload: version 1.01, no density/aspect-ratio info
#: (units=0), no embedded thumbnail. Byte-for-byte identical to the APP0
#: segment confirmed in a genuine Xiaomi sample (18-byte total segment).
_JFIF_PAYLOAD = (
    b"JFIF\x00"  # identifier
    b"\x01\x01"  # version 1.01
    b"\x00"  # units: 0 = aspect ratio only (no absolute density)
    b"\x00\x01"  # Xdensity = 1
    b"\x00\x01"  # Ydensity = 1
    b"\x00\x00"  # thumbnail width=0, height=0 (no embedded thumbnail)
)


def build_jfif_segment() -> bytes:
    """
    Build a canonical APP0/JFIF segment.

    Some ffmpeg builds/versions omit the APP0/JFIF segment from extracted
    cover frames (observed to vary across environments); a genuine Xiaomi
    sample always has one immediately after SOI. This function produces
    an APP0 segment byte-for-byte identical to the one confirmed in that
    sample, for use as a fallback when the cover image lacks its own.

    Returns:
        Complete 18-byte APP0 JPEG segment.
    """
    return _build_app_segment(0xE0, _JFIF_PAYLOAD)
