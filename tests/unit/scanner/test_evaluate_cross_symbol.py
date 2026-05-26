"""Cross-symbol engine evaluation tests (Phase 2 of cross-ticker support).

Verifies :func:`evaluate_field_at` honors ``ref.symbol`` by pulling
bars + memo from ``ctx.bars_registry``. Covers the bar-time-snap rule
(active timestamp → largest dep-symbol bar at-or-before), the
None-on-missing fallback, the HA-streak builtin against a non-active
symbol (the user's concrete "3 flat bullish HA on SPY → enter AAPL"
example), and combined cross-symbol + cross-interval evaluation.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import tradinglab.indicators  # noqa: F401  (registers indicator factories)
from tradinglab.core.bars_registry import BarsRegistry
from tradinglab.data.multi_interval_cache import MultiIntervalCache
from tradinglab.models import Candle
from tradinglab.scanner.engine import (
    _sub_context_for_symbol,
    _sub_context_for_symbol_at_ts,
    evaluate_field,
    evaluate_group,
    make_context,
)
from tradinglab.scanner.model import (
    OP_GT,
    Condition,
    FieldRef,
    Group,
)


def _candles(
    closes: list[float],
    *,
    start: datetime = datetime(2026, 5, 4, 9, 30),
    interval_min: int = 5,
) -> list[Candle]:
    out = []
    for i, c in enumerate(closes):
        out.append(Candle(
            date=start + timedelta(minutes=i * interval_min),
            open=c - 0.5, high=c + 1.0, low=c - 1.0, close=c,
            volume=1000 + i, session="regular",
        ))
    return out


def _ha_aligned_candles(
    n: int,
    *,
    start: datetime = datetime(2026, 5, 4, 9, 30),
    interval_min: int = 5,
) -> list[Candle]:
    """``n`` bullish HA bars with flat bottoms (open == low)."""
    out = []
    for i in range(n):
        o = 100.0 + i
        h = o + 2.0
        c = o + 1.5
        out.append(Candle(
            date=start + timedelta(minutes=i * interval_min),
            open=o, high=h, low=o, close=c,
            volume=1000 + i, session="regular",
        ))
    return out


def _registry_with(*pairs) -> BarsRegistry:
    cache = MultiIntervalCache()
    for sym, iv, candles in pairs:
        cache.set_bars(sym, iv, candles)
    return BarsRegistry(cache)


# --- cross-symbol field resolution ----------------------------------------


def test_builtin_close_resolves_to_dependency_symbol():
    """A SPY-pinned builtin close on an AAPL context returns SPY's close."""
    aapl = _candles([180.0, 181.0, 182.0, 183.0])
    spy  = _candles([400.0, 401.0, 402.0, 403.0])
    reg = _registry_with(("AAPL", "5m", aapl), ("SPY", "5m", spy))
    ctx = make_context("AAPL", "5m", aapl, bars_registry=reg)
    assert evaluate_field(FieldRef.builtin("close", symbol="SPY"), ctx) == 403.0


def test_indicator_ema_resolves_to_dependency_symbol():
    """A SPY-pinned EMA on an AAPL context computes against SPY bars."""
    # Build 60 bars each so EMA(20) has plenty of warmup.
    aapl = _candles([180.0 + 0.1 * i for i in range(60)])
    spy  = _candles([400.0 + 0.2 * i for i in range(60)])
    reg = _registry_with(("AAPL", "5m", aapl), ("SPY", "5m", spy))
    ctx = make_context("AAPL", "5m", aapl, bars_registry=reg)
    spy_ctx = make_context("SPY", "5m", spy, bars_registry=reg)
    ref = FieldRef.indicator("ema", params={"length": 20}, symbol="SPY")
    cross = evaluate_field(ref, ctx)
    same  = evaluate_field(FieldRef.indicator("ema", params={"length": 20}),
                           spy_ctx)
    assert cross is not None
    assert same is not None
    assert abs(cross - same) < 1e-9


def test_returns_none_when_registry_lacks_symbol():
    """Registry has no SPY view → None (graceful, not raise)."""
    aapl = _candles([180.0, 181.0, 182.0])
    reg = _registry_with(("AAPL", "5m", aapl))
    ctx = make_context("AAPL", "5m", aapl, bars_registry=reg)
    assert evaluate_field(FieldRef.builtin("close", symbol="SPY"), ctx) is None


def test_returns_none_when_dependency_starts_after_active():
    """Active bar ts < dep's first bar ts → None (bar-time-snap, IPO case)."""
    # AAPL starts 2026-05-04 09:30, SPY starts a year LATER.
    aapl = _candles([180.0, 181.0, 182.0])
    spy  = _candles([400.0, 401.0],
                    start=datetime(2027, 5, 4, 9, 30))
    reg = _registry_with(("AAPL", "5m", aapl), ("SPY", "5m", spy))
    ctx = make_context("AAPL", "5m", aapl, bars_registry=reg)
    assert evaluate_field(FieldRef.builtin("close", symbol="SPY"), ctx) is None


