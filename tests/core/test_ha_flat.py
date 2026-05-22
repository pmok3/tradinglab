"""Tests for tradinglab.core.ha_flat."""

from __future__ import annotations

import datetime as _dt
import math
from typing import List

import numpy as np
import pytest

from tradinglab.core.ha_flat import (
    HA_FLAT_BEAR,
    HA_FLAT_BULL,
    HA_FLAT_NONE,
    HA_FLAT_UNKNOWN,
    HAFlatArrays,
    compute_ha_flat_arrays,
    compute_ha_flat_arrays_np,
)
from tradinglab.core.heikin_ashi import ha_arrays
from tradinglab.models import Candle


# ---------- helpers ---------------------------------------------------------


def _candle(
    o: float, h: float, l_: float, c: float,
    *, t: _dt.datetime | None = None, session: str = "regular",
    volume: int = 1000,
) -> Candle:
    return Candle(
        date=t or _dt.datetime(2024, 1, 2, 9, 30),
        open=o, high=h, low=l_, close=c,
        volume=volume, session=session,
    )


def _series(prices: List[tuple]) -> List[Candle]:
    """Build a candle list from (o, h, l, c) tuples; timestamps spaced 1m apart."""
    t0 = _dt.datetime(2024, 1, 2, 9, 30)
    out: List[Candle] = []
    for i, (o, h, l_, c) in enumerate(prices):
        out.append(_candle(o, h, l_, c, t=t0 + _dt.timedelta(minutes=i)))
    return out


# ---------- empty / shape ---------------------------------------------------


def test_empty_returns_empty_arrays():
    res = compute_ha_flat_arrays([])
    assert isinstance(res, HAFlatArrays)
    assert res.bull_flat_bottom.size == 0
    assert res.bear_flat_top.size == 0
    assert res.signed.size == 0
    assert res.bull_flat_bottom.dtype == bool
    assert res.bear_flat_top.dtype == bool
    assert res.signed.dtype == np.int8


def test_array_variant_empty():
    res = compute_ha_flat_arrays_np(
        np.empty(0), np.empty(0), np.empty(0), np.empty(0)
    )
    assert len(res) == 0


def test_array_variant_length_mismatch_raises():
    with pytest.raises(ValueError):
        compute_ha_flat_arrays_np(
            np.array([1.0, 2.0]), np.array([1.0]),
            np.array([1.0, 2.0]), np.array([1.0, 2.0]),
        )


# ---------- canonical bull flat-bottom -------------------------------------


def test_bull_flat_bottom_marubozu():
    # Two warm-up bars + a strong bull HA bar with no lower wick.
    # We synthesize the arrays directly to control HA outputs precisely.
    # First, work out a series whose HA produces an HA_low == HA_open.
    # Easiest path: a long up-trend so HA_open is rising and the real
    # ``low`` of the next bar is >= HA_open (HA_low picks HA_open).
    series = _series([
        (100.0, 100.5, 99.5, 100.4),  # warm-up bar 0
        (100.4, 101.0, 100.3, 100.9),  # warm-up bar 1
        # Bar 2: real low (101.5) >= HA_open of bar 2.
        # HA_open[2] = (HA_open[1] + HA_close[1]) / 2 ≈ around 100.5
        # so any real_low >= 100.5 makes HA_low = HA_open.
        (101.6, 102.5, 101.5, 102.4),
    ])
    res = compute_ha_flat_arrays(series)
    # Bar 2 should be a bull flat-bottom.
    ha_o, ha_h, ha_l, ha_c = ha_arrays(
        np.array([s.open for s in series]),
        np.array([s.high for s in series]),
        np.array([s.low for s in series]),
        np.array([s.close for s in series]),
    )
    # Sanity: the bar truly is a flat-bottom bull under HA arithmetic.
    assert ha_l[2] == ha_o[2]
    assert ha_c[2] > ha_o[2]
    assert res.bull_flat_bottom[2] is np.True_ or bool(res.bull_flat_bottom[2])
    assert not bool(res.bear_flat_top[2])
    assert int(res.signed[2]) == HA_FLAT_BULL


