"""Unit tests for ``disk_cache.list_entries()`` — the helper used by
the Export Bars dialog to enumerate everything currently cached.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tradinglab import disk_cache
from tradinglab.models import Candle


@pytest.fixture(autouse=True)
def _isolated_cache_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route the cache dir to a tmp_path so we don't see real entries."""
    monkeypatch.setenv("TRADINGLAB_DATA_DIR", str(tmp_path))


def _make_candle() -> Candle:
    return Candle(
        date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        open=1, high=2, low=1, close=1.5,
        volume=100, session="regular",
    )


class TestListEntries:
    def test_empty_cache_returns_empty(self) -> None:
        assert disk_cache.list_entries() == []

    def test_lists_one_entry_after_save(self) -> None:
        disk_cache.save("yfinance", "AAPL", "5m", [_make_candle()])
        out = disk_cache.list_entries()
        assert out == [("yfinance", "AAPL", "5m")]

    def test_lists_multiple_entries_sorted(self) -> None:
        disk_cache.save("yfinance", "MSFT", "1d", [_make_candle()])
        disk_cache.save("polygon", "AAPL", "5m", [_make_candle()])
        disk_cache.save("yfinance", "AAPL", "5m", [_make_candle()])
        out = disk_cache.list_entries()
        # Sorted lexicographically.
        assert out == [
            ("polygon", "AAPL", "5m"),
            ("yfinance", "AAPL", "5m"),
            ("yfinance", "MSFT", "1d"),
        ]

    def test_non_pkl_files_ignored(self, tmp_path: Path) -> None:
        disk_cache.save("yfinance", "AAPL", "5m", [_make_candle()])
        # Drop a lock file + a stray .csv in the cache dir.
        (tmp_path / "cache.lock").touch()
        (tmp_path / "random.csv").touch()
        out = disk_cache.list_entries()
        assert out == [("yfinance", "AAPL", "5m")]

    def test_malformed_pkl_filename_ignored(self, tmp_path: Path) -> None:
        disk_cache.save("yfinance", "AAPL", "5m", [_make_candle()])
        # Drop a .pkl that doesn't match the source__ticker__interval pattern.
        (tmp_path / "notarealkey.pkl").touch()
        (tmp_path / "too__manyfields__here__oops.pkl").touch()
        out = disk_cache.list_entries()
        assert out == [("yfinance", "AAPL", "5m")]

    def test_byod_no_persist_source_never_appears(self) -> None:
        # A no-persist source has save() as no-op, so it can never have
        # a pickle on disk — and thus must never show up in list_entries.
        disk_cache.mark_no_persist("test-byod")
        try:
            disk_cache.save("test-byod", "AAPL", "5m", [_make_candle()])
            disk_cache.save("yfinance", "MSFT", "5m", [_make_candle()])
            out = disk_cache.list_entries()
            assert out == [("yfinance", "MSFT", "5m")]
        finally:
            disk_cache.unmark_no_persist("test-byod")
