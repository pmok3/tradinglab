"""Engine tests: tri-valued operators, field resolution, group combinators.

The engine is the highest-risk subsystem in the scanner stack (per
the principal-SWE critique). These tests cover:

1. Field evaluation (literal / builtin / indicator / NaN / OOB).
2. Every operator (19 total): hand-crafted bars where the answer is
   obvious by inspection.
3. Tri-valued AND/OR semantics + disabled-child handling.
4. ``validate_scan`` over a fully-formed scan with bad fields injected.
5. ``IndicatorMemo`` reuse + error recording.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np
import pytest

import tradinglab.indicators  # noqa: F401  -- registers indicators at import
from tradinglab.models import Candle
from tradinglab.scanner.engine import (
    EvaluationContext,
    IndicatorMemo,
    evaluate_condition,
    evaluate_field,
    evaluate_field_at,
    evaluate_group,
    evaluate_scan,
    make_context,
    validate_scan,
)
from tradinglab.scanner.model import (
    OP_BETWEEN,
    OP_CROSSES_ABOVE,
    OP_CROSSES_BELOW,
    OP_EQ,
    OP_GE,
    OP_GT,
    OP_HOLDING_ABOVE,
    OP_HOLDING_BELOW,
    OP_INSIDE_BAR,
    OP_IS_FALLING,
    OP_IS_RISING,
    OP_LE,
    OP_LT,
    OP_NE,
    OP_NEW_HIGH_N,
    OP_NEW_LOW_N,
    OP_NR7,
    OP_OUTSIDE_BAR,
    OP_WITHIN_PCT,
    Condition,
    FieldRef,
    Group,
    ScanDefinition,
    UniverseFilter,
)

# Indicators register on import — no explicit init needed.


# ---------------------------------------------------------------------------
# Candle fixtures
# ---------------------------------------------------------------------------


def _candles(closes: list[float], *,
             opens: list[float] = None,
             highs: list[float] = None,
             lows: list[float] = None,
             vols: list[int] = None,
             session: str = "regular",
             start: datetime = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc),
             interval_min: int = 5) -> list[Candle]:
    n = len(closes)
    opens  = opens or [c - 0.5 for c in closes]
    highs  = highs or [max(o, c) + 1.0 for o, c in zip(opens, closes, strict=False)]
    lows   = lows  or [min(o, c) - 1.0 for o, c in zip(opens, closes, strict=False)]
    vols   = vols  or [1000 + i for i in range(n)]
    out = []
    for i in range(n):
        out.append(Candle(date=start + timedelta(minutes=i*interval_min),
                          open=opens[i], high=highs[i], low=lows[i],
                          close=closes[i], volume=vols[i], session=session))
    return out


def _ctx(candles, idx=None, interval="5m", symbol="TEST"):
    return make_context(symbol, interval, candles, idx)


# ---------------------------------------------------------------------------
# Field resolution
# ---------------------------------------------------------------------------


def test_field_literal():
    ctx = _ctx(_candles([100, 101]))
    assert evaluate_field(FieldRef.literal(42.0), ctx) == 42.0


def test_field_builtin_close():
    ctx = _ctx(_candles([100, 101, 102]), idx=2)
    assert evaluate_field(FieldRef.builtin("close"), ctx) == 102.0


def test_field_builtin_oob_returns_none():
    ctx = _ctx(_candles([100, 101]), idx=2)  # idx past end
    assert evaluate_field(FieldRef.builtin("close"), ctx) is None


def test_field_indicator_sma_basic():
    closes = [10.0] * 5 + [20.0] * 5
    ctx = _ctx(_candles(closes), idx=9)
    ref = FieldRef.indicator("sma", params={"length": 5})
    # Last 5 closes are 20 → sma == 20.
    assert evaluate_field(ref, ctx) == pytest.approx(20.0)


def test_field_indicator_warmup_returns_none():
    """Indicator NaN warmup propagates as None."""
    ctx = _ctx(_candles([10.0, 11.0]), idx=0)
    ref = FieldRef.indicator("sma", params={"length": 5})
    assert evaluate_field(ref, ctx) is None


def test_field_interval_override_raises():
    ctx = _ctx(_candles([10.0]))
    ref = FieldRef.builtin("close", interval="1d")  # ctx is "5m"
    with pytest.raises(NotImplementedError):
        evaluate_field(ref, ctx)


def test_field_interval_override_matching_ctx_ok():
    """Override that equals the ctx interval is a no-op."""
    ctx = _ctx(_candles([10.0]), interval="5m")
    ref = FieldRef.builtin("close", interval="5m")
    assert evaluate_field(ref, ctx) == 10.0


# ---------------------------------------------------------------------------
# Comparison operators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op,expected", [
    (OP_GT,  True),
    (OP_LT,  False),
    (OP_GE,  True),
    (OP_LE,  False),
    (OP_EQ,  False),
    (OP_NE,  True),
])
def test_comparison_operators(op, expected):
    ctx = _ctx(_candles([100.0, 105.0]), idx=1)  # close=105
    cond = Condition(left=FieldRef.builtin("close"), op=op,
                     params={"right": FieldRef.literal(100.0)}, interval="5m")
    assert evaluate_condition(cond, ctx) is expected


def test_comparison_none_propagates():
    """Insufficient data on either side → None."""
    ctx = _ctx(_candles([100.0]), idx=5)  # OOB
    cond = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                     params={"right": FieldRef.literal(0.0)}, interval="5m")
    assert evaluate_condition(cond, ctx) is None


# ---------------------------------------------------------------------------
# Between
# ---------------------------------------------------------------------------


def test_between_inclusive():
    ctx = _ctx(_candles([100.0, 105.0]), idx=1)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_BETWEEN,
        params={"low": FieldRef.literal(100.0), "high": FieldRef.literal(110.0)},
        interval="5m",
    )
    assert evaluate_condition(cond, ctx) is True


def test_between_outside_returns_false():
    ctx = _ctx(_candles([100.0, 200.0]), idx=1)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_BETWEEN,
        params={"low": FieldRef.literal(0.0), "high": FieldRef.literal(150.0)},
        interval="5m",
    )
    assert evaluate_condition(cond, ctx) is False


# ---------------------------------------------------------------------------
# Crosses
# ---------------------------------------------------------------------------


def test_crosses_above_simple():
    # close: 100, 99, 99, 99, 101 → close was below 100 lookback=4 ago, now above.
    ctx = _ctx(_candles([100.0, 99.0, 99.0, 99.0, 101.0]), idx=4)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_CROSSES_ABOVE,
        params={"right": FieldRef.literal(100.0), "lookback": 4},
        interval="5m",
    )
    # prev_l (close[0]) = 100, prev_r = 100, 100<=100 ✓; cur_l=101 > cur_r=100 ✓.
    assert evaluate_condition(cond, ctx) is True


def test_crosses_above_did_not_cross():
    # Already above the whole time → not a cross.
    ctx = _ctx(_candles([105.0, 106.0, 107.0]), idx=2)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_CROSSES_ABOVE,
        params={"right": FieldRef.literal(100.0), "lookback": 2},
        interval="5m",
    )
    # prev_l=105 > prev_r=100 → fails the prev_l <= prev_r leg.
    assert evaluate_condition(cond, ctx) is False


def test_crosses_below_simple():
    ctx = _ctx(_candles([101.0, 102.0, 103.0, 99.0]), idx=3)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_CROSSES_BELOW,
        params={"right": FieldRef.literal(100.0), "lookback": 3},
        interval="5m",
    )
    assert evaluate_condition(cond, ctx) is True


def test_crosses_lookback_too_far():
    """If lookback exceeds available bars → None."""
    ctx = _ctx(_candles([100.0, 101.0]), idx=1)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_CROSSES_ABOVE,
        params={"right": FieldRef.literal(100.0), "lookback": 5},
        interval="5m",
    )
    assert evaluate_condition(cond, ctx) is None


# ---------------------------------------------------------------------------
# is_rising / is_falling
# ---------------------------------------------------------------------------


def test_is_rising_strict():
    ctx = _ctx(_candles([100.0, 101.0, 102.0, 103.0]), idx=3)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_IS_RISING,
        params={"lookback": 3}, interval="5m",
    )
    assert evaluate_condition(cond, ctx) is True


def test_is_rising_flat_fails():
    ctx = _ctx(_candles([100.0, 100.0, 100.0]), idx=2)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_IS_RISING,
        params={"lookback": 2}, interval="5m",
    )
    assert evaluate_condition(cond, ctx) is False


def test_is_falling_strict():
    ctx = _ctx(_candles([105.0, 104.0, 103.0]), idx=2)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_IS_FALLING,
        params={"lookback": 2}, interval="5m",
    )
    assert evaluate_condition(cond, ctx) is True


# ---------------------------------------------------------------------------
# within_pct
# ---------------------------------------------------------------------------


def test_within_pct_inside_tolerance():
    ctx = _ctx(_candles([100.0, 101.0]), idx=1)  # close=101
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_WITHIN_PCT,
        params={"target": FieldRef.literal(100.0), "tolerance_pct": 2.0},
        interval="5m",
    )
    assert evaluate_condition(cond, ctx) is True


def test_within_pct_outside_tolerance():
    ctx = _ctx(_candles([100.0, 110.0]), idx=1)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_WITHIN_PCT,
        params={"target": FieldRef.literal(100.0), "tolerance_pct": 2.0},
        interval="5m",
    )
    assert evaluate_condition(cond, ctx) is False


def test_within_pct_zero_target_returns_none():
    ctx = _ctx(_candles([100.0, 0.5]), idx=1)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_WITHIN_PCT,
        params={"target": FieldRef.literal(0.0), "tolerance_pct": 2.0},
        interval="5m",
    )
    assert evaluate_condition(cond, ctx) is None


# ---------------------------------------------------------------------------
# new_high_n / new_low_n
# ---------------------------------------------------------------------------


def test_new_high_n_bars_true():
    ctx = _ctx(_candles([100.0, 101.0, 102.0, 103.0, 110.0]), idx=4)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_NEW_HIGH_N,
        params={"n": 4}, interval="5m",
    )
    assert evaluate_condition(cond, ctx) is True


def test_new_high_n_bars_false():
    ctx = _ctx(_candles([100.0, 101.0, 105.0, 103.0, 104.0]), idx=4)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_NEW_HIGH_N,
        params={"n": 4}, interval="5m",
    )
    assert evaluate_condition(cond, ctx) is False  # cur=104 < prior max 105


def test_new_low_n_bars_true():
    ctx = _ctx(_candles([100.0, 99.0, 98.0, 95.0]), idx=3)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_NEW_LOW_N,
        params={"n": 3}, interval="5m",
    )
    assert evaluate_condition(cond, ctx) is True


# ---------------------------------------------------------------------------
# holding_above / holding_below
# ---------------------------------------------------------------------------


def test_holding_above_all_satisfy():
    # Last 3 closes all > 50.
    ctx = _ctx(_candles([100.0, 101.0, 102.0]), idx=2)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_HOLDING_ABOVE,
        params={"reference": FieldRef.literal(50.0), "bars": 3},
        interval="5m",
    )
    assert evaluate_condition(cond, ctx) is True


def test_holding_above_one_breach_fails():
    ctx = _ctx(_candles([100.0, 49.0, 102.0]), idx=2)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_HOLDING_ABOVE,
        params={"reference": FieldRef.literal(50.0), "bars": 3},
        interval="5m",
    )
    assert evaluate_condition(cond, ctx) is False


def test_holding_below_all_satisfy():
    ctx = _ctx(_candles([10.0, 11.0, 12.0]), idx=2)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_HOLDING_BELOW,
        params={"reference": FieldRef.literal(100.0), "bars": 3},
        interval="5m",
    )
    assert evaluate_condition(cond, ctx) is True


# ---------------------------------------------------------------------------
# Inside / Outside / NR7
# ---------------------------------------------------------------------------


def test_inside_bar_true():
    # Bar 0: H=110 L=90; Bar 1: H=105 L=95 → inside.
    candles = _candles([100.0, 100.0],
                       opens=[100.0, 100.0],
                       highs=[110.0, 105.0],
                       lows=[90.0, 95.0])
    ctx = _ctx(candles, idx=1)
    cond = Condition(left=FieldRef.builtin("close"), op=OP_INSIDE_BAR,
                     params={}, interval="5m")
    assert evaluate_condition(cond, ctx) is True


def test_inside_bar_false():
    candles = _candles([100.0, 100.0],
                       highs=[110.0, 115.0],
                       lows=[90.0, 95.0])
    ctx = _ctx(candles, idx=1)
    cond = Condition(left=FieldRef.builtin("close"), op=OP_INSIDE_BAR,
                     params={}, interval="5m")
    assert evaluate_condition(cond, ctx) is False  # high broke out


def test_outside_bar_true():
    candles = _candles([100.0, 100.0],
                       highs=[105.0, 110.0],
                       lows=[95.0, 90.0])
    ctx = _ctx(candles, idx=1)
    cond = Condition(left=FieldRef.builtin("close"), op=OP_OUTSIDE_BAR,
                     params={}, interval="5m")
    assert evaluate_condition(cond, ctx) is True


def test_inside_bar_first_bar_returns_none():
    ctx = _ctx(_candles([100.0]), idx=0)
    cond = Condition(left=FieldRef.builtin("close"), op=OP_INSIDE_BAR,
                     params={}, interval="5m")
    assert evaluate_condition(cond, ctx) is None


def test_nr7_true():
    # 7 bars; last has the smallest range.
    candles = _candles([100]*7,
                       highs=[110, 109, 108, 107, 106, 105, 100.5],
                       lows =[ 90,  91,  92,  93,  94,  95,  99.5])
    ctx = _ctx(candles, idx=6)
    cond = Condition(left=FieldRef.builtin("close"), op=OP_NR7,
                     params={}, interval="5m")
    assert evaluate_condition(cond, ctx) is True


def test_nr7_false():
    candles = _candles([100]*7,
                       highs=[101, 109, 108, 107, 106, 105, 110],
                       lows =[ 99,  91,  92,  93,  94,  95,  90])
    ctx = _ctx(candles, idx=6)
    cond = Condition(left=FieldRef.builtin("close"), op=OP_NR7,
                     params={}, interval="5m")
    assert evaluate_condition(cond, ctx) is False  # last range = 20, prior min = 2


def test_nr7_too_few_bars_returns_none():
    ctx = _ctx(_candles([100.0]*5), idx=4)
    cond = Condition(left=FieldRef.builtin("close"), op=OP_NR7,
                     params={}, interval="5m")
    assert evaluate_condition(cond, ctx) is None


# ---------------------------------------------------------------------------
# Group combinators (tri-valued)
# ---------------------------------------------------------------------------


def _const_cond(result_value: float, op=OP_GT, threshold: float = 0.0,
                enabled: bool = True) -> Condition:
    """Build a Condition that evaluates to True iff result_value > threshold."""
    return Condition(
        left=FieldRef.literal(result_value),
        op=op,
        params={"right": FieldRef.literal(threshold)},
        interval="5m",
        enabled=enabled,
    )


def test_group_and_all_true():
    ctx = _ctx(_candles([100.0]))
    grp = Group(combinator="and", children=[
        _const_cond(1.0), _const_cond(2.0)
    ])
    assert evaluate_group(grp, ctx) is True


def test_group_and_one_false():
    ctx = _ctx(_candles([100.0]))
    grp = Group(combinator="and", children=[
        _const_cond(1.0), _const_cond(-1.0)  # -1 > 0 is False
    ])
    assert evaluate_group(grp, ctx) is False


def test_group_or_one_true():
    ctx = _ctx(_candles([100.0]))
    grp = Group(combinator="or", children=[
        _const_cond(-1.0), _const_cond(1.0)
    ])
    assert evaluate_group(grp, ctx) is True


def test_group_or_all_false():
    ctx = _ctx(_candles([100.0]))
    grp = Group(combinator="or", children=[
        _const_cond(-1.0), _const_cond(-2.0)
    ])
    assert evaluate_group(grp, ctx) is False


def test_group_and_with_none_propagates():
    """AND of (True, None) → None."""
    ctx = _ctx(_candles([100.0]), idx=99)  # OOB → comparisons over 'close' = None
    none_cond = Condition(
        left=FieldRef.builtin("close"), op=OP_GT,
        params={"right": FieldRef.literal(0.0)}, interval="5m",
    )
    grp = Group(combinator="and", children=[_const_cond(1.0), none_cond])
    assert evaluate_group(grp, ctx) is None


def test_group_and_with_false_short_circuits_none():
    """AND of (False, None) → False (False dominates)."""
    ctx = _ctx(_candles([100.0]), idx=99)
    none_cond = Condition(
        left=FieldRef.builtin("close"), op=OP_GT,
        params={"right": FieldRef.literal(0.0)}, interval="5m",
    )
    grp = Group(combinator="and", children=[_const_cond(-1.0), none_cond])
    assert evaluate_group(grp, ctx) is False


def test_group_or_with_true_dominates_none():
    ctx = _ctx(_candles([100.0]), idx=99)
    none_cond = Condition(
        left=FieldRef.builtin("close"), op=OP_GT,
        params={"right": FieldRef.literal(0.0)}, interval="5m",
    )
    grp = Group(combinator="or", children=[_const_cond(1.0), none_cond])
    assert evaluate_group(grp, ctx) is True


def test_group_disabled_child_skipped():
    """Disabled children are NOT contributing None — they're skipped entirely."""
    ctx = _ctx(_candles([100.0]))
    grp = Group(combinator="and", children=[
        _const_cond(1.0),
        _const_cond(-1.0, enabled=False),  # disabled → skipped
    ])
    assert evaluate_group(grp, ctx) is True


