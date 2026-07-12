"""
converter.py
============

High-level orchestration of a single MP4 -> Motion Photo (.jpg) conversion.

This module ties together:

* :mod:`v2mp.ffmpeg` -- extracting the still cover frame (and,
  optionally, trimming the source video).
* :mod:`v2mp.metadata` -- building the Exif and Xiaomi APP4 segments.
* :mod:`v2mp.xmp` -- building the Google Motion Photo XMP segment.
* :mod:`v2mp.jpeg` -- splicing everything into a single JPEG and
  appending the MP4 payload.
* :mod:`v2mp.validator` -- verifying the result before reporting
  success.

The public entry point is :func:`convert_video_to_motion_photo`, which
performs one complete, validated conversion and returns a
:class:`ConversionResult`.
"""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import ffmpeg as ffmpeg_module
from .config import ConversionConfig
from .jpeg import assemble_motion_photo, extract_orientation, read_bytes, write_bytes
from .metadata import (
    build_exif_segment,
    build_icc_profile_segment,
    build_jfif_segment,
    build_xiaomi_segment,
    build_xmp_segment,
)
from .utils import default_output_path, ensure_unique_path, get_logger, human_size
from .validator import ValidationReport, validate_motion_photo_file
from .xmp import build_motion_photo_xmp

logger = get_logger()


def _read_jpeg_dimensions(jpeg_bytes: bytes) -> tuple[int | None, int | None]:
    """
    Best-effort extraction of a JPEG's pixel width/height via Pillow.

    Args:
        jpeg_bytes: JPEG image bytes.

    Returns:
        A ``(width, height)`` tuple, or ``(None, None)`` if the image
        could not be read (never fatal -- Exif dimension tags are
        optional extras, not required for a valid Motion Photo).
    """
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(jpeg_bytes)) as img:
            return img.width, img.height
    except Exception:  # noqa: BLE001 - best effort only, never fatal
        return None, None


class ConversionError(RuntimeError):
    """Raised when a video cannot be converted to a Motion Photo."""


@dataclass
class ConversionResult:
    """
    Outcome of a single successful conversion.

    Attributes:
        input_path: The source video that was converted.
        output_path: The final Motion Photo ``.jpg`` file written.
        cover_path: Path to the (possibly kept) intermediate cover JPEG,
            or ``None`` if it was not retained.
        video_size_bytes: Size of the MP4 payload appended to the output.
        total_size_bytes: Total size of the output file.
        validation: The validation report produced for the output file.
    """

    input_path: Path
    output_path: Path
    cover_path: Path | None
    video_size_bytes: int
    total_size_bytes: int
    validation: ValidationReport


