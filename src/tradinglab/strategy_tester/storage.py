"""On-disk persistence for Strategy Tester runs.

Storage layout under :func:`disk_cache._cache_dir`::

    strategy_tests/
        index.json
        <run_id>-<iso_ts>/
            config.json
            manifest.json
            per_symbol/
                <SYMBOL>.json
            aggregate.json
            trades.csv
            screenshots/
                <order_id>_post.png
            report.html
            report.pdf

The ``<run_id>-<iso_ts>`` directory naming reflects the locked design
decision that re-running an identical config always creates a fresh
Run while still letting two runs with the same fingerprint be
detected programmatically (both share the same ``<run_id>`` prefix).

PR 1 ships only ``save_config`` + ``save_manifest`` +
``save_session_result_for_symbol`` + ``list_runs``. CSV / HTML / PDF
emission lives in dedicated modules in PR 2 / PR 3 / PR 5 but reuse
the directory layout here.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path

from .. import disk_cache
from ..backtest.session import SessionResult
from ..core.io_helpers import atomic_write_json
from .model import TestConfig, TestRun

__all__ = [
    "ROOT_DIR_NAME",
    "runs_dir",
    "run_dir_for",
    "save_config",
    "save_manifest",
    "load_manifest",
    "save_session_result_for_symbol",
    "load_session_result_for_symbol",
    "list_runs",
    "list_runs_with_paths",
    "delete_run",
]


ROOT_DIR_NAME = "strategy_tests"


def runs_dir() -> Path:
    """Resolve the root strategy-tests directory under the cache dir.

    Creates the directory on first access. Honors
    ``TRADINGLAB_CACHE_DIR`` via :func:`disk_cache._cache_dir`.
    """
    d = disk_cache._cache_dir() / ROOT_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_iso() -> str:
    """Filename-safe UTC timestamp suffix."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def run_dir_for(run_id: str, *, started_iso: str = "") -> Path:
    """Return the per-run directory path, creating its scaffolding.

    The directory name is ``<run_id>-<iso_ts>``. ``started_iso`` is
    optional — when empty, a fresh timestamp is generated. Callers
    that need a deterministic directory across "save_config" then
    later "save_manifest" should pass the same ``started_iso`` both
    times (the runner does this).
    """
    stamp = started_iso or _safe_iso()
    name = f"{run_id}-{stamp}"
    d = runs_dir() / name
    (d / "per_symbol").mkdir(parents=True, exist_ok=True)
    (d / "screenshots").mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Config & manifest
# ---------------------------------------------------------------------------


def save_config(run_dir: Path, config: TestConfig) -> None:
    atomic_write_json(run_dir / "config.json", config.to_dict(), indent=2)


def save_manifest(run_dir: Path, run: TestRun) -> None:
    atomic_write_json(run_dir / "manifest.json", run.to_dict(), indent=2)


def load_manifest(run_dir: Path) -> TestRun | None:
    path = run_dir / "manifest.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, Mapping):
        return None
    try:
        return TestRun.from_dict(raw)
    except (ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Per-symbol SessionResult
# ---------------------------------------------------------------------------


def _per_symbol_path(run_dir: Path, symbol: str) -> Path:
    # Symbols are upper-cased ASCII tickers; even the gnarliest yfinance
    # quirks (``BRK-B`` / ``BF.B``) are filename-safe on Windows.
    return run_dir / "per_symbol" / f"{symbol}.json"


def save_session_result_for_symbol(
    run_dir: Path, symbol: str, result: SessionResult
) -> None:
    """Write the headless engine's :class:`SessionResult` for one symbol.

    Atomic + indent=2 — the file is occasionally inspected by humans
    when debugging mechanical-tester surprises. Reuses ``SessionResult.to_dict``
    so the on-disk schema matches the existing Sandbox post-mortem format.
    """
    payload = result.to_dict()
    atomic_write_json(_per_symbol_path(run_dir, symbol), payload, indent=2)


def load_session_result_for_symbol(
    run_dir: Path, symbol: str
) -> SessionResult | None:
    p = _per_symbol_path(run_dir, symbol)
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, Mapping):
        return None
    try:
        return SessionResult.from_dict(raw)
    except (ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Index / listing
# ---------------------------------------------------------------------------


def list_runs() -> list[TestRun]:
    """Return every persisted Run, newest-first.

    Iterates :func:`runs_dir` and loads each ``<run_id>-<ts>/manifest.json``.
    Directories without a parseable manifest are skipped silently — a
    half-written run (e.g. crashed before manifest finalisation) does
    not break the Recent runs sidebar.
    """
    return [run for _path, run in list_runs_with_paths()]


def list_runs_with_paths() -> list[tuple[Path, TestRun]]:
    """Return ``[(run_dir, TestRun), ...]`` newest-first.

    Used by the GUI Recent Runs sidebar (PR 5) — the sidebar needs
    both the on-disk directory (to load ``aggregate.json``,
    ``trades.csv``, screenshots) and the manifest (for the display
    label / status / timestamps).
    """
    base = runs_dir()
    if not base.exists():
        return []
    pairs: list[tuple[str, Path, TestRun]] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        run = load_manifest(child)
        if run is None:
            continue
        pairs.append((child.name, child, run))
    pairs.sort(key=lambda triple: triple[0], reverse=True)
    return [(p, r) for _n, p, r in pairs]


def delete_run(run_dir: Path) -> bool:
    """Recursively delete a Run directory. Returns True on success."""
    import shutil
    try:
        shutil.rmtree(run_dir)
    except OSError:
        return False
    return True
