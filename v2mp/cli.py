"""
cli.py
======

Command-line interface for the Motion Photo converter.

Supported invocation forms::

    # Conversion
    python main.py input.mp4
    python main.py input.mp4 output.jpg
    python main.py folder/
    python main.py folder/ --recursive
    python main.py --batch folder/
    python main.py folder/ --jobs 4
    python main.py folder/ --dry-run

    # Cover frame selection
    python main.py input.mp4 --cover-timestamp 2.5
    python main.py input.mp4 --cover-frame 42
    python main.py input.mp4 --cover-auto

    # Audio control
    python main.py input.mp4 --no-audio

    # Utilities
    python main.py input.mp4 --preview-cover preview.jpg
    python main.py motion_photo.jpg --extract-video out.mp4
    python main.py motion_photo.jpg --inspect
"""

from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import ConversionConfig
from .converter import (
    ConversionError,
    convert_video_to_motion_photo,
    extract_video_from_motion_photo,
    preview_cover_frame,
)
from .utils import ProgressBar, configure_logging, iter_video_files
from .validator import format_inspection_report


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="v2mp",
        description=(
            "Convert MP4 videos into Google Motion Photo compatible JPEG files "
            "(a single .jpg containing an embedded MP4, readable by Google "
            "Photos, Xiaomi Gallery, and compatible Android gallery apps)."
        ),
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Input video file, a directory (folder/batch mode), or an "
        "existing Motion Photo (for --extract-video/--inspect).",
    )
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=None,
        help="Output .jpg path (single-file mode only). Ignored for directories.",
    )

    conversion_group = parser.add_argument_group("conversion")
    conversion_group.add_argument(
        "--batch",
        action="store_true",
        help="Treat 'input' as a directory and convert every video file within it.",
    )
    conversion_group.add_argument(
        "--recursive",
        action="store_true",
        help="When converting a directory, also search subdirectories.",
    )
    conversion_group.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output files instead of generating a unique name.",
    )
    conversion_group.add_argument(
        "--jobs",
        type=int,
        default=1,
        metavar="N",
        help="Convert up to N files concurrently in folder/batch mode (default: 1).",
    )
    conversion_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Folder/batch mode: list the files that would be converted, without converting them.",
    )
    conversion_group.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Directory to write outputs into (folder/batch mode), overriding "
            "the default location alongside each input."
        ),
    )

    cover_group = parser.add_argument_group("cover frame selection")
    cover_selection = cover_group.add_mutually_exclusive_group()
    cover_selection.add_argument(
        "--cover-timestamp",
        type=str,
        default="0.0",
        help="ffmpeg-compatible timestamp to extract the still cover frame from (default: 0.0).",
    )
    cover_selection.add_argument(
        "--cover-frame",
        type=int,
        default=None,
        metavar="N",
        help="Use exact 0-indexed frame N as the cover image instead of a timestamp.",
    )
    cover_selection.add_argument(
        "--cover-auto",
        action="store_true",
        help="Automatically pick a representative, non-blurry frame as the cover image.",
    )

    audio_group = parser.add_argument_group("audio")
    audio_group.add_argument(
        "--no-audio",
        action="store_true",
        help="Strip the audio track from the embedded video (video stream is kept untouched).",
    )

    video_group = parser.add_argument_group("video trimming")
    video_group.add_argument(
        "--trim-start",
        type=str,
        default=None,
        help="Optional ffmpeg-compatible start time to trim the embedded video to.",
    )
    video_group.add_argument(
        "--trim-end",
        type=str,
        default=None,
        help="Optional ffmpeg-compatible end time to trim the embedded video to.",
    )

    metadata_group = parser.add_argument_group("metadata")
    metadata_group.add_argument(
        "--disable-xiaomi",
        action="store_true",
        help="Do not embed the Xiaomi XIAOMI_CUSTOMIZE APP4 segment.",
    )
    metadata_group.add_argument(
        "--disable-icc",
        action="store_true",
        help="Do not embed a generated sRGB ICC profile (APP2) in the output.",
    )
    metadata_group.add_argument(
        "--keep-cover",
        action="store_true",
        help="Keep the intermediate extracted cover JPEG on disk after conversion.",
    )

    utility_group = parser.add_argument_group("utilities (bypass conversion)")
    utility_group.add_argument(
        "--preview-cover",
        type=Path,
        default=None,
        metavar="PATH",
        help="Extract and save only the cover frame to PATH, without building a full Motion Photo.",
    )
    utility_group.add_argument(
        "--extract-video",
        type=Path,
        default=None,
        metavar="PATH",
        help="Treat 'input' as an existing Motion Photo and extract its embedded video to PATH.",
    )
    utility_group.add_argument(
        "--inspect",
        action="store_true",
        help="Treat 'input' as a JPEG and print a segment-by-segment + validation report.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG-level) logging.",
    )
    parser.add_argument(
        "--ffmpeg-binary",
        type=str,
        default="ffmpeg",
        help="Name or path of the ffmpeg executable to use (default: 'ffmpeg').",
    )
    return parser