def test_bear_flat_top_marubozu():
    # Mirror: a downtrend where the next bar's real_high <= HA_open.
    series = _series([
        (100.0, 100.5, 99.5, 99.6),  # warm-up bar 0 — bearish
        (99.6, 99.8, 99.0, 99.1),    # warm-up bar 1 — bearish
        # Bar 2: real_high low enough that HA_high = HA_open.
        (98.5, 98.6, 97.5, 97.6),
    ])
    ha_o, ha_h, ha_l, ha_c = ha_arrays(
        np.array([s.open for s in series]),
        np.array([s.high for s in series]),
        np.array([s.low for s in series]),
        np.array([s.close for s in series]),
    )
    assert ha_h[2] == ha_o[2]
    assert ha_c[2] < ha_o[2]

    res = compute_ha_flat_arrays(series)
    assert bool(res.bear_flat_top[2])
    assert not bool(res.bull_flat_bottom[2])
    assert int(res.signed[2]) == HA_FLAT_BEAR


# ---------- negatives ------------------------------------------------------


def test_bull_with_lower_wick_does_not_qualify():
    # Construct so HA_low < HA_open: the real_low must be lower than
    # both HA_open and HA_close on a bull bar.
    series = _series([
        (100.0, 100.5, 99.5, 100.4),
        (100.4, 101.0, 100.3, 100.9),
        # Real_low = 99.0 (well below HA_open ≈ 100.5).
        (101.0, 102.0, 99.0, 101.5),
    ])
    res = compute_ha_flat_arrays(series)
    assert not bool(res.bull_flat_bottom[2])
    assert int(res.signed[2]) == HA_FLAT_NONE


def test_bear_with_upper_wick_does_not_qualify():
    series = _series([
        (100.0, 100.5, 99.5, 99.6),
        (99.6, 99.8, 99.0, 99.1),
        # Real_high reaches well above HA_open ≈ 99.3.
        (98.5, 105.0, 97.5, 97.6),
    ])
    res = compute_ha_flat_arrays(series)
    assert not bool(res.bear_flat_top[2])
    assert int(res.signed[2]) == HA_FLAT_NONE


def test_doji_excluded():
    """ha_close == ha_open → neither bull nor bear flat (no direction)."""
    # First bar is seeded as (o + c) / 2; if o == c, ha_open == ha_close.
    series = _series([
        (100.0, 100.5, 99.5, 100.0),  # ha_open[0] = 100, ha_close[0] = (100+100.5+99.5+100)/4 = 100.0
    ])
    ha_o, _hh, _hl, ha_c = ha_arrays(
        np.array([s.open for s in series]),
        np.array([s.high for s in series]),
        np.array([s.low for s in series]),
        np.array([s.close for s in series]),
    )
    # Confirm doji.
    assert ha_o[0] == ha_c[0]
    res = compute_ha_flat_arrays(series)
    # Doji never qualifies as flat-bottom OR flat-top.
    assert not bool(res.bull_flat_bottom[0])
    assert not bool(res.bear_flat_top[0])
    assert int(res.signed[0]) == HA_FLAT_NONE


# ---------- NaN / gap handling ---------------------------------------------


def test_gap_candle_unknown_does_not_poison_neighbors():
    """A gap (NaN OHLC) at index k → UNKNOWN at k; bar k+1 re-seeds."""
    bars: List[Candle] = []
    t0 = _dt.datetime(2024, 1, 2, 9, 30)
    bars.append(_candle(100.0, 100.5, 99.5, 100.4, t=t0))
    bars.append(Candle.gap(t0 + _dt.timedelta(minutes=1)))
    # After the gap: a strong bull bar that should be re-seeded then
    # classified. A few warmups before are needed to settle HA_open.
    bars.append(_candle(100.4, 101.0, 100.3, 100.9,
                        t=t0 + _dt.timedelta(minutes=2)))
    bars.append(_candle(101.6, 102.5, 101.5, 102.4,
                        t=t0 + _dt.timedelta(minutes=3)))

    res = compute_ha_flat_arrays(bars)
    # Gap bar reports UNKNOWN.
    assert int(res.signed[1]) == HA_FLAT_UNKNOWN
    assert not bool(res.bull_flat_bottom[1])
    assert not bool(res.bear_flat_top[1])
    # Bars after the gap are valid (re-seeded).
    assert int(res.signed[3]) in (HA_FLAT_NONE, HA_FLAT_BULL)


def test_array_variant_propagates_nan_to_unknown():
    o = np.array([100.0, np.nan, 101.0, 101.5])
    h = np.array([100.5, np.nan, 101.5, 102.0])
    l_ = np.array([99.5, np.nan, 100.5, 101.0])
    c = np.array([100.4, np.nan, 101.3, 101.8])
    res = compute_ha_flat_arrays_np(o, h, l_, c)
    assert int(res.signed[1]) == HA_FLAT_UNKNOWN
    assert not bool(res.bull_flat_bottom[1])


