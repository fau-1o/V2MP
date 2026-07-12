"""
jpeg.py
=======

Low-level JPEG byte-stream manipulation.

This module is responsible for:

* Parsing/validating the SOI and EOI markers of a JPEG file.
* Splicing newly-built APP segments (Exif, Xiaomi APP4, XMP) into a JPEG
  immediately after the SOI marker, ahead of whatever segments the source
  cover image already contains (APP0/JFIF, APP2/ICC, DQT, SOF, DHT, SOS,
  scan data).
* Appending an MP4 payload byte-for-byte after the JPEG's EOI marker to
  produce the final single-file Motion Photo.
* Splitting a Motion Photo file back apart into its JPEG and MP4
  components (used by the validator and test suite).

No image re-encoding ever happens here: the cover JPEG's compressed scan
data is treated as an opaque blob and copied verbatim, exactly as the
project specification requires ("Do not rewrite image unnecessarily").
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import JPEG_EOI, JPEG_SOI, JPEG_SOS

try:
    import piexif
except ImportError:  # pragma: no cover - piexif is a hard requirement in practice
    piexif = None  # type: ignore[assignment]


class JpegFormatError(ValueError):
    """Raised when a JPEG byte stream is malformed or missing required markers."""


def validate_soi(data: bytes) -> None:
    """
    Raise if ``data`` does not begin with a valid JPEG SOI marker.

    Args:
        data: Candidate JPEG bytes.

    Raises:
        JpegFormatError: If the first two bytes are not ``0xFFD8``.
    """
    if len(data) < 2 or data[:2] != JPEG_SOI:
        raise JpegFormatError("Data does not start with a valid JPEG SOI marker (FFD8)")


def find_eoi_index(data: bytes) -> int:
    """
    Locate the index of the EOI marker's first byte (``0xFF``) in ``data``.

    This performs a proper forward walk of the JPEG marker segments (using
    each segment's length field to jump to the next marker) until the
    Start-Of-Scan (SOS) marker is reached, and then scans the following
    entropy-coded data byte-by-byte -- honoring JPEG byte-stuffing
    (``0xFF 0x00``) and restart markers (``0xFF 0xD0``-``0xD7``) -- until
    the real EOI marker is found.

    A naive right-to-left byte-pattern search for ``FFD9`` is NOT safe
    once arbitrary binary data (such as an appended MP4 payload) follows
    the JPEG: that trailing data can easily contain an accidental
    ``FFD9`` byte pair, which would select the wrong split point. Forward
    marker-based parsing anchored at the JPEG's own structure avoids this
    entirely, since it locates the first genuine EOI immediately
    following the JPEG's own scan data, regardless of what data follows.

    Args:
        data: Full byte stream, starting with a JPEG (must already have
            passed :func:`validate_soi`). May be followed by arbitrary
            trailing data (e.g. an appended MP4).

    Returns:
        The index of the ``0xFF`` byte that begins the JPEG's EOI marker.

    Raises:
        JpegFormatError: If the marker structure is malformed or no EOI
            marker can be found.
    """
    length = len(data)
    pos = len(JPEG_SOI)

    while True:
        if pos + 1 >= length:
            raise JpegFormatError("Reached end of data while parsing JPEG markers")
        if data[pos] != 0xFF:
            raise JpegFormatError(
                f"Expected JPEG marker (0xFF) at offset {pos}, found 0x{data[pos]:02X}"
            )

        marker = data[pos + 1]

        # EOI reached before any SOS (e.g. a truncated/empty image).
        if marker == 0xD9:
            return pos

        # Markers with no length field / no payload: TEM and RSTn.
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue

        if pos + 4 > length:
            raise JpegFormatError("Truncated JPEG segment length field")
        seg_len = int.from_bytes(data[pos + 2 : pos + 4], "big")
        if seg_len < 2:
            raise JpegFormatError(f"Invalid JPEG segment length {seg_len} at offset {pos}")

        if marker == JPEG_SOS:
            scan_start = pos + 2 + seg_len
            return _find_eoi_after_scan(data, scan_start)

        pos = pos + 2 + seg_len


def _find_eoi_after_scan(data: bytes, scan_start: int) -> int:
    """
    Scan entropy-coded data (following SOS) for the terminating EOI marker.

    Args:
        data: Full byte stream.
        scan_start: Index of the first byte of entropy-coded scan data.

    Returns:
        The index of the ``0xFF`` byte beginning the EOI marker.

    Raises:
        JpegFormatError: If no EOI marker is found before the data ends.
    """
    length = len(data)
    i = scan_start
    while i < length - 1:
        if data[i] == 0xFF:
            nxt = data[i + 1]
            if nxt == 0x00:
                i += 2  # Byte-stuffed literal 0xFF within scan data.
                continue
            if 0xD0 <= nxt <= 0xD7:
                i += 2  # Restart marker, legitimately embedded in scan data.
                continue
            if nxt == 0xFF:
                i += 1  # Fill byte; keep scanning.
                continue
            if nxt == 0xD9:
                return i
            # Any other marker appearing mid-scan is unexpected for a
            # single-scan baseline JPEG; treat conservatively as data
            # rather than aborting, since some encoders emit harmless
            # padding here.
            i += 1
            continue
        i += 1
    raise JpegFormatError("No JPEG EOI marker (FFD9) found after scan data")


def extract_orientation(jpeg_bytes: bytes) -> int | None:
    """
    Best-effort extraction of the Exif ``Orientation`` tag from a source JPEG.

    Used so that if the ffmpeg-extracted cover frame happens to carry
    orientation metadata, it is preserved in the newly generated Exif
    segment rather than silently dropped.

    Args:
        jpeg_bytes: Source JPEG bytes (e.g. the raw ffmpeg-extracted cover).

    Returns:
        The orientation value (1-8), or ``None`` if absent/unreadable.
    """
    if piexif is None:  # pragma: no cover
        return None
    try:
        exif_dict = piexif.load(jpeg_bytes)
    except Exception:  # noqa: BLE001 - best effort only, never fatal
        return None
    orientation = exif_dict.get("0th", {}).get(piexif.ImageIFD.Orientation)
    if isinstance(orientation, int) and 1 <= orientation <= 8:
        return orientation
    return None


def strip_soi(jpeg_bytes: bytes) -> bytes:
    """
    Return the JPEG body (everything after the SOI marker, EOI inclusive).

    Args:
        jpeg_bytes: Full source JPEG bytes.

    Returns:
        Bytes from immediately after the SOI marker through the EOI
        marker inclusive. Any trailer bytes after EOI (there should be
        none for a clean ffmpeg-produced JPEG) are discarded.

    Raises:
        JpegFormatError: If the input is not a valid JPEG.
    """
    validate_soi(jpeg_bytes)
    eoi_index = find_eoi_index(jpeg_bytes)
    body_end = eoi_index + len(JPEG_EOI)
    return jpeg_bytes[len(JPEG_SOI) : body_end]


def ensure_app0_present(body: bytes, jfif_segment: bytes) -> bytes:
    """
    Guarantee a JPEG body starts with an APP0/JFIF segment.

    Some ffmpeg builds/versions omit the APP0/JFIF segment from extracted
    cover frames; a genuine Xiaomi Motion Photo always has one immediately
    after SOI. If ``body`` already starts with an APP0 segment, it is
    left untouched (no duplication). Otherwise, ``jfif_segment`` is
    inserted at the very start.

    Args:
        body: JPEG body bytes as returned by :func:`strip_soi`.
        jfif_segment: A fully-formed APP0 segment to insert if ``body``
            does not already start with one (see
            :func:`v2mp.metadata.build_jfif_segment`).

    Returns:
        ``body``, guaranteed to start with an APP0 segment.
    """
    if len(body) >= 2 and body[0] == 0xFF and body[1] == 0xE0:
        return body
    return jfif_segment + body


def insert_after_app0(body: bytes, new_segments: list[bytes]) -> bytes:
    """
    Insert one or more fully-formed segments immediately after the first
    APP0 (JFIF) segment in a JPEG body, if one is present.

    Used to place a generated ICC profile (APP2) in the same position a
    real camera/gallery JPEG has it: after APP0/JFIF, before DQT/SOF/DHT.
    If no APP0 segment is found (uncommon, but possible depending on the
    encoder that produced the cover image), the new segments are inserted
    at the very start of ``body`` instead.

    Args:
        body: JPEG body bytes as returned by :func:`strip_soi` (i.e.
            everything after SOI, EOI inclusive). Must begin with a
            marker byte (``0xFF``).
        new_segments: Fully-formed APPn segment byte strings to insert.

    Returns:
        A new bytes object with ``new_segments`` spliced in at the
        correct position.

    Raises:
        JpegFormatError: If ``body`` does not start with a valid marker.
    """
    insertion_point = 0
    length = len(body)

    if length >= 2 and body[0] == 0xFF and body[1] == 0xE0:
        seg_len = int.from_bytes(body[2:4], "big")
        insertion_point = 2 + seg_len
    else:
        insertion_point = 0

    to_insert = b"".join(new_segments)
    return body[:insertion_point] + to_insert + body[insertion_point:]


def assemble_motion_photo(
    cover_jpeg_bytes: bytes,
    video_bytes: bytes,
    header_segments: list[bytes],
    post_app0_segments: list[bytes] | None = None,
    ensure_app0_segment: bytes | None = None,
) -> bytes:
    """
    Assemble the final single-file Motion Photo JPEG.

    Produces, in order:

    1. SOI
    2. Each segment in ``header_segments`` (already fully-formed APPn
       segments, e.g. Exif, Xiaomi APP4, XMP), in the order given.
    3. An APP0/JFIF segment: the cover JPEG's own one if present,
       otherwise ``ensure_app0_segment`` if provided.
    4. Each segment in ``post_app0_segments`` (e.g. a generated ICC
       profile APP2), matching the position a real camera/gallery JPEG
       carries its color profile in.
    5. The remainder of the cover JPEG's own segments and scan data,
       verbatim (DQT, SOF, DHT, SOS, scan data), through EOI.
    6. The raw MP4 bytes, appended immediately after EOI with no
       separator, no container, and no modification.

    Args:
        cover_jpeg_bytes: The still-frame cover JPEG produced by ffmpeg.
        video_bytes: The raw MP4 bytes to append.
        header_segments: Fully-formed APPn segment byte strings to insert
            immediately after SOI, in the desired order.
        post_app0_segments: Optional fully-formed APPn segment byte
            strings (e.g. an ICC profile) to insert immediately after the
            cover's own APP0/JFIF segment (or at the very start of the
            cover's segments if it has none).
        ensure_app0_segment: Optional fully-formed APP0 segment to insert
            if the cover JPEG does not already have one of its own (see
            :func:`v2mp.metadata.build_jfif_segment`). Has no
            effect if the cover already starts with an APP0 segment.

    Returns:
        The complete Motion Photo file bytes.

    Raises:
        JpegFormatError: If ``cover_jpeg_bytes`` is not a valid JPEG.
    """
    body = strip_soi(cover_jpeg_bytes)
    if ensure_app0_segment is not None:
        body = ensure_app0_present(body, ensure_app0_segment)
    if post_app0_segments:
        body = insert_after_app0(body, post_app0_segments)
    header = b"".join(header_segments)
    return JPEG_SOI + header + body + video_bytes


def split_motion_photo(data: bytes) -> tuple[bytes, bytes]:
    """
    Split a Motion Photo file back into its JPEG and MP4 components.

    Args:
        data: Full Motion Photo file bytes.

    Returns:
        A ``(jpeg_bytes, mp4_bytes)`` tuple. ``jpeg_bytes`` runs from SOI
        through EOI inclusive; ``mp4_bytes`` is everything after that.

    Raises:
        JpegFormatError: If no valid JPEG header/EOI is found.
    """
    validate_soi(data)
    eoi_index = find_eoi_index(data)
    jpeg_end = eoi_index + len(JPEG_EOI)
    return data[:jpeg_end], data[jpeg_end:]


#: Human-readable names for common JPEG markers, used by :func:`iter_markers`.
_MARKER_NAMES: dict[int, str] = {
    0xD8: "SOI",
    0xD9: "EOI",
    0xDA: "SOS",
    0xDB: "DQT",
    0xC4: "DHT",
    0xC0: "SOF0",
    0xC1: "SOF1",
    0xC2: "SOF2",
    0xC3: "SOF3",
    0xFE: "COM",
    0x01: "TEM",
    **{0xD0 + i: f"RST{i}" for i in range(8)},
    **{0xE0 + i: f"APP{i}" for i in range(16)},
}


@dataclass(frozen=True, slots=True)
class MarkerInfo:
    """
    Describes a single JPEG marker segment, for inspection/reporting.

    Attributes:
        marker: Human-readable marker name (e.g. ``"APP1"``, ``"SOS"``).
        offset: Byte offset of the marker's leading ``0xFF`` within the
            JPEG.
        length: Total length of this marker segment in bytes, including
            the 2-byte ``0xFF`` + marker-code prefix (and, for markers
            with a length field, the field itself and its payload).
    """

    marker: str
    offset: int
    length: int


def iter_markers(data: bytes) -> list[MarkerInfo]:
    """
    Walk a JPEG's marker segments from SOI through EOI.

    Intended for diagnostics/inspection (e.g. the CLI's ``--inspect``
    flag) -- a lightweight, dependency-free equivalent of manually
    hex-dumping a file to see its segment structure.

    Args:
        data: Full JPEG byte stream (may be followed by trailing data,
            e.g. an appended MP4 -- that trailing data is not included
            in the returned markers).

    Returns:
        A list of :class:`MarkerInfo`, in file order, from SOI through EOI.

    Raises:
        JpegFormatError: If the marker structure is malformed.
    """
    validate_soi(data)
    markers = [MarkerInfo("SOI", 0, len(JPEG_SOI))]

    length = len(data)
    pos = len(JPEG_SOI)

    while True:
        if pos + 1 >= length:
            raise JpegFormatError("Reached end of data while parsing JPEG markers")
        if data[pos] != 0xFF:
            raise JpegFormatError(
                f"Expected JPEG marker (0xFF) at offset {pos}, found 0x{data[pos]:02X}"
            )

        marker_byte = data[pos + 1]
        name = _MARKER_NAMES.get(marker_byte, f"0x{marker_byte:02X}")

        if marker_byte == 0xD9:
            markers.append(MarkerInfo(name, pos, 2))
            return markers

        if marker_byte == 0x01 or 0xD0 <= marker_byte <= 0xD7:
            markers.append(MarkerInfo(name, pos, 2))
            pos += 2
            continue

        if pos + 4 > length:
            raise JpegFormatError("Truncated JPEG segment length field")
        seg_len = int.from_bytes(data[pos + 2 : pos + 4], "big")
        if seg_len < 2:
            raise JpegFormatError(f"Invalid JPEG segment length {seg_len} at offset {pos}")

        markers.append(MarkerInfo(name, pos, 2 + seg_len))

        if marker_byte == JPEG_SOS:
            scan_start = pos + 2 + seg_len
            eoi_index = _find_eoi_after_scan(data, scan_start)
            markers.append(MarkerInfo("EOI", eoi_index, 2))
            return markers

        pos = pos + 2 + seg_len


def read_bytes(path: Path) -> bytes:
    """Read and return the full contents of a file as bytes."""
    return path.read_bytes()


def write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path``, creating parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