def test_group_all_disabled_returns_none():
    ctx = _ctx(_candles([100.0]))
    grp = Group(combinator="and", children=[
        _const_cond(1.0, enabled=False),
        _const_cond(2.0, enabled=False),
    ])
    assert evaluate_group(grp, ctx) is None


def test_group_empty_returns_none():
    ctx = _ctx(_candles([100.0]))
    assert evaluate_group(Group(combinator="and", children=[]), ctx) is None


def test_group_disabled_group_returns_none():
    ctx = _ctx(_candles([100.0]))
    grp = Group(combinator="and",
                children=[_const_cond(1.0)], enabled=False)
    assert evaluate_group(grp, ctx) is None


def test_group_nested_and_or():
    """(close > 0 AND (close > 1000 OR close > 0))"""
    ctx = _ctx(_candles([100.0]))
    inner = Group(combinator="or", children=[
        _const_cond(-1.0),  # False
        _const_cond(1.0),   # True
    ])
    outer = Group(combinator="and", children=[
        _const_cond(2.0),  # True
        inner,
    ])
    assert evaluate_group(outer, ctx) is True


# ---------------------------------------------------------------------------
# Full scan
# ---------------------------------------------------------------------------


def test_evaluate_scan_round_trip():
    candles = _candles([100.0, 101.0, 102.0])
    ctx = _ctx(candles, idx=2)
    scan = ScanDefinition(
        name="t",
        primary_interval="5m",
        root=Group(combinator="and", children=[_const_cond(1.0)]),
    )
    assert evaluate_scan(scan, ctx) is True


