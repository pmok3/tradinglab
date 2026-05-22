"""Tests for the per-vendor JSON → Candle mappers.

These exercise pure functions over hand-crafted payloads matching each
vendor's documented response shape — no network, no credentials.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradinglab.data import (
    candles_from_alpaca_response,
    candles_from_json_rows,
    candles_from_polygon_response,
    candles_from_schwab_response,
)

# ---------------------------------------------------------------------------
# candles_from_json_rows — generic mapper
# ---------------------------------------------------------------------------

_ANY_KEYMAP = {
    "ts": "datetime",
    "open": "open", "high": "high", "low": "low",
    "close": "close", "volume": "volume",
}


def test_json_rows_empty_returns_empty():
    assert candles_from_json_rows([], interval="1d", keymap=_ANY_KEYMAP, ts_unit="ms") == []


def test_json_rows_keymap_validated():
    incomplete = {k: k for k in ("ts", "open", "high", "low", "close")}
    with pytest.raises(ValueError, match="missing logical fields"):
        candles_from_json_rows(
            [{"ts": 0}], interval="1d", keymap=incomplete, ts_unit="ms",
        )


def test_json_rows_invalid_ts_unit_raises():
    with pytest.raises(ValueError, match="ts_unit"):
        candles_from_json_rows(
            [{"datetime": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
            interval="1d", keymap=_ANY_KEYMAP, ts_unit="weird",
        )


def test_json_rows_epoch_seconds():
    rows = [{"datetime": 1700000000, "open": 1, "high": 2, "low": 0.5,
             "close": 1.5, "volume": 100}]
    out = candles_from_json_rows(rows, interval="1d", keymap=_ANY_KEYMAP, ts_unit="s")
    assert len(out) == 1
    expected = datetime.fromtimestamp(1700000000, tz=timezone.utc)
    assert out[0].date == expected
    assert out[0].open == 1.0 and out[0].close == 1.5
    assert out[0].volume == 100
    # 1d → session "regular".
    assert out[0].session == "regular"


def test_json_rows_iso_with_z_suffix():
    rows = [{"datetime": "2024-03-07T14:30:00Z", "open": 1, "high": 1,
             "low": 1, "close": 1, "volume": 0}]
    out = candles_from_json_rows(rows, interval="5m", keymap=_ANY_KEYMAP, ts_unit="iso")
    assert out[0].date == datetime(2024, 3, 7, 14, 30, tzinfo=timezone.utc)


def test_json_rows_volume_none_becomes_zero():
    rows = [{"datetime": 1700000000000, "open": 1, "high": 1, "low": 1,
             "close": 1, "volume": None}]
    out = candles_from_json_rows(rows, interval="1d", keymap=_ANY_KEYMAP, ts_unit="ms")
    assert out[0].volume == 0


def test_json_rows_volume_string_coerced():
    rows = [{"datetime": 1700000000000, "open": 1, "high": 1, "low": 1,
             "close": 1, "volume": "1234.0"}]
    out = candles_from_json_rows(rows, interval="1d", keymap=_ANY_KEYMAP, ts_unit="ms")
    assert out[0].volume == 1234


# ---------------------------------------------------------------------------
# Schwab mapper
# ---------------------------------------------------------------------------


_SCHWAB_PAYLOAD = {
    "candles": [
        {"open": 175.00, "high": 175.50, "low": 174.80, "close": 175.20,
         "volume": 1234567, "datetime": 1709821800000},  # 2024-03-07 14:30 UTC
        {"open": 175.20, "high": 175.80, "low": 175.10, "close": 175.55,
         "volume": 987654, "datetime": 1709822100000},   # +5min
    ],
    "symbol": "AAPL",
    "empty": False,
}


def test_schwab_mapper_basic():
    out = candles_from_schwab_response(_SCHWAB_PAYLOAD, interval="5m")
    assert len(out) == 2
    assert out[0].open == 175.0 and out[0].close == 175.2
    assert out[0].volume == 1234567
    assert out[0].date == datetime(2024, 3, 7, 14, 30, tzinfo=timezone.utc)


def test_schwab_mapper_handles_empty_envelope():
    assert candles_from_schwab_response(
        {"candles": [], "symbol": "AAPL", "empty": True}, interval="1d") == []
    assert candles_from_schwab_response({"empty": True}, interval="1d") == []
    assert candles_from_schwab_response({}, interval="1d") == []


def test_schwab_mapper_accepts_bare_list():
    out = candles_from_schwab_response(_SCHWAB_PAYLOAD["candles"], interval="5m")
    assert len(out) == 2


def test_schwab_mapper_session_classification_intraday():
    # 14:30 UTC = 09:30 ET DST — opening bell, "regular" session.
    out = candles_from_schwab_response(_SCHWAB_PAYLOAD, interval="5m")
    assert out[0].session == "regular"


# ---------------------------------------------------------------------------
# Alpaca mapper
# ---------------------------------------------------------------------------


_ALPACA_PAYLOAD = {
    "bars": [
        {"t": "2024-03-07T14:30:00Z", "o": 175.00, "h": 175.50,
         "l": 174.80, "c": 175.20, "v": 1234567, "n": 1500, "vw": 175.10},
        {"t": "2024-03-07T14:35:00Z", "o": 175.20, "h": 175.80,
         "l": 175.10, "c": 175.55, "v": 987654, "n": 1100, "vw": 175.30},
    ],
    "symbol": "AAPL",
    "next_page_token": None,
}


def test_alpaca_mapper_basic():
    out = candles_from_alpaca_response(_ALPACA_PAYLOAD, interval="5m")
    assert len(out) == 2
    assert out[0].close == 175.2
    assert out[0].volume == 1234567
    assert out[0].date == datetime(2024, 3, 7, 14, 30, tzinfo=timezone.utc)
    assert out[1].date == datetime(2024, 3, 7, 14, 35, tzinfo=timezone.utc)


def test_alpaca_mapper_empty():
    assert candles_from_alpaca_response({"bars": []}, interval="5m") == []
    assert candles_from_alpaca_response({}, interval="5m") == []


def test_alpaca_mapper_accepts_bare_list():
    out = candles_from_alpaca_response(_ALPACA_PAYLOAD["bars"], interval="5m")
    assert len(out) == 2


# ---------------------------------------------------------------------------
# Polygon mapper
# ---------------------------------------------------------------------------


_POLYGON_PAYLOAD = {
    "ticker": "AAPL",
    "results": [
        {"t": 1709821800000, "o": 175.00, "h": 175.50, "l": 174.80,
         "c": 175.20, "v": 1234567, "n": 1500, "vw": 175.10},
        {"t": 1709822100000, "o": 175.20, "h": 175.80, "l": 175.10,
         "c": 175.55, "v": 987654, "n": 1100, "vw": 175.30},
    ],
    "resultsCount": 2,
    "next_url": None,
}


def test_polygon_mapper_basic():
    out = candles_from_polygon_response(_POLYGON_PAYLOAD, interval="5m")
    assert len(out) == 2
    assert out[0].open == 175.0
    assert out[0].volume == 1234567
    assert out[0].date == datetime(2024, 3, 7, 14, 30, tzinfo=timezone.utc)


def test_polygon_mapper_empty():
    assert candles_from_polygon_response({"results": []}, interval="5m") == []
    assert candles_from_polygon_response({}, interval="5m") == []


def test_polygon_mapper_daily_session_regular():
    daily = {"results": [{"t": 1709821800000, "o": 1, "h": 2, "l": 0.5,
                          "c": 1.5, "v": 100}]}
    out = candles_from_polygon_response(daily, interval="1d")
    assert out[0].session == "regular"


# ---------------------------------------------------------------------------
# Cross-vendor parity — same epoch should produce same Candle date
# ---------------------------------------------------------------------------


def test_cross_vendor_timestamp_parity():
    """Schwab (epoch ms), Polygon (epoch ms), Alpaca (ISO) should all
    map the same physical instant to the same datetime."""
    schwab_out = candles_from_schwab_response(_SCHWAB_PAYLOAD, interval="5m")
    poly_out = candles_from_polygon_response(_POLYGON_PAYLOAD, interval="5m")
    alpaca_out = candles_from_alpaca_response(_ALPACA_PAYLOAD, interval="5m")
    assert schwab_out[0].date == poly_out[0].date == alpaca_out[0].date