def test_dependency_with_gap_uses_last_bar_at_or_before_active():
    """SPY has a gap; active AAPL ts falls inside → use prior SPY bar."""
    aapl = _candles([180.0, 181.0, 182.0, 183.0, 184.0])  # 09:30..09:50
    # SPY: bars at 09:30 and 09:50 only; AAPL's 09:35/09:40/09:45 are
    # between SPY bars. evaluate_field at AAPL[2] (09:40) → SPY[0] (09:30).
    spy = [
        Candle(date=datetime(2026, 5, 4, 9, 30),
               open=400.0, high=401.0, low=399.0, close=400.5,
               volume=1000, session="regular"),
        Candle(date=datetime(2026, 5, 4, 9, 50),
               open=405.0, high=406.0, low=404.0, close=405.5,
               volume=1100, session="regular"),
    ]
    reg = _registry_with(("AAPL", "5m", aapl), ("SPY", "5m", spy))
    # Active index 2 is AAPL's 09:40 bar (still between SPY 09:30 and 09:50).
    ctx = make_context("AAPL", "5m", aapl, current_index=2, bars_registry=reg)
    val = evaluate_field(FieldRef.builtin("close", symbol="SPY"), ctx)
    assert val == 400.5  # SPY[0].close, the most recent at-or-before
    # And active index 4 (AAPL 09:50) → SPY[1] (09:50, exact match).
    ctx2 = make_context("AAPL", "5m", aapl, current_index=4, bars_registry=reg)
    val2 = evaluate_field(FieldRef.builtin("close", symbol="SPY"), ctx2)
    assert val2 == 405.5


def test_cross_symbol_and_cross_interval_combined():
    """SPY 1d EMA on AAPL 5m context resolves through both swaps."""
    aapl_5m = _candles([180.0 + 0.1 * i for i in range(60)])
    spy_5m  = _candles([400.0 + 0.2 * i for i in range(60)])
    spy_1d  = _candles([390.0 + i for i in range(40)],
                       start=datetime(2026, 4, 1, 9, 30),
                       interval_min=60 * 24)  # daily
    reg = _registry_with(
        ("AAPL", "5m", aapl_5m),
        ("SPY",  "5m", spy_5m),
        ("SPY",  "1d", spy_1d),
    )
    ctx = make_context("AAPL", "5m", aapl_5m, bars_registry=reg)
    ref = FieldRef.indicator("ema", params={"length": 10},
                             symbol="SPY", interval="1d")
    cross = evaluate_field(ref, ctx)
    # Equivalent: evaluate against the SPY 1d context directly at its
    # last-at-or-before bar (snapped to AAPL's current ts).
    spy_ctx = make_context("SPY", "1d", spy_1d, bars_registry=reg)
    same = evaluate_field(FieldRef.indicator("ema", params={"length": 10}),
                          spy_ctx)
    assert cross is not None
    assert same is not None
    assert abs(cross - same) < 1e-9


def test_ha_streak_on_dependency_symbol():
    """User-spec guardrail: '3 flat bullish HA on SPY → enter AAPL'.

    Verifies the ``ha_streak`` builtin field resolves correctly when
    pinned to a non-active symbol — the sub-context's ``bars`` and
    ``current_index`` come from the swapped SPY view so the existing
    BarsNp-based HA compute "just works".
    """
    aapl = _candles([180.0, 181.0, 182.0, 183.0, 184.0])
    spy = _ha_aligned_candles(6)
    reg = _registry_with(("AAPL", "5m", aapl), ("SPY", "5m", spy))
    ctx = make_context("AAPL", "5m", aapl, bars_registry=reg)
    streak = evaluate_field(FieldRef.builtin("ha_streak", symbol="SPY"), ctx)
    # 6 bullish-aligned bars → streak should be a positive integer ≥ 3.
    assert streak is not None
    assert streak >= 3.0
    # Sanity: when no registry → cross-symbol returns None (would-be coincident
    # value can't bleed through from the active symbol).
    ctx_no_reg = make_context("AAPL", "5m", aapl)
    assert evaluate_field(
        FieldRef.builtin("ha_streak", symbol="SPY"), ctx_no_reg
    ) is None


def test_evaluate_group_with_cross_symbol_condition():
    """A Group whose Condition pins SPY evaluates the SPY data path.

    Mirrors the user's bread-and-butter pattern: SPY close > SPY EMA(20)
    on an AAPL context — the comparison runs against SPY data and the
    Boolean result decides whether AAPL gets entered.
    """
    # SPY in a clear uptrend so close >> EMA(20).
    spy = _candles([400.0 + i for i in range(60)])
    aapl = _candles([180.0 - 0.1 * i for i in range(60)])  # AAPL trending down
    reg = _registry_with(("SPY", "5m", spy), ("AAPL", "5m", aapl))
    ctx = make_context("AAPL", "5m", aapl, bars_registry=reg)
    group = Group(combinator="and", children=[
        Condition(
            left=FieldRef.builtin("close", symbol="SPY"),
            op=OP_GT,
            params={"right": FieldRef.indicator(
                "ema", params={"length": 20}, symbol="SPY"
            )},
        ),
    ])
    assert evaluate_group(group, ctx) is True


# --- sub-context helpers (direct unit) ------------------------------------


def test_sub_context_for_symbol_returns_none_without_registry():
    aapl = _candles([180.0])
    ctx = make_context("AAPL", "5m", aapl)  # no registry
    assert _sub_context_for_symbol(ctx, "SPY") is None
    assert _sub_context_for_symbol_at_ts(ctx, "SPY") is None


def test_sub_context_for_symbol_targets_other_buffer():
    aapl = _candles([180.0, 181.0])
    spy = _candles([400.0, 401.0, 402.0])
    reg = _registry_with(("AAPL", "5m", aapl), ("SPY", "5m", spy))
    ctx = make_context("AAPL", "5m", aapl, bars_registry=reg)
    sub = _sub_context_for_symbol(ctx, "SPY")
    assert sub is not None
    assert sub.symbol == "SPY"
    assert len(sub.bars) == 3
    assert sub.current_index == 2  # last-available for the no-snap variant
