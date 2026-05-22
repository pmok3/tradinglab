"""Tests for the direction-aware HA flat-pattern scanner builtins.

Three new builtins were added alongside the
View → Highlight Flat HA Candles overlay so entries / exits / scans
can reference the same strong-trend signal:

* ``ha_flat_bottom_bull`` — bull HA bar with no lower wick
* ``ha_flat_top_bear``    — bear HA bar with no upper wick
* ``ha_flat_strong``      — signed: +1 / -1 / 0; None during warm-up

These narrow the existing direction-agnostic ``ha_flat_top`` /
``ha_flat_bottom`` builtins (which fire on any bar with no upper /
lower wick regardless of bull/bear); the chart overlay only
emphasises the direction-aware variants.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np
import pytest

from tradinglab.models import Candle
from tradinglab.scanner.fields import (
    BarsNp,
    builtin_compute,
    get_field,
    validate_field_ref,
)
from tradinglab.scanner.model import FieldRef


# ---------------------------------------------------------------------------
# Helpers — mirror the local helpers in test_fields.py for consistency
# ---------------------------------------------------------------------------


def _bars_from_ohlc(opens, highs, lows, closes) -> BarsNp:
    start = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)
    out: List[Candle] = []
    for i, (o, h, l_, c) in enumerate(zip(opens, highs, lows, closes)):
        out.append(Candle(date=start + timedelta(minutes=5 * i),
                          open=o, high=h, low=l_, close=c,
                          volume=1000, session="regular"))
    return BarsNp.from_candles(out)


def _uptrend(n: int = 10) -> BarsNp:
    """Strong uptrend with no real lower wicks → bull-flat-bottom."""
    opens = [100.0 + i for i in range(n)]
    closes = [o + 1.0 for o in opens]
    highs = [c + 0.5 for c in closes]
    lows = [o - 0.5 for o in opens]
    return _bars_from_ohlc(opens, highs, lows, closes)


def _downtrend(n: int = 10) -> BarsNp:
    """Strong downtrend with no real upper wicks → bear-flat-top."""
    opens = [100.0 - i for i in range(n)]
    closes = [o - 1.0 for o in opens]
    lows = [c - 0.5 for c in closes]
    highs = [o + 0.5 for o in opens]
    return _bars_from_ohlc(opens, highs, lows, closes)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fid", [
    "ha_flat_bottom_bull", "ha_flat_top_bear", "ha_flat_strong",
])
def test_field_registered(fid):
    """Each new builtin must be discoverable via ``get_field``."""
    spec = get_field(fid)
    assert spec is not None
    assert spec.kind == "builtin"
    assert spec.builtin_compute is not None


@pytest.mark.parametrize("fid", [
    "ha_flat_bottom_bull", "ha_flat_top_bear", "ha_flat_strong",
])
def test_field_ref_validates(fid):
    """The new ids round-trip through ``validate_field_ref`` cleanly."""
    validate_field_ref(FieldRef(kind="builtin", id=fid))


# ---------------------------------------------------------------------------
# Direction-aware classification
# ---------------------------------------------------------------------------


def test_uptrend_bull_flat_bottom_fires():
    """In a clean uptrend, post-warm-up bars register as bull-flat-bottom."""
    bars = _uptrend(n=10)
    fb = builtin_compute("ha_flat_bottom_bull")
    ft = builtin_compute("ha_flat_top_bear")
    sgn = builtin_compute("ha_flat_strong")
    # After a couple of warm-up bars HA_Open settles into the flat regime.
    bull_hits = sum(1 for i in range(3, 10) if fb(bars, i, {}) == 1.0)
    bear_hits = sum(1 for i in range(3, 10) if ft(bars, i, {}) == 1.0)
    assert bull_hits >= 5
    assert bear_hits == 0
    # ``ha_flat_strong`` mirrors the booleans (positive on those bars).
    for i in range(3, 10):
        if fb(bars, i, {}) == 1.0:
            assert sgn(bars, i, {}) == 1.0


def test_downtrend_bear_flat_top_fires():
    """Mirror: clean downtrend produces bear-flat-top firings."""
    bars = _downtrend(n=10)
    fb = builtin_compute("ha_flat_bottom_bull")
    ft = builtin_compute("ha_flat_top_bear")
    sgn = builtin_compute("ha_flat_strong")
    bear_hits = sum(1 for i in range(3, 10) if ft(bars, i, {}) == 1.0)
    bull_hits = sum(1 for i in range(3, 10) if fb(bars, i, {}) == 1.0)
    assert bear_hits >= 5
    assert bull_hits == 0
    for i in range(3, 10):
        if ft(bars, i, {}) == 1.0:
            assert sgn(bars, i, {}) == -1.0


def test_signed_consistent_with_booleans():
    """``ha_flat_strong`` must agree pointwise with the two boolean fields."""
    bars = _uptrend(n=8)
    fb = builtin_compute("ha_flat_bottom_bull")
    ft = builtin_compute("ha_flat_top_bear")
    sgn = builtin_compute("ha_flat_strong")
    for i in range(8):
        bb = fb(bars, i, {})
        bt = ft(bars, i, {})
        s = sgn(bars, i, {})
        if bb is None or bt is None or s is None:
            # All three propagate None on the same indices (NaN inputs).
            assert bb is None and bt is None and s is None
            continue
        if s == 1.0:
            assert bb == 1.0 and bt == 0.0
        elif s == -1.0:
            assert bt == 1.0 and bb == 0.0
        else:
            assert bb == 0.0 and bt == 0.0


# ---------------------------------------------------------------------------
# Bounds + warm-up + caching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fid", [
    "ha_flat_bottom_bull", "ha_flat_top_bear", "ha_flat_strong",
])
def test_oob_returns_none(fid):
    """Out-of-bounds indices return None (no IndexError leaks)."""
    bars = _uptrend(n=3)
    f = builtin_compute(fid)
    assert f(bars, -1, {}) is None
    assert f(bars, 5, {}) is None


def test_cache_hit_repeated_calls():
    """Repeat calls on the same BarsNp must not recompute the HA flat
    arrays from scratch — the BarsKeyedCache (``_ha_flat_cache``) keys on
    ``id(bars)`` so the first call populates and the second call reuses.
    """
    from tradinglab.scanner import fields as _fields
    bars = _uptrend(n=12)
    f = builtin_compute("ha_flat_bottom_bull")
    # Warm the cache.
    f(bars, 5, {})
    # Snapshot the cache contents — we expect at least one entry now.
    before_size = len(_fields._ha_flat_cache._data)  # type: ignore[attr-defined]
    assert before_size >= 1
    # Call again with the same bars; cache size must NOT grow.
    f(bars, 6, {})
    after_size = len(_fields._ha_flat_cache._data)  # type: ignore[attr-defined]
    assert after_size == before_size


def test_doji_does_not_qualify():
    """ha_close == ha_open → neither bull nor bear; ``ha_flat_strong`` is 0."""
    # Single bar where the formula yields a doji at index 0:
    # HA_Open[0] = (10+10)/2 = 10; HA_Close[0] = (10+10.5+9.5+10)/4 = 10.0.
    bars = _bars_from_ohlc([10.0], [10.5], [9.5], [10.0])
    fb = builtin_compute("ha_flat_bottom_bull")
    ft = builtin_compute("ha_flat_top_bear")
    sgn = builtin_compute("ha_flat_strong")
    assert fb(bars, 0, {}) == 0.0
    assert ft(bars, 0, {}) == 0.0
    assert sgn(bars, 0, {}) == 0.0


# ---------------------------------------------------------------------------
# Cross-check parity with the chart overlay's compute
# ---------------------------------------------------------------------------


def test_scanner_matches_chart_overlay_compute():
    """The scanner cluster and the chart overlay must classify every bar
    identically (the chart goes through ``compute_ha_flat_arrays`` with
    a candle list; the scanner goes through ``_compute_ha_flat_np`` over
    the same OHLC). Disagreement would silently desync the highlight
    from any condition that references one of the new builtins.
    """
    from tradinglab.core.ha_flat import compute_ha_flat_arrays_np

    bars = _uptrend(n=15)
    res = compute_ha_flat_arrays_np(
        bars.open, bars.high, bars.low, bars.close,
    )
    fb = builtin_compute("ha_flat_bottom_bull")
    ft = builtin_compute("ha_flat_top_bear")
    sgn = builtin_compute("ha_flat_strong")
    for i in range(15):
        bb = fb(bars, i, {})
        bt = ft(bars, i, {})
        s = sgn(bars, i, {})
        if bb is None:
            # Both report warm-up at the same index.
            assert int(res.signed[i]) == -128  # HA_FLAT_UNKNOWN
            continue
        # Boolean masks line up with the scanner's per-index outputs.
        assert bool(bb) == bool(res.bull_flat_bottom[i])
        assert bool(bt) == bool(res.bear_flat_top[i])
        assert int(s) == int(res.signed[i])