# ---------------------------------------------------------------------------
# IndicatorMemo
# ---------------------------------------------------------------------------


def test_indicator_memo_caches_compute():
    candles = _candles([10.0]*30)
    memo = IndicatorMemo(candles=candles)
    out1 = memo.get("sma", {"length": 5})
    out2 = memo.get("sma", {"length": 5})
    assert out1 is out2  # identity → cached
    # Different params → different entry.
    out3 = memo.get("sma", {"length": 10})
    assert out3 is not out1


def test_indicator_memo_unregistered_indicator_records_error():
    memo = IndicatorMemo(candles=_candles([10.0]*5))
    out = memo.get("not_a_real_indicator", {})
    assert out == {}
    assert "not_a_real_indicator" in memo.errors


def test_make_context_shares_memo_across_calls():
    candles = _candles([10.0]*30)
    memo = IndicatorMemo(candles=candles)
    ctx1 = make_context("AAA", "5m", candles, current_index=29, memo=memo)
    ctx2 = make_context("AAA", "5m", candles, current_index=29, memo=memo)
    # Both should hit the same cache entry.
    evaluate_field(FieldRef.indicator("sma", params={"length": 5}), ctx1)
    cache_size_after_ctx1 = len(memo.cache)
    evaluate_field(FieldRef.indicator("sma", params={"length": 5}), ctx2)
    assert len(memo.cache) == cache_size_after_ctx1


