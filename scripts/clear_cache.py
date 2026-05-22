"""Wipe the on-disk yfinance cache and settings used by tradinglab.

Usage:
    python scripts/clear_cache.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def cache_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "tradinglab"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="List files without deleting.")
    args = ap.parse_args()

    d = cache_dir()
    if not d.is_dir():
        print(f"No cache dir at {d}")
        return 0
    files = sorted(d.glob("*"))
    if not files:
        print(f"Cache dir {d} is empty")
        return 0
    for p in files:
        if args.dry_run:
            print(f"would delete: {p}")
        else:
            try:
                p.unlink()
                print(f"deleted: {p}")
            except Exception as e:  # noqa: BLE001
                print(f"failed: {p}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
