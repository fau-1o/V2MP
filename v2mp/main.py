"""
main.py
=======

Executable entry point for the Motion Photo converter.

Usage::

    python main.py input.mp4
    python main.py input.mp4 output.jpg
    python main.py folder/
    python main.py folder/ --recursive
    python main.py --batch folder/

This module supports being invoked two ways:

* As a script directly (``python main.py ...`` or ``python v2mp/main.py ...``),
  in which case it adjusts ``sys.path`` so the ``v2mp`` package can
  still be imported normally.
* As part of the ``v2mp`` package (``python -m v2mp.main`` or
  via an installed console-script entry point), in which case normal
  relative imports are used.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    # Invoked directly as a script; make the parent directory (which
    # contains the 'v2mp' package) importable.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from v2mp.cli import main
else:
    from .cli import main

if __name__ == "__main__":
    main()