# ---------------------------------------------------------------------------
# validate_scan
# ---------------------------------------------------------------------------


def test_validate_scan_clean_returns_empty():
    scan = ScanDefinition(
        name="t",
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(100.0)}, interval="5m"),
        ]),
    )
    assert validate_scan(scan) == []


def test_validate_scan_unknown_field_left():
    scan = ScanDefinition(
        name="t",
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("not_a_real_field"), op=OP_GT,
                      params={"right": FieldRef.literal(0.0)}, interval="5m"),
        ]),
    )
    errs = validate_scan(scan)
    assert len(errs) == 1
    assert "not_a_real_field" in errs[0]
    assert "left" in errs[0]


def test_validate_scan_unknown_indicator_param():
    scan = ScanDefinition(
        name="t",
        root=Group(combinator="and", children=[
            Condition(
                left=FieldRef.builtin("close"), op=OP_GT,
                params={"right": FieldRef.indicator("not_a_real_indicator")},
                interval="5m",
            ),
        ]),
    )
    errs = validate_scan(scan)
    assert any("not_a_real_indicator" in e for e in errs)


def test_validate_scan_bad_rank_by():
    scan = ScanDefinition(
        name="t",
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(0.0)}, interval="5m"),
        ]),
        rank_by=FieldRef.indicator("not_a_real_indicator"),
    )
    errs = validate_scan(scan)
    assert any("rank_by" in e for e in errs)


