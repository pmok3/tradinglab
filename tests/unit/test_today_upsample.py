"""Unit tests for ``data/today_upsample.py``."""

from __future__ import annotations

import datetime as dt

import pytest

from tradinglab.data.today_upsample import (
    SUPPORTED_INTERVALS,
    find_best_intraday_source,
    synthesize_today_daily_candle,
    upsample_daily_with_today,
)
from tradinglab.models import Candle


def _intraday_day(date: dt.date, *, n_bars: int = 30, start_vol: int = 1000,
                  open_price: float = 100.0, session: str = "regular",
                  start_hour: int = 9, start_minute: int = 30) -> list[Candle]:
    """Build ``n_bars`` 5-minute candles for ``date`` with deterministic OHLCV."""
    out: list[Candle] = []
    t0 = dt.datetime.combine(date, dt.time(start_hour, start_minute))
    p = open_price
    for i in range(n_bars):
        ts = t0 + dt.timedelta(minutes=5 * i)
        o = p
        c = p + 0.1
        h = max(o, c) + 0.05
        lo = min(o, c) - 0.05
        v = start_vol + i * 10
        out.append(Candle(date=ts, open=o, high=h, low=lo, close=c,
                          volume=v, session=session))
        p = c
    return out


def _daily(date: dt.date, *, open_=50.0, high=55.0, low=49.0,
           close=54.0, volume=1_000_000) -> Candle:
    return Candle(
        date=dt.datetime.combine(date, dt.time(0, 0)),
        open=open_, high=high, low=low, close=close,
        volume=volume, session="regular",
    )


# ----------------------------------------------------------------------
# synthesize_today_daily_candle
# ----------------------------------------------------------------------

def test_synthesize_returns_none_on_empty_input():
    assert synthesize_today_daily_candle([]) is None


def test_synthesize_returns_none_when_no_bars_match_today():
    today = dt.date(2024, 6, 10)
    yesterday = today - dt.timedelta(days=1)
    bars = _intraday_day(yesterday, n_bars=10)
    assert synthesize_today_daily_candle(bars, today_et=today) is None


def test_synthesize_aggregates_today_bars():
    today = dt.date(2024, 6, 10)
    bars = _intraday_day(today, n_bars=10, open_price=100.0, start_vol=1000)
    synth = synthesize_today_daily_candle(bars, today_et=today)
    assert synth is not None
    # O = first bar's open
    assert synth.open == pytest.approx(100.0)
    # C = last bar's close (10 bars of +0.1 → 100.0 + 10*0.1 = 101.0)
    assert synth.close == pytest.approx(101.0)
    # H = max of all highs
    assert synth.high == pytest.approx(max(b.high for b in bars))
    # L = min of all lows
    assert synth.low == pytest.approx(min(b.low for b in bars))
    # V = sum of all volumes
    assert synth.volume == sum(b.volume for b in bars)
    # session
    assert synth.session == "regular"
    # date preserves first bar's timestamp
    assert synth.date == bars[0].date


def test_synthesize_filters_to_regular_session_by_default():
    today = dt.date(2024, 6, 10)
    # 5 pre-market bars (should be excluded) + 10 regular bars
    pre = _intraday_day(today, n_bars=5, session="pre",
                        start_hour=7, start_minute=0,
                        open_price=50.0, start_vol=999)
    regular = _intraday_day(today, n_bars=10, session="regular",
                            open_price=100.0, start_vol=1000)
    synth = synthesize_today_daily_candle(pre + regular, today_et=today)
    assert synth is not None
    # Should match the regular-only synthesis
    expected = synthesize_today_daily_candle(regular, today_et=today)
    assert synth.open == expected.open
    assert synth.close == expected.close
    assert synth.volume == expected.volume
    # The pre-market 999 volume bars MUST NOT be in the sum
    assert synth.volume == sum(b.volume for b in regular)


def test_synthesize_mixed_days_only_picks_today():
    today = dt.date(2024, 6, 10)
    yesterday = today - dt.timedelta(days=1)
    bars = _intraday_day(yesterday, n_bars=78) + _intraday_day(
        today, n_bars=10, open_price=200.0, start_vol=500,
    )
    synth = synthesize_today_daily_candle(bars, today_et=today)
    assert synth is not None
    assert synth.open == pytest.approx(200.0)
    assert synth.volume == sum(b.volume for b in bars if b.date.date() == today)


def test_synthesize_accepts_extended_sessions_when_requested():
    today = dt.date(2024, 6, 10)
    pre = _intraday_day(today, n_bars=5, session="pre",
                        start_hour=7, start_minute=0,
                        open_price=50.0, start_vol=999)
    regular = _intraday_day(today, n_bars=10, session="regular",
                            open_price=100.0, start_vol=1000)
    synth = synthesize_today_daily_candle(
        pre + regular, today_et=today,
        sessions=frozenset({"pre", "regular"}),
    )
    assert synth is not None
    assert synth.volume == sum(
        b.volume for b in pre + regular
    )


# ----------------------------------------------------------------------
# find_best_intraday_source
# ----------------------------------------------------------------------

