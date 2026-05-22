"""Unit tests for the BYOD CSV exporter (data/local_export.py).

The headline test is round-trip integrity: export a list of Candles,
re-import via :mod:`data.local_source`, and assert the two lists are
identical. The exporter and importer share no code besides the
canonical schema constant, so this catches any drift.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pytest

from tradinglab.data.local_export import (
    LocalExportError,
    _sanitize_segment,
    export_entries,
    write_csv,
)
from tradinglab.data.local_source import make_local_fetcher
from tradinglab.models import Candle


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_ET = timezone(timedelta(hours=-4))


def _make_candles(n: int = 5, *, start: datetime | None = None) -> List[Candle]:
    """Generate ``n`` synthetic intraday Candles, 5 minutes apart."""
    start = start or datetime(2024, 3, 15, 9, 30, tzinfo=_ET)
    out: List[Candle] = []
    for i in range(n):
        ts = start + timedelta(minutes=5 * i)
        out.append(Candle(
            date=ts,
            open=100.0 + i,
            high=101.0 + i,
            low=99.5 + i,
            close=100.5 + i,
            volume=1000 + 100 * i,
            session="regular",
        ))
    return out


# ---------------------------------------------------------------------------
# write_csv
# ---------------------------------------------------------------------------


class TestWriteCsv:
    def test_writes_canonical_header(self, tmp_path: Path) -> None:
        out = tmp_path / "AAPL_5m.csv"
        n = write_csv(out, _make_candles(3))
        assert n == 3
        body = out.read_text(encoding="utf-8")
        first_line = body.splitlines()[0]
        assert first_line == "timestamp,open,high,low,close,volume"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        out = tmp_path / "nested" / "more" / "AAPL_5m.csv"
        n = write_csv(out, _make_candles(2))
        assert n == 2
        assert out.exists()

    def test_atomic_temp_cleanup_on_success(self, tmp_path: Path) -> None:
        out = tmp_path / "AAPL_5m.csv"
        write_csv(out, _make_candles(2))
        assert out.exists()
        # The .tmp file must not linger after a successful write.
        assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())

    def test_naive_timestamp_rejected(self, tmp_path: Path) -> None:
        out = tmp_path / "AAPL_5m.csv"
        naive = Candle(
            date=datetime(2024, 3, 15, 9, 30),  # no tzinfo
            open=1.0, high=2.0, low=1.0, close=1.5,
            volume=100, session="regular",
        )
        with pytest.raises(LocalExportError, match="no timezone"):
            write_csv(out, [naive])

    def test_temp_cleaned_up_on_failure(self, tmp_path: Path) -> None:
        out = tmp_path / "AAPL_5m.csv"
        naive = Candle(
            date=datetime(2024, 3, 15, 9, 30),
            open=1.0, high=2.0, low=1.0, close=1.5,
            volume=100, session="regular",
        )
        with pytest.raises(LocalExportError):
            write_csv(out, [naive])
        # Failed export must not leave stray .tmp file behind.
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_iso_format_preserves_offset(self, tmp_path: Path) -> None:
        out = tmp_path / "AAPL_5m.csv"
        write_csv(out, _make_candles(1))
        body = out.read_text(encoding="utf-8")
        # The default _ET tz is -04:00.
        assert "-04:00" in body

    def test_empty_candles_writes_header_only(self, tmp_path: Path) -> None:
        out = tmp_path / "EMPTY_5m.csv"
        n = write_csv(out, [])
        assert n == 0
        body = out.read_text(encoding="utf-8")
        assert body.strip() == "timestamp,open,high,low,close,volume"


# ---------------------------------------------------------------------------
# Round-trip integrity
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_export_then_import_preserves_candles(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "yfinance"
        src_dir.mkdir()
        original = _make_candles(10)
        write_csv(src_dir / "AAPL_5m.csv", original)

        fetch = make_local_fetcher(src_dir)
        loaded = fetch("AAPL", "5m")
        assert loaded is not None
        assert len(loaded) == len(original)
        for a, b in zip(original, loaded):
            assert a.date == b.date
            assert a.open == b.open
            assert a.high == b.high
            assert a.low == b.low
            assert a.close == b.close
            assert a.volume == b.volume

    def test_roundtrip_preserves_utc(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "polygon"
        src_dir.mkdir()
        utc_candles = _make_candles(
            3, start=datetime(2024, 3, 15, 13, 30, tzinfo=timezone.utc),
        )
        write_csv(src_dir / "SPY_5m.csv", utc_candles)

        fetch = make_local_fetcher(src_dir)
        loaded = fetch("SPY", "5m")
        assert loaded is not None
        assert all(c.date.tzinfo is not None for c in loaded)
        # Loaded UTC offset must equal what we wrote.
        assert loaded[0].date.utcoffset() == timezone.utc.utcoffset(loaded[0].date)


# ---------------------------------------------------------------------------
# export_entries (multi-entry batch)
# ---------------------------------------------------------------------------


class TestExportEntries:
    def test_subfolder_per_source(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        dest.parent.mkdir(exist_ok=True)
        c = _make_candles(2)
        results = export_entries(
            [
                ("yfinance", "AAPL", "5m", c),
                ("polygon", "AAPL", "5m", c),
                ("polygon", "MSFT", "1d", c),
            ],
            dest,
        )
        assert (dest / "yfinance" / "AAPL_5m.csv").exists()
        assert (dest / "polygon" / "AAPL_5m.csv").exists()
        assert (dest / "polygon" / "MSFT_1d.csv").exists()
        # All three should have succeeded.
        assert all(err is None for *_p, err in results)
        assert all(n == 2 for *_p, n, _err in results)

    def test_ticker_uppercased_on_disk(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        c = _make_candles(1)
        export_entries([("yfinance", "aapl", "5m", c)], dest)
        assert (dest / "yfinance" / "AAPL_5m.csv").exists()

    def test_per_entry_results_returned(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        c = _make_candles(3)
        results = export_entries([("yfinance", "AAPL", "5m", c)], dest)
        assert len(results) == 1
        src, tkr, intv, n, err = results[0]
        assert (src, tkr, intv, n, err) == ("yfinance", "AAPL", "5m", 3, None)

    def test_empty_source_recorded_as_failure(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        c = _make_candles(1)
        results = export_entries([("", "AAPL", "5m", c)], dest)
        assert results[0][4] is not None  # error message
        assert results[0][3] == 0  # zero rows written

    def test_destination_parent_must_exist(self, tmp_path: Path) -> None:
        bad = tmp_path / "missing_parent_dir" / "child"
        with pytest.raises(LocalExportError, match="parent does not exist"):
            export_entries([], bad)

    def test_continues_after_single_failure(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        good = _make_candles(1)
        naive = [Candle(
            date=datetime(2024, 3, 15, 9, 30),
            open=1, high=2, low=1, close=1.5,
            volume=100, session="regular",
        )]
        results = export_entries(
            [
                ("yfinance", "AAPL", "5m", good),
                ("yfinance", "MSFT", "5m", naive),
                ("yfinance", "SPY", "5m", good),
            ],
            dest,
        )
        # First & third succeed, middle one fails — but the exporter
        # continues and reports per-entry results.
        assert results[0][4] is None
        assert results[1][4] is not None
        assert results[2][4] is None

    def test_sanitize_segment_strips_path_separators(self) -> None:
        assert _sanitize_segment("foo/bar") == "foo_bar"
        assert _sanitize_segment("foo\\bar") == "foo_bar"
        assert _sanitize_segment("..") == "_"
        assert _sanitize_segment("../../etc/passwd") == "____etc_passwd"
        assert _sanitize_segment("  trimmed  ") == "trimmed"
