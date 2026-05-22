"""Unit tests for :func:`tradinglab.status.prune_old_logs`."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tradinglab import status


def _make_log(d: Path, name: str, *, age_seconds: float) -> Path:
    p = d / name
    p.write_text("dummy\n", encoding="utf-8")
    mtime = time.time() - age_seconds
    os.utime(p, (mtime, mtime))
    return p


def test_prune_drops_old_logs(tmp_path):
    fresh = _make_log(tmp_path, "status-2026-05-01.log", age_seconds=1)
    old = _make_log(tmp_path, "status-2020-01-01.log", age_seconds=400 * 86400)
    removed = status.prune_old_logs(tmp_path, keep_days=30)
    assert removed == 1
    assert fresh.exists()
    assert not old.exists()


def test_prune_skips_non_log_files(tmp_path):
    # Crash dumps and other artefacts must not be swept by the log pruner.
    keep = _make_log(tmp_path, "crash-2020-01-01.txt", age_seconds=400 * 86400)
    removed = status.prune_old_logs(tmp_path, keep_days=30)
    assert removed == 0
    assert keep.exists()


def test_prune_skips_when_keep_days_zero(tmp_path):
    _make_log(tmp_path, "status-2020-01-01.log", age_seconds=400 * 86400)
    removed = status.prune_old_logs(tmp_path, keep_days=0)
    assert removed == 0


def test_prune_handles_missing_dir(tmp_path):
    nonexistent = tmp_path / "missing"
    removed = status.prune_old_logs(nonexistent, keep_days=30)
    assert removed == 0


def test_prune_keeps_files_inside_window(tmp_path):
    # 5-day-old log under a 30-day window stays.
    p = _make_log(tmp_path, "status-2026-04-25.log", age_seconds=5 * 86400)
    removed = status.prune_old_logs(tmp_path, keep_days=30)
    assert removed == 0
    assert p.exists()


def test_prune_default_retention_constant_positive():
    # The constant must be sensible (>0 so default sweep does something
    # useful, <365 so we don't accidentally keep years of logs).
    assert 0 < status._LOG_RETENTION_DAYS <= 365


def test_status_log_invokes_prune_on_construction(tmp_path, monkeypatch):
    """``StatusLog(...)`` should call ``prune_old_logs`` once at __init__."""
    import tkinter as tk
    # Build a minimal Tk root so StringVar works. If Tk isn't available
    # (headless CI without an X server), skip — the prune sweep itself
    # doesn't need Tk.
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        pytest.skip("Tk not available")
    try:
        var = tk.StringVar(master=root)
        # Plant one ancient log.
        old = _make_log(
            tmp_path, "status-2020-01-01.log", age_seconds=400 * 86400,
        )
        status.StatusLog(var, log_dir=tmp_path, retention_days=30,
                         also_stdout=False)
        assert not old.exists()
    finally:
        root.destroy()
