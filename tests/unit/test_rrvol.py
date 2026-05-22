"""Unit tests for the unified RRVOL indicator."""

from __future__ import annotations

import datetime as dt
import random
from typing import List

import numpy as np
import pytest

from tradinglab.core import reference_data as rd
from tradinglab.core.bars import Bars
from tradinglab.core.render_context import render_context
from tradinglab.indicators.rrvol import RRVOL
from tradinglab.indicators.rvol import RVOL
from tradinglab.models import Candle

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry():
    rd.clear()
    yield
    rd.clear()


def _intraday_candles(n_days: int = 25, bars_per_day: int = 30,
                      seed: int = 7, vol_scale: float = 1.0) -> list[Candle]:
    """Synthesize ``n_days`` regular sessions of 5m bars."""
    rng = random.Random(seed)
    out: list[Candle] = []
    base = 100.0
    for d in range(n_days):
        # Skip weekends — keep date arithmetic simple.
        day = dt.date(2024, 1, 2) + dt.timedelta(days=d)
        if day.weekday() >= 5:
            continue
        t0 = dt.datetime.combine(day, dt.time(9, 30))
        for i in range(bars_per_day):
            ts = t0 + dt.timedelta(minutes=5 * i)
            o = base
            c = max(0.5, base + rng.uniform(-0.5, 0.5))
            h = max(o, c) + abs(rng.uniform(0, 0.3))
            lo = min(o, c) - abs(rng.uniform(0, 0.3))
            v = int(rng.randint(1000, 5000) * vol_scale)
            out.append(Candle(date=ts, open=o, high=h, low=lo, close=c,
                              volume=v, session="regular"))
            base = c
    return out


def _daily_candles(n: int = 50, seed: int = 11,
                   vol_scale: float = 1.0) -> list[Candle]:
    rng = random.Random(seed)
    out: list[Candle] = []
    base = 100.0
    for i in range(n):
        ts = dt.datetime(2024, 1, 2) + dt.timedelta(days=i)
        o = base
        c = max(0.5, base + rng.uniform(-1, 1))
        h = max(o, c) + abs(rng.uniform(0, 0.5))
        lo = min(o, c) - abs(rng.uniform(0, 0.5))
        v = int(rng.randint(10_000, 50_000) * vol_scale)
        out.append(Candle(date=ts, open=o, high=h, low=lo, close=c,
                          volume=v, session="regular"))
        base = c
    return out


def _simple_rrvol(length: int = 10, **kwargs) -> RRVOL:
    return RRVOL(mode="simple", length=length, **kwargs)


# ----------------------------------------------------------------------
# Behavioural tests
# ----------------------------------------------------------------------


def test_returns_nan_when_no_render_context():
    bars = Bars.from_candles(_intraday_candles())
    out = _simple_rrvol().compute_arr(bars)
    assert np.all(np.isnan(out["rvol"]))


def test_returns_nan_when_spy_not_warmed():
    bars = Bars.from_candles(_intraday_candles())
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(bars)
    assert np.all(np.isnan(out["rvol"]))


def test_primary_equals_spy_emits_flat_one():
    """When primary symbol IS SPY, ratio is identically 1.0 wherever
    primary RVOL is finite — no SPY fetch needed."""
    candles = _intraday_candles(n_days=15)
    bars = Bars.from_candles(candles)
    parent = RVOL(mode="simple", length=10)
    parent_out = parent.compute_arr(bars)["rvol"]
    with render_context(interval="5m", source="yfinance", primary_symbol="SPY"):
        out = _simple_rrvol().compute_arr(bars)["rvol"]
    finite = np.isfinite(parent_out)
    assert np.all(np.isfinite(out[finite]))
    np.testing.assert_array_equal(out[finite], np.ones(int(finite.sum())))
    assert np.all(np.isnan(out[~finite]))


def test_identical_volume_streams_yield_one():
    """Same OHLCV on primary and SPY → ratio = 1.0 across the board."""
    candles = _intraday_candles(n_days=15, seed=3)
    bars = Bars.from_candles(candles)
    rd.set_reference_bars("yfinance", "SPY", "5m", bars)
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(bars)["rvol"]
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 1.0, rtol=1e-12)


def test_double_volume_yields_one_when_baselines_double_too():
    """If SPY's volumes are 2x primary's at every bar (incl baseline),
    RVOL is identical and ratio = 1.0."""
    candles_a = _intraday_candles(n_days=15, seed=5, vol_scale=1.0)
    candles_b = _intraday_candles(n_days=15, seed=5, vol_scale=2.0)
    bars_a = Bars.from_candles(candles_a)
    bars_b = Bars.from_candles(candles_b)
    rd.set_reference_bars("yfinance", "SPY", "5m", bars_b)
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(bars_a)["rvol"]
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 1.0, rtol=1e-9)