# ---------------------------------------------------------------------------
# Disabled condition direct call
# ---------------------------------------------------------------------------


def test_evaluate_disabled_condition_returns_none():
    ctx = _ctx(_candles([100.0]))
    cond = _const_cond(1.0, enabled=False)
    assert evaluate_condition(cond, ctx) is None


# ---------------------------------------------------------------------------
# Within-last-N-bars walk (Phase 4)
# ---------------------------------------------------------------------------


def _close_gt_lit_cond(threshold: float, *, within_last_bars: int = 0,
                       within_last_mode: str = "any") -> Condition:
    """Convenience: ``close > threshold`` with optional look-back fields."""
    return Condition(
        left=FieldRef.builtin("close"),
        op=OP_GT,
        params={"right": FieldRef.literal(threshold)},
        interval="5m",
        within_last_bars=within_last_bars,
        within_last_mode=within_last_mode,
    )


def test_within_last_n_zero_is_baseline():
    """N=0 should be byte-identical to the no-modifier path (sentinel)."""
    candles = _candles([100.0, 101.0, 102.0, 103.0])
    ctx_baseline = _ctx(candles, idx=3)
    ctx_lookback = _ctx(candles, idx=3)
    cond_a = _close_gt_lit_cond(102.5)  # N=0
    cond_b = _close_gt_lit_cond(102.5, within_last_bars=0)
    assert evaluate_condition(cond_a, ctx_baseline) is True
    assert evaluate_condition(cond_b, ctx_lookback) is True
    # N=0 walk path should NOT touch the evidence list.
    assert ctx_lookback.evidence == []