def convert_video_to_motion_photo(
    input_path: Path,
    output_path: Path | None = None,
    config: ConversionConfig | None = None,
) -> ConversionResult:
    """
    Convert a single MP4 video into a Google Motion Photo ``.jpg`` file.

    Args:
        input_path: Path to the source video (MP4/MOV/M4V).
        output_path: Desired output ``.jpg`` path. If ``None``, defaults
            to the input's filename with a ``.jpg`` extension in the same
            directory (or ``config.output_dir`` if set).
        config: Conversion options. Defaults to
            ``ConversionConfig()`` if omitted.

    Returns:
        A :class:`ConversionResult` describing the completed, validated
        conversion.

    Raises:
        ConversionError: If the input is missing, ffmpeg fails, the
            output already exists and overwrite is disabled, or the
            resulting file fails validation.
    """
    cfg = config if config is not None else ConversionConfig()

    input_path = Path(input_path)
    if not input_path.is_file():
        raise ConversionError(f"Input video not found: {input_path}")

    resolved_output = (
        Path(output_path)
        if output_path is not None
        else default_output_path(input_path, cfg.output_dir)
    )

    if resolved_output.exists() and not cfg.overwrite:
        resolved_output = ensure_unique_path(resolved_output)
        logger.warning(
            "Output exists and --overwrite not set; writing to %s instead",
            resolved_output,
        )

    with tempfile.TemporaryDirectory(prefix="v2mp_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        working_video_path = input_path

        try:
            if cfg.trim_start is not None or cfg.trim_end is not None:
                trimmed_path = tmp_dir / f"trimmed_{uuid.uuid4().hex}.mp4"
                logger.debug("Trimming video before embedding: %s", input_path)
                working_video_path = ffmpeg_module.trim_video(
                    video_path=input_path,
                    output_path=trimmed_path,
                    start=cfg.trim_start,
                    end=cfg.trim_end,
                    ffmpeg_binary=cfg.ffmpeg_binary,
                )

            if cfg.strip_audio:
                no_audio_path = tmp_dir / f"noaudio_{uuid.uuid4().hex}.mp4"
                logger.debug("Stripping audio track before embedding: %s", working_video_path)
                working_video_path = ffmpeg_module.remux_strip_audio(
                    video_path=working_video_path,
                    output_path=no_audio_path,
                    ffmpeg_binary=cfg.ffmpeg_binary,
                )

            cover_target_dir = resolved_output.parent if cfg.keep_cover else tmp_dir
            cover_path = cover_target_dir / f"{resolved_output.stem}_cover.jpg"

            logger.info("Extracting cover frame from %s", input_path)
            ffmpeg_module.extract_cover_frame(
                video_path=working_video_path,
                output_path=cover_path,
                timestamp=cfg.cover_timestamp,
                frame_index=cfg.cover_frame,
                auto=cfg.cover_auto,
                ffmpeg_binary=cfg.ffmpeg_binary,
            )
        except ffmpeg_module.FFmpegError as exc:
            raise ConversionError(str(exc)) from exc

        cover_bytes = read_bytes(cover_path)
        video_bytes = read_bytes(working_video_path)
        video_size = len(video_bytes)

        logger.debug(
            "Cover frame: %s, video payload: %s",
            human_size(len(cover_bytes)),
            human_size(video_size),
        )

        orientation = extract_orientation(cover_bytes)
        cover_width, cover_height = _read_jpeg_dimensions(cover_bytes)
        header_segments = [
            build_exif_segment(
                orientation=orientation,
                image_width=cover_width,
                image_height=cover_height,
            )
        ]

        if cfg.enable_xiaomi:
            header_segments.append(build_xiaomi_segment())

        xmp_packet = build_motion_photo_xmp(
            video_length_bytes=video_size,
            presentation_timestamp_us=cfg.motion_photo_presentation_timestamp_us,
        )
        header_segments.append(build_xmp_segment(xmp_packet))

        post_app0_segments: list[bytes] = []
        if cfg.embed_icc_profile:
            try:
                post_app0_segments = build_icc_profile_segment()
            except RuntimeError as exc:
                logger.warning("Skipping ICC profile embedding: %s", exc)

        logger.debug("Assembling final Motion Photo JPEG")
        output_bytes = assemble_motion_photo(
            cover_jpeg_bytes=cover_bytes,
            video_bytes=video_bytes,
            header_segments=header_segments,
            post_app0_segments=post_app0_segments,
            ensure_app0_segment=build_jfif_segment(),
        )

        write_bytes(resolved_output, output_bytes)

        if not cfg.keep_cover:
            try:
                cover_path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - best effort cleanup
                logger.debug("Could not remove temporary cover file %s", cover_path)

    logger.info(
        "Wrote Motion Photo: %s (%s)",
        resolved_output,
        human_size(resolved_output.stat().st_size),
    )

    report = validate_motion_photo_file(resolved_output)
    if not report.is_valid:
        logger.error("Validation failed for %s:\n%s", resolved_output, report.summary())
        raise ConversionError(
            f"Generated file failed validation: {resolved_output}\n{report.summary()}"
        )

    logger.debug("Validation passed for %s", resolved_output)

    return ConversionResult(
        input_path=input_path,
        output_path=resolved_output,
        cover_path=cover_path if cfg.keep_cover else None,
        video_size_bytes=video_size,
        total_size_bytes=resolved_output.stat().st_size,
        validation=report,
    )


def preview_cover_frame(
    input_path: Path,
    output_path: Path,
    config: ConversionConfig | None = None,
) -> Path:
    """
    Extract and save only the would-be cover frame, without building a
    full Motion Photo.

    Useful for quickly checking which frame ``cover_timestamp``/
    ``cover_frame``/``cover_auto`` would select before committing to a
    full conversion (which also has to read/hash/embed the entire video).

    Args:
        input_path: Path to the source video.
        output_path: Path to write the extracted JPEG frame to.
        config: Conversion options; only the cover-selection fields
            (``cover_timestamp``, ``cover_frame``, ``cover_auto``,
            ``ffmpeg_binary``) are used. Defaults to ``ConversionConfig()``.

    Returns:
        The ``output_path`` that was written.

    Raises:
        ConversionError: If the input is missing or ffmpeg fails.
    """
    cfg = config if config is not None else ConversionConfig()

    input_path = Path(input_path)
    output_path = Path(output_path)
    if not input_path.is_file():
        raise ConversionError(f"Input video not found: {input_path}")

    try:
        ffmpeg_module.extract_cover_frame(
            video_path=input_path,
            output_path=output_path,
            timestamp=cfg.cover_timestamp,
            frame_index=cfg.cover_frame,
            auto=cfg.cover_auto,
            ffmpeg_binary=cfg.ffmpeg_binary,
        )
    except ffmpeg_module.FFmpegError as exc:
        raise ConversionError(str(exc)) from exc

    logger.info("Wrote cover preview: %s", output_path)
    return output_path


def extract_video_from_motion_photo(input_path: Path, output_path: Path) -> Path:
    """
    Extract the embedded MP4 back out of an existing Motion Photo file.

    Pure Python, no ffmpeg involved -- this is the exact reverse of the
    "append after EOI" step in :func:`convert_video_to_motion_photo`, and
    the extracted bytes are byte-for-byte identical to whatever was
    originally embedded (see :func:`v2mp.jpeg.split_motion_photo`).

    Args:
        input_path: Path to an existing Motion Photo ``.jpg`` file.
        output_path: Path to write the extracted ``.mp4`` to.

    Returns:
        The ``output_path`` that was written.

    Raises:
        ConversionError: If the input is missing, is not a valid JPEG, or
            has no video payload appended after its EOI marker.
    """
    from .jpeg import JpegFormatError, split_motion_photo

    input_path = Path(input_path)
    output_path = Path(output_path)
    if not input_path.is_file():
        raise ConversionError(f"Input file not found: {input_path}")

    data = read_bytes(input_path)
    try:
        _, video_bytes = split_motion_photo(data)
    except JpegFormatError as exc:
        raise ConversionError(f"{input_path} is not a valid JPEG: {exc}") from exc

    if not video_bytes:
        raise ConversionError(f"{input_path} has no video payload appended after its EOI marker")

    write_bytes(output_path, video_bytes)
    logger.info("Extracted %s of video to %s", human_size(len(video_bytes)), output_path)
    return output_path