def test_unmatched_timestamps_emit_nan():
    """Primary bars whose timestamps don't appear in SPY get NaN."""
    pri = _intraday_candles(n_days=15, seed=2)
    spy = [
        Candle(date=c.date.replace(year=c.date.year + 5),
               open=c.open, high=c.high,
               low=c.low, close=c.close, volume=c.volume, session=c.session)
        for c in pri
    ]
    rd.set_reference_bars("yfinance", "SPY", "5m", Bars.from_candles(spy))
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(
            Bars.from_candles(pri))["rvol"]
    assert np.all(np.isnan(out))


def test_partial_overlap_alignment():
    """Primary has a strict superset of SPY's timestamps. The first
    half (no SPY match) is NaN; the second half has finite ratios."""
    pri = _intraday_candles(n_days=20, seed=8)
    half = len(pri) // 2
    spy = pri[half:]
    rd.set_reference_bars("yfinance", "SPY", "5m", Bars.from_candles(spy))
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(
            Bars.from_candles(pri))["rvol"]
    assert np.all(np.isnan(out[:half]))
    second = out[half:]
    finite = second[np.isfinite(second)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 1.0, rtol=1e-9)


def test_zero_spy_baseline_emits_zero_not_inf():
    """If SPY's RVOL at the matched bar is 0.0, RRVOL emits 0.0 — not inf."""
    pri = _intraday_candles(n_days=15, seed=6)
    spy = list(pri)
    last = spy[-1]
    spy[-1] = Candle(date=last.date, open=last.open, high=last.high,
                     low=last.low, close=last.close, volume=0,
                     session=last.session)
    rd.set_reference_bars("yfinance", "SPY", "5m", Bars.from_candles(spy))
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(
            Bars.from_candles(pri))["rvol"]
    assert out[-1] == 0.0


def test_daily_interval_alignment():
    """RRVOL simple-rolling works on 1d as well."""
    pri = _daily_candles(n=60, seed=13, vol_scale=1.0)
    spy = _daily_candles(n=60, seed=13, vol_scale=3.0)  # same dates
    rd.set_reference_bars("yfinance", "SPY", "1d", Bars.from_candles(spy))
    with render_context(interval="1d", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(
            Bars.from_candles(pri))["rvol"]
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 1.0, rtol=1e-9)


def test_source_aware_keying_does_not_leak():
    """If only 'synthetic' SPY is cached, an indicator running under
    the 'yfinance' source must not pick it up."""
    bars = Bars.from_candles(_intraday_candles(n_days=15, seed=4))
    rd.set_reference_bars("synthetic", "SPY", "5m", bars)
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(bars)["rvol"]
    assert np.all(np.isnan(out))


@pytest.mark.parametrize("mode", ["simple", "time_of_day", "cumulative"])
def test_each_mode_runs_end_to_end(mode):
    candles = _intraday_candles(n_days=25, seed=20)
    bars = Bars.from_candles(candles)
    rd.set_reference_bars("yfinance", "SPY", "5m", bars)
    ind = RRVOL(mode=mode, length=10)
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = ind.compute_arr(bars)["rvol"]
    finite = out[np.isfinite(out)]
    assert finite.size > 0
    np.testing.assert_allclose(finite, 1.0, rtol=1e-9)


def test_cache_miss_schedules_provider():
    calls: list[tuple] = []
    rd.set_provider(lambda s, sym, iv: calls.append((s, sym, iv)))
    bars = Bars.from_candles(_intraday_candles(n_days=15))
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = _simple_rrvol().compute_arr(bars)["rvol"]
    assert np.all(np.isnan(out))
    assert calls == [("yfinance", "SPY", "5m")]


# ----------------------------------------------------------------------
# Z-score support (NEW capability)
# ----------------------------------------------------------------------


def test_rrvol_z_score_pane_group():
    """``z_score=True`` routes RRVOL onto the rvol_z pane."""
    assert RRVOL.pane_group_for({"z_score": True}) == "rvol_z"
    assert RRVOL.pane_group_for({"z_score": False}) == "rvol"


def test_rrvol_z_score_constant_ratio_yields_zero_or_nan():
    """Identical primary/SPY → ratio ≡ 1.0 → z stays NaN (zero stddev)."""
    candles = _intraday_candles(n_days=15, seed=3)
    bars = Bars.from_candles(candles)
    rd.set_reference_bars("yfinance", "SPY", "5m", bars)
    with render_context(interval="5m", source="yfinance", primary_symbol="AMD"):
        out = RRVOL(mode="simple", length=10, z_score=True).compute_arr(bars)["rvol"]
    finite = out[np.isfinite(out)]
    if finite.size:
        assert float(np.nanmax(np.abs(finite))) < 1e-6


def test_rrvol_z_score_length_validation():
    """``length < 2`` rejected when ``z_score=True`` (need 2+ samples)."""
    with pytest.raises(ValueError):
        RRVOL(mode="simple", length=1, z_score=True)