def test_within_last_any_finds_match_in_recent_history():
    """N=2, any: condition was True 1 bar ago, False now → True."""
    # close: [100, 101, 105, 102]; threshold = 104. Only idx=2 satisfies.
    ctx = _ctx(_candles([100.0, 101.0, 105.0, 102.0]), idx=3)
    cond = _close_gt_lit_cond(104.0, within_last_bars=2, within_last_mode="any")
    assert evaluate_condition(cond, ctx) is True
    assert len(ctx.evidence) == 1
    ev = ctx.evidence[0]
    assert ev.node_id == cond.id
    assert ev.bars_ago == 1  # i=3, trigger at j=2 → 3-2=1
    assert ev.value == 105.0  # LHS at trigger bar
    assert ev.timestamp != ""  # ISO string populated


def test_within_last_any_misses_when_window_too_small():
    """Match exists at i-3, but N=2 only walks i-2..i → False."""
    ctx = _ctx(_candles([100.0, 105.0, 99.0, 99.0, 99.0]), idx=4)
    cond = _close_gt_lit_cond(104.0, within_last_bars=2, within_last_mode="any")
    assert evaluate_condition(cond, ctx) is False
    assert ctx.evidence == []


def test_within_last_all_requires_every_bar_true():
    """N=2, all: every bar in window must satisfy."""
    # close: [100, 105, 106, 107]. close > 104 holds at idx 1,2,3 → all True.
    ctx = _ctx(_candles([100.0, 105.0, 106.0, 107.0]), idx=3)
    cond = _close_gt_lit_cond(104.0, within_last_bars=2, within_last_mode="all")
    assert evaluate_condition(cond, ctx) is True
    # One False bar in the window → False.
    ctx2 = _ctx(_candles([100.0, 105.0, 103.0, 106.0]), idx=3)
    cond2 = _close_gt_lit_cond(104.0, within_last_bars=2, within_last_mode="all")
    assert evaluate_condition(cond2, ctx2) is False


def test_within_last_exactly_targets_oldest_bar_only():
    """exactly N: True iff predicate holds at exactly bar i-N."""
    # close: [100, 105, 102, 101]; threshold=104. Match at idx=1 only.
    ctx = _ctx(_candles([100.0, 105.0, 102.0, 101.0]), idx=3)
    cond_match = _close_gt_lit_cond(104.0, within_last_bars=2,
                                    within_last_mode="exactly")
    # i=3, N=2 → target=1, close[1]=105 > 104 → True.
    assert evaluate_condition(cond_match, ctx) is True
    assert ctx.evidence[0].bars_ago == 2

    cond_miss = _close_gt_lit_cond(104.0, within_last_bars=1,
                                   within_last_mode="exactly")
    # i=3, N=1 → target=2, close[2]=102 < 104 → False.
    ctx2 = _ctx(_candles([100.0, 105.0, 102.0, 101.0]), idx=3)
    assert evaluate_condition(cond_miss, ctx2) is False


