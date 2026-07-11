"""GUI tests for the ExpressionBuilder widget + its picker integration.

Exercises the widget's model I/O and mutation methods, the new
"Expression" operand type in ``_FieldRefPicker``, and round-tripping an
expression operand through a full ``BlockEditor`` condition. The modal
operand editor (``_OperandDialog``) is not driven here — the builder's
programmatic mutation surface is tested directly.
"""
from __future__ import annotations

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.gui.expression_builder import (
    ExpressionBuilder,
    expression_text,
    operand_summary,
)
from tradinglab.gui.scanner_block_editor import BlockEditor, _FieldRefPicker
from tradinglab.scanner.model import (
    FIELD_KIND_EXPRESSION,
    OP_GT,
    Condition,
    ExprToken,
    FieldRef,
    Group,
)


def _opnd(ref):
    return ExprToken.operand_token(ref)


def _op(o):
    return ExprToken.op_token(o)


def _seed():
    return FieldRef.expression((
        _opnd(FieldRef.indicator("ema", params={"length": 9})), _op("*"),
        _opnd(FieldRef.literal(0.5)), _op("+"),
        _opnd(FieldRef.indicator("rsi", params={"length": 14})),
    ))


# --------------------------------------------------------------- pure helpers

def test_operand_summary_labels():
    assert operand_summary(FieldRef.literal(0.5)) == "0.5"
    assert operand_summary(FieldRef.builtin("close")) == "close"
    assert operand_summary(FieldRef.indicator("ema", params={"length": 9})) == "ema(9)"
    assert operand_summary(None) == "?"


def test_expression_text_render():
    assert expression_text(_seed().terms) == "ema(9) * 0.5 + rsi(14)"


# ------------------------------------------------------------------- widget

def test_builder_empty_is_invalid(root):
    eb = ExpressionBuilder(root)
    assert eb.is_valid() is False
    got = eb.get()
    assert got.kind == FIELD_KIND_EXPRESSION
    assert got.terms == ()


def test_builder_seeded_round_trips(root):
    eb = ExpressionBuilder(root, ref=_seed())
    assert eb.is_valid() is True
    assert eb.get().to_dict() == _seed().to_dict()


def test_builder_set_then_get(root):
    eb = ExpressionBuilder(root)
    eb.set(_seed())
    assert eb.get().to_dict() == _seed().to_dict()


def test_builder_mutations_fire_change(root):
    fired = {"n": 0}
    eb = ExpressionBuilder(root, on_change=lambda: fired.__setitem__("n", fired["n"] + 1))
    eb._terms.append(_opnd(FieldRef.literal(1.0)))
    eb._add_op("+")
    eb._terms.append(_opnd(FieldRef.literal(2.0)))
    eb._changed()
    assert fired["n"] >= 1
    assert eb.is_valid() is True
    assert expression_text(eb.get().terms) == "1 + 2"


def test_builder_remove_token(root):
    eb = ExpressionBuilder(root, ref=_seed())
    n0 = len(eb.get().terms)
    eb._remove(0)
    assert len(eb.get().terms) == n0 - 1


def test_builder_set_op_changes_operator(root):
    eb = ExpressionBuilder(root, ref=_seed())
    eb._set_op(1, "-")  # terms[1] is "*"
    assert eb.get().terms[1].op == "-"


# ------------------------------------------------------- picker integration

def test_picker_has_expression_type(root):
    p = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    assert "Expression" in p._TYPE_LABELS
    assert p._TYPE_LABELS["Expression"] == FIELD_KIND_EXPRESSION


def test_picker_with_expression_ref_returns_expression(root):
    p = _FieldRefPicker(root, ref=_seed())
    got = p.get()
    assert got.kind == FIELD_KIND_EXPRESSION
    assert got.to_dict() == _seed().to_dict()


def test_picker_switch_to_expression_type_yields_empty(root):
    p = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    p._type_var.set("Expression")
    p._on_type_change()
    got = p.get()
    assert got.kind == FIELD_KIND_EXPRESSION
    assert got.terms == ()  # empty until the user stacks tokens


def test_picker_switch_expression_to_builtin(root):
    p = _FieldRefPicker(root, ref=_seed())
    p._type_var.set("Builtin")
    p._on_type_change()
    assert p.get().kind == "builtin"


# ------------------------------------------------------- block editor path

def test_block_editor_preserves_expression_operand(root):
    cond = Condition(left=_seed(), op=OP_GT,
                     params={"right": FieldRef.literal(30.0)}, interval="5m")
    ed = BlockEditor(root, root=Group(combinator="and", children=[cond]))
    out = ed.get_root().children[0]
    assert out.left.kind == FIELD_KIND_EXPRESSION
    assert out.left.to_dict() == _seed().to_dict()
    assert out.op == OP_GT
    assert out.params["right"].value == 30.0
