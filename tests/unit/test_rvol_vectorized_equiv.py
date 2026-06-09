"""Equivalence: vectorized RVOL time-of-day + rolling z-score vs the prior
per-bar Python loops (audit compute #5).

The vectorized ``_compute_time_of_day`` (session×tod-key matrix + rolling
session aggregation) and ``_rolling_zscore`` (NaN-padded sliding window)
must match the original loops bit-for-bit on the median path and within
float64 round-off on the mean/std path (summation-order ULPs only). These
tests pin both against inline copies of the original loop logic, fuzzed
over many multi-day intraday datasets incl. missing tod-keys + duplicate
(DST) timestamps + NaN volumes.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators import rvol as R
from tradinglab.models import Candle

_ET = ZoneInfo("America/New_York")
_MIN = R._MIN_WARMUP_SESSIONS


def _intraday_bars(days, *, seed, drop_p=0.0, dup_p=0.0, nan_vol_p=0.0):
    rng = np.random.default_rng(seed)
    out: list[Candle] = []
    price = 100.0
    d0 = datetime(2024, 6, 3, 9, 30, tzinfo=_ET)  # Monday
    for d in range(days):
        start = d0 + timedelta(days=d)
        while start.weekday() >= 5:
            start += timedelta(days=1)
        t = start
        for _b in range(78):  # 5-min RTH 09:30..16:00
            if drop_p and rng.random() < drop_p:
                t += timedelta(minutes=5)
                continue
            c = price + float(rng.normal(0, 0.5))
            v = float(rng.integers(100, 5000))
            if nan_vol_p and rng.random() < nan_vol_p:
                v = float("nan")
            out.append(Candle(date=t, open=price, high=max(price, c) + 0.2,
                              low=min(price, c) - 0.2, close=c, volume=v,
                              session="regular"))
            if dup_p and rng.random() < dup_p:
                # duplicate-timestamp bar (DST-style); same tod-key
                out.append(Candle(date=t, open=c, high=c + 0.1, low=c - 0.1,
                                  close=c, volume=float(rng.integers(100, 5000)),
                                  session="regular"))
            price = c
            t += timedelta(minutes=5)
    return Bars.from_candles(out)


# --- inline reference: the ORIGINAL per-bar loops ------------------------

def _ref_time_of_day(bars, length, aggregator):
    n = len(bars)
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0 or not R.is_intraday_np(bars):
        return out
    admit_mask = R.session_filter_mask_np(bars, "regular_only")
    groups = R.session_groups_np(bars, regular_only=True)
    if len(groups) < _MIN + 1:
        return out
    vol = bars.volume
    tk = R.tod_key_np(bars)
    per_session = []
    for grp in groups:
        keyed = {}
        for idx in grp:
            if not admit_mask[idx]:
                continue
            v = float(vol[idx])
            if not np.isfinite(v):
                v = 0.0
            k = int(tk[idx])
            keyed[k] = keyed.get(k, 0.0) + v
        per_session.append(keyed)
    for s in range(_MIN, len(groups)):
        cur_grp = groups[s]
        prior_window = per_session[max(0, s - length):s]
        for idx in cur_grp:
            if not admit_mask[idx]:
                continue
            k = int(tk[idx])
            baseline = [d[k] for d in prior_window if k in d]
            if len(baseline) < _MIN:
                continue
            v_now = float(vol[idx])
            if not np.isfinite(v_now):
                v_now = 0.0
            denom = R._aggregate(np.asarray(baseline, dtype=np.float64), aggregator)
            out[idx] = 0.0 if denom <= 0.0 else v_now / denom
    return out


import bisect


def _ref_cumulative(bars, length, aggregator):
    n = len(bars)
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0 or not R.is_intraday_np(bars):
        return out
    admit_mask = R.session_filter_mask_np(bars, "regular_only")
    groups = R.session_groups_np(bars, regular_only=True)
    if len(groups) < _MIN + 1:
        return out
    vol = bars.volume
    tk = R.tod_key_np(bars)
    session_cum = []
    for grp in groups:
        cum = 0.0
        keyed = {}
        for idx in grp:
            if not admit_mask[idx]:
                continue
            v = float(vol[idx])
            if not np.isfinite(v):
                v = 0.0
            cum += v
            keyed[int(tk[idx])] = cum
        session_cum.append(keyed)
    sorted_keyed = []
    for keyed in session_cum:
        if not keyed:
            sorted_keyed.append(([], []))
            continue
        items = sorted(keyed.items())
        sorted_keyed.append(([k for k, _ in items], [v for _, v in items]))
    for s in range(_MIN, len(groups)):
        cur_keyed = session_cum[s]
        prior_window = sorted_keyed[max(0, s - length):s]
        for idx in groups[s]:
            if not admit_mask[idx]:
                continue
            k = int(tk[idx])
            today_cum = cur_keyed.get(k)
            if today_cum is None:
                continue
            baseline = []
            for keys_list, vals_list in prior_window:
                if not keys_list:
                    continue
                pos = bisect.bisect_right(keys_list, k) - 1
                if pos >= 0:
                    baseline.append(vals_list[pos])
            if len(baseline) < _MIN:
                continue
            denom = R._aggregate(np.asarray(baseline, dtype=np.float64), aggregator)
            out[idx] = 0.0 if denom <= 0.0 else float(today_cum) / denom
    return out


def _ref_zscore(rvol, length):
    n = rvol.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0 or length < 2:
        return out
    L = int(length)
    for i in range(n):
        if not np.isfinite(rvol[i]):
            continue
        lo = max(0, i - L + 1)
        w = rvol[lo:i + 1]
        f = w[np.isfinite(w)]
        if f.size < 2:
            continue
        m = float(f.mean())
        s = float(f.std(ddof=1))
        if not np.isfinite(s) or s <= 0.0:
            continue
        out[i] = (float(rvol[i]) - m) / s
    return out


# --- time-of-day equivalence ---------------------------------------------

@pytest.mark.parametrize("aggregator", ["mean", "median"])
@pytest.mark.parametrize("seed", list(range(12)))
def test_time_of_day_matches_reference(aggregator, seed):
    bars = _intraday_bars(
        days=int(10 + seed % 6), seed=100 + seed,
        drop_p=0.08 * (seed % 3), dup_p=0.04 * (seed % 2),
        nan_vol_p=0.05 * (seed % 2),
    )
    for length in (5, 10, 20):
        ref = _ref_time_of_day(bars, length, aggregator)
        got = R._compute_time_of_day(bars, length, aggregator, "regular_only")
        # median path is exact; mean path matches within summation-order ULPs.
        assert np.array_equal(np.isnan(ref), np.isnan(got)), \
            f"NaN mask differs (agg={aggregator}, L={length}, seed={seed})"
        np.testing.assert_allclose(got, ref, rtol=1e-9, atol=1e-9, equal_nan=True,
                                   err_msg=f"agg={aggregator} L={length} seed={seed}")


def test_time_of_day_too_few_sessions_all_nan():
    bars = _intraday_bars(days=3, seed=7)
    got = R._compute_time_of_day(bars, 20, "mean", "regular_only")
    assert np.all(np.isnan(got))


def test_time_of_day_non_intraday_all_nan():
    # daily bars → not intraday → all NaN
    base = datetime(2024, 1, 1)
    cs = [Candle(date=base + timedelta(days=i), open=1, high=2, low=0.5,
                 close=1.5, volume=100, session="regular") for i in range(40)]
    bars = Bars.from_candles(cs)
    got = R._compute_time_of_day(bars, 20, "mean", "regular_only")
    assert np.all(np.isnan(got))


# --- cumulative equivalence ----------------------------------------------

@pytest.mark.parametrize("aggregator", ["mean", "median"])
@pytest.mark.parametrize("seed", list(range(12)))
def test_cumulative_matches_reference(aggregator, seed):
    bars = _intraday_bars(
        days=int(10 + seed % 6), seed=300 + seed,
        drop_p=0.08 * (seed % 3), dup_p=0.04 * (seed % 2),
        nan_vol_p=0.05 * (seed % 2),
    )
    for length in (5, 10, 20):
        ref = _ref_cumulative(bars, length, aggregator)
        got = R._compute_cumulative(bars, length, aggregator, "regular_only")
        assert np.array_equal(np.isnan(ref), np.isnan(got)), \
            f"NaN mask differs (agg={aggregator}, L={length}, seed={seed})"
        np.testing.assert_allclose(got, ref, rtol=1e-9, atol=1e-9, equal_nan=True,
                                   err_msg=f"agg={aggregator} L={length} seed={seed}")


# --- rolling z-score equivalence -----------------------------------------

@pytest.mark.parametrize("seed", list(range(10)))
def test_zscore_matches_reference(seed):
    rng = np.random.default_rng(500 + seed)
    rvol = rng.uniform(0.2, 4.0, int(rng.integers(50, 600)))
    if seed % 2:
        rvol[rng.random(rvol.size) < 0.12] = np.nan
    for L in (2, 5, 20, 50):
        ref = _ref_zscore(rvol, L)
        got = R._rolling_zscore(rvol, L)
        assert np.array_equal(np.isnan(ref), np.isnan(got)), f"L={L} seed={seed}"
        np.testing.assert_allclose(got, ref, rtol=1e-9, atol=1e-9, equal_nan=True,
                                   err_msg=f"L={L} seed={seed}")