def test_within_last_exactly_clamped_past_returns_none():
    """If target index < 0 (clamped past start) → None."""
    ctx = _ctx(_candles([100.0, 101.0]), idx=1)
    cond = _close_gt_lit_cond(50.0, within_last_bars=5, within_last_mode="exactly")
    # i=1, N=5 → target=-4, < 0 → None.
    assert evaluate_condition(cond, ctx) is None


def test_within_last_crosses_above_in_window():
    """crosses_above 1 bar ago: at idx=i-1 the cross fired; at idx=i it didn't."""
    # Crafted: close [100, 99, 99, 101, 99]. lookback=1:
    #   at j=3: prev_l=close[2]=99, prev_r=100, cur_l=close[3]=101, cur_r=100.
    #            99 <= 100 ✓ AND 101 > 100 ✓ → True.
    #   at j=4: prev_l=close[3]=101, cur_l=close[4]=99 → no cross.
    ctx = _ctx(_candles([100.0, 99.0, 99.0, 101.0, 99.0]), idx=4)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_CROSSES_ABOVE,
        params={"right": FieldRef.literal(100.0), "lookback": 1},
        interval="5m",
        within_last_bars=2, within_last_mode="any",
    )
    assert evaluate_condition(cond, ctx) is True
    assert ctx.evidence[0].bars_ago == 1


def test_within_last_forming_bar_skips_transitions():
    """Transitions on the forming bar are not counted when N>0.

    The skip returns None for the forming bar's contribution to the
    walk; per tri-valued reduction this propagates to None overall
    when no other bar in the window registers a True. The trader-spec
    intent is "the forming-bar cross doesn't fire eagerly" — a None
    means "wait for the close to confirm" rather than firing.
    """
    # Close [100, 99, 101]. With ctx idx=2 NOT forming → cross fires.
    candles = _candles([100.0, 99.0, 101.0])
    ctx_closed = _ctx(candles, idx=2)
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_CROSSES_ABOVE,
        params={"right": FieldRef.literal(100.0), "lookback": 1},
        interval="5m",
        within_last_bars=1, within_last_mode="any",
    )
    assert evaluate_condition(cond, ctx_closed) is True
    # Same bars but mark current bar as forming → cross at i is skipped.
    # Walking [1, 2]: j=2 (forming) → None; j=1 → no cross (close[1]=99
    # was not above 100 first). Reduce: saw_none=True, no True → None.
    ctx_forming = make_context("TEST", "5m", candles, current_index=2,
                               is_forming=True)
    assert evaluate_condition(cond, ctx_forming) is None
    # No evidence recorded since nothing fired True.
    assert ctx_forming.evidence == []


def test_within_last_forming_skip_finds_earlier_match():
    """Even when the forming bar's would-be cross is skipped, an
    earlier closed-bar cross in the window still fires."""
    # close: [100, 99, 101, 99, 102]. With lookback=1:
    #   j=2: prev=close[1]=99, cur=close[2]=101 → cross above 100 ✓
    #   j=3: prev=close[2]=101 → no cross.
    #   j=4: prev=close[3]=99,  cur=close[4]=102 → cross above 100 ✓
    candles = _candles([100.0, 99.0, 101.0, 99.0, 102.0])
    cond = Condition(
        left=FieldRef.builtin("close"), op=OP_CROSSES_ABOVE,
        params={"right": FieldRef.literal(100.0), "lookback": 1},
        interval="5m",
        within_last_bars=3, within_last_mode="any",
    )
    # Mark idx=4 as forming → walk skips j=4, but finds j=2 cross.
    ctx = make_context("TEST", "5m", candles, current_index=4, is_forming=True)
    assert evaluate_condition(cond, ctx) is True
    assert ctx.evidence[0].bars_ago == 2  # i=4, j=2 → 4-2=2


def test_within_last_forming_bar_does_not_skip_comparisons():
    """Comparison ops fire on the forming bar even when N>0."""
    candles = _candles([100.0, 100.0, 105.0])
    ctx = make_context("TEST", "5m", candles, current_index=2, is_forming=True)
    cond = _close_gt_lit_cond(104.0, within_last_bars=2, within_last_mode="any")
    assert evaluate_condition(cond, ctx) is True
    # Trigger bar IS the forming current bar.
    assert ctx.evidence[0].bars_ago == 0


