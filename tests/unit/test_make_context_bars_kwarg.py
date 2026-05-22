"""Tests for :func:`make_context`'s ``bars=`` kwarg and memo._bars rebind fix."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tradinglab.core.bars import Bars
from tradinglab.models import Candle
from tradinglab.scanner.engine import IndicatorMemo, make_context
from tradinglab.scanner.fields import BarsNp


def _mk(i: int) -> Candle:
    base = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)
    return Candle(
        date=base + timedelta(minutes=i),
        open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.5 + i,
        volume=1000.0 * (i + 1), session="regular",
    )


def _series(n: int) -> list[Candle]:
    return [_mk(i) for i in range(n)]


def test_make_context_with_provided_bars_reuses_object():
    candles = _series(5)
    bars = BarsNp.from_candles(candles)
    ctx = make_context("AAPL", "1m", candles, bars=bars)
    assert ctx.bars is bars  # not rebuilt
    # The memo also has the same bars bound (so indicator computes share the view).
    assert ctx.memo._bars is bars


def test_make_context_provided_bars_length_mismatch_raises():
    candles = _series(5)
    bars = BarsNp.from_candles(_series(4))  # one short
    with pytest.raises(ValueError, match="length"):
        make_context("AAPL", "1m", candles, bars=bars)


def test_make_context_default_path_unchanged():
    candles = _series(3)
    ctx = make_context("AAPL", "1m", candles)
    assert isinstance(ctx.bars, Bars)
    assert len(ctx.bars) == 3
    # Default current_index = last.
    assert ctx.current_index == 2


def test_make_context_rebind_clears_both_cache_and_bars():
    """Latent bug: prior code cleared ``memo.cache`` on candle rebind but
    not ``memo._bars``. Fixed in this slice."""
    first = _series(4)
    memo = IndicatorMemo(candles=first)
    # Force ``_bars`` population so we can see whether it is cleared.
    memo.cache[("fake", ())] = {"x": __import__("numpy").array([1.0, 2.0, 3.0, 4.0])}
    memo.errors["fake"] = "stale"
    _ = memo._get_bars()
    assert memo._bars is not None
    stale_bars = memo._bars

    second = _series(5)
    ctx = make_context("AAPL", "1m", second, memo=memo)
    # After rebinding to a new candle list, both the indicator-output
    # cache AND the lazy bars MUST be cleared.
    assert memo.candles is second
    assert memo.cache == {}
    assert memo.errors == {}
    assert memo._bars is not stale_bars  # the bug fix
    # And the new bars should be what the context exposes.
    assert ctx.memo._bars is ctx.bars


def test_make_context_bars_bound_for_compute_via_bars():
    """Even without rebind, providing bars= must bind it onto memo so
    indicator computes hit the same view."""
    candles = _series(3)
    bars = BarsNp.from_candles(candles)
    memo = IndicatorMemo(candles=candles)
    ctx = make_context("AAPL", "1m", candles, memo=memo, bars=bars)
    assert ctx.memo._bars is bars
    # And ``_get_bars`` is now a no-op identity.
    assert memo._get_bars() is bars
