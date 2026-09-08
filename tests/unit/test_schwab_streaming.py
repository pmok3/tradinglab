"""Tests for the Schwab streaming aggregator + login URL helpers.

Pure-logic tests against :mod:`schwab_aggregator` and
:mod:`schwab_login`. No sockets, no real network.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradinglab.core.timezones import ET
from tradinglab.data.schwab_login import build_authorize_url, extract_code
from tradinglab.streaming.schwab import (
    _is_login_ok,
    build_login_request,
    build_subs_request,
)
from tradinglab.streaming.schwab_aggregator import (
    MinuteBarBuilder,
    chart_equity_to_candle,
    decode_chart_equity_content,
    decode_levelone_content,
)

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
    raw = {"key": "AAPL", "seq": 0, "1": 779, "2": 175.0, "3": 175.5, "4": 174.8,
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
    return datetime(2024, 3, 7, h, m, s, tzinfo=ET)


def _trade(at, price=100.0, volume=None):
    out = {"last_price": price, "trade_time_ms": int(at.timestamp() * 1000)}
    if volume is not None:
        out["total_volume"] = volume
    return out


def test_first_actual_trade_emits_rollover_without_a_seed():
    b = MinuteBarBuilder()
    kind, candle = b.apply_levelone(_trade(_at(14, 30, 17)))[0]
    assert kind == "rollover"
    assert candle.date == _at(14, 30)  # floored to minute
    assert candle.open == 100.0
    assert candle.close == 100.0


def test_apply_levelone_advances_close_and_envelope():
    b = MinuteBarBuilder()
    b.apply_levelone(_trade(_at(14, 30)))
    events = b.apply_levelone(_trade(_at(14, 30, 5), 101.5))
    assert len(events) == 1
    kind, c = events[0]
    assert kind == "tick"
    assert c.close == 101.5
    assert c.high == 101.5
    assert c.low == 100.0


def test_apply_levelone_lower_extends_low():
    b = MinuteBarBuilder()
    b.apply_levelone(_trade(_at(14, 30), 102))
    events = b.apply_levelone(_trade(_at(14, 30, 2), 99))
    _, c = events[-1]
    assert c.low == 99.0
    assert c.high == 102.0


def test_bid_ask_only_never_becomes_a_trade():
    b = MinuteBarBuilder()
    assert b.apply_levelone({"bid_price": 99, "ask_price": 101}, now=_at(14, 30)) == []
    b.apply_levelone(_trade(_at(14, 30)))
    events = b.apply_levelone(
        {"bid_price": 199.0, "ask_price": 201.0}, now=_at(14, 31))
    assert events == []
    assert b._bar.close == 100


def test_apply_levelone_volume_baselines_cumulative():
    b = MinuteBarBuilder()
    # First volume sets baseline; per-bar = 0.
    e1 = b.apply_levelone(_trade(_at(14, 30, 1), volume=5_000_000))
    _, c1 = e1[0]
    assert c1.volume == 0
    # Subsequent: per-bar volume = current - baseline.
    e2 = b.apply_levelone({"total_volume": 5_001_500})
    _, c2 = e2[0]
    assert c2.volume == 1500


def test_apply_levelone_no_emit_when_no_price_or_volume():
    b = MinuteBarBuilder()
    events = b.apply_levelone({"close_price": 123}, now=_at(14, 30, 1))
    # Heartbeat-y update — no tick.
    assert events == []


def test_minute_rollover_uses_first_observed_trade_and_preserves_volume_delta():
    b = MinuteBarBuilder()
    b.apply_levelone(_trade(_at(14, 30, 30), 101, 1000))
    events = b.apply_levelone(_trade(_at(14, 31, 5), 102, 1150))
    assert [e[0] for e in events] == ["rollover"]
    c = events[0][1]
    assert c.open == c.close == c.high == c.low == 102
    assert c.date == _at(14, 31)
    assert c.volume == 150


def test_multi_hour_gap_emits_only_observed_minute_and_rebaselines_volume():
    b = MinuteBarBuilder()
    b.apply_levelone(_trade(_at(9, 30), 101, 1000))
    events = b.apply_levelone(_trade(_at(14, 33, 1), 99, 50000))
    assert [e[0] for e in events] == ["rollover"]
    assert events[0][1].date == _at(14, 33)
    assert events[0][1].volume == 0


@pytest.mark.parametrize("now", [
    _at(14, 31), _at(21, 0), _at(14, 30) + timedelta(days=2),
    datetime(2024, 12, 25, 12, tzinfo=ET),
])
def test_clock_never_fabricates_quiet_offhours_weekend_or_holiday_bars(now):
    b = MinuteBarBuilder()
    assert b.maybe_rollover(now) == []
    b.apply_levelone(_trade(_at(14, 30)))
    assert b.maybe_rollover(now) == []


def test_delta_timestamp_reuses_last_trade_price_not_bid_ask():
    b = MinuteBarBuilder()
    b.apply_levelone(_trade(_at(14, 30)))
    events = b.apply_levelone({"trade_time_ms": int(_at(14, 31).timestamp() * 1000)})
    assert events[0][0] == "rollover"
    assert events[0][1].open == 100


def test_session_classification_propagates_to_emitted_candles():
    b = MinuteBarBuilder()
    events = b.apply_levelone(_trade(_at(4, 0, 5)))
    _, c = events[0]
    assert c.session == "pre"


def test_receive_time_never_rewrites_historical_trade_timestamp():
    b = MinuteBarBuilder()
    _, c = b.apply_levelone(_trade(_at(9, 30)), now=_at(14, 30))[0]
    assert c.date == _at(9, 30)
    assert c.date.tzinfo is ET


def test_day_reset_and_out_of_order_updates_do_not_double_count_volume():
    b = MinuteBarBuilder()
    b.apply_levelone(_trade(_at(14, 30), volume=1000))
    assert b.apply_levelone(_trade(_at(14, 30, 10), volume=1200))[0][1].volume == 200
    assert b.apply_levelone(_trade(_at(14, 30, 20), volume=1100))[0][1].volume == 200
    assert b.apply_levelone(_trade(_at(14, 30, 30), volume=1250))[0][1].volume == 250
    assert b.apply_levelone(_trade(_at(14, 30, 5), 999, 99000)) == []
    tomorrow = _at(9, 30) + timedelta(days=1)
    assert b.apply_levelone(_trade(tomorrow, 102, 200))[0][1].volume == 0
    assert b.apply_levelone(_trade(tomorrow + timedelta(seconds=1), 103, 250))[0][1].volume == 50


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), None, "bad"])
def test_invalid_initial_price_never_emits_a_zero_placeholder(price):
    b = MinuteBarBuilder()
    assert b.apply_levelone(_trade(_at(14, 30), price)) == []
    assert b._bar is None


def test_missing_timestamp_never_uses_receive_time():
    b = MinuteBarBuilder()
    assert b.apply_levelone({"last_price": 100}, now=_at(14, 30)) == []
    events = b.apply_levelone({"trade_time_ms": int(_at(14, 30).timestamp() * 1000)})
    assert events[0][1].close == 100


def test_split_initial_image_keeps_volume_baseline_until_price_and_time_arrive():
    b = MinuteBarBuilder()
    assert b.apply_levelone({"total_volume": 1000}) == []
    assert b.apply_levelone({"last_price": 100}) == []
    assert b.apply_levelone({"trade_time_ms": int(_at(14, 30).timestamp() * 1000)})[0][1].volume == 0
    assert b.apply_levelone({"total_volume": 1100})[0][1].volume == 100


@pytest.mark.parametrize("at", [_at(3, 59), _at(20, 0), _at(14, 30) + timedelta(days=2)])
def test_invalid_equity_session_snapshot_does_not_open_bar(at):
    assert MinuteBarBuilder().apply_levelone(_trade(at)) == []


@pytest.mark.parametrize("at,session", [
    (_at(9, 30), "regular"), (_at(16, 0), "post"),
    (datetime(2024, 7, 1, 9, 30, tzinfo=ET), "regular"),
])
def test_chart_session_classification_uses_et_in_winter_and_summer(at, session):
    c = chart_equity_to_candle(decode_chart_equity_content({
        "key": "AAPL", "seq": 0, "1": 779, "2": 100, "3": 102, "4": 99,
        "5": 101, "6": 50, "7": int(at.timestamp() * 1000), "8": 19859,
    }))
    assert c.date == at
    assert c.date.tzinfo is ET
    assert c.session == session


@pytest.mark.parametrize("field,value", [("2", float("nan")), ("3", 50), ("6", -1), ("7", "bad")])
def test_invalid_chart_bar_is_rejected(field, value):
    raw = {"key": "AAPL", "2": 100, "3": 102, "4": 99, "5": 101, "6": 50,
           "7": int(_at(14, 30).timestamp() * 1000)}
    raw[field] = value
    assert chart_equity_to_candle(decode_chart_equity_content(raw)) is None


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


def test_build_subs_request_command_is_independent_of_request_id():
    first = build_subs_request(
        "LEVELONE_EQUITIES", ["AAPL", "MSFT"], ["0", "3"],
        _STREAMER_INFO, request_id=99, command="SUBS")
    assert first["command"] == "SUBS"
    assert first["parameters"]["keys"] == "AAPL,MSFT"
    assert first["parameters"]["fields"] == "0,3"

    later = build_subs_request(
        "CHART_EQUITY", ["TSLA"], ["0", "5"],
        _STREAMER_INFO, request_id=1, command="ADD")
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
