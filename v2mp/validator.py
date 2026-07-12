"""
validator.py
============

Post-conversion validation of Motion Photo output files.

These checks are deliberately independent of the code path that builds
the file (:mod:`v2mp.converter`, :mod:`v2mp.jpeg`), so that
a bug in construction is likely to be caught by validation rather than
silently producing a broken (but plausible-looking) file.

:class:`ValidationReport` aggregates every individual check so callers
(CLI, tests) can inspect exactly what passed/failed rather than only
getting a single boolean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import MP4_FTYP_BOX, MP4_FTYP_OFFSET
from .jpeg import JpegFormatError, find_eoi_index, split_motion_photo, validate_soi
from .utils import human_size
from .xmp import (
    has_container_item_element,
    has_motion_photo_flag,
    parse_video_length_from_xmp,
    required_namespaces_present,
)


@dataclass
class ValidationReport:
    """
    Aggregated result of running all validation checks on a Motion Photo file.

    Attributes:
        path: The file that was validated.
        checks: Mapping of check name to pass/fail boolean.
        messages: Mapping of check name to a human-readable detail string,
            populated especially for failed checks.
    """

    path: Path
    checks: dict[str, bool] = field(default_factory=dict)
    messages: dict[str, str] = field(default_factory=dict)

    def add(self, name: str, passed: bool, message: str = "") -> None:
        """Record the result of a single named check."""
        self.checks[name] = passed
        self.messages[name] = message

    @property
    def is_valid(self) -> bool:
        """True only if every recorded check passed."""
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        """Return a multi-line human-readable summary of all checks."""
        lines = [f"Validation report for {self.path}:"]
        for name, passed in self.checks.items():
            status = "PASS" if passed else "FAIL"
            detail = f" - {self.messages[name]}" if self.messages.get(name) else ""
            lines.append(f"  [{status}] {name}{detail}")
        lines.append(f"Overall: {'VALID' if self.is_valid else 'INVALID'}")
        return "\n".join(lines)


def validate_jpeg_starts_with_soi(data: bytes) -> tuple[bool, str]:
    """Check that ``data`` begins with the JPEG SOI marker (FFD8)."""
    try:
        validate_soi(data)
        return True, ""
    except JpegFormatError as exc:
        return False, str(exc)


def validate_jpeg_contains_eoi(data: bytes) -> tuple[bool, str]:
    """Check that ``data`` contains a JPEG EOI marker (FFD9)."""
    try:
        find_eoi_index(data)
        return True, ""
    except JpegFormatError as exc:
        return False, str(exc)


def validate_mp4_starts_with_ftyp(mp4_bytes: bytes) -> tuple[bool, str]:
    """Check that ``mp4_bytes`` begins with a standard ``ftyp`` box."""
    if len(mp4_bytes) < MP4_FTYP_OFFSET + 4:
        return False, "MP4 data too short to contain an ftyp box"
    box_type = mp4_bytes[MP4_FTYP_OFFSET : MP4_FTYP_OFFSET + 4]
    if box_type != MP4_FTYP_BOX:
        return False, f"Expected 'ftyp' box at offset 4, found {box_type!r}"
    return True, ""


def validate_output_size(jpeg_bytes: bytes, mp4_bytes: bytes, total_size: int) -> tuple[bool, str]:
    """Check that the JPEG + MP4 component sizes sum to the total file size."""
    expected = len(jpeg_bytes) + len(mp4_bytes)
    if expected != total_size:
        return False, f"Expected total size {expected}, file is {total_size} bytes"
    return True, ""


def validate_video_length_field(xmp_bytes: bytes, actual_video_length: int) -> tuple[bool, str]:
    """Check that the XMP's declared ``Item:Length`` matches the real MP4 size."""
    declared = parse_video_length_from_xmp(xmp_bytes)
    if declared is None:
        return False, "Could not find Item:Length for the MotionPhoto item in XMP"
    if declared != actual_video_length:
        return (
            False,
            f"XMP declares length {declared}, actual appended MP4 is {actual_video_length} bytes",
        )
    return True, ""


def validate_xmp_namespaces(xmp_bytes: bytes) -> tuple[bool, str]:
    """Check that all three required Google namespaces are present in the XMP."""
    if required_namespaces_present(xmp_bytes):
        return True, ""
    return False, "One or more required GCamera/Container/Item namespaces missing"


def validate_motion_photo_flag(xmp_bytes: bytes) -> tuple[bool, str]:
    """Check that ``GCamera:MotionPhoto="1"`` is present in the XMP."""
    if has_motion_photo_flag(xmp_bytes):
        return True, ""
    return False, 'GCamera:MotionPhoto="1" flag not found in XMP'


def validate_container_item_structure(xmp_bytes: bytes) -> tuple[bool, str]:
    """
    Check that container entries are proper ``<Container:Item>`` elements.

    Gallery apps (notably Xiaomi Gallery) that strictly walk the XMP
    container looking for ``Container:Item`` elements will fail to
    recognize a file where this element was instead built as
    ``<Item:Item>`` -- a subtle but easy mistake since both are
    XML-well-formed and pass looser validators (e.g. ExifTool).
    """
    if has_container_item_element(xmp_bytes):
        return True, ""
    return False, "Container entries are not proper <Container:Item> elements"