# ---------- determinism ----------------------------------------------------


def test_pure_idempotent():
    """Same input twice yields byte-identical arrays (no hidden state)."""
    series = _series([
        (100.0, 100.5, 99.5, 100.4),
        (100.4, 101.0, 100.3, 100.9),
        (101.6, 102.5, 101.5, 102.4),
    ])
    a = compute_ha_flat_arrays(series)
    b = compute_ha_flat_arrays(series)
    np.testing.assert_array_equal(a.bull_flat_bottom, b.bull_flat_bottom)
    np.testing.assert_array_equal(a.bear_flat_top, b.bear_flat_top)
    np.testing.assert_array_equal(a.signed, b.signed)


# ---------- sentinel constants ---------------------------------------------


def test_sentinel_constants():
    """The four signed sentinels must be int8-fit and pairwise distinct."""
    vals = {HA_FLAT_NONE, HA_FLAT_BULL, HA_FLAT_BEAR, HA_FLAT_UNKNOWN}
    assert len(vals) == 4
    assert HA_FLAT_BULL == 1
    assert HA_FLAT_BEAR == -1
    assert HA_FLAT_NONE == 0
    assert HA_FLAT_UNKNOWN == -128
    # Round-trips through int8 unchanged.
    arr = np.array([HA_FLAT_NONE, HA_FLAT_BULL, HA_FLAT_BEAR,
                    HA_FLAT_UNKNOWN], dtype=np.int8)
    assert int(arr[3]) == HA_FLAT_UNKNOWN


# ---------- vectorised consistency vs scanner field semantics --------------


def test_signed_is_consistent_with_masks():
    """signed[i] must agree with (bull_flat_bottom[i], bear_flat_top[i])."""
    series = _series([
        (100.0, 100.5, 99.5, 100.4),
        (100.4, 101.0, 100.3, 100.9),
        (101.6, 102.5, 101.5, 102.4),  # bull flat-bottom
        (101.6, 102.0, 101.0, 101.4),  # not flat
        (101.0, 101.4, 100.5, 100.5),  # bear-ish
    ])
    res = compute_ha_flat_arrays(series)
    for i in range(len(series)):
        s = int(res.signed[i])
        bb = bool(res.bull_flat_bottom[i])
        bt = bool(res.bear_flat_top[i])
        if s == HA_FLAT_BULL:
            assert bb and not bt
        elif s == HA_FLAT_BEAR:
            assert bt and not bb
        elif s == HA_FLAT_NONE:
            assert not bb and not bt
        else:  # UNKNOWN
            assert not bb and not bt


def test_eps_tolerance_does_not_overfire_at_high_prices():
    """At price 1e6, eps = 1e-3. A bar with HA_low one full cent below
    HA_open must NOT be classified as flat-bottom.
    """
    # Synthesise HA arrays directly (bypass candle list to control inputs).
    n = 3
    ha_o = np.array([1_000_000.0, 1_000_000.5, 1_000_001.0])
    ha_c = np.array([1_000_000.5, 1_000_001.0, 1_000_002.0])
    # HA_low one cent below ha_open (which is 1e6 + something) → diff = 0.01,
    # exceeds eps (1e-3 at this price scale) by 10x.
    ha_l = np.array([999_999.0, 1_000_000.0, 1_000_000.99])
    ha_h = np.array([1_000_001.0, 1_000_002.0, 1_000_003.0])
    # Use the internal ``_classify`` directly via the array entry point's
    # innards — easier: just call _classify after constructing.
    from tradinglab.core.ha_flat import _classify
    res = _classify(ha_o, ha_h, ha_l, ha_c)
    # Bar 2: ha_l = 1_000_000.99, ha_o = 1_000_001.0 → diff 0.01, eps 0.001.
    # Should NOT qualify.
    assert not bool(res.bull_flat_bottom[2])


def test_eps_tolerance_does_fire_within_tolerance():
    """At price 1e3, eps = 1e-6. A diff of 1e-9 is well inside eps."""
    from tradinglab.core.ha_flat import _classify
    ha_o = np.array([1000.0])
    ha_c = np.array([1001.0])
    ha_l = np.array([1000.0 - 1e-12])  # within eps
    ha_h = np.array([1002.0])
    res = _classify(ha_o, ha_h, ha_l, ha_c)
    assert bool(res.bull_flat_bottom[0])
    assert int(res.signed[0]) == HA_FLAT_BULL
