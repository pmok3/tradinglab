"""Engine-path tests for the Expression operand.

Verifies the scanner engine resolves an expression FieldRef per-bar:
composing builtins / indicators / literals, None-propagation on warmup /
OOB, and use as either side of a Condition.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import tradinglab.indicators  # noqa: F401  -- registers indicators at import
from tradinglab.models import Candle
from tradinglab.scanner.engine import (
    evaluate_condition,
    evaluate_field,
    make_context,
)
from tradinglab.scanner.model import (
    OP_GT,
    Condition,
    ExprToken,
    FieldRef,
)

_START = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)


def _candles(closes):
    return [
        Candle(date=_START + timedelta(minutes=5 * i), open=c - 0.5,
               high=c + 1.0, low=c - 1.0, close=c, volume=1000 + i,
               session="regular")
        for i, c in enumerate(closes)
    ]


def _ctx(closes, idx):
    return make_context("TEST", "5m", _candles(closes), idx)


def _opnd(ref):
    return ExprToken.operand_token(ref)


def _op(o):
    return ExprToken.op_token(o)


def _lit(v):
    return FieldRef.literal(v)


def test_expression_over_builtin_and_literals():
    ctx = _ctx([100, 101, 102], idx=2)  # close = 102
    # close * 0.5 + 10 = 61
    ref = FieldRef.expression((
        _opnd(FieldRef.builtin("close")), _op("*"), _opnd(_lit(0.5)),
        _op("+"), _opnd(_lit(10.0)),
    ))
    assert evaluate_field(ref, ctx) == pytest.approx(61.0)


def test_expression_combines_indicator_and_builtin():
    closes = [10.0] * 5 + [20.0] * 5
    ctx = _ctx(closes, idx=9)  # sma(5) = 20, close = 20
    ref = FieldRef.expression((
        _opnd(FieldRef.builtin("close")), _op("+"),
        _opnd(FieldRef.indicator("sma", params={"length": 5})),
    ))
    assert evaluate_field(ref, ctx) == pytest.approx(40.0)


def test_expression_parens_and_precedence_via_engine():
    ctx = _ctx([100, 101, 102], idx=2)  # close = 102
    # (close + 8) * 0.5 = 55
    ref = FieldRef.expression((
        _op("("), _opnd(FieldRef.builtin("close")), _op("+"), _opnd(_lit(8.0)),
        _op(")"), _op("*"), _opnd(_lit(0.5)),
    ))
    assert evaluate_field(ref, ctx) == pytest.approx(55.0)


def test_expression_none_when_indicator_in_warmup():
    ctx = _ctx([10.0, 11.0], idx=1)  # sma(5) is NaN warmup
    ref = FieldRef.expression((
        _opnd(FieldRef.builtin("close")), _op("+"),
        _opnd(FieldRef.indicator("sma", params={"length": 5})),
    ))
    assert evaluate_field(ref, ctx) is None


def test_expression_oob_returns_none():
    ctx = _ctx([100, 101], idx=5)  # index past end
    ref = FieldRef.expression((
        _opnd(FieldRef.builtin("close")), _op("+"), _opnd(_lit(1.0)),
    ))
    assert evaluate_field(ref, ctx) is None


def test_expression_as_condition_left_operand():
    ctx = _ctx([100, 101, 102], idx=2)  # close = 102
    expr = FieldRef.expression((
        _opnd(FieldRef.builtin("close")), _op("*"), _opnd(_lit(0.5)),
    ))  # = 51
    assert evaluate_condition(
        Condition(left=expr, op=OP_GT, params={"right": _lit(50.0)},
                  interval="5m"), ctx) is True
    assert evaluate_condition(
        Condition(left=expr, op=OP_GT, params={"right": _lit(60.0)},
                  interval="5m"), ctx) is False


def test_expression_as_condition_right_operand():
    ctx = _ctx([100, 101, 102], idx=2)  # close = 102
    expr = FieldRef.expression((
        _opnd(FieldRef.builtin("close")), _op("*"), _opnd(_lit(0.5)),
    ))  # = 51
    # close > (close * 0.5)  ->  102 > 51  -> True
    assert evaluate_condition(
        Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": expr}, interval="5m"), ctx) is True
