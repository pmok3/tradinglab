"""Unit tests for :func:`tradinglab.paths.wipe_yfinance_timezone_cache`.

yfinance keeps a small SQLite cache of ticker → timezone mappings at
``platformdirs.user_cache_dir("py-yfinance")/tkr-tz.db``. Concurrent
access from a parallel Python process (e.g. a pytest run while the
live app is open) corrupts the file, after which yfinance returns
the misleading ``Ticker '...' not found`` for every uncached symbol.

We mitigate by wiping the cache on every launch from
:class:`tradinglab.app.ChartApp.__init__`. This module covers the
helper's contract:

* Removes ``tkr-tz.db`` and its SQLite sidecars (``-journal`` /
  ``-wal`` / ``-shm``) when present.
* Leaves ``cookies.db`` (the session-cookie cache) untouched.
* Symlinks are unlinked, not followed, so a planted
  ``tkr-tz.db → /etc/shadow`` cannot trick the helper into deleting
  something outside yfinance's cache dir.
* Idempotent: second call returns 0.
* No-op when ``platformdirs`` is unavailable (returns 0).
* Tolerates a missing or empty cache directory.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tradinglab import paths


def _seed_yf_cache(root: Path) -> dict[str, Path]:
    """Populate ``root`` with a representative yfinance cache layout."""
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "tz_db": root / "tkr-tz.db",
        "tz_journal": root / "tkr-tz.db-journal",
        "tz_wal": root / "tkr-tz.db-wal",
        "tz_shm": root / "tkr-tz.db-shm",
        "cookies": root / "cookies.db",
        "unrelated": root / "some-other.db",
    }
    for p in files.values():
        p.write_bytes(b"data")
    return files


def test_wipes_tz_db_and_all_sidecars(tmp_path: Path) -> None:
    files = _seed_yf_cache(tmp_path)
    with patch.object(paths, "_yfinance_cache_dir", return_value=tmp_path):
        removed = paths.wipe_yfinance_timezone_cache()
    assert removed == 4
    assert not files["tz_db"].exists()
    assert not files["tz_journal"].exists()
    assert not files["tz_wal"].exists()
    assert not files["tz_shm"].exists()


def test_leaves_cookies_db_untouched(tmp_path: Path) -> None:
    """``cookies.db`` is yfinance's session cookie cache — completely
    different corruption class. Must survive the wipe."""
    files = _seed_yf_cache(tmp_path)
    with patch.object(paths, "_yfinance_cache_dir", return_value=tmp_path):
        paths.wipe_yfinance_timezone_cache()
    assert files["cookies"].exists()
    assert files["cookies"].read_bytes() == b"data"
    assert files["unrelated"].exists()


def test_idempotent_second_call_returns_zero(tmp_path: Path) -> None:
    _seed_yf_cache(tmp_path)
    with patch.object(paths, "_yfinance_cache_dir", return_value=tmp_path):
        first = paths.wipe_yfinance_timezone_cache()
        second = paths.wipe_yfinance_timezone_cache()
    assert first == 4
    assert second == 0


def test_missing_cache_dir_returns_zero(tmp_path: Path) -> None:
    """Helper must no-op (not raise) when the cache dir doesn't exist
    — fresh installs have no cache yet."""
    missing = tmp_path / "does-not-exist"
    with patch.object(paths, "_yfinance_cache_dir", return_value=missing):
        removed = paths.wipe_yfinance_timezone_cache()
    assert removed == 0


def test_empty_cache_dir_returns_zero(tmp_path: Path) -> None:
    with patch.object(paths, "_yfinance_cache_dir", return_value=tmp_path):
        removed = paths.wipe_yfinance_timezone_cache()
    assert removed == 0


def test_platformdirs_unavailable_returns_zero() -> None:
    """When ``platformdirs`` can't be imported the helper returns 0
    (graceful degrade — never raises)."""
    with patch.object(paths, "_yfinance_cache_dir", return_value=None):
        removed = paths.wipe_yfinance_timezone_cache()
    assert removed == 0


@pytest.mark.skipif(
    os.name == "nt", reason="symlinks require admin on Windows")
def test_symlink_is_unlinked_not_followed(tmp_path: Path) -> None:
    """Defense against a planted ``tkr-tz.db → /etc/shadow`` symlink:
    the link itself is removed and the target survives."""
    real_root = tmp_path / "real"
    cache_root = tmp_path / "yf"
    real_root.mkdir()
    cache_root.mkdir()
    target = real_root / "important.txt"
    target.write_text("DO NOT DELETE")
    link = cache_root / "tkr-tz.db"
    os.symlink(target, link)
    with patch.object(paths, "_yfinance_cache_dir", return_value=cache_root):
        removed = paths.wipe_yfinance_timezone_cache()
    assert removed == 1
    assert not link.exists()
    assert target.exists()
    assert target.read_text() == "DO NOT DELETE"


def test_directory_named_like_db_is_ignored(tmp_path: Path) -> None:
    """If something has created a directory at ``tkr-tz.db`` (extremely
    unlikely, but possible), the helper skips it rather than raising
    ``IsADirectoryError``."""
    (tmp_path / "tkr-tz.db").mkdir()
    with patch.object(paths, "_yfinance_cache_dir", return_value=tmp_path):
        removed = paths.wipe_yfinance_timezone_cache()
    assert removed == 0
    assert (tmp_path / "tkr-tz.db").is_dir()


def test_helper_exported_from_module() -> None:
    """Public API contract — listed in ``__all__`` so callers can
    discover it via ``dir(paths)``."""
    assert "wipe_yfinance_timezone_cache" in paths.__all__


def test_resolves_to_platformdirs_yfinance_path() -> None:
    """The default cache-dir resolver uses ``platformdirs.user_cache_dir
    ("py-yfinance")`` — the exact path yfinance itself uses, so the
    files we unlink are the ones yfinance reads."""
    import platformdirs
    expected = Path(platformdirs.user_cache_dir("py-yfinance"))
    resolved = paths._yfinance_cache_dir()
    assert resolved == expected
