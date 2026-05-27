"""Tests for the operator dispatch registry in ``scanner.operators``.

These pin:

* Registry completeness: every op declared in
  :data:`scanner.model.OPERATOR_PARAM_SCHEMA` has a matching
  :class:`OpHandler` in :data:`scanner.operators.OPERATOR_EVALUATORS`,
  and vice versa.
* Handler shape: each ``OpHandler.evaluate`` is callable + has the
  expected ``(cond, ctx, i) -> bool | None`` signature.
* Transition flagging: ``crosses_above`` and ``crosses_below`` are the
  only transition ops, matching the prior hand-rolled ``_TRANSITION_OPS``
  frozenset.
* Forming-bar guard: a transition op inside a look-back walk on the
  forming bar returns ``None`` (the central guard in
  ``_evaluate_condition_at``), regardless of operand values; comparison
  ops stay live.
* Behavioural parity: a handful of representative handlers
  (``gt``, ``lt``, ``between``, ``crosses_above``, ``inside_bar``)
  produce the same booleans the prior if/elif chain would have.
* Late-binding: ``operators._evaluate_field_at`` / ``_is_nan_like`` are
  wired by engine.py at module load (without it every handler would
  crash with TypeError on the first None.__call__).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import tradinglab.indicators  # noqa: F401  -- registers indicators at import
from tradinglab.models import Candle
from tradinglab.scanner import operators as ops
from tradinglab.scanner.engine import _evaluate_condition_at, make_context
from tradinglab.scanner.model import (
    OP_BETWEEN,
    OP_CROSSES_ABOVE,
    OP_CROSSES_BELOW,
    OP_GT,
    OP_INSIDE_BAR,
    OP_LT,
    OPERATOR_PARAM_SCHEMA,
    Condition,
    FieldRef,
)

# ---------------------------------------------------------------------------
# Candle fixtures (mirror tests/scanner/test_engine.py)
# ---------------------------------------------------------------------------


def _candles(
    closes,
    *,
    opens=None,
    highs=None,
    lows=None,
    vols=None,
    start=datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc),
    interval_min=5,
):
    n = len(closes)
    opens = opens or [c - 0.5 for c in closes]
    highs = highs or [max(o, c) + 1.0 for o, c in zip(opens, closes, strict=False)]
    lows = lows or [min(o, c) - 1.0 for o, c in zip(opens, closes, strict=False)]
    vols = vols or [1000 + i for i in range(n)]
    out = []
    for i in range(n):
        out.append(
            Candle(
                date=start + timedelta(minutes=i * interval_min),
                open=opens[i],
                high=highs[i],
                low=lows[i],
                close=closes[i],
                volume=vols[i],
                session="regular",
            )
        )
    return out


def _ctx(candles, idx=None):
    return make_context("TEST", "5m", candles, idx)


def _lit(v):
    return FieldRef.literal(float(v))


def _builtin(name):
    return FieldRef.builtin(name)


# ---------------------------------------------------------------------------
# Registry-completeness invariants
# ---------------------------------------------------------------------------


def test_registry_keys_match_schema_keys():
    schema_keys = set(OPERATOR_PARAM_SCHEMA.keys())
    registry_keys = set(ops.OPERATOR_EVALUATORS.keys())
    assert schema_keys == registry_keys, (
        f"missing in registry: {schema_keys - registry_keys}; "
        f"extra in registry: {registry_keys - schema_keys}"
    )


def test_handler_count_matches_schema():
    assert len(ops.OPERATOR_EVALUATORS) == len(OPERATOR_PARAM_SCHEMA) == 19


def test_every_handler_is_callable_with_expected_metadata():
    for op, handler in ops.OPERATOR_EVALUATORS.items():
        assert isinstance(handler, ops.OpHandler), op
        assert callable(handler.evaluate), op
        assert isinstance(handler.is_transition, bool), op


def test_transition_flag_matches_legacy_set():
    expected = {OP_CROSSES_ABOVE, OP_CROSSES_BELOW}
    derived = {op for op, h in ops.OPERATOR_EVALUATORS.items() if h.is_transition}
    assert derived == expected
    assert ops.TRANSITION_OPS == frozenset(expected)


def test_non_transition_ops_are_not_flagged():
    for op, handler in ops.OPERATOR_EVALUATORS.items():
        if op in (OP_CROSSES_ABOVE, OP_CROSSES_BELOW):
            continue
        assert handler.is_transition is False, op


def test_register_op_replaces_handler():
    sentinel = ops.OpHandler(lambda c, x, i: True, is_transition=False)
    original = ops.OPERATOR_EVALUATORS[OP_GT]
    try:
        ops.register_op(OP_GT, sentinel)
        assert ops.OPERATOR_EVALUATORS[OP_GT] is sentinel
    finally:
        ops.register_op(OP_GT, original)
    assert ops.OPERATOR_EVALUATORS[OP_GT] is original


@pytest.mark.parametrize("op", sorted(OPERATOR_PARAM_SCHEMA.keys()))
def test_each_op_has_matching_handler_and_schema(op):
    assert op in ops.OPERATOR_EVALUATORS
    handler = ops.OPERATOR_EVALUATORS[op]
    assert callable(handler.evaluate)
    assert isinstance(handler.is_transition, bool)


# ---------------------------------------------------------------------------
# Forming-bar guard (centralised in _evaluate_condition_at)
# ---------------------------------------------------------------------------


def test_forming_bar_guard_returns_none_for_transition_op():
    ctx = _ctx(_candles([10.0, 10.5, 12.0, 13.0]), idx=3)
    ctx.is_forming = True
    cond = Condition(
        id="c1",
        left=_builtin("close"),
        op=OP_CROSSES_ABOVE,
        params={"right": _lit(11.0), "lookback": 1},
    )
    # Inside a look-back walk, on the forming bar, the central guard
    # MUST suppress transition ops.
    assert _evaluate_condition_at(cond, ctx, 3, _in_lookback_walk=True) is None


def test_forming_bar_guard_does_not_fire_outside_lookback_walk():
    # Series chosen so a transition fires AT bar 3:
    # close[2]=10.5 <= 11, close[3]=12 > 11 → crosses_above bar 3 = True.
    ctx = _ctx(_candles([10.0, 10.2, 10.5, 12.0]), idx=3)
    ctx.is_forming = True
    cond = Condition(
        id="c1",
        left=_builtin("close"),
        op=OP_CROSSES_ABOVE,
        params={"right": _lit(11.0), "lookback": 1},
    )
    # Without _in_lookback_walk, the forming flag is ignored: today's
    # behavior is preserved (the cross fires).
    assert _evaluate_condition_at(cond, ctx, 3) is True


def test_forming_bar_guard_skips_non_transition_ops():
    ctx = _ctx(_candles([10.0, 10.5, 12.0, 13.0]), idx=3)
    ctx.is_forming = True
    cond = Condition(
        id="c1",
        left=_builtin("close"),
        op=OP_GT,
        params={"right": _lit(11.0)},
    )
    # Comparison ops stay live on the forming bar even inside a walk.
    assert _evaluate_condition_at(cond, ctx, 3, _in_lookback_walk=True) is True


# ---------------------------------------------------------------------------
# Per-op behavioural parity (representative coverage)
# ---------------------------------------------------------------------------


def test_eval_gt_true_and_false():
    ctx = _ctx(_candles([10.0, 12.0]))
    cond_t = Condition(id="t", left=_builtin("close"), op=OP_GT, params={"right": _lit(11.0)})
    cond_f = Condition(id="f", left=_builtin("close"), op=OP_GT, params={"right": _lit(20.0)})
    assert _evaluate_condition_at(cond_t, ctx, 1) is True
    assert _evaluate_condition_at(cond_f, ctx, 1) is False


def test_eval_lt_returns_none_when_operand_missing():
    ctx = _ctx(_candles([10.0, 12.0]))
    # OOB index → builtin returns None → tri-valued contract returns None.
    cond = Condition(id="c", left=_builtin("close"), op=OP_LT, params={"right": _lit(20.0)})
    assert _evaluate_condition_at(cond, ctx, -5) is None


def test_eval_between_inclusive_bounds():
    ctx = _ctx(_candles([9.0, 10.0, 11.0]))
    cond = Condition(
        id="c",
        left=_builtin("close"),
        op=OP_BETWEEN,
        params={"low": _lit(10.0), "high": _lit(11.0)},
    )
    # 9 → below low → False; 10 → equal low → True (inclusive);
    # 11 → equal high → True (inclusive).
    assert _evaluate_condition_at(cond, ctx, 0) is False
    assert _evaluate_condition_at(cond, ctx, 1) is True
    assert _evaluate_condition_at(cond, ctx, 2) is True


def test_eval_crosses_above_fires_only_on_transition():
    # close[1]=10.5 < 11 → False at bar 1.
    # close[2]=12 > 11 with prev close[1]=10.5 ≤ 11 → True at bar 2.
    # close[3]=13 > 11 with prev close[2]=12 > 11 → False (no fresh transition).
    ctx = _ctx(_candles([10.0, 10.5, 12.0, 13.0]))
    cond = Condition(
        id="c",
        left=_builtin("close"),
        op=OP_CROSSES_ABOVE,
        params={"right": _lit(11.0), "lookback": 1},
    )
    assert _evaluate_condition_at(cond, ctx, 0) is None  # lookback OOB
    assert _evaluate_condition_at(cond, ctx, 1) is False
    assert _evaluate_condition_at(cond, ctx, 2) is True
    assert _evaluate_condition_at(cond, ctx, 3) is False


def test_eval_inside_bar_detects_inside_pattern():
    # Build candles where bar 2 has narrower high/low than bar 1.
    candles = _candles(
        [11.0, 10.0, 10.0],
        opens=[10.0, 10.0, 10.0],
        highs=[12.0, 15.0, 14.0],
        lows=[8.0, 5.0, 6.0],
    )
    ctx = _ctx(candles)
    cond = Condition(id="c", left=_builtin("close"), op=OP_INSIDE_BAR, params={})
    assert _evaluate_condition_at(cond, ctx, 0) is None   # i < 1
    assert _evaluate_condition_at(cond, ctx, 1) is False  # bar 1 wider than bar 0
    assert _evaluate_condition_at(cond, ctx, 2) is True   # bar 2 inside bar 1


# ---------------------------------------------------------------------------
# Unknown-op contract (logs + returns None, never raises)
# ---------------------------------------------------------------------------


def test_unknown_op_returns_none(caplog):
    # Condition.__post_init__ validates op against OPERATOR_PARAM_SCHEMA,
    # so build a valid Condition then bypass the dataclass field to
    # mutate op into something unknown. This pins the engine's
    # "unknown op → log + return None, never raise" contract.
    ctx = _ctx(_candles([10.0, 12.0]))
    cond = Condition(
        id="c", left=_builtin("close"), op=OP_GT, params={"right": _lit(0.0)}
    )
    object.__setattr__(cond, "op", "not_a_real_op")
    with caplog.at_level("ERROR"):
        result = _evaluate_condition_at(cond, ctx, 1)
    assert result is None
    assert any("unknown op" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------------------
# Late-binding sanity: operators._evaluate_field_at must be wired by engine
# ---------------------------------------------------------------------------


def test_late_binding_wired_by_engine():
    # If engine.py forgets to wire these slots, every handler crashes
    # with TypeError on the first None.__call__. Pin the wiring here.
    from tradinglab.scanner import engine

    assert ops._evaluate_field_at is engine.evaluate_field_at
    assert ops._is_nan_like is engine._is_nan_like
