"""Unit tests for the BYOD CSV parser (data/local_source.py).

Covers: schema validation, ISO-8601 + tz enforcement, OHLC numeric
rejection, volume coercion, duplicate timestamp dedupe, sort order,
fetcher closure contract (returns ``Optional[List[Candle]]``,
never raises), subsource discovery.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tradinglab.data.local_source import (
    CANONICAL_HEADER,
    DOCS_HINT,
    LocalDataError,
    _parse_float,
    _parse_iso_with_tz,
    _parse_volume,
    _path_for,
    _read_candles_strict,
    _validate_header,
    discover_subsources,
    list_symbols,
    make_local_fetcher,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_VALID_CSV = (
    "timestamp,open,high,low,close,volume\n"
    "2024-03-15T09:30:00-04:00,100.0,101.0,99.5,100.5,1000\n"
    "2024-03-15T09:35:00-04:00,100.5,101.5,100.0,101.0,1500\n"
)


def _write(p: Path, body: str) -> Path:
    p.write_text(body, encoding="utf-8", newline="")
    return p


# ---------------------------------------------------------------------------
# Header validation
# ---------------------------------------------------------------------------


class TestHeaderValidation:
    def test_canonical_header_is_six_lowercase_tokens(self) -> None:
        assert CANONICAL_HEADER == (
            "timestamp", "open", "high", "low", "close", "volume"
        )

    def test_empty_header_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="file is empty"):
            _validate_header([], file_path=Path("x.csv"))

    def test_missing_column_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="header mismatch"):
            _validate_header(
                ["timestamp", "open", "high", "low", "close"],
                file_path=Path("x.csv"),
            )

    def test_extra_column_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="header mismatch"):
            _validate_header(
                ["timestamp", "open", "high", "low", "close", "volume", "extra"],
                file_path=Path("x.csv"),
            )

    def test_capitalized_header_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="header mismatch"):
            _validate_header(
                ["Timestamp", "Open", "High", "Low", "Close", "Volume"],
                file_path=Path("x.csv"),
            )

    def test_wrong_order_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="header mismatch"):
            _validate_header(
                ["timestamp", "high", "open", "low", "close", "volume"],
                file_path=Path("x.csv"),
            )

    def test_header_with_leading_space_normalized_then_compared(self) -> None:
        _validate_header(
            ["timestamp ", " open", " high ", "low", "close", "volume"],
            file_path=Path("x.csv"),
        )

    def test_error_message_contains_docs_link(self) -> None:
        with pytest.raises(LocalDataError) as exc:
            _validate_header(["wrong"], file_path=Path("x.csv"))
        assert DOCS_HINT in str(exc.value)


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


class TestTimestampParsing:
    def test_iso_with_offset(self) -> None:
        dt = _parse_iso_with_tz("2024-03-15T09:30:00-04:00", line_no=2)
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == -4 * 3600

    def test_z_suffix_normalized_to_utc(self) -> None:
        dt = _parse_iso_with_tz("2024-03-15T13:30:00Z", line_no=2)
        assert dt.utcoffset() == timezone.utc.utcoffset(dt)

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="no timezone"):
            _parse_iso_with_tz("2024-03-15T09:30:00", line_no=2)

    def test_empty_timestamp_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="empty timestamp"):
            _parse_iso_with_tz("", line_no=2)

    def test_garbage_timestamp_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="unparseable"):
            _parse_iso_with_tz("not-a-date", line_no=2)

    def test_line_no_in_error(self) -> None:
        with pytest.raises(LocalDataError, match=r"row 42"):
            _parse_iso_with_tz("", line_no=42)


# ---------------------------------------------------------------------------
# OHLC + Volume parsing
# ---------------------------------------------------------------------------


class TestFloatParsing:
    def test_valid_float(self) -> None:
        assert _parse_float("100.5", field="open", line_no=2) == 100.5

    def test_integer_float(self) -> None:
        assert _parse_float("100", field="open", line_no=2) == 100.0

    def test_negative_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="negative"):
            _parse_float("-1.0", field="open", line_no=2)

    def test_nan_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="NaN"):
            _parse_float("nan", field="open", line_no=2)

    def test_inf_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="not finite"):
            _parse_float("inf", field="open", line_no=2)

    def test_negative_inf_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="not finite"):
            _parse_float("-inf", field="open", line_no=2)

    def test_empty_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="empty 'open' value"):
            _parse_float("", field="open", line_no=2)

    def test_garbage_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="not a number"):
            _parse_float("oops", field="open", line_no=2)

    def test_zero_allowed(self) -> None:
        # Zero is technically valid for OHLC (penny stock can trade at 0.0).
        assert _parse_float("0", field="open", line_no=2) == 0.0


class TestVolumeParsing:
    def test_integer(self) -> None:
        assert _parse_volume("1234", line_no=2) == 1234

    def test_float_coerced(self) -> None:
        assert _parse_volume("1234.0", line_no=2) == 1234

    def test_scientific_coerced(self) -> None:
        assert _parse_volume("1.234e3", line_no=2) == 1234

    def test_blank_becomes_zero(self) -> None:
        assert _parse_volume("", line_no=2) == 0
        assert _parse_volume("   ", line_no=2) == 0

    def test_negative_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="negative"):
            _parse_volume("-1", line_no=2)

    def test_garbage_rejected(self) -> None:
        with pytest.raises(LocalDataError, match="not a number"):
            _parse_volume("abc", line_no=2)


# ---------------------------------------------------------------------------
# End-to-end strict parser
# ---------------------------------------------------------------------------


class TestReadCandlesStrict:
    def test_happy_path(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "AAPL_5m.csv", _VALID_CSV)
        candles = _read_candles_strict(p, interval="5m")
        assert len(candles) == 2
        assert candles[0].open == 100.0
        assert candles[1].close == 101.0
        assert all(c.date.tzinfo is not None for c in candles)

    def test_session_classification_intraday(self, tmp_path: Path) -> None:
        # 09:30 ET → "regular" session, 04:00 → "pre", 16:30 → "post"
        body = (
            "timestamp,open,high,low,close,volume\n"
            "2024-03-15T04:00:00-04:00,1,2,1,1.5,100\n"
            "2024-03-15T09:30:00-04:00,1,2,1,1.5,100\n"
            "2024-03-15T16:30:00-04:00,1,2,1,1.5,100\n"
        )
        p = _write(tmp_path / "AAPL_5m.csv", body)
        candles = _read_candles_strict(p, interval="5m")
        sessions = [c.session for c in candles]
        assert sessions == ["pre", "regular", "post"]

    def test_daily_interval_sets_regular_session(self, tmp_path: Path) -> None:
        body = (
            "timestamp,open,high,low,close,volume\n"
            "2024-03-15T00:00:00-04:00,100,101,99,100.5,1000\n"
        )
        p = _write(tmp_path / "AAPL_1d.csv", body)
        candles = _read_candles_strict(p, interval="1d")
        assert candles[0].session == "regular"

    def test_utf8_bom_tolerated(self, tmp_path: Path) -> None:
        p = tmp_path / "AAPL_5m.csv"
        p.write_text("\ufeff" + _VALID_CSV, encoding="utf-8", newline="")
        candles = _read_candles_strict(p, interval="5m")
        assert len(candles) == 2

    def test_crlf_line_endings_tolerated(self, tmp_path: Path) -> None:
        body = _VALID_CSV.replace("\n", "\r\n")
        p = tmp_path / "AAPL_5m.csv"
        p.write_text(body, encoding="utf-8", newline="")
        candles = _read_candles_strict(p, interval="5m")
        assert len(candles) == 2

    def test_unsorted_rows_get_sorted(self, tmp_path: Path) -> None:
        body = (
            "timestamp,open,high,low,close,volume\n"
            "2024-03-15T09:35:00-04:00,2,3,2,2.5,1500\n"
            "2024-03-15T09:30:00-04:00,1,2,1,1.5,1000\n"
        )
        p = _write(tmp_path / "AAPL_5m.csv", body)
        candles = _read_candles_strict(p, interval="5m")
        assert candles[0].date < candles[1].date

    def test_duplicate_timestamps_keep_first(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        body = (
            "timestamp,open,high,low,close,volume\n"
            "2024-03-15T09:30:00-04:00,1,2,1,1.0,100\n"
            "2024-03-15T09:30:00-04:00,9,9,9,9.0,999\n"
        )
        p = _write(tmp_path / "AAPL_5m.csv", body)
        with caplog.at_level("WARNING"):
            candles = _read_candles_strict(p, interval="5m")
        assert len(candles) == 1
        assert candles[0].close == 1.0  # first wins
        assert any("duplicate timestamp" in r.message for r in caplog.records)

    def test_empty_file_rejected(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "AAPL_5m.csv", "")
        with pytest.raises(LocalDataError, match="completely empty"):
            _read_candles_strict(p, interval="5m")

    def test_header_only_file_rejected(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "AAPL_5m.csv", "timestamp,open,high,low,close,volume\n")
        with pytest.raises(LocalDataError, match="zero data rows"):
            _read_candles_strict(p, interval="5m")

    def test_wrong_column_count_rejected(self, tmp_path: Path) -> None:
        body = (
            "timestamp,open,high,low,close,volume\n"
            "2024-03-15T09:30:00-04:00,1,2,3\n"
        )
        p = _write(tmp_path / "AAPL_5m.csv", body)
        with pytest.raises(LocalDataError, match="expected 6 columns, got 4"):
            _read_candles_strict(p, interval="5m")

    def test_trailing_blank_lines_tolerated(self, tmp_path: Path) -> None:
        body = _VALID_CSV + "\n\n\n"
        p = _write(tmp_path / "AAPL_5m.csv", body)
        candles = _read_candles_strict(p, interval="5m")
        assert len(candles) == 2

    def test_naive_timestamp_rejected(self, tmp_path: Path) -> None:
        body = (
            "timestamp,open,high,low,close,volume\n"
            "2024-03-15T09:30:00,1,2,1,1.5,100\n"
        )
        p = _write(tmp_path / "AAPL_5m.csv", body)
        with pytest.raises(LocalDataError, match="no timezone"):
            _read_candles_strict(p, interval="5m")


# ---------------------------------------------------------------------------
# Fetcher contract
# ---------------------------------------------------------------------------


class TestMakeLocalFetcher:
    def test_happy_path(self, tmp_path: Path) -> None:
        _write(tmp_path / "AAPL_5m.csv", _VALID_CSV)
        fetch = make_local_fetcher(tmp_path)
        candles = fetch("AAPL", "5m")
        assert candles is not None
        assert len(candles) == 2

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        fetch = make_local_fetcher(tmp_path)
        assert fetch("MISSING", "5m") is None

    def test_ticker_case_insensitive(self, tmp_path: Path) -> None:
        _write(tmp_path / "AAPL_5m.csv", _VALID_CSV)
        fetch = make_local_fetcher(tmp_path)
        assert fetch("aapl", "5m") is not None
        assert fetch("AaPl", "5m") is not None

    def test_bad_csv_returns_none_does_not_raise(self, tmp_path: Path) -> None:
        _write(tmp_path / "AAPL_5m.csv", "not even close to a csv")
        fetch = make_local_fetcher(tmp_path)
        # MUST not raise — the DataFetcher contract requires Optional[List].
        assert fetch("AAPL", "5m") is None

    def test_naive_timestamps_return_none(self, tmp_path: Path) -> None:
        body = (
            "timestamp,open,high,low,close,volume\n"
            "2024-03-15T09:30:00,1,2,1,1.5,100\n"
        )
        _write(tmp_path / "AAPL_5m.csv", body)
        fetch = make_local_fetcher(tmp_path)
        assert fetch("AAPL", "5m") is None

    def test_root_with_slash_in_ticker_sanitized(self, tmp_path: Path) -> None:
        # BRK/B-style tickers should be resolved to BRK_B_5m.csv on disk
        _write(tmp_path / "BRK_B_5m.csv", _VALID_CSV)
        fetch = make_local_fetcher(tmp_path)
        assert fetch("BRK/B", "5m") is not None

    def test_path_for_uppercases_ticker(self, tmp_path: Path) -> None:
        path = _path_for(tmp_path, "aapl", "5m")
        assert path.name == "AAPL_5m.csv"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestListSymbols:
    def test_lists_csv_files(self, tmp_path: Path) -> None:
        (tmp_path / "AAPL_5m.csv").touch()
        (tmp_path / "MSFT_1d.csv").touch()
        (tmp_path / "README.md").touch()
        out = list_symbols(tmp_path)
        assert ("AAPL", "5m") in out
        assert ("MSFT", "1d") in out
        # README.md is not a .csv — should be skipped silently.
        assert len(out) == 2

    def test_empty_dir(self, tmp_path: Path) -> None:
        assert list_symbols(tmp_path) == []

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert list_symbols(tmp_path / "does_not_exist") == []

    def test_ticker_with_underscore_uses_last_underscore(self, tmp_path: Path) -> None:
        # BRK_B is a legitimate ticker; the interval is always the suffix
        # after the LAST underscore. So BRK_B_5m.csv → ('BRK_B', '5m').
        (tmp_path / "BRK_B_5m.csv").touch()
        out = list_symbols(tmp_path)
        assert out == [("BRK_B", "5m")]

    def test_lowercase_ticker_uppercased(self, tmp_path: Path) -> None:
        (tmp_path / "aapl_5m.csv").touch()
        out = list_symbols(tmp_path)
        assert out == [("AAPL", "5m")]


class TestDiscoverSubsources:
    def test_subdirs_become_sources(self, tmp_path: Path) -> None:
        (tmp_path / "yfinance").mkdir()
        (tmp_path / "polygon").mkdir()
        out = discover_subsources(tmp_path, "share-2024")
        names = sorted(item[0] for item in out)
        assert names == ["share-2024-polygon", "share-2024-yfinance"]

    def test_files_at_root_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "yfinance").mkdir()
        (tmp_path / "loose_file.csv").touch()
        out = discover_subsources(tmp_path, "share")
        assert len(out) == 1
        assert out[0][0] == "share-yfinance"

    def test_hidden_dirs_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "yfinance").mkdir()
        (tmp_path / ".hidden").mkdir()
        out = discover_subsources(tmp_path, "share")
        names = [item[0] for item in out]
        assert ".hidden" not in [n.split("-", 1)[-1] for n in names]
        assert names == ["share-yfinance"]

    def test_missing_root_returns_empty(self, tmp_path: Path) -> None:
        out = discover_subsources(tmp_path / "nope", "share")
        assert out == []

    def test_fetcher_returns_candles_for_file_in_subdir(
        self, tmp_path: Path,
    ) -> None:
        sub = tmp_path / "yfinance"
        sub.mkdir()
        _write(sub / "AAPL_5m.csv", _VALID_CSV)
        out = discover_subsources(tmp_path, "share")
        key, subdir, fetcher = out[0]
        assert key == "share-yfinance"
        assert subdir == sub
        candles = fetcher("AAPL", "5m")
        assert candles is not None
        assert len(candles) == 2

    def test_results_sorted_alphabetically(self, tmp_path: Path) -> None:
        for name in ["zebra", "alpha", "mike"]:
            (tmp_path / name).mkdir()
        out = discover_subsources(tmp_path, "r")
        assert [item[0] for item in out] == ["r-alpha", "r-mike", "r-zebra"]