def test_within_last_tri_valued_none_propagates():
    """If only Nones and Falses in window → None."""
    # SMA(50) on a 4-bar series yields all NaN (warmup) → None at every
    # bar in the walk → walk returns None.
    ctx = _ctx(_candles([100.0, 101.0, 102.0, 103.0]), idx=3)
    cond = Condition(
        left=FieldRef.indicator("sma", params={"length": 50}),
        op=OP_GT,
        params={"right": FieldRef.literal(99.0)},
        interval="5m",
        within_last_bars=2, within_last_mode="any",
    )
    assert evaluate_condition(cond, ctx) is None


def test_within_last_resets_daily_clamps_to_session_open():
    """A look-back into yesterday is clamped when the field resets daily.

    HOD is daily-resetting per FieldSpec metadata. The walk must NOT
    cross the prior session boundary even when N is large enough to
    reach it.
    """
    from datetime import datetime, timezone

    # Day-1 highs are HIGH (HOD reaches ~113) and day-2 highs are LOW
    # (HOD only reaches ~102). A "HOD > 105" within-last-N walk:
    #   - no clamp: would match at any day-1 bar.
    #   - with clamp: window stays in day-2 → no match → False.
    day1_start = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)
    day2_start = datetime(2026, 5, 5, 9, 30, tzinfo=timezone.utc)
    c1 = _candles([110.0, 111.0, 112.0], start=day1_start, interval_min=5)
    c2 = _candles([100.0, 99.0, 101.0], start=day2_start, interval_min=5)
    candles = c1 + c2

    # Sanity: a non-daily-resetting field (close) DOES walk past the
    # session boundary — confirms our test is observing the clamp,
    # not some unrelated short-circuit.
    cond_close = _close_gt_lit_cond(108.0, within_last_bars=10)
    ctx_close = _ctx(candles, idx=5)
    assert evaluate_condition(cond_close, ctx_close) is True
    # Match found in day-1 (close[2]=112) → bars_ago=3 (i=5, j=2).
    assert ctx_close.evidence[0].bars_ago == 3

    # Same shape on HOD (daily-resetting) → clamp engages → no match.
    cond_daily = Condition(
        left=FieldRef.builtin("hod"),
        op=OP_GT,
        params={"right": FieldRef.literal(105.0)},
        interval="5m",
        within_last_bars=10,  # would reach day-1 without clamp.
        within_last_mode="any",
    )
    ctx_daily = _ctx(candles, idx=5)
    assert evaluate_condition(cond_daily, ctx_daily) is False
    assert ctx_daily.evidence == []


def test_within_last_group_level_walk_requires_same_bar():
    """Group within_last requires all children to fire on the SAME bar."""
    # close: [100, 105, 99, 101, 99]
    # Setup: child A is "close > 104" (true at i=1 only).
    #        child B is "close > 100" (true at i=1, i=3).
    # Per-Condition look-back any with N=4 would fire BOTH and AND → True
    # (different bars OK). Per-Group look-back N=2 with anchor i=4 walks
    # window [2, 3, 4] for the AND-of-A-and-B → A is False everywhere
    # in that window → group walk returns False.
    ctx = _ctx(_candles([100.0, 105.0, 99.0, 101.0, 99.0]), idx=4)
    cond_a = _close_gt_lit_cond(104.0)
    cond_b = _close_gt_lit_cond(100.0)
    grp = Group(
        combinator="and",
        children=[cond_a, cond_b],
        within_last_bars=2,
        within_last_mode="any",
    )
    assert evaluate_group(grp, ctx) is False
    assert ctx.evidence == []  # no group-level match → no group evidence


def test_within_last_group_walk_records_group_evidence_on_match():
    """When a Group walk fires, evidence carries the group's id (value=None)."""
    # close: [100, 105, 106, 99]; AND of (>104 AND >100). Both true at i=1
    # and i=2 simultaneously. Anchor i=3 with N=2 → window [1,2,3].
    ctx = _ctx(_candles([100.0, 105.0, 106.0, 99.0]), idx=3)
    cond_a = _close_gt_lit_cond(104.0)
    cond_b = _close_gt_lit_cond(100.0)
    grp = Group(
        combinator="and",
        children=[cond_a, cond_b],
        within_last_bars=2,
        within_last_mode="any",
    )
    assert evaluate_group(grp, ctx) is True
    # 'any' mode walks most-recent-first → match at j=2 (bars_ago=1).
    assert len(ctx.evidence) == 1
    ev = ctx.evidence[0]
    assert ev.node_id == grp.id
    assert ev.value is None  # groups don't carry a scalar value
    assert ev.bars_ago == 1


