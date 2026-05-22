"""Tests for the Schwab streaming aggregator + login URL helpers.

Pure-logic tests against :mod:`schwab_aggregator` and
:mod:`schwab_login`. No sockets, no real network.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradinglab.streaming.schwab_aggregator import (
    MinuteBarBuilder,
    chart_equity_to_candle,
    decode_chart_equity_content,
    decode_levelone_content,
)
from tradinglab.streaming.schwab import (
    build_login_request,
    build_subs_request,
    _is_login_ok,
)
from tradinglab.data.schwab_login import build_authorize_url, extract_code


# ---------------------------------------------------------------------------
# decode_*
# ---------------------------------------------------------------------------


def test_decode_levelone_known_fields():
    raw = {"0": "AAPL", "3": 175.42, "8": 1234567, "35": 1709821800000}
    out = decode_levelone_content(raw)
    assert out == {
        "symbol": "AAPL", "last_price": 175.42,
        "total_volume": 1234567, "trade_time_ms": 1709821800000,
    }


def test_decode_levelone_unknown_keys_dropped():
    raw = {"0": "AAPL", "999": "junk", "3": 1.0}
    out = decode_levelone_content(raw)
    assert "999" not in out
    assert "junk" not in out.values()
    assert out["last_price"] == 1.0


def test_decode_chart_equity():
    raw = {"0": "AAPL", "2": 175.0, "3": 175.5, "4": 174.8,
           "5": 175.2, "6": 1234567, "7": 1709821800000}
    out = decode_chart_equity_content(raw)
    assert out["symbol"] == "AAPL"
    assert out["open"] == 175.0
    assert out["close"] == 175.2
    assert out["chart_time_ms"] == 1709821800000


def test_chart_equity_to_candle_full():
    raw = {"0": "AAPL", "2": 175.0, "3": 175.5, "4": 174.8,
           "5": 175.2, "6": 1234567, "7": 1709821800000}
    decoded = decode_chart_equity_content(raw)
    candle = chart_equity_to_candle(decoded)
    assert candle is not None
    assert candle.open == 175.0
    assert candle.close == 175.2
    assert candle.volume == 1234567


def test_chart_equity_to_candle_returns_none_when_incomplete():
    decoded = {"symbol": "AAPL", "open": 1.0, "high": 2.0}  # no low/close/vol/ts
    assert chart_equity_to_candle(decoded) is None


# ---------------------------------------------------------------------------
# MinuteBarBuilder
# ---------------------------------------------------------------------------


def _at(h, m, s=0):
    return datetime(2024, 3, 7, h, m, s)


def test_open_initial_bar_emits_rollover():
    b = MinuteBarBuilder(seed_close=100.0)
    kind, candle = b.open_initial_bar(_at(14, 30, 17))
    assert kind == "rollover"
    assert candle.date == _at(14, 30)  # floored to minute
    assert candle.open == 100.0
    assert candle.close == 100.0


def test_apply_levelone_advances_close_and_envelope():
    b = MinuteBarBuilder(seed_close=100.0)
    b.open_initial_bar(_at(14, 30, 0))
    events = b.apply_levelone({"last_price": 101.5}, now=_at(14, 30, 5))
    assert len(events) == 1
    kind, c = events[0]
    assert kind == "tick"
    assert c.close == 101.5
    assert c.high == 101.5
    assert c.low == 100.0  # seed was the floor


def test_apply_levelone_lower_extends_low():
    b = MinuteBarBuilder(seed_close=100.0)
    b.open_initial_bar(_at(14, 30, 0))
    b.apply_levelone({"last_price": 102.0}, now=_at(14, 30, 1))
    events = b.apply_levelone({"last_price": 99.0}, now=_at(14, 30, 2))
    _, c = events[-1]
    assert c.low == 99.0
    assert c.high == 102.0


def test_apply_levelone_falls_back_to_midpoint_without_trade():
    b = MinuteBarBuilder(seed_close=100.0)
    b.open_initial_bar(_at(14, 30, 0))
    events = b.apply_levelone(
        {"bid_price": 99.0, "ask_price": 101.0}, now=_at(14, 30, 1))
    _, c = events[0]
    assert c.close == 100.0  # midpoint


def test_apply_levelone_volume_baselines_cumulative():
    b = MinuteBarBuilder(seed_close=100.0)
    b.open_initial_bar(_at(14, 30, 0))
    # First volume sets baseline; per-bar = 0.
    e1 = b.apply_levelone({"total_volume": 5_000_000}, now=_at(14, 30, 1))
    _, c1 = e1[0]
    assert c1.volume == 0
    # Subsequent: per-bar volume = current - baseline.
    e2 = b.apply_levelone({"total_volume": 5_001_500}, now=_at(14, 30, 2))
    _, c2 = e2[0]
    assert c2.volume == 1500


def test_apply_levelone_no_emit_when_no_price_or_volume():
    b = MinuteBarBuilder(seed_close=100.0)
    b.open_initial_bar(_at(14, 30, 0))
    events = b.apply_levelone({"trade_time_ms": 123}, now=_at(14, 30, 1))
    # Heartbeat-y update — no tick.
    assert events == []


def test_minute_rollover_emits_rollover_seeded_from_prior_close():
    b = MinuteBarBuilder(seed_close=100.0)
    b.open_initial_bar(_at(14, 30, 0))
    b.apply_levelone({"last_price": 101.0}, now=_at(14, 30, 30))
    # Cross into 14:31.
    events = b.apply_levelone({"last_price": 102.0}, now=_at(14, 31, 5))
    kinds = [e[0] for e in events]
    assert kinds == ["rollover", "tick"]
    rollover_candle = events[0][1]
    tick_candle = events[1][1]
    # Rollover seeded from 101.0 (prior close).
    assert rollover_candle.open == 101.0
    assert rollover_candle.date == _at(14, 31)
    # Tick on the new bar shows the new price.
    assert tick_candle.close == 102.0
    assert tick_candle.date == _at(14, 31)


def test_multi_minute_jump_emits_one_rollover_per_minute():
    b = MinuteBarBuilder(seed_close=100.0)
    b.open_initial_bar(_at(14, 30, 0))
    b.apply_levelone({"last_price": 101.0}, now=_at(14, 30, 10))
    # Jump 3 minutes ahead in a single update.
    events = b.apply_levelone({"last_price": 99.0}, now=_at(14, 33, 1))
    kinds = [e[0] for e in events]
    assert kinds == ["rollover", "rollover", "rollover", "tick"]
    # The final tick lands on the 14:33 bar.
    assert events[-1][1].date == _at(14, 33)


def test_maybe_rollover_handles_quiet_minute():
    b = MinuteBarBuilder(seed_close=100.0)
    b.open_initial_bar(_at(14, 30, 0))
    events = b.maybe_rollover(_at(14, 31, 30))
    kinds = [e[0] for e in events]
    assert kinds == ["rollover"]
    # Open of new minute = seed (no ticks happened).
    assert events[0][1].open == 100.0


def test_maybe_rollover_before_initial_opens_bar():
    b = MinuteBarBuilder(seed_close=42.0)
    events = b.maybe_rollover(_at(14, 30, 5))
    assert len(events) == 1
    assert events[0][0] == "rollover"
    assert events[0][1].open == 42.0


def test_session_classification_propagates_to_emitted_candles():
    b = MinuteBarBuilder(seed_close=100.0)
    # 03:00 ET-ish (whatever hour, point is classify_session is called).
    b.open_initial_bar(_at(3, 0, 0))
    events = b.apply_levelone({"last_price": 101.0}, now=_at(3, 0, 5))
    _, c = events[0]
    assert c.session == "pre"


# ---------------------------------------------------------------------------
# build_login_request / build_subs_request / _is_login_ok
# ---------------------------------------------------------------------------


_STREAMER_INFO = {
    "schwabClientCustomerId": "CUST1",
    "schwabClientCorrelId": "CORR1",
    "schwabClientChannel": "CH1",
    "schwabClientFunctionId": "FN1",
    "streamerSocketUrl": "wss://example/ws",
}


def test_build_login_request_shape():
    req = build_login_request(_STREAMER_INFO, "ACCESS-TOKEN", request_id=0)
    assert req["service"] == "ADMIN"
    assert req["command"] == "LOGIN"
    assert req["requestid"] == "0"
    assert req["SchwabClientCustomerId"] == "CUST1"
    assert req["parameters"]["Authorization"] == "ACCESS-TOKEN"
    assert req["parameters"]["SchwabClientChannel"] == "CH1"


def test_build_subs_request_uses_subs_for_first_then_add():
    first = build_subs_request(
        "LEVELONE_EQUITIES", ["AAPL", "MSFT"], ["0", "3"],
        _STREAMER_INFO, request_id=1)
    assert first["command"] == "SUBS"
    assert first["parameters"]["keys"] == "AAPL,MSFT"
    assert first["parameters"]["fields"] == "0,3"

    later = build_subs_request(
        "CHART_EQUITY", ["TSLA"], ["0", "5"],
        _STREAMER_INFO, request_id=5)
    assert later["command"] == "ADD"


def test_is_login_ok_accepts_code_zero():
    msg = {"response": [{"service": "ADMIN", "command": "LOGIN",
                          "content": {"code": 0, "msg": "ok"}}]}
    assert _is_login_ok(msg)


def test_is_login_ok_rejects_nonzero():
    msg = {"response": [{"service": "ADMIN", "command": "LOGIN",
                          "content": {"code": 3, "msg": "auth failed"}}]}
    assert not _is_login_ok(msg)


def test_is_login_ok_rejects_unrelated_response():
    assert not _is_login_ok({"response": [{"service": "OTHER"}]})
    assert not _is_login_ok(None)
    assert not _is_login_ok({})


# ---------------------------------------------------------------------------
# Login script helpers
# ---------------------------------------------------------------------------


def test_build_authorize_url_has_required_params():
    url = build_authorize_url("APPKEY", "https://127.0.0.1")
    assert url.startswith("https://api.schwabapi.com/v1/oauth/authorize?")
    assert "client_id=APPKEY" in url
    assert "redirect_uri=https%3A%2F%2F127.0.0.1" in url
    assert "response_type=code" in url


def test_extract_code_happy_path():
    url = "https://127.0.0.1/?code=ABC123&session=xx"
    assert extract_code(url) == "ABC123"


def test_extract_code_missing_raises():
    with pytest.raises(ValueError, match="no 'code'"):
        extract_code("https://127.0.0.1/?session=xx")


def test_extract_code_strips_whitespace():
    assert extract_code("  https://127.0.0.1/?code=Z  ") == "Z"
