"""
utils.py
========

Small, dependency-light utility helpers shared across the codebase:

* Logging configuration.
* Human-readable byte-size formatting.
* Recursive/non-recursive video file discovery.
* Safe output-path generation (collision avoidance, directory creation).
* A minimal, dependency-free terminal progress bar.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Iterable, Iterator

_LOGGER_NAME = "v2mp"


def configure_logging(verbose: bool = False) -> logging.Logger:
    """
    Configure and return the package-wide logger.

    Args:
        verbose: If True, set the logging level to DEBUG; otherwise INFO.

    Returns:
        The configured :class:`logging.Logger` instance used throughout
        the ``v2mp`` package.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    level = logging.DEBUG if verbose else logging.INFO

    # Avoid duplicate handlers if configure_logging() is called more than
    # once within the same process (e.g. from tests).
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stderr)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(level)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    """Return the shared package logger, configuring it with defaults if needed."""
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        return configure_logging(verbose=False)
    return logger


def human_size(num_bytes: int) -> str:
    """
    Format a byte count as a human-readable string.

    Args:
        num_bytes: Number of bytes.

    Returns:
        A string such as ``"12.3 MB"``.
    """
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"  # pragma: no cover - unreachable in practice


def iter_video_files(
    root: Path,
    extensions: frozenset[str],
    recursive: bool = False,
) -> Iterator[Path]:
    """
    Yield video files under ``root`` matching ``extensions``.

    Args:
        root: Directory to search.
        extensions: Set of lowercase file extensions (including the dot,
            e.g. ``{".mp4"}``) considered convertible.
        recursive: If True, search subdirectories as well.

    Yields:
        Paths to matching video files, sorted for deterministic ordering.
    """
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")

    pattern = "**/*" if recursive else "*"
    candidates = sorted(root.glob(pattern))
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.lower() in extensions:
            yield candidate


def default_output_path(input_path: Path, output_dir: Path | None = None) -> Path:
    """
    Compute the default ``.jpg`` output path for a given input video.

    Args:
        input_path: Path to the source video file.
        output_dir: If provided, the output is placed in this directory
            instead of alongside the input file.

    Returns:
        A path with the same stem as ``input_path`` and a ``.jpg`` suffix.
    """
    target_dir = output_dir if output_dir is not None else input_path.parent
    return target_dir / f"{input_path.stem}.jpg"


def ensure_unique_path(path: Path) -> Path:
    """
    Return a path guaranteed not to collide with an existing file.

    If ``path`` does not exist, it is returned unchanged. Otherwise a
    numeric suffix (``_1``, ``_2``, ...) is appended to the stem until a
    free path is found.

    Args:
        path: The desired output path.

    Returns:
        A path that does not currently exist on disk.
    """
    if not path.exists():
        return path

    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


class ProgressBar:
    """
    A minimal, dependency-free terminal progress bar.

    Designed for batch conversion feedback without pulling in an external
    dependency such as ``tqdm``. Writes to stderr so it does not interfere
    with any structured stdout output.
    """

    def __init__(self, total: int, label: str = "Converting", width: int = 30) -> None:
        """
        Args:
            total: Total number of items to process.
            label: Text label shown before the bar.
            width: Character width of the bar itself.
        """
        self.total = max(total, 1)
        self.label = label
        self.width = width
        self.count = 0
        self._enabled = sys.stderr.isatty()

    def update(self, increment: int = 1, suffix: str = "") -> None:
        """Advance the progress bar by ``increment`` steps."""
        self.count += increment
        self._render(suffix)

    def _render(self, suffix: str) -> None:
        fraction = min(self.count / self.total, 1.0)
        filled = int(self.width * fraction)
        bar = "#" * filled + "-" * (self.width - filled)
        line = f"\r{self.label}: [{bar}] {self.count}/{self.total} {suffix}"
        if self._enabled:
            sys.stderr.write(line)
            sys.stderr.flush()
        if self.count >= self.total:
            if self._enabled:
                sys.stderr.write("\n")
            else:
                # Non-interactive environments (CI, redirected output): print
                # a single final line instead of spamming carriage returns.
                sys.stderr.write(line + "\n")

    def close(self) -> None:
        """Finalize the progress bar, ensuring the cursor moves to a new line."""
        if self.count < self.total:
            self.count = self.total
            self._render("")


def chunked(iterable: Iterable, size: int) -> Iterator[list]:
    """
    Split an iterable into chunks of at most ``size`` elements.

    Args:
        iterable: Source iterable.
        size: Maximum chunk size.

    Yields:
        Lists of up to ``size`` elements.
    """
    chunk: list = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