def extract_xmp_bytes(jpeg_bytes: bytes) -> bytes | None:
    """
    Extract the raw XMP APP1 segment payload (identifier stripped) from a JPEG.

    Args:
        jpeg_bytes: Full JPEG byte stream (SOI through EOI).

    Returns:
        The XMP packet bytes (starting at ``<?xpacket`` or ``<x:xmpmeta``),
        or ``None`` if no XMP APP1 segment is found.
    """
    from .config import JPEG_SOS, XMP_IDENTIFIER

    pos = 2  # skip SOI
    length = len(jpeg_bytes)
    while pos + 4 <= length:
        if jpeg_bytes[pos] != 0xFF:
            # Not aligned on a marker boundary; bail out defensively.
            break
        marker = jpeg_bytes[pos + 1]
        if marker == JPEG_SOS or marker == 0xD9:
            break
        if marker in (0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0x01):
            pos += 2
            continue
        seg_len = int.from_bytes(jpeg_bytes[pos + 2 : pos + 4], "big")
        seg_start = pos + 4
        seg_end = pos + 2 + seg_len
        if marker == 0xE1:
            payload = jpeg_bytes[seg_start:seg_end]
            if payload.startswith(XMP_IDENTIFIER):
                return payload[len(XMP_IDENTIFIER) :]
        pos = seg_end
    return None


def validate_motion_photo_file(path: Path) -> ValidationReport:
    """
    Run the full validation suite against a completed Motion Photo file.

    Args:
        path: Path to the ``.jpg`` Motion Photo file on disk.

    Returns:
        A populated :class:`ValidationReport`.
    """
    report = ValidationReport(path=path)
    data = path.read_bytes()

    passed, msg = validate_jpeg_starts_with_soi(data)
    report.add("jpeg_starts_with_soi", passed, msg)
    if not passed:
        return report

    passed, msg = validate_jpeg_contains_eoi(data)
    report.add("jpeg_contains_eoi", passed, msg)
    if not passed:
        return report

    jpeg_bytes, mp4_bytes = split_motion_photo(data)

    passed, msg = validate_output_size(jpeg_bytes, mp4_bytes, len(data))
    report.add("output_size_matches_components", passed, msg)

    mp4_present = len(mp4_bytes) > 0
    report.add(
        "mp4_payload_present", mp4_present, "" if mp4_present else "No trailing MP4 data found"
    )

    if mp4_present:
        passed, msg = validate_mp4_starts_with_ftyp(mp4_bytes)
        report.add("mp4_starts_with_ftyp", passed, msg)

    xmp_bytes = extract_xmp_bytes(jpeg_bytes)
    xmp_present = xmp_bytes is not None
    report.add(
        "xmp_segment_present", xmp_present, "" if xmp_present else "No XMP APP1 segment found"
    )

    if xmp_present and xmp_bytes is not None:
        passed, msg = validate_xmp_namespaces(xmp_bytes)
        report.add("xmp_namespaces_correct", passed, msg)

        passed, msg = validate_motion_photo_flag(xmp_bytes)
        report.add("motion_photo_flag_set", passed, msg)

        passed, msg = validate_container_item_structure(xmp_bytes)
        report.add("container_item_structure_correct", passed, msg)

        if mp4_present:
            passed, msg = validate_video_length_field(xmp_bytes, len(mp4_bytes))
            report.add("video_length_field_matches", passed, msg)

    return report


def format_inspection_report(path: Path) -> str:
    """
    Build a human-readable segment-by-segment + validation report for a
    JPEG (Motion Photo or otherwise), for the CLI's ``--inspect`` flag.

    This is a lightweight, dependency-free equivalent of manually
    hex-dumping and diffing a file (as was done during Xiaomi-compatibility
    reverse-engineering) -- useful for quickly checking whether a given
    output "looks right" without leaving the tool.

    Args:
        path: Path to the JPEG file to inspect.

    Returns:
        A multi-line report string listing every marker segment (name,
        offset, length), any trailing data after EOI (typically an
        appended video), and the full validation report.
    """
    from .jpeg import JpegFormatError, iter_markers, split_motion_photo

    data = path.read_bytes()
    lines = [f"Inspecting {path} ({human_size(len(data))})", "", "Marker segments:"]

    try:
        markers = iter_markers(data)
    except JpegFormatError as exc:
        lines.append(f"  <failed to parse markers: {exc}>")
        return "\n".join(lines)

    for m in markers:
        lines.append(f"  {m.marker:<6} offset={m.offset:<10} length={m.length}")

    try:
        _, trailing = split_motion_photo(data)
        if trailing:
            preview = trailing[:12].hex(" ")
            lines.append("")
            lines.append(f"Trailing data after EOI: {human_size(len(trailing))}")
            lines.append(f"  first bytes: {preview}")
        else:
            lines.append("")
            lines.append("Trailing data after EOI: none")
    except JpegFormatError:
        pass

    lines.append("")
    report = validate_motion_photo_file(path)
    lines.append(report.summary())

    return "\n".join(lines)
