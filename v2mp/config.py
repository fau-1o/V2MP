"""
config.py
=========

Central configuration for the Motion Photo converter.

This module defines:

* Immutable constants describing JPEG/MP4 markers and namespaces used
  throughout the project.
* The :class:`ConversionConfig` dataclass, which carries every user-facing
  option (CLI flags) through the pipeline in a single, typed object.

Keeping all "magic numbers" and shared constants in one place avoids
duplication and makes the on-disk binary format easy to audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# JPEG marker constants
# --------------------------------------------------------------------------- #

#: Start Of Image marker.
JPEG_SOI: bytes = b"\xff\xd8"

#: End Of Image marker.
JPEG_EOI: bytes = b"\xff\xd9"

#: APP0 marker byte (JFIF).
JPEG_APP0: int = 0xE0

#: APP1 marker byte (Exif / XMP both live here).
JPEG_APP1: int = 0xE1

#: APP2 marker byte (ICC profile).
JPEG_APP2: int = 0xE2

#: APP4 marker byte (Xiaomi custom segment in reverse-engineered samples).
JPEG_APP4: int = 0xE4

#: Start Of Scan marker -- once this is hit, JPEG segment parsing must stop,
#: since everything after is entropy-coded scan data (which may itself
#: legally contain byte sequences that look like markers, e.g. 0xFF 0x00).
JPEG_SOS: int = 0xDA

#: Exif APP1 identifier (immediately follows the 2-byte segment length).
EXIF_IDENTIFIER: bytes = b"Exif\x00\x00"

#: Standard Adobe XMP APP1 identifier.
XMP_IDENTIFIER: bytes = b"http://ns.adobe.com/xap/1.0/\x00"

#: Xiaomi custom APP4 identifier, as observed in reverse-engineered samples.
XIAOMI_IDENTIFIER: bytes = b"XIAOMI_CUSTOMIZE\x00"

#: Maximum payload size for a single JPEG segment. The 2-byte length field
#: covers the length bytes themselves, leaving 65533 bytes of usable
#: identifier + payload space.
MAX_SEGMENT_PAYLOAD: int = 0xFFFF - 2

# --------------------------------------------------------------------------- #
# MP4 constants
# --------------------------------------------------------------------------- #

#: Byte offset at which the 'ftyp' box type appears in a well-formed MP4.
MP4_FTYP_OFFSET: int = 4

#: Expected box type bytes for the first box of a standard MP4 container.
MP4_FTYP_BOX: bytes = b"ftyp"

# --------------------------------------------------------------------------- #
# XMP / Google Motion Photo namespaces
# --------------------------------------------------------------------------- #

NS_GCAMERA: str = "http://ns.google.com/photos/1.0/camera/"
NS_CONTAINER: str = "http://ns.google.com/photos/1.0/container/"
NS_CONTAINER_ITEM: str = "http://ns.google.com/photos/1.0/container/item/"
NS_RDF: str = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
NS_X: str = "adobe:ns:meta/"

#: Default Xiaomi APP4 payload. Confirmed byte-for-byte against a full
#: (not truncated) hex dump of a genuine sample: exactly these 3 keys,
#: in this order -- no "88b2" key (an earlier version of this constant
#: included one, which was never actually observed in the real file;
#: the previous partial dump had simply cut off before revealing the
#: real key set).
DEFAULT_XIAOMI_PAYLOAD: dict[str, str] = {
    "9a01": "1",
    "8897": "1",
    "version": "32",
}

#: Recognized video file extensions eligible for conversion in batch/folder mode.
SUPPORTED_VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mov", ".m4v"})


@dataclass(slots=True)
class ConversionConfig:
    """
    Holds all user-configurable options for a Motion Photo conversion run.

    Attributes:
        overwrite: If True, silently overwrite existing output files.
            If False (default) and the output already exists, the
            conversion is skipped with a warning.
        verbose: If True, enable DEBUG-level logging output.
        enable_xiaomi: If True (default), embed the Xiaomi APP4
            ``XIAOMI_CUSTOMIZE`` segment. Controlled by ``--disable-xiaomi``.
        embed_icc_profile: If True (default), embed a standard sRGB ICC
            profile as an APP2 segment (positioned after APP0/JFIF,
            matching real camera/gallery-generated Motion Photos). Many
            ffmpeg-extracted video frames carry no color profile at all;
            this closes that structural gap. Controlled by
            ``--disable-icc``.
        keep_cover: If True, keep the intermediate extracted cover JPEG
            on disk next to the output, instead of deleting it after use.
        recursive: If True, recurse into subdirectories when the input
            is a directory.
        batch: If True, the positional input path is treated as a
            directory to be batch-converted (equivalent to passing a
            directory path directly; kept for CLI-compatibility with
            ``--batch``).
        cover_timestamp: Timestamp (ffmpeg ``-ss`` compatible string) at
            which to extract the still cover frame from the source video.
            Ignored if ``cover_frame`` is set or ``cover_auto`` is True.
        cover_frame: Optional 0-indexed exact frame number to use as the
            cover image instead of a timestamp. Takes precedence over
            ``cover_timestamp``. Controlled by ``--cover-frame``.
        cover_auto: If True, automatically pick a representative,
            non-blurry frame instead of using ``cover_timestamp``/
            ``cover_frame``. Takes precedence over both. Controlled by
            ``--cover-auto``.
        strip_audio: If True, remove the audio track from the embedded
            video (the video stream itself is copied verbatim; only the
            container is remuxed to drop audio). Controlled by
            ``--no-audio``.
        trim_start: Optional start time (ffmpeg ``-ss`` compatible string)
            to trim the video before embedding. ``None`` disables trimming.
        trim_end: Optional end time (ffmpeg ``-to`` compatible string) to
            trim the video before embedding. ``None`` disables trimming.
        jpeg_quality: JPEG quality (1-100) used only if the cover frame
            must be re-encoded (it normally is not).
        motion_photo_presentation_timestamp_us: Value written to
            ``GCamera:MotionPhotoPresentationTimestampUs``. Defaults to 0,
            meaning "use the extracted cover frame as the still image".
        ffmpeg_binary: Path/name of the ffmpeg executable to invoke.
        output_dir: Optional directory to place outputs in, overriding
            the default of "next to the input file".
        jobs: Number of files to convert concurrently in batch/folder
            mode. ``1`` (default) processes sequentially. Controlled by
            ``--jobs``.
        dry_run: If True, batch/folder mode only lists the files that
            would be converted without actually converting them.
            Controlled by ``--dry-run``.
    """

    overwrite: bool = False
    verbose: bool = False
    enable_xiaomi: bool = True
    embed_icc_profile: bool = True
    keep_cover: bool = False
    recursive: bool = False
    batch: bool = False
    cover_timestamp: str = "0.0"
    cover_frame: int | None = None
    cover_auto: bool = False
    strip_audio: bool = False
    trim_start: str | None = None
    trim_end: str | None = None
    jpeg_quality: int = 95
    motion_photo_presentation_timestamp_us: int = 0
    ffmpeg_binary: str = "ffmpeg"
    output_dir: Path | None = None
    jobs: int = 1
    dry_run: bool = False
    extra_video_extensions: frozenset[str] = field(default_factory=frozenset)

    def video_extensions(self) -> frozenset[str]:
        """Return the full set of video extensions considered convertible."""
        return SUPPORTED_VIDEO_EXTENSIONS | self.extra_video_extensions

    def __post_init__(self) -> None:
        """Validate cross-field constraints not expressible via type hints alone."""
        if self.jobs < 1:
            raise ValueError("jobs must be >= 1")
        if self.cover_frame is not None and self.cover_frame < 0:
            raise ValueError("cover_frame must be >= 0")
