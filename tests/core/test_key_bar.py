"""Tests for tradinglab.core.key_bar."""

from __future__ import annotations

import datetime as _dt
from typing import List

import numpy as np
import pytest

from tradinglab.core.key_bar import (
    BODY_RATIO_THRESHOLD,
    KEY_BAR_BEAR,
    KEY_BAR_BULL,
    KEY_BAR_NONE,
    KEY_BAR_UNKNOWN,
    LOOKBACK_BARS_NON_INTRADAY,
    RVOL_THRESHOLD,
    TR_THRESHOLD,
    KeyBarArrays,
    compute_key_bar_arrays,
)
from tradinglab.models import Candle

# ---------- intraday fixtures ------------------------------------------------


def _intraday_session(
    day: _dt.date,
    *,
    n_bars: int = 12,
    base_price: float = 100.0,
    range_: float = 1.0,
    body_frac: float = 0.5,
    bull: bool = True,
    volume: int = 1000,
) -> list[Candle]:
    out: list[Candle] = []
    p = base_price
    t0 = _dt.datetime.combine(day, _dt.time(9, 30))
    for i in range(n_bars):
        h = p + range_
        l_ = p - range_
        body = body_frac * (h - l_)
        if bull:
            o, c = p - body / 2.0, p + body / 2.0
        else:
            o, c = p + body / 2.0, p - body / 2.0
        out.append(Candle(
            date=t0 + _dt.timedelta(minutes=5 * i),
            open=o, high=h, low=l_, close=c,
            volume=volume, session="regular",
        ))
    return out


def _calm_then_event(*, body_frac: float, range_mult: float,
                    vol_mult: float, bull: bool) -> list[Candle]:
    """8 calm sessions of 12 bars each, then a 9th session whose
    bar #5 has the requested ``range``/``body_frac``/``volume`` profile."""
    bars: list[Candle] = []
    day = _dt.date(2026, 1, 5)
    sessions_added = 0
    while sessions_added < 8:
        if day.weekday() < 5:
            bars.extend(_intraday_session(day, body_frac=0.3, range_=1.0, volume=1000))
            sessions_added += 1
        day += _dt.timedelta(days=1)
    # 9th session: regular for first 5 bars, event at index 5, then resume
    while day.weekday() >= 5:
        day += _dt.timedelta(days=1)
    s9 = _intraday_session(day, n_bars=12, body_frac=0.3, range_=1.0, volume=1000)
    # mutate bar 5 to be the event candidate
    t = s9[5].date
    rng = 1.0 * range_mult
    body = body_frac * 2 * rng
    if bull:
        o = 100.0 - body / 2.0
        c = 100.0 + body / 2.0
    else:
        o = 100.0 + body / 2.0
        c = 100.0 - body / 2.0
    s9[5] = Candle(
        date=t, open=o, high=100.0 + rng, low=100.0 - rng,
        close=c, volume=int(1000 * vol_mult), session="regular",
    )
    bars.extend(s9)
    return bars, len(bars) - 12 + 5  # absolute index of event bar


# ---------- bare-bones contract tests ---------------------------------------


def test_empty_input_returns_empty_arrays():
    res = compute_key_bar_arrays([])
    assert isinstance(res, KeyBarArrays)
    assert len(res) == 0


def test_short_history_returns_unknown_everywhere():
    bars = _intraday_session(_dt.date(2026, 1, 5), n_bars=10)
    res = compute_key_bar_arrays(bars)
    # No prior sessions for ToD baseline → all unknown.
    assert (res.signed == KEY_BAR_UNKNOWN).all()
    assert (res.bars_since_bull == -1).all()
    assert np.isnan(res.last_bull_high).all()


# ---------- happy-path key-bar detection ------------------------------------


def test_bull_key_bar_is_detected_when_all_three_thresholds_pass():
    bars, idx = _calm_then_event(
        body_frac=0.80,                # > 0.69 ✓
        range_mult=2.0,                # 2× → > 1× baseline TR ✓
        vol_mult=2.0,                  # 2× → > 1.1× rvol ✓
        bull=True,
    )
    res = compute_key_bar_arrays(bars)
    assert res.signed[idx] == KEY_BAR_BULL
    assert res.bars_since_bull[idx] == 0
    assert res.last_bull_high[idx] == bars[idx].high
    assert res.last_bull_low[idx] == bars[idx].low


def test_bear_key_bar_is_detected_with_negative_body():
    bars, idx = _calm_then_event(
        body_frac=0.80, range_mult=2.0, vol_mult=2.0, bull=False,
    )
    res = compute_key_bar_arrays(bars)
    assert res.signed[idx] == KEY_BAR_BEAR
    assert res.bars_since_bear[idx] == 0


# ---------- threshold rejections --------------------------------------------


def test_thin_body_disqualifies_key_bar():
    bars, idx = _calm_then_event(
        body_frac=0.50, range_mult=2.0, vol_mult=2.0, bull=True,
    )
    res = compute_key_bar_arrays(bars)
    assert res.signed[idx] == KEY_BAR_NONE


def test_low_volume_disqualifies_key_bar():
    bars, idx = _calm_then_event(
        body_frac=0.80, range_mult=2.0, vol_mult=0.9, bull=True,
    )
    res = compute_key_bar_arrays(bars)
    assert res.signed[idx] == KEY_BAR_NONE


def test_narrow_range_disqualifies_key_bar():
    bars, idx = _calm_then_event(
        body_frac=0.80, range_mult=0.5, vol_mult=2.0, bull=True,
    )
    res = compute_key_bar_arrays(bars)
    assert res.signed[idx] == KEY_BAR_NONE


# ---------- helper-array semantics ------------------------------------------


def test_bars_since_and_last_extremes_propagate_forward():
    bars, idx = _calm_then_event(
        body_frac=0.80, range_mult=2.0, vol_mult=2.0, bull=True,
    )
    res = compute_key_bar_arrays(bars)
    # Following bars should preserve last_bull_high/low and increment bars_since.
    n = len(bars)
    for j in range(idx + 1, min(idx + 4, n)):
        assert res.bars_since_bull[j] == j - idx
        assert res.last_bull_high[j] == bars[idx].high
        assert res.last_bull_low[j] == bars[idx].low
    # No bear key bar yet.
    assert (res.bars_since_bear == -1).all()


# ---------- non-intraday fallback -------------------------------------------


def test_non_intraday_uses_rolling_20bar_means():
    """Daily fallback: a wide+heavy bar after 25 calm bars should fire."""
    base = _dt.date(2026, 1, 5)
    bars: list[Candle] = []
    for i in range(25):
        bars.append(Candle(
            date=_dt.datetime.combine(
                base + _dt.timedelta(days=i), _dt.time(0, 0)),
            open=100.0, high=100.5, low=99.5, close=100.05,
            volume=1000, session="regular",
        ))
    # Event bar: wider range + heavy volume + fat bull body.
    bars.append(Candle(
        date=_dt.datetime.combine(
            base + _dt.timedelta(days=25), _dt.time(0, 0)),
        open=99.5, high=103.0, low=99.0, close=102.8,
        volume=3000, session="regular",
    ))
    res = compute_key_bar_arrays(bars)
    # Index 25 is the event; baseline uses bars 6..25 (20 bars including event).
    assert res.signed[25] == KEY_BAR_BULL


def test_thresholds_constants_are_canonical():
    """Lock the canonical numbers — changing them is an explicit decision."""
    assert TR_THRESHOLD == 1.0
    assert RVOL_THRESHOLD == 1.1
    assert BODY_RATIO_THRESHOLD == 0.69
    assert LOOKBACK_BARS_NON_INTRADAY == 20
