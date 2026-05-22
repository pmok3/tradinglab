"""Engine mixed-interval relaxation tests.

Verifies the cross-interval slot on :class:`FieldRef` and
:class:`Condition`:

* With ``EvaluationContext.bars_registry`` bound, a non-null
  ``FieldRef.interval`` resolves against the registry's
  other-interval ``BarsView`` instead of raising.
* Without a registry, the historical ``NotImplementedError`` gate is
  preserved (back-compat).
* :func:`validate_scan` accepts mixed-interval scans when given a
  registry; rejects them otherwise.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

import pytest

import tradinglab.indicators  # noqa: F401  (registers indicators)
from tradinglab.core.bars_registry import BarsRegistry
from tradinglab.data.multi_interval_cache import MultiIntervalCache
from tradinglab.models import Candle
from tradinglab.scanner.engine import (
    evaluate_field,
    make_context,
    validate_scan,
)
from tradinglab.scanner.model import (
    OP_GT,
    Condition,
    FieldRef,
    Group,
    ScanDefinition,
    UniverseFilter,
)

# --- helpers ---------------------------------------------------------------


def _candles(closes: list[float],
             start: datetime = datetime(2026, 5, 4, 9, 30),
             interval_min: int = 5) -> list[Candle]:
    out = []
    for i, c in enumerate(closes):
        out.append(Candle(
            date=start + timedelta(minutes=i * interval_min),
            open=c - 0.5, high=c + 1.0, low=c - 1.0, close=c,
            volume=1000 + i, session="regular",
        ))
    return out


def _registry_with(*pairs) -> BarsRegistry:
    """``pairs = ((sym, iv, candles), ...)``."""
    cache = MultiIntervalCache()
    for sym, iv, candles in pairs:
        cache.set_bars(sym, iv, candles)
    return BarsRegistry(cache)


# --- evaluate_field cross-interval ----------------------------------------


def test_evaluate_field_cross_interval_pulls_from_registry():
    """A 1d FieldRef on a 5m context resolves to the 1d buffer's last close."""
    five = _candles([100.0, 105.0, 108.0, 110.0])
    daily = _candles([90.0, 95.0, 99.0])  # last close = 99
    reg = _registry_with(("AAPL", "5m", five), ("AAPL", "1d", daily))

    ctx = make_context("AAPL", "5m", five, bars_registry=reg)
    ref_1d_close = FieldRef.builtin("close", interval="1d")
    val = evaluate_field(ref_1d_close, ctx)
    # Last close of the 1d buffer.
    assert val == 99.0


def test_evaluate_field_cross_interval_no_registry_raises():
    """No registry on the context → preserve v1 NotImplementedError."""
    five = _candles([100.0, 105.0, 108.0, 110.0])
    ctx = make_context("AAPL", "5m", five)  # no bars_registry
    ref_1d_close = FieldRef.builtin("close", interval="1d")
    with pytest.raises(NotImplementedError):
        evaluate_field(ref_1d_close, ctx)


def test_validate_scan_mixed_interval_passes_with_registry():
    """A scan with one 5m and one 1d condition validates cleanly with a registry."""
    five = _candles([100.0, 105.0, 108.0])
    daily = _candles([90.0, 95.0])
    reg = _registry_with(("AAPL", "5m", five), ("AAPL", "1d", daily))
    scan = ScanDefinition(
        name="mixed",
        primary_interval="5m",
        universe_filter=UniverseFilter.all(),
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(50.0)},
                      interval="5m"),
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(50.0)},
                      interval="1d"),
        ]),
    )
    errs = validate_scan(scan, bars_registry=reg)
    assert errs == []


def test_validate_scan_mixed_interval_fails_without_registry():
    """Same scan, no registry → mixed-interval rejected."""
    scan = ScanDefinition(
        name="mixed",
        primary_interval="5m",
        universe_filter=UniverseFilter.all(),
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(50.0)},
                      interval="5m"),
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(50.0)},
                      interval="1d"),
        ]),
    )
    errs = validate_scan(scan)
    # At least one error mentions the cross-interval mismatch.
    assert any("1d" in e and "5m" in e for e in errs)


def test_evaluate_field_cross_interval_missing_buffer_returns_none():
    """Registry has no buffer for the alternate interval → field resolves to None."""
    five = _candles([100.0, 105.0, 108.0, 110.0])
    # Only 5m loaded; no 1d.
    reg = _registry_with(("AAPL", "5m", five))
    ctx = make_context("AAPL", "5m", five, bars_registry=reg)
    ref_1d_close = FieldRef.builtin("close", interval="1d")
    val = evaluate_field(ref_1d_close, ctx)
    assert val is None


def test_two_cross_interval_conditions_evaluate_from_right_source():
    """5m and 1d conditions in the same scan each pull their own buffer."""
    five = _candles([100.0, 105.0, 108.0, 200.0])  # last close = 200
    daily = _candles([90.0, 50.0])                  # last close = 50
    reg = _registry_with(("AAPL", "5m", five), ("AAPL", "1d", daily))
    ctx = make_context("AAPL", "5m", five, bars_registry=reg)

    ref_5m = FieldRef.builtin("close", interval="5m")
    ref_1d = FieldRef.builtin("close", interval="1d")
    # 5m FieldRef with interval matching ctx → no cross-interval branch,
    # resolves to ctx.bars's last close (200).
    assert evaluate_field(ref_5m, ctx) == 200.0
    # 1d FieldRef → cross-interval, pulls from 1d buffer (50).
    assert evaluate_field(ref_1d, ctx) == 50.0
