"""Tests for ATR ToD mode (extension of tradinglab.indicators.atr.ATR)."""

from __future__ import annotations

import datetime as _dt
from typing import List

import numpy as np
import pytest

from tradinglab.indicators.atr import ATR
from tradinglab.models import Candle


def _intraday_session(
    day: _dt.date,
    *,
    n_bars: int = 78,                # ~6.5h × 12 / hr — fewer is fine for test speed
    base: float = 100.0,
    range_: float = 1.0,
    step_min: int = 5,
) -> List[Candle]:
    out: List[Candle] = []
    p = base
    t0 = _dt.datetime.combine(day, _dt.time(9, 30))
    for i in range(n_bars):
        out.append(Candle(
            date=t0 + _dt.timedelta(minutes=step_min * i),
            open=p, high=p + range_, low=p - range_, close=p + 0.05 * range_,
            volume=1000, session="regular",
        ))
    return out


def _multi_session_intraday(n_sessions: int, **bar_kwargs) -> List[Candle]:
    bars: List[Candle] = []
    day = _dt.date(2026, 1, 5)
    sess_count = 0
    while sess_count < n_sessions:
        if day.weekday() < 5:  # weekday
            bars.extend(_intraday_session(day, **bar_kwargs))
            sess_count += 1
        day += _dt.timedelta(days=1)
    return bars


def test_default_length_is_14_in_rolling_mode():
    a = ATR()
    assert a.length == 14
    assert a.mode == "rolling"
    assert a.name == "ATR(14)"


def test_default_length_flips_to_20_in_tod_mode():
    a = ATR(mode="tod")
    assert a.length == 20
    assert a.mode == "tod"
    assert a.name == "ATR ToD(20)"


def test_explicit_length_is_honored_in_either_mode():
    assert ATR(length=8, mode="rolling").length == 8
    assert ATR(length=30, mode="tod").length == 30


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        ATR(mode="bogus")


def test_invalid_session_filter_raises():
    with pytest.raises(ValueError):
        ATR(mode="tod", session_filter="weekends_only")


def test_invalid_aggregator_raises():
    with pytest.raises(ValueError):
        ATR(mode="tod", aggregator="mode")


def test_rolling_path_unchanged_by_new_params():
    """Adding mode/session_filter/aggregator must not alter rolling output."""
    bars = _intraday_session(_dt.date(2026, 1, 5), n_bars=60)
    a = ATR(length=14, ma_type="RMA").compute(bars)["atr"]
    b = ATR(
        length=14, ma_type="RMA", mode="rolling",
        session_filter="regular_only", aggregator="mean",
    ).compute(bars)["atr"]
    np.testing.assert_array_equal(np.nan_to_num(a, nan=-1.0),
                                  np.nan_to_num(b, nan=-1.0))


def test_tod_intraday_warmup_returns_nan_for_first_5_sessions():
    """tod-mode intraday emits NaN until at least 5 prior regular sessions."""
    bars = _multi_session_intraday(3, n_bars=10)
    out = ATR(mode="tod", length=20).compute(bars)["atr"]
    assert np.isnan(out).all(), "All values should be NaN below warmup"


def test_tod_intraday_emits_finite_after_warmup_with_constant_range():
    """With constant TR per same-tod slot, the baseline mean equals that TR."""
    bars = _multi_session_intraday(8, n_bars=12, range_=1.5)
    # constant range_ means TR within a session ~ 2*range_ = 3.0 (except bar 0
    # which uses h-l = 3.0 too since no prior close). After 5 prior sessions,
    # tod baseline mean should be ~3.0 for all same-tod slots.
    out = ATR(mode="tod", length=20).compute(bars)["atr"]
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 3.0, atol=0.5)


def test_tod_non_intraday_falls_back_to_rolling_20_mean_of_tr():
    """Daily-bar fallback: ATR ToD = simple 20-bar mean of TR."""
    base = _dt.date(2026, 1, 5)
    bars: List[Candle] = []
    p = 100.0
    for i in range(40):
        bars.append(Candle(
            date=_dt.datetime.combine(
                base + _dt.timedelta(days=i), _dt.time(0, 0)),
            open=p, high=p + 1.0, low=p - 1.0, close=p + 0.1,
            volume=1, session="regular",
        ))
    out = ATR(mode="tod").compute(bars)["atr"]
    # First 20 indices should be NaN (need 20 prior bars).
    assert np.isnan(out[:20]).all()
    # Each later value = mean of TR over the last 20 bars.
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    # All bars constructed identically → TR is constant 2.0 (except tr[0]),
    # so the rolling mean stabilizes at ~2.0.
    np.testing.assert_allclose(finite[-5:], 2.0, atol=0.2)


def test_tod_aggregator_median_differs_from_mean_when_outlier_present():
    """Drop in one outlier session — median ignores it, mean shifts."""
    # 8 calm sessions then 1 outlier; sample at session 9.
    days = []
    base = _dt.date(2026, 1, 5)
    sessions_added = 0
    d = base
    while sessions_added < 9:
        if d.weekday() < 5:
            days.append(d)
            sessions_added += 1
        d += _dt.timedelta(days=1)
    bars: List[Candle] = []
    for idx, day in enumerate(days):
        rng = 5.0 if idx == 4 else 1.0  # session 4 = outlier
        bars.extend(_intraday_session(day, n_bars=12, range_=rng))
    mean_arr = ATR(mode="tod", length=20, aggregator="mean").compute(bars)["atr"]
    med_arr = ATR(mode="tod", length=20, aggregator="median").compute(bars)["atr"]
    # Compare values from final session (index late enough to have warmup).
    finite_mean = mean_arr[np.isfinite(mean_arr)]
    finite_med = med_arr[np.isfinite(med_arr)]
    assert finite_mean.size > 0 and finite_med.size > 0
    # Mean must be pulled higher by the outlier than the median.
    assert finite_mean.mean() > finite_med.mean()
