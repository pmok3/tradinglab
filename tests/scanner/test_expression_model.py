"""Pure-model tests for the composable Expression operand (FieldRef kind).

Covers ExprToken validation, FieldRef.expression guards + JSON round-trip,
structural validation, and the pure infix evaluator (precedence, parens,
right-assoc power, modulo, divide-by-zero, None-propagation, nesting).
"""
from __future__ import annotations

import pytest

from tradinglab.scanner.model import (
    EXPR_BINARY_OPS,
    FIELD_KIND_EXPRESSION,
    ExprToken,
    FieldRef,
    _eval_infix,
    evaluate_expression,
    validate_expression,
)


def _lit(v):
    return FieldRef.literal(v)


def _opnd(ref):
    return ExprToken.operand_token(ref)


def _op(o):
    return ExprToken.op_token(o)


def _expr(*terms):
    return FieldRef.expression(terms)


# --------------------------------------------------------------- ExprToken

def test_operand_token_requires_ref():
    with pytest.raises(ValueError):
        ExprToken(kind="operand")


def test_operand_token_rejects_op_text():
    with pytest.raises(ValueError):
        ExprToken(kind="operand", operand=_lit(1), op="+")


def test_op_token_rejects_unknown_op():
    with pytest.raises(ValueError):
        ExprToken(kind="op", op="&&")


def test_op_token_rejects_operand():
    with pytest.raises(ValueError):
        ExprToken(kind="op", op="+", operand=_lit(1))


def test_token_rejects_unknown_kind():
    with pytest.raises(ValueError):
        ExprToken(kind="banana")


def test_all_binary_ops_and_parens_construct():
    for o in (*EXPR_BINARY_OPS, "(", ")"):
        assert ExprToken.op_token(o).op == o


# ------------------------------------------------------ FieldRef.expression

def test_expression_ref_rejects_id_value_output():
    with pytest.raises(ValueError):
        FieldRef(kind=FIELD_KIND_EXPRESSION, id="x")
    with pytest.raises(ValueError):
        FieldRef(kind=FIELD_KIND_EXPRESSION, value=1.0)
    with pytest.raises(ValueError):
        FieldRef(kind=FIELD_KIND_EXPRESSION, output_key="k")


def test_non_expression_kind_rejects_terms():
    with pytest.raises(ValueError):
        FieldRef(kind="indicator", id="ema", terms=(_op("+"),))


def test_expression_factory_freezes_terms_to_tuple():
    ref = FieldRef.expression([_opnd(_lit(1)), _op("+"), _opnd(_lit(2))])
    assert isinstance(ref.terms, tuple)
    assert len(ref.terms) == 3


# ------------------------------------------------------------- round-trip

def test_round_trip_simple():
    ref = _expr(_opnd(FieldRef.indicator("ema", params={"length": 9})),
                _op("*"), _opnd(_lit(0.5)))
    assert FieldRef.from_dict(ref.to_dict()).to_dict() == ref.to_dict()


def test_round_trip_nested_and_parens():
    inner = _expr(_opnd(_lit(1)), _op("+"), _opnd(_lit(2)))
    ref = _expr(_op("("), _opnd(inner), _op(")"), _op("*"), _opnd(_lit(3)))
    assert FieldRef.from_dict(ref.to_dict()).to_dict() == ref.to_dict()


def test_round_trip_preserves_operand_kinds():
    ref = _expr(_opnd(FieldRef.builtin("close")), _op("+"),
                _opnd(FieldRef.indicator("rsi", params={"length": 14})),
                _op("-"), _opnd(_lit(30)))
    rt = FieldRef.from_dict(ref.to_dict())
    kinds = [t.operand.kind for t in rt.terms if t.kind == "operand"]
    assert kinds == ["builtin", "indicator", "literal"]


# --------------------------------------------------------- validate_expression

@pytest.mark.parametrize("terms,ok", [
    ((_opnd(_lit(1)), _op("+"), _opnd(_lit(2))), True),
    ((_opnd(_lit(1)),), True),
    ((_op("("), _opnd(_lit(1)), _op(")")), True),
    ((), False),
    ((_op("+"), _opnd(_lit(1))), False),          # leading operator
    ((_opnd(_lit(1)), _op("+")), False),           # trailing operator
    ((_opnd(_lit(1)), _opnd(_lit(2))), False),     # missing operator
    ((_op("("), _opnd(_lit(1))), False),           # unbalanced (
    ((_opnd(_lit(1)), _op(")")), False),           # unbalanced )
])
def test_validate(terms, ok):
    assert validate_expression(terms)[0] is ok


# -------------------------------------------------- evaluate / _eval_infix

def _resolve(m):
    def r(ref):
        return ref.value if ref.kind == "literal" else m.get(ref.id)
    return r


def test_precedence_mul_before_add():
    terms = (_opnd(_lit(2)), _op("+"), _opnd(_lit(3)), _op("*"), _opnd(_lit(4)))
    assert evaluate_expression(terms, _resolve({})) == 14.0


def test_parens_override_precedence():
    terms = (_op("("), _opnd(_lit(2)), _op("+"), _opnd(_lit(3)), _op(")"),
             _op("*"), _opnd(_lit(4)))
    assert evaluate_expression(terms, _resolve({})) == 20.0


def test_power_is_right_associative():
    assert _eval_infix([2.0, "**", 3.0, "**", 2.0]) == 512.0


def test_modulo():
    assert _eval_infix([7.0, "%", 3.0]) == 1.0


def test_divide_by_zero_is_none():
    assert _eval_infix([1.0, "/", 0.0]) is None


def test_modulo_by_zero_is_none():
    assert _eval_infix([1.0, "%", 0.0]) is None


def test_malformed_infix_is_none():
    assert _eval_infix([1.0, "+"]) is None
    assert _eval_infix(["+", 1.0]) is None
    assert _eval_infix([1.0, 2.0]) is None
    assert _eval_infix(["(", 1.0]) is None       # unbalanced
    assert _eval_infix([1.0, ")"]) is None


def test_none_operand_propagates():
    terms = (_opnd(FieldRef.indicator("ema", params={"length": 9})),
             _op("+"), _opnd(_lit(1)))
    assert evaluate_expression(terms, _resolve({})) is None  # ema unresolved


def test_non_finite_result_is_none():
    assert _eval_infix([1e308, "*", 1e308]) is None  # overflow -> inf -> None


def test_nested_expression_operand_evaluates():
    inner = _expr(_opnd(_lit(10)), _op("+"), _opnd(_lit(40)))
    outer = (_opnd(inner), _op("*"), _opnd(_lit(2)))

    def r(ref):
        if ref.kind == "literal":
            return ref.value
        if ref.kind == "expression":
            return evaluate_expression(ref.terms, r)
        return None

    assert evaluate_expression(outer, r) == 100.0
