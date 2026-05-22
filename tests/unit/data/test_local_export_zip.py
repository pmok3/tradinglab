"""Tests for the zip-mode exporter and the default-filename helper.

Audit: ``local-export-zip``. Covers `format_csv`, `export_entries_zip`,
and `default_zip_filename`.
"""
from __future__ import annotations

import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pytest

from tradinglab.data.local_export import (
    LocalExportError,
    default_zip_filename,
    export_entries_zip,
    format_csv,
)
from tradinglab.models import Candle

_ET = timezone(timedelta(hours=-4))


def _make_candles(n: int = 5, *, start: datetime | None = None) -> list[Candle]:
    start = start or datetime(2024, 3, 15, 9, 30, tzinfo=_ET)
    out: list[Candle] = []
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
# format_csv
# ---------------------------------------------------------------------------


class TestFormatCsv:
    def test_header_first_line(self):
        out = format_csv(_make_candles(2))
        first_line = out.split("\n", 1)[0]
        assert first_line == "timestamp,open,high,low,close,volume"

    def test_naive_timestamp_rejected(self):
        c = Candle(
            date=datetime(2024, 1, 1, 9, 30),  # no tzinfo
            open=1.0, high=1.0, low=1.0, close=1.0, volume=100,
            session="regular",
        )
        with pytest.raises(LocalExportError):
            format_csv([c])

    def test_returns_str(self):
        out = format_csv(_make_candles(3))
        assert isinstance(out, str)
        assert "\n" in out

    def test_row_count_matches_candle_count(self):
        out = format_csv(_make_candles(7))
        # Header + 7 data rows + trailing newline → 9 splits.
        rows = [r for r in out.split("\n") if r]
        assert len(rows) == 8  # header + 7 data rows


# ---------------------------------------------------------------------------
# default_zip_filename
# ---------------------------------------------------------------------------


class TestDefaultZipFilename:
    def test_format_is_dated(self):
        name = default_zip_filename(today=date(2024, 3, 15))
        assert name == "tradinglab-export-2024-03-15.zip"

    def test_uses_today_when_no_arg(self):
        name = default_zip_filename()
        # Don't pin the exact date — just verify the shape.
        assert name.startswith("tradinglab-export-")
        assert name.endswith(".zip")
        assert len(name) == len("tradinglab-export-2024-01-01.zip")


# ---------------------------------------------------------------------------
# export_entries_zip
# ---------------------------------------------------------------------------


class TestExportEntriesZip:
    def test_single_entry_creates_zip_with_csv(self, tmp_path):
        out_zip = tmp_path / "out.zip"
        candles = _make_candles(3)
        results = export_entries_zip(
            [("yfinance", "AAPL", "5m", candles)], out_zip,
        )
        assert out_zip.is_file()
        assert len(results) == 1
        source, ticker, interval, rows, err = results[0]
        assert (source, ticker, interval) == ("yfinance", "AAPL", "5m")
        assert rows == 3
        assert err is None

    def test_arcname_uses_forward_slash(self, tmp_path):
        out_zip = tmp_path / "out.zip"
        export_entries_zip(
            [("yfinance", "AAPL", "5m", _make_candles(2))], out_zip,
        )
        with zipfile.ZipFile(out_zip) as zf:
            names = zf.namelist()
        assert names == ["yfinance/AAPL_5m.csv"]

    def test_arcname_ticker_uppercased(self, tmp_path):
        out_zip = tmp_path / "out.zip"
        export_entries_zip(
            [("yfinance", "aapl", "1d", _make_candles(2))], out_zip,
        )
        with zipfile.ZipFile(out_zip) as zf:
            names = zf.namelist()
        assert names == ["yfinance/AAPL_1d.csv"]

    def test_multiple_entries_separate_arcnames(self, tmp_path):
        out_zip = tmp_path / "multi.zip"
        results = export_entries_zip([
            ("yfinance", "AAPL", "5m", _make_candles(2)),
            ("polygon", "SPY", "1m", _make_candles(3)),
            ("yfinance", "MSFT", "1d", _make_candles(1)),
        ], out_zip)
        assert all(err is None for *_p, err in results)
        with zipfile.ZipFile(out_zip) as zf:
            names = sorted(zf.namelist())
        assert names == sorted([
            "yfinance/AAPL_5m.csv",
            "polygon/SPY_1m.csv",
            "yfinance/MSFT_1d.csv",
        ])

    def test_csv_content_inside_zip_matches_format_csv(self, tmp_path):
        out_zip = tmp_path / "out.zip"
        candles = _make_candles(4)
        export_entries_zip(
            [("yfinance", "AAPL", "5m", candles)], out_zip,
        )
        with zipfile.ZipFile(out_zip) as zf:
            text = zf.read("yfinance/AAPL_5m.csv").decode("utf-8")
        assert text == format_csv(candles)

    def test_uses_deflate_compression(self, tmp_path):
        out_zip = tmp_path / "compressed.zip"
        export_entries_zip(
            [("yfinance", "AAPL", "5m", _make_candles(20))], out_zip,
        )
        with zipfile.ZipFile(out_zip) as zf:
            info = zf.infolist()[0]
        assert info.compress_type == zipfile.ZIP_DEFLATED
        # CSV should compress meaningfully (text → at least 30% smaller).
        assert info.compress_size < info.file_size

    def test_bad_entry_does_not_break_others(self, tmp_path):
        out_zip = tmp_path / "mixed.zip"
        bad = Candle(
            date=datetime(2024, 1, 1, 9, 30),  # naive → reject
            open=1.0, high=1.0, low=1.0, close=1.0, volume=100,
            session="regular",
        )
        results = export_entries_zip([
            ("yfinance", "AAPL", "5m", [bad]),
            ("yfinance", "MSFT", "1d", _make_candles(2)),
        ], out_zip)
        # First entry recorded as error, second still wrote.
        assert results[0][4] is not None
        assert results[1][4] is None
        with zipfile.ZipFile(out_zip) as zf:
            assert zf.namelist() == ["yfinance/MSFT_1d.csv"]

    def test_atomic_publish_no_orphan_tmp(self, tmp_path):
        out_zip = tmp_path / "atomic.zip"
        export_entries_zip(
            [("yfinance", "AAPL", "5m", _make_candles(2))], out_zip,
        )
        # No leftover .tmp file.
        leftovers = [
            p for p in tmp_path.iterdir() if p.suffix == ".tmp"
        ]
        assert leftovers == []

    def test_refuses_when_parent_missing(self, tmp_path):
        out_zip = tmp_path / "no" / "such" / "dir" / "out.zip"
        with pytest.raises(LocalExportError):
            export_entries_zip(
                [("yfinance", "AAPL", "5m", _make_candles(2))], out_zip,
            )

    def test_refuses_zip_path_that_is_directory(self, tmp_path):
        with pytest.raises(LocalExportError):
            export_entries_zip(
                [("yfinance", "AAPL", "5m", _make_candles(2))], tmp_path,
            )

    def test_path_segment_sanitization_zip(self, tmp_path):
        out_zip = tmp_path / "out.zip"
        # Malicious source name with separators should be flattened
        # to a single safe segment.
        export_entries_zip([
            ("yf/../oops", "AAPL", "5m", _make_candles(2)),
        ], out_zip)
        with zipfile.ZipFile(out_zip) as zf:
            names = zf.namelist()
        # Sanitization replaces / and .. with _; no path-escape.
        assert all(not n.startswith(".") for n in names)
        assert all("/.." not in n for n in names)