def test_find_best_intraday_source_picks_finest_available():
    cache = {
        ("yfinance", "AMD", "5m"): _intraday_day(dt.date(2024, 6, 10)),
        ("yfinance", "AMD", "1h"): _intraday_day(dt.date(2024, 6, 10)),
    }
    bars = find_best_intraday_source(cache, source="yfinance", symbol="AMD")
    assert bars is not None
    # 5m is finer than 1h → 5m wins
    assert bars == cache[("yfinance", "AMD", "5m")]


def test_find_best_intraday_source_picks_1m_over_5m():
    cache = {
        ("yfinance", "AMD", "1m"): _intraday_day(dt.date(2024, 6, 10)),
        ("yfinance", "AMD", "5m"): _intraday_day(dt.date(2024, 6, 10)),
    }
    bars = find_best_intraday_source(cache, source="yfinance", symbol="AMD")
    assert bars == cache[("yfinance", "AMD", "1m")]


def test_find_best_intraday_source_returns_none_when_no_intraday():
    cache = {
        ("yfinance", "AMD", "1d"): [_daily(dt.date(2024, 6, 10))],
        ("yfinance", "SPY", "5m"): _intraday_day(dt.date(2024, 6, 10)),
    }
    assert find_best_intraday_source(
        cache, source="yfinance", symbol="AMD",
    ) is None


def test_find_best_intraday_source_returns_none_on_empty_cache():
    assert find_best_intraday_source(
        {}, source="yfinance", symbol="AMD",
    ) is None


def test_find_best_intraday_source_source_specific():
    """Same symbol on different sources upsamples independently."""
    bars = _intraday_day(dt.date(2024, 6, 10))
    cache = {("yfinance", "AMD", "5m"): bars}
    assert find_best_intraday_source(
        cache, source="schwab", symbol="AMD",
    ) is None
    assert find_best_intraday_source(
        cache, source="yfinance", symbol="AMD",
    ) == bars


# ----------------------------------------------------------------------
# upsample_daily_with_today
# ----------------------------------------------------------------------

def test_upsample_appends_when_daily_ends_yesterday():
    today = dt.date(2024, 6, 10)
    yesterday = today - dt.timedelta(days=1)
    daily = [_daily(yesterday - dt.timedelta(days=1)), _daily(yesterday)]
    intraday = _intraday_day(today, n_bars=10, open_price=200.0,
                             start_vol=500)
    out = upsample_daily_with_today(
        daily, intraday_candles=intraday, today_et=today,
    )
    assert len(out) == 3
    # Original two bars intact
    assert out[0] is daily[0]
    assert out[1] is daily[1]
    # Synth bar at the end
    assert out[-1].open == pytest.approx(200.0)


def test_upsample_overwrites_when_daily_already_has_today():
    """Provider that emits a partial today-bar — we overwrite it with our
    intraday-derived running OHLCV (fresher)."""
    today = dt.date(2024, 6, 10)
    partial_today = _daily(today, open_=99.0, close=99.5, volume=100)
    daily = [_daily(today - dt.timedelta(days=1)), partial_today]
    intraday = _intraday_day(today, n_bars=10, open_price=200.0,
                             start_vol=500)
    out = upsample_daily_with_today(
        daily, intraday_candles=intraday, today_et=today,
    )
    assert len(out) == 2
    assert out[-1].open == pytest.approx(200.0)  # synth overwrote partial
    assert out[-1].volume == sum(b.volume for b in intraday)


def test_upsample_noop_when_no_intraday():
    today = dt.date(2024, 6, 10)
    daily = [_daily(today - dt.timedelta(days=1))]
    out = upsample_daily_with_today(
        daily, intraday_candles=None, today_et=today,
    )
    assert out == daily
    # Fresh copy: mutating return doesn't affect input
    out.append(_daily(today))
    assert len(daily) == 1


def test_upsample_noop_when_intraday_has_no_today_bars():
    today = dt.date(2024, 6, 10)
    yesterday = today - dt.timedelta(days=1)
    daily = [_daily(yesterday)]
    intraday = _intraday_day(yesterday, n_bars=10)
    out = upsample_daily_with_today(
        daily, intraday_candles=intraday, today_et=today,
    )
    assert out == daily


def test_upsample_returns_copy_not_input():
    today = dt.date(2024, 6, 10)
    daily = [_daily(today - dt.timedelta(days=1))]
    intraday = _intraday_day(today, n_bars=5)
    out = upsample_daily_with_today(
        daily, intraday_candles=intraday, today_et=today,
    )
    assert out is not daily
    # Mutating out doesn't affect input
    out.clear()
    assert len(daily) == 1


def test_upsample_handles_empty_daily():
    """Edge case: empty daily list (cold cache). Append the synth bar."""
    today = dt.date(2024, 6, 10)
    intraday = _intraday_day(today, n_bars=5, open_price=300.0)
    out = upsample_daily_with_today(
        [], intraday_candles=intraday, today_et=today,
    )
    assert len(out) == 1
    assert out[0].open == pytest.approx(300.0)


def test_supported_intervals_contains_1d_only():
    """Scope guard — weekly/monthly synthesis not implemented yet."""
    assert "1d" in SUPPORTED_INTERVALS
    assert "1wk" not in SUPPORTED_INTERVALS
    assert "1mo" not in SUPPORTED_INTERVALS
    assert "5m" not in SUPPORTED_INTERVALS
