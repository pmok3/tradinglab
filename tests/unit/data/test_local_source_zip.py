"""Tests for zip-as-root support in :mod:`tradinglab.data.local_source`.

Audit: ``local-source-zip``. Covers the full BYOD round-trip when the
user picks a zip archive (produced by Export Bars to CSV) as the
Configure Local Data root, without unzipping it first.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pytest

from tradinglab.data.local_export import export_entries_zip
from tradinglab.data.local_source import (
    LocalDataError,
    _read_candles_from_zip,
    _zip_top_level_dirs,
    discover_subsources,
    make_local_zip_fetcher,
)
from tradinglab.models import Candle

_ET = timezone(timedelta(hours=-4))


def _make_candles(n: int = 5) -> list[Candle]:
    base = datetime(2024, 3, 15, 9, 30, tzinfo=_ET)
    out: list[Candle] = []
    for i in range(n):
        ts = base + timedelta(minutes=5 * i)
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


def _build_zip(tmp_path: Path, entries) -> Path:
    """Build a zip via the production exporter; returns the path."""
    out_zip = tmp_path / "data.zip"
    export_entries_zip(entries, out_zip)
    return out_zip


# ---------------------------------------------------------------------------
# _zip_top_level_dirs
# ---------------------------------------------------------------------------


class TestZipTopLevelDirs:
    def test_returns_sorted_unique_dirs(self, tmp_path):
        z = _build_zip(tmp_path, [
            ("yfinance", "AAPL", "5m", _make_candles(2)),
            ("polygon",  "SPY",  "1m", _make_candles(2)),
            ("yfinance", "MSFT", "1d", _make_candles(1)),
        ])
        dirs = _zip_top_level_dirs(z)
        assert dirs == ["polygon", "yfinance"]

    def test_missing_zip_returns_empty(self, tmp_path):
        assert _zip_top_level_dirs(tmp_path / "nope.zip") == []

    def test_skips_hidden_and_macosx_metadata(self, tmp_path):
        import zipfile
        z = tmp_path / "mixed.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("yfinance/AAPL_5m.csv", "x")
            zf.writestr("__MACOSX/yfinance/._AAPL_5m.csv", "x")
            zf.writestr(".hidden/AAPL_5m.csv", "x")
        assert _zip_top_level_dirs(z) == ["yfinance"]


# ---------------------------------------------------------------------------
# _read_candles_from_zip
# ---------------------------------------------------------------------------


class TestReadCandlesFromZip:
    def test_happy_path_returns_candles(self, tmp_path):
        candles = _make_candles(4)
        z = _build_zip(tmp_path, [("yfinance", "AAPL", "5m", candles)])
        loaded = _read_candles_from_zip(
            z, "yfinance/AAPL_5m.csv", interval="5m",
        )
        assert len(loaded) == 4

    def test_round_trip_preserves_ohlcv(self, tmp_path):
        candles = _make_candles(6)
        z = _build_zip(tmp_path, [("yfinance", "AAPL", "5m", candles)])
        loaded = _read_candles_from_zip(
            z, "yfinance/AAPL_5m.csv", interval="5m",
        )
        assert len(loaded) == len(candles)
        for orig, back in zip(candles, loaded, strict=False):
            assert orig.date == back.date
            assert orig.open == pytest.approx(back.open)
            assert orig.high == pytest.approx(back.high)
            assert orig.low == pytest.approx(back.low)
            assert orig.close == pytest.approx(back.close)
            assert int(orig.volume) == int(back.volume)

    def test_missing_arcname_raises(self, tmp_path):
        z = _build_zip(tmp_path, [("yfinance", "AAPL", "5m", _make_candles(2))])
        with pytest.raises(LocalDataError):
            _read_candles_from_zip(
                z, "yfinance/NOPE_5m.csv", interval="5m",
            )

    def test_bad_zip_raises(self, tmp_path):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        with pytest.raises(LocalDataError):
            _read_candles_from_zip(bad, "x/y.csv", interval="5m")


# ---------------------------------------------------------------------------
# make_local_zip_fetcher
# ---------------------------------------------------------------------------


class TestLocalZipFetcher:
    def test_fetches_existing_member(self, tmp_path):
        z = _build_zip(tmp_path, [
            ("yfinance", "AAPL", "5m", _make_candles(3)),
        ])
        fetch = make_local_zip_fetcher(z, "yfinance")
        candles = fetch("AAPL", "5m")
        assert candles is not None
        assert len(candles) == 3

    def test_uppercases_ticker(self, tmp_path):
        z = _build_zip(tmp_path, [
            ("yfinance", "AAPL", "5m", _make_candles(2)),
        ])
        fetch = make_local_zip_fetcher(z, "yfinance")
        # Lowercase ticker resolves correctly.
        assert fetch("aapl", "5m") is not None

    def test_missing_member_returns_none(self, tmp_path):
        z = _build_zip(tmp_path, [
            ("yfinance", "AAPL", "5m", _make_candles(2)),
        ])
        fetch = make_local_zip_fetcher(z, "yfinance")
        assert fetch("MSFT", "5m") is None

    def test_missing_zip_returns_none(self, tmp_path):
        fetch = make_local_zip_fetcher(tmp_path / "nope.zip", "yfinance")
        assert fetch("AAPL", "5m") is None

    def test_wrong_subdir_returns_none(self, tmp_path):
        z = _build_zip(tmp_path, [
            ("yfinance", "AAPL", "5m", _make_candles(2)),
        ])
        fetch = make_local_zip_fetcher(z, "polygon")
        assert fetch("AAPL", "5m") is None


# ---------------------------------------------------------------------------
# discover_subsources branching on zip-vs-dir
# ---------------------------------------------------------------------------


class TestDiscoverSubsourcesZip:
    def test_zip_root_yields_one_entry_per_subdir(self, tmp_path):
        z = _build_zip(tmp_path, [
            ("yfinance", "AAPL", "5m", _make_candles(2)),
            ("polygon",  "SPY",  "1m", _make_candles(2)),
        ])
        out = discover_subsources(z, "share-2024")
        keys = sorted(key for key, _p, _f in out)
        assert keys == ["share-2024-polygon", "share-2024-yfinance"]

    def test_zip_root_fetchers_load_back_data(self, tmp_path):
        z = _build_zip(tmp_path, [
            ("yfinance", "AAPL", "5m", _make_candles(3)),
        ])
        out = discover_subsources(z, "share")
        assert len(out) == 1
        _, _p, fetcher = out[0]
        loaded = fetcher("AAPL", "5m")
        assert loaded is not None
        assert len(loaded) == 3

    def test_dir_root_still_works(self, tmp_path):
        # Directory layout the legacy path expects.
        subdir = tmp_path / "yfinance"
        subdir.mkdir()
        (subdir / "AAPL_5m.csv").write_text(
            "timestamp,open,high,low,close,volume\n"
            "2024-03-15T09:30:00-04:00,100,101,99,100.5,1000\n",
            encoding="utf-8",
        )
        out = discover_subsources(tmp_path, "myroot")
        keys = [key for key, _p, _f in out]
        assert keys == ["myroot-yfinance"]

    def test_nonexistent_path_returns_empty(self, tmp_path):
        assert discover_subsources(tmp_path / "missing", "x") == []

    def test_non_zip_file_returns_empty(self, tmp_path):
        f = tmp_path / "stray.txt"
        f.write_text("not a zip", encoding="utf-8")
        assert discover_subsources(f, "x") == []


# ---------------------------------------------------------------------------
# End-to-end round trip: export → discover → fetch back identical
# ---------------------------------------------------------------------------


def test_export_zip_then_load_back_as_root_round_trip(tmp_path):
    candles = _make_candles(8)
    z = _build_zip(tmp_path, [
        ("yfinance", "AAPL", "5m", candles),
        ("polygon",  "SPY",  "1m", _make_candles(4)),
    ])

    sources = {key: fetcher for key, _p, fetcher in discover_subsources(z, "share")}
    assert set(sources.keys()) == {"share-yfinance", "share-polygon"}

    loaded = sources["share-yfinance"]("AAPL", "5m")
    assert loaded is not None
    assert len(loaded) == len(candles)
    for orig, back in zip(candles, loaded, strict=False):
        assert orig.date == back.date
        assert orig.open == pytest.approx(back.open)
        assert int(orig.volume) == int(back.volume)