def _config_from_args(args: argparse.Namespace) -> ConversionConfig:
    """Build a :class:`ConversionConfig` from parsed CLI arguments."""
    return ConversionConfig(
        overwrite=args.overwrite,
        verbose=args.verbose,
        enable_xiaomi=not args.disable_xiaomi,
        embed_icc_profile=not args.disable_icc,
        keep_cover=args.keep_cover,
        recursive=args.recursive,
        batch=args.batch,
        cover_timestamp=args.cover_timestamp,
        cover_frame=args.cover_frame,
        cover_auto=args.cover_auto,
        strip_audio=args.no_audio,
        trim_start=args.trim_start,
        trim_end=args.trim_end,
        ffmpeg_binary=args.ffmpeg_binary,
        output_dir=args.output_dir,
        jobs=max(args.jobs, 1),
        dry_run=args.dry_run,
    )


def run(argv: list[str] | None = None) -> int:
    """
    Parse CLI arguments and execute the requested conversion or utility action.

    Args:
        argv: Argument list (excluding program name). Defaults to
            ``sys.argv[1:]`` when ``None``.

    Returns:
        Process exit code: ``0`` on success, ``1`` if any operation failed.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logger = configure_logging(verbose=args.verbose)

    # Utility actions bypass conversion entirely.
    if args.inspect:
        return _run_inspect(args.input, logger)
    if args.extract_video is not None:
        return _run_extract_video(args.input, args.extract_video, logger)

    cfg = _config_from_args(args)

    if args.preview_cover is not None:
        return _run_preview_cover(args.input, args.preview_cover, cfg, logger)

    is_directory_mode = args.batch or args.input.is_dir()

    if is_directory_mode:
        return _run_directory(args.input, cfg, logger)

    if not args.input.is_file():
        logger.error("Input file not found: %s", args.input)
        return 1

    return _run_single(args.input, args.output, cfg, logger)


def _run_single(
    input_path: Path,
    output_path: Path | None,
    cfg: ConversionConfig,
    logger: logging.Logger,
) -> int:
    """Run a single-file conversion and report the result. Returns exit code."""
    try:
        result = convert_video_to_motion_photo(input_path, output_path, cfg)
    except ConversionError as exc:
        logger.error("Conversion failed for %s: %s", input_path, exc)
        return 1

    logger.info("Success: %s -> %s", result.input_path, result.output_path)
    return 0


def _run_preview_cover(
    input_path: Path,
    preview_path: Path,
    cfg: ConversionConfig,
    logger: logging.Logger,
) -> int:
    """Extract just the cover frame and report the result. Returns exit code."""
    try:
        preview_cover_frame(input_path, preview_path, cfg)
    except ConversionError as exc:
        logger.error("Cover preview failed for %s: %s", input_path, exc)
        return 1

    logger.info("Cover preview saved: %s", preview_path)
    return 0


def _run_extract_video(input_path: Path, output_path: Path, logger: logging.Logger) -> int:
    """Extract an existing Motion Photo's embedded video and report. Returns exit code."""
    try:
        extract_video_from_motion_photo(input_path, output_path)
    except ConversionError as exc:
        logger.error("Video extraction failed for %s: %s", input_path, exc)
        return 1

    logger.info("Extracted video: %s", output_path)
    return 0


