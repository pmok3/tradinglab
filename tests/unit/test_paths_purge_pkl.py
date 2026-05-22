"""Unit tests for the legacy-``.pkl``-purge in :mod:`tradinglab.paths`.

Prior versions wrote candle and event caches as ``.pkl`` files. The
new code only reads / writes JSON (see ``disk_cache.spec.md`` and
``events/cache.spec.md`` — switched away from pickle in the security
sprint because :func:`pickle.load` is arbitrary-code-execution by
design). Any leftover ``.pkl`` on disk is at best stale and at worst
a planted RCE payload; the purge unlinks them all on first launch
after the upgrade.

The purge is implemented in :func:`tradinglab.paths._purge_legacy_pickle_caches`
and wired into :func:`_migrate_legacy_locations`. We test the
lower-level helper directly because the high-level migration is
gated by a process-wide flag and a default-pathed root that's
clumsy to exercise.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tradinglab import paths


def _seed_cache(root: Path) -> dict:
    """Create a representative cache tree under ``root`` and return inventory."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "events").mkdir(parents=True, exist_ok=True)
    files = {
        "candle_pkl": root / "yfinance__AAPL__5m.pkl",
        "candle_json": root / "yfinance__AAPL__5m.jsonl",
        "event_pkl": root / "events" / "yfinance__AAPL.pkl",
        "event_json": root / "events" / "yfinance__AAPL.json",
        "subdir": root / "events" / "subdir",
        "other_file": root / "settings.json",
    }
    files["candle_pkl"].write_bytes(b"pickle bytes")
    files["candle_json"].write_text("[]", encoding="utf-8")
    files["event_pkl"].write_bytes(b"pickle bytes")
    files["event_json"].write_text("{}", encoding="utf-8")
    files["subdir"].mkdir(parents=True, exist_ok=True)
    files["other_file"].write_text("{}", encoding="utf-8")
    return files


def test_purge_removes_pkl_files_in_root_and_events_dir(tmp_path: Path) -> None:
    files = _seed_cache(tmp_path)
    removed = paths._purge_legacy_pickle_caches(tmp_path)
    assert removed == 2, "should have removed exactly the two .pkl files"
    assert not files["candle_pkl"].exists()
    assert not files["event_pkl"].exists()


def test_purge_preserves_non_pkl_files(tmp_path: Path) -> None:
    files = _seed_cache(tmp_path)
    paths._purge_legacy_pickle_caches(tmp_path)
    assert files["candle_json"].exists()
    assert files["event_json"].exists()
    assert files["other_file"].exists()
    assert files["subdir"].is_dir()


def test_purge_is_idempotent(tmp_path: Path) -> None:
    _seed_cache(tmp_path)
    first = paths._purge_legacy_pickle_caches(tmp_path)
    second = paths._purge_legacy_pickle_caches(tmp_path)
    assert first == 2
    assert second == 0, "second call has nothing to do"


def test_purge_handles_missing_directories_gracefully(tmp_path: Path) -> None:
    # No cache root, no events dir, no nothing.
    target = tmp_path / "nonexistent"
    removed = paths._purge_legacy_pickle_caches(target)
    assert removed == 0


def test_purge_handles_missing_events_subdir(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "yfinance__AAPL__5m.pkl").write_bytes(b"x")
    # No events subdirectory exists yet.
    removed = paths._purge_legacy_pickle_caches(tmp_path)
    assert removed == 1


@pytest.mark.skipif(os.name == "nt", reason="symlinks require admin on Windows")
def test_purge_does_not_follow_symlinks(tmp_path: Path) -> None:
    """A symlink at ``cache/legacy.pkl`` → ``/etc/shadow`` must be
    unlinked (not have its target unlinked)."""
    sensitive = tmp_path / "sensitive_dir"
    sensitive.mkdir()
    decoy_target = sensitive / "secret_file"
    decoy_target.write_text("must_survive", encoding="utf-8")

    cache = tmp_path / "cache"
    cache.mkdir()
    link = cache / "fake.pkl"
    link.symlink_to(decoy_target)

    paths._purge_legacy_pickle_caches(cache)

    assert not link.exists(), "the symlink itself must be removed"
    assert decoy_target.exists(), (
        "the symlink target must NOT be deleted — purge must not follow links"
    )
    assert decoy_target.read_text(encoding="utf-8") == "must_survive"
