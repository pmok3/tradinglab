"""Self-heal prefetch in ``ChartApp._maybe_upsample_today_daily``.

Audit ``daily-today-upsample``. When the daily synth finds no cached
intraday for a symbol, it must kick a 5m companion prefetch so the synthetic
today-bar can form — even when the daily was served WARM from cache (the SPY
bug: SPY is preloaded as the default compare + ChartStack reference, so its
cold-path companion prefetch never fired and its 1d chart stuck on yesterday).

Bound to a ``SimpleNamespace`` stub (no Tk) so only the prefetch-decision
logic is exercised.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import tradinglab.app as app_mod
from tradinglab.models import Candle

_TODAY = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def _prev_weekday(d: dt.datetime) -> dt.datetime:
    d = d - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d = d - dt.timedelta(days=1)
    return d


_YESTERDAY = _prev_weekday(_TODAY)


def _daily(day: dt.datetime) -> Candle:
    return Candle(date=day.replace(hour=0, minute=0), open=1.0, high=1.0,
                  low=1.0, close=1.0, volume=1, session="regular")


def _five_min_today(n: int = 12) -> list[Candle]:
    out, t, px = [], _TODAY.replace(hour=9, minute=30), 200.0
    for _ in range(n):
        out.append(Candle(date=t, open=px, high=px + 0.2, low=px - 0.2,
                          close=px + 0.05, volume=1000, session="regular"))
        px += 0.05
        t = t + dt.timedelta(minutes=5)
    return out


def _stub(full_cache=None, *, session_open=True):
    calls: list[list[str]] = []
    stub = SimpleNamespace(
        _full_cache=dict(full_cache or {}),
        _intraday_session_open=lambda _now_s: session_open,
        _prefetch_companion_intervals=lambda syms: calls.append(list(syms)),
    )
    stub._maybe_upsample_today_daily = (
        app_mod.ChartApp._maybe_upsample_today_daily.__get__(stub)
    )
    return stub, calls


def test_kicks_prefetch_when_intraday_missing_and_synth_needed():
    stub, calls = _stub(session_open=True)
    daily = [_daily(_prev_weekday(_YESTERDAY)), _daily(_YESTERDAY)]
    out = stub._maybe_upsample_today_daily(
        daily, source="yfinance", symbol="SPY", interval="1d")
    assert calls == [["SPY"]], "must prefetch SPY's 5m companion"
    assert out is daily or out == daily  # unchanged (no intraday yet)


def test_no_prefetch_when_session_closed():
    stub, calls = _stub(session_open=False)
    daily = [_daily(_YESTERDAY)]
    stub._maybe_upsample_today_daily(
        daily, source="yfinance", symbol="SPY", interval="1d")
    assert calls == []


def test_no_prefetch_when_daily_already_has_today():
    stub, calls = _stub(session_open=True)
    daily = [_daily(_YESTERDAY), _daily(_TODAY)]
    stub._maybe_upsample_today_daily(
        daily, source="yfinance", symbol="SPY", interval="1d")
    assert calls == []


def test_no_prefetch_when_interval_not_daily():
    stub, calls = _stub(session_open=True)
    daily = [_daily(_YESTERDAY)]
    stub._maybe_upsample_today_daily(
        daily, source="yfinance", symbol="SPY", interval="5m")
    assert calls == []


def test_no_prefetch_when_no_symbol():
    stub, calls = _stub(session_open=True)
    daily = [_daily(_YESTERDAY)]
    stub._maybe_upsample_today_daily(
        daily, source="yfinance", symbol="", interval="1d")
    assert calls == []


def test_synth_applied_and_no_prefetch_when_intraday_present():
    full_cache = {("yfinance", "SPY", "5m"): _five_min_today()}
    stub, calls = _stub(full_cache=full_cache, session_open=True)
    daily = [_daily(_prev_weekday(_YESTERDAY)), _daily(_YESTERDAY)]
    out = stub._maybe_upsample_today_daily(
        daily, source="yfinance", symbol="SPY", interval="1d")
    assert calls == [], "no prefetch needed when intraday already cached"
    assert out[-1].date.date() == _TODAY.date(), "synth today-bar must be appended"
    assert len(out) == len(daily) + 1