def _run_inspect(input_path: Path, logger: logging.Logger) -> int:
    """Print a segment/validation report for an existing JPEG. Returns exit code."""
    if not input_path.is_file():
        logger.error("Input file not found: %s", input_path)
        return 1

    print(format_inspection_report(input_path))
    return 0


def _run_directory(directory: Path, cfg: ConversionConfig, logger: logging.Logger) -> int:
    """Run a batch conversion over all videos in a directory. Returns exit code."""
    if not directory.is_dir():
        logger.error("Not a directory: %s", directory)
        return 1

    videos = list(iter_video_files(directory, cfg.video_extensions(), recursive=cfg.recursive))
    if not videos:
        logger.warning("No video files found in %s", directory)
        return 0

    if cfg.dry_run:
        logger.info("Dry run: %d video(s) would be converted in %s", len(videos), directory)
        for video_path in videos:
            print(video_path)
        return 0

    logger.info("Found %d video(s) to convert in %s", len(videos), directory)

    if cfg.jobs <= 1:
        failures = _convert_sequential(videos, cfg, logger)
    else:
        failures = _convert_parallel(videos, cfg, logger)

    succeeded = len(videos) - len(failures)
    logger.info("Batch complete: %d succeeded, %d failed", succeeded, len(failures))

    if failures:
        logger.error("The following files failed to convert:")
        for path, message in failures:
            logger.error("  %s: %s", path, message)
        return 1

    return 0


def _convert_sequential(
    videos: list[Path],
    cfg: ConversionConfig,
    logger: logging.Logger,
) -> list[tuple[Path, str]]:
    """Convert files one at a time with a progress bar. Returns list of (path, error)."""
    progress = ProgressBar(total=len(videos), label="Converting")
    failures: list[tuple[Path, str]] = []

    for video_path in videos:
        try:
            result = convert_video_to_motion_photo(video_path, None, cfg)
            progress.update(1, suffix=f"OK: {result.output_path.name}")
        except ConversionError as exc:
            failures.append((video_path, str(exc)))
            progress.update(1, suffix=f"FAILED: {video_path.name}")
            logger.error("Failed to convert %s: %s", video_path, exc)

    progress.close()
    return failures


def _convert_parallel(
    videos: list[Path],
    cfg: ConversionConfig,
    logger: logging.Logger,
) -> list[tuple[Path, str]]:
    """
    Convert files concurrently using a thread pool.

    A thread pool (rather than processes) is sufficient here because each
    conversion spends almost all of its time waiting on ffmpeg subprocess
    calls, which release the GIL while running.

    Returns:
        List of ``(path, error_message)`` for any files that failed.
    """
    progress = ProgressBar(total=len(videos), label="Converting")
    failures: list[tuple[Path, str]] = []

    with ThreadPoolExecutor(max_workers=cfg.jobs) as executor:
        future_to_path = {
            executor.submit(convert_video_to_motion_photo, video_path, None, cfg): video_path
            for video_path in videos
        }
        for future in as_completed(future_to_path):
            video_path = future_to_path[future]
            try:
                result = future.result()
                progress.update(1, suffix=f"OK: {result.output_path.name}")
            except ConversionError as exc:
                failures.append((video_path, str(exc)))
                progress.update(1, suffix=f"FAILED: {video_path.name}")
                logger.error("Failed to convert %s: %s", video_path, exc)

    progress.close()
    return failures


def main(argv: list[str] | None = None) -> None:
    """Entry point suitable for ``python -m v2mp`` / console scripts."""
    sys.exit(run(argv))
