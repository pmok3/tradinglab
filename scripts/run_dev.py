"""Dev launcher — runs the packaged app from a source checkout.

Usage:
    python scripts/run_dev.py
or (after `pip install -e .`):
    python -m tradinglab
    tradinglab
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running without `pip install -e .` — prepend src/ to sys.path.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tradinglab.app import main  # noqa: E402

if __name__ == "__main__":
    main()
