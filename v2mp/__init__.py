"""
v2mp
===========

A production-quality, pure-Python (plus ffmpeg for frame extraction)
converter that turns ordinary MP4 videos into Google Motion Photo
compatible ``.jpg`` files -- single-file outputs playable as a still
image or, in Google Photos / Xiaomi Gallery / compatible Android gallery
apps, as a short embedded video.

Public API:

* :func:`v2mp.converter.convert_video_to_motion_photo`
* :func:`v2mp.validator.validate_motion_photo_file`
"""

from __future__ import annotations

__version__ = "1.0.0"

from .config import ConversionConfig
from .converter import ConversionError, ConversionResult, convert_video_to_motion_photo
from .validator import ValidationReport, validate_motion_photo_file

__all__ = [
    "ConversionConfig",
    "ConversionError",
    "ConversionResult",
    "convert_video_to_motion_photo",
    "ValidationReport",
    "validate_motion_photo_file",
    "__version__",
]
