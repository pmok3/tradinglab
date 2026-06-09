"""Equivalence: ``evaluate_group_vec`` (all-bars numpy) vs the per-bar
``evaluate_group`` scalar path.

The vectorized evaluator is the Conditions-mode custom-indicator fast path
(compute #2). It MUST return, for every bar, exactly the tri-valued result the
scalar ``evaluate_group`` produces (True→1.0, False→0.0, None→NaN), OR return
``None`` to signal "unsupported — fall back to the scalar loop". These tests
pin both: bit-equivalence on the supported subset, and the ``None`` fallback
sentinel on the unsupported remainder.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.models import Candle
from tradinglab.scanner.engine import (
    EvaluationContext,
    IndicatorMemo,
    evaluate_group,
    evaluate_group_vec,
)
from tradinglab.scanner.model import (
    Condition,
    FieldRef,
    Group,
)

_INTERVAL = "1d"


def _candles(n: int, seed: int = 0) -> list[Candle]:
    rng = np.random.default_rng(seed)
    base = datetime(2024, 1, 2, 9, 30)
    out: list[Candle] = []
    price = 100.0
    for i in range(n):
        o = price
        c = price + float(rng.normal(0, 1.2))
        hi = max(o, c) + abs(float(rng.normal(0, 0.6)))
        lo = min(o, c) - abs(float(rng.normal(0, 0.6)))
        out.append(Candle(date=base + timedelta(days=i), open=o, high=hi,
                          low=lo, close=c, volume=1000 + i * 5, session="regular"))
        price = c
    return out


def _ctx(candles: list[Candle]) -> EvaluationContext:
    bars = Bars.from_candles(candles)
    memo = IndicatorMemo(candles=candles)
    memo._bars = bars
    return EvaluationContext(
        symbol="<custom>", interval=_INTERVAL, bars=bars, candles=candles,
        current_index=0, memo=memo,
    )


def _scalar_series(grp: Group, ctx: EvaluationContext) -> np.ndarray:
    """The per-bar reference: exactly what the codegen loop produces."""
    n = len(ctx.bars)
    out = np.full(n, np.nan, dtype=float)
    for i in range(n):
        ctx.current_index = i
        ctx.evidence = []
        try:
            v = evaluate_group(grp, ctx)
        except Exception:  # noqa: BLE001
            v = None
        if v is True:
            out[i] = 1.0
        elif v is False:
            out[i] = 0.0
    return out


def _vec_series(grp: Group, ctx: EvaluationContext) -> np.ndarray | None:
    n = len(ctx.bars)
    masks = evaluate_group_vec(grp, ctx)
    if masks is None:
        return None
    is_true, is_false = masks
    out = np.full(n, np.nan, dtype=float)
    out[is_true] = 1.0
    out[is_false] = 0.0
    return out


def _assert_equiv(grp: Group, candles: list[Candle]) -> None:
    ref = _scalar_series(grp, _ctx(candles))
    vec = _vec_series(grp, _ctx(candles))
    assert vec is not None, "expected vectorizable tree to engage"
    # NaN-aware exact equality.
    assert np.array_equal(ref, vec, equal_nan=True), (
        f"\nref={ref}\nvec={vec}\ndiff_idx={np.where(~((ref == vec) | (np.isnan(ref) & np.isnan(vec))))[0]}"
    )


# --- explicit trees --------------------------------------------------------

def _cond(left, op, **params):
    return Condition(left=left, op=op, params=params, interval=_INTERVAL)


def test_close_gt_ema():
    g = Group(combinator="and", children=[
        _cond(FieldRef.builtin("close"), ">",
              right=FieldRef.indicator("ema", params={"length": 20})),
    ])
    _assert_equiv(g, _candles(120, 1))


def test_ema_cross_above():
    g = Group(combinator="and", children=[
        _cond(FieldRef.indicator("ema", params={"length": 9}), "crosses_above",
              right=FieldRef.indicator("ema", params={"length": 20}), lookback=1),
    ])
    _assert_equiv(g, _candles(150, 2))


def test_ema_cross_below_lookback3():
    g = Group(combinator="and", children=[
        _cond(FieldRef.indicator("ema", params={"length": 5}), "crosses_below",
              right=FieldRef.indicator("ema", params={"length": 15}), lookback=3),
    ])
    _assert_equiv(g, _candles(150, 3))


def test_between_literal():
    g = Group(combinator="and", children=[
        _cond(FieldRef.builtin("close"), "between",
              low=FieldRef.literal(95.0), high=FieldRef.literal(105.0)),
    ])
    _assert_equiv(g, _candles(120, 4))


def test_within_pct():
    g = Group(combinator="and", children=[
        _cond(FieldRef.builtin("close"), "within_pct",
              target=FieldRef.indicator("ema", params={"length": 20}),
              tolerance_pct=1.5),
    ])
    _assert_equiv(g, _candles(120, 5))


def test_nested_and_or_mixed_fields():
    inner = Group(combinator="or", children=[
        _cond(FieldRef.builtin("close"), ">",
              right=FieldRef.indicator("ema", params={"length": 10})),
        _cond(FieldRef.builtin("volume"), ">", right=FieldRef.literal(1050.0)),
    ])
    outer = Group(combinator="and", children=[
        inner,
        _cond(FieldRef.builtin("high"), ">", right=FieldRef.builtin("low")),
    ])
    _assert_equiv(outer, _candles(140, 6))


def test_or_combinator_top():
    g = Group(combinator="or", children=[
        _cond(FieldRef.builtin("close"), ">", right=FieldRef.literal(1e9)),  # ~never
        _cond(FieldRef.builtin("high"), ">=", right=FieldRef.builtin("low")),  # ~always
    ])
    _assert_equiv(g, _candles(80, 7))


def test_disabled_child_skipped():
    g = Group(combinator="and", children=[
        Condition(left=FieldRef.builtin("close"), op=">",
                  params={"right": FieldRef.literal(1e9)}, interval=_INTERVAL,
                  enabled=False),  # disabled "never" — must be ignored
        _cond(FieldRef.builtin("high"), ">=", right=FieldRef.builtin("low")),
    ])
    _assert_equiv(g, _candles(60, 8))


def test_all_six_comparisons():
    for op in (">", "<", ">=", "<=", "==", "!="):
        g = Group(combinator="and", children=[
            _cond(FieldRef.builtin("close"), op,
                  right=FieldRef.indicator("ema", params={"length": 8})),
        ])
        _assert_equiv(g, _candles(100, 9))


def test_warmup_region_is_nan_in_both():
    g = Group(combinator="and", children=[
        _cond(FieldRef.builtin("close"), ">",
              right=FieldRef.indicator("ema", params={"length": 30})),
    ])
    candles = _candles(60, 10)
    ref = _scalar_series(g, _ctx(candles))
    vec = _vec_series(g, _ctx(candles))
    # Both NaN over the EMA warmup (first 29 bars).
    assert np.all(np.isnan(ref[:29]))
    assert np.all(np.isnan(vec[:29]))
    assert np.array_equal(ref, vec, equal_nan=True)


# --- unsupported trees must return None (fall back) -------------------------

def test_within_last_falls_back():
    g = Group(combinator="and", children=[
        Condition(left=FieldRef.builtin("close"), op=">",
                  params={"right": FieldRef.literal(100.0)}, interval=_INTERVAL,
                  within_last_bars=3),
    ])
    assert evaluate_group_vec(g, _ctx(_candles(40, 11))) is None


def test_group_within_last_falls_back():
    g = Group(combinator="and", within_last_bars=5, children=[
        _cond(FieldRef.builtin("close"), ">", right=FieldRef.literal(100.0)),
    ])
    assert evaluate_group_vec(g, _ctx(_candles(40, 12))) is None


def test_is_rising_op_falls_back():
    g = Group(combinator="and", children=[
        Condition(left=FieldRef.builtin("close"), op="is_rising",
                  params={"lookback": 3}, interval=_INTERVAL),
    ])
    assert evaluate_group_vec(g, _ctx(_candles(40, 13))) is None


def test_cross_interval_falls_back():
    g = Group(combinator="and", children=[
        Condition(left=FieldRef.builtin("close"), op=">",
                  params={"right": FieldRef.literal(100.0)}, interval="5m"),
    ])
    assert evaluate_group_vec(g, _ctx(_candles(40, 14))) is None


def test_non_column_builtin_falls_back():
    g = Group(combinator="and", children=[
        _cond(FieldRef.builtin("ha_streak"), ">", right=FieldRef.literal(0.0)),
    ])
    assert evaluate_group_vec(g, _ctx(_candles(40, 15))) is None


def test_cross_symbol_field_falls_back():
    g = Group(combinator="and", children=[
        _cond(FieldRef.builtin("close"), ">",
              right=FieldRef.indicator("ema", params={"length": 20}, symbol="SPY")),
    ])
    assert evaluate_group_vec(g, _ctx(_candles(40, 16))) is None


def test_empty_group_all_nan():
    g = Group(combinator="and", children=[])
    candles = _candles(20, 17)
    vec = _vec_series(g, _ctx(candles))
    ref = _scalar_series(g, _ctx(candles))
    assert vec is not None
    assert np.all(np.isnan(vec))
    assert np.array_equal(ref, vec, equal_nan=True)


# --- randomized fuzz over the supported subset -----------------------------

_EMA_LENS = (5, 9, 12, 20)
_COLUMNS = ("close", "open", "high", "low", "volume")
_CMP = (">", "<", ">=", "<=", "==", "!=")


def _rand_field(rng):
    r = rng.integers(0, 3)
    if r == 0:
        return FieldRef.builtin(_COLUMNS[int(rng.integers(0, len(_COLUMNS)))])
    if r == 1:
        return FieldRef.indicator("ema", params={"length": int(rng.choice(_EMA_LENS))})
    return FieldRef.literal(float(rng.uniform(90, 110)))


def _rand_condition(rng):
    op_r = rng.integers(0, 4)
    left = _rand_field(rng)
    if op_r == 0:  # comparison
        return _cond(left, _CMP[int(rng.integers(0, len(_CMP)))], right=_rand_field(rng))
    if op_r == 1:  # between
        lo = float(rng.uniform(85, 100))
        return _cond(left, "between", low=FieldRef.literal(lo),
                     high=FieldRef.literal(lo + float(rng.uniform(1, 20))))
    if op_r == 2:  # cross
        op = "crosses_above" if rng.integers(0, 2) else "crosses_below"
        return _cond(left, op, right=_rand_field(rng),
                     lookback=int(rng.integers(1, 4)))
    return _cond(left, "within_pct", target=_rand_field(rng),
                 tolerance_pct=float(rng.uniform(0.5, 5.0)))


def _rand_group(rng, depth=0):
    combinator = "and" if rng.integers(0, 2) else "or"
    k = int(rng.integers(1, 4))
    children = []
    for _ in range(k):
        if depth < 2 and rng.integers(0, 4) == 0:
            children.append(_rand_group(rng, depth + 1))
        else:
            children.append(_rand_condition(rng))
    return Group(combinator=combinator, children=children)


@pytest.mark.parametrize("seed", list(range(40)))
def test_random_trees_match_scalar(seed):
    rng = np.random.default_rng(1000 + seed)
    grp = _rand_group(rng)
    candles = _candles(int(rng.integers(60, 160)), seed=2000 + seed)
    _assert_equiv(grp, candles)
