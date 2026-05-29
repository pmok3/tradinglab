"""Auto-stack ConditionFrame classification tests (CLAUDE.md §7.19).

Pins the layout-selection rule that flips
:class:`_ConditionFrame` between the historical inline 1-row layout
and the new stacked 3-row layout. The rule lives in
:meth:`_ConditionFrame._classify_layout` and the helper
:func:`_picker_ref_is_complex`.

Coverage matrix:

- inline: ``close > 100`` (Number RHS, builtin LHS)
- inline: ``ema(20) > 100`` (1-param indicator stays simple)
- stacked: ``rvol(...) > 100`` (6 trigger-relevant params)
- stacked: ``close > rvol(...)`` (complex RHS)
- stacked: ``bbands > 100`` (multi-output indicator: middle/upper/lower)
- stacked: ``close between low=0 high=10`` (op-forced via OP_BETWEEN)
- stacked: cross-symbol pin on LEFT
- stacked: cross-symbol pin on RHS
- inline → stacked transition on op change (gt → between)
- stacked → inline transition on indicator change (rvol → close)
- structural ops with hidden LEFT (inside_bar) → inline even if
  ``cond.left`` would otherwise be flagged complex (the picker isn't
  visible).
"""

from __future__ import annotations

import math

import pytest

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.gui.scanner_block_editor import (
    BlockEditor,
    _picker_ref_is_complex,
)
from tradinglab.scanner.model import (
    OP_BETWEEN,
    OP_CROSSES_ABOVE,
    OP_GT,
    OP_INSIDE_BAR,
    OP_WITHIN_PCT,
    Condition,
    FieldRef,
    Group,
)

# ---------------------------------------------------------------------------
# Pure ref-classifier (no Tk, no widget)
# ---------------------------------------------------------------------------


def test_literal_is_not_complex():
    assert _picker_ref_is_complex(FieldRef.literal(100.0)) is False


def test_builtin_without_symbol_is_not_complex():
    assert _picker_ref_is_complex(FieldRef.builtin("close")) is False


def test_builtin_with_symbol_pin_is_complex():
    assert _picker_ref_is_complex(
        FieldRef.builtin("close", symbol="SPY")) is True


def test_one_param_indicator_is_not_complex():
    # EMA has a single ``length`` param and a single output — simple.
    assert _picker_ref_is_complex(
        FieldRef.indicator("ema", params={"length": 20})) is False


def test_indicator_with_three_plus_params_is_complex():
    # RVOL exposes 6 trigger-relevant params (the threshold_warn /
    # threshold_extreme are pruned by TRIGGER_RELEVANT_PARAMS).
    assert _picker_ref_is_complex(FieldRef.indicator("rvol")) is True


def test_multi_output_indicator_is_complex():
    # Bollinger has 2 params (length, k_value) but 3 outputs
    # (middle / upper / lower) — multi-output flips it complex.
    assert _picker_ref_is_complex(FieldRef.indicator("bbands")) is True


def test_adx_is_complex_via_multi_output():
    # ADX: small params_schema, but 3 outputs (adx, +di, -di).
    assert _picker_ref_is_complex(FieldRef.indicator("adx")) is True


def test_indicator_with_symbol_pin_is_complex():
    assert _picker_ref_is_complex(
        FieldRef.indicator("ema", params={"length": 20}, symbol="QQQ")) is True


def test_none_ref_is_not_complex():
    # Defensive: classifier should not crash on a missing ref.
    assert _picker_ref_is_complex(None) is False


def test_unknown_indicator_kind_is_not_complex():
    # Cross-symbol pin not set + indicator id not in the registry =>
    # fall through to False (so the classifier never raises on a
    # stale persisted ref pointing at a deleted indicator).
    assert _picker_ref_is_complex(
        FieldRef.indicator("zzz_not_registered")) is False


# ---------------------------------------------------------------------------
# _ConditionFrame._classify_layout integration
# ---------------------------------------------------------------------------


def _cond_frame_for(cond: Condition, root):
    """Build a BlockEditor + return the inner _ConditionFrame for ``cond``."""
    ed = BlockEditor(root, root=Group(combinator="and", children=[cond]))
    return ed._root_frame._child_frames[0]


def test_close_gt_100_is_inline(root):
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    cf = _cond_frame_for(c, root)
    assert cf._classify_layout() == "inline"


def test_ema_20_gt_100_is_inline(root):
    c = Condition(left=FieldRef.indicator("ema", params={"length": 20}),
                  op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    cf = _cond_frame_for(c, root)
    assert cf._classify_layout() == "inline"


def test_rvol_gt_100_is_stacked(root):
    c = Condition(left=FieldRef.indicator("rvol"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    cf = _cond_frame_for(c, root)
    assert cf._classify_layout() == "stacked"


def test_close_gt_rvol_is_stacked_via_complex_rhs(root):
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.indicator("rvol")},
                  interval="5m")
    cf = _cond_frame_for(c, root)
    assert cf._classify_layout() == "stacked"


def test_bbands_gt_100_is_stacked_via_multi_output(root):
    c = Condition(left=FieldRef.indicator("bbands"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    cf = _cond_frame_for(c, root)
    assert cf._classify_layout() == "stacked"


def test_between_op_forces_stacked(root):
    # ``close between low=0 high=10`` — neither picker is complex
    # individually but BETWEEN's two RHS slots always go stacked.
    c = Condition(left=FieldRef.builtin("close"), op=OP_BETWEEN,
                  params={"low": FieldRef.literal(0.0),
                          "high": FieldRef.literal(10.0)},
                  interval="5m")
    cf = _cond_frame_for(c, root)
    assert cf._classify_layout() == "stacked"


def test_cross_symbol_pin_on_left_classifies_per_fit(root):
    """Cross-symbol pin is no longer a hard "always stacked" trigger.

    Under the new fit-based classifier (CLAUDE.md §7.19), the pin
    just adds an ``@TICKER`` cluster to the picker's flow. Whether
    the row stacks depends on whether the total inline width fits
    the available dialog width — not on the pin's presence alone.
    A simple ``close@SPY > 100`` fits the default 1200 px assumed
    width and stays inline; widening or narrowing the dialog flips
    the classification accordingly.
    """
    c = Condition(left=FieldRef.builtin("close", symbol="SPY"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    cf = _cond_frame_for(c, root)
    # At the default 1200 px assumed width, `close@SPY > 100`
    # comfortably fits — stays inline.
    assert cf._classify_layout() == "inline"


def test_cross_symbol_pin_on_rhs_classifies_per_fit(root):
    """Symmetric of :func:`test_cross_symbol_pin_on_left_classifies_per_fit`."""
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.builtin("close", symbol="QQQ")},
                  interval="5m")
    cf = _cond_frame_for(c, root)
    assert cf._classify_layout() == "inline"


def test_inside_bar_is_inline_even_with_complex_left(root):
    # NO_LEFT_OPS hide the LEFT picker entirely → even if left is
    # complex, the picker has no presence, so we stay inline.
    c = Condition(left=FieldRef.indicator("rvol"), op=OP_INSIDE_BAR,
                  params={}, interval="5m")
    cf = _cond_frame_for(c, root)
    assert cf._classify_layout() == "inline"


# ---------------------------------------------------------------------------
# Live transitions
# ---------------------------------------------------------------------------


def test_op_change_gt_to_between_flips_to_stacked(root):
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    cf = _cond_frame_for(c, root)
    assert cf._current_layout == "inline"
    # Simulate the user picking BETWEEN in the op combo.
    cf._op_var.set(OP_BETWEEN)
    cf._on_op_change()
    assert cf._current_layout == "stacked"


def test_left_change_rvol_to_close_collapses_to_inline(root):
    # Start with rvol → stacked. Switch the LEFT picker to ``close``
    # (builtin) → should collapse to inline.
    c = Condition(left=FieldRef.indicator("rvol"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    cf = _cond_frame_for(c, root)
    assert cf._current_layout == "stacked"
    # Mutate the LEFT picker's ref to a simple builtin and fire the
    # change handler the picker would have invoked.
    cf._left_picker.set(FieldRef.builtin("close"))
    cf.cond.left = cf._left_picker.get()
    cf._on_left_change()
    assert cf._current_layout == "inline"


def test_left_change_close_to_rvol_expands_to_stacked(root):
    # Reverse direction — start simple, switch LEFT to RVOL.
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    cf = _cond_frame_for(c, root)
    assert cf._current_layout == "inline"
    cf._left_picker.set(FieldRef.indicator("rvol"))
    cf.cond.left = cf._left_picker.get()
    cf._on_left_change()
    assert cf._current_layout == "stacked"


def test_int_scalar_param_rejects_fractional_decimal_without_truncating(root):
    c = Condition(
        left=FieldRef.builtin("close"),
        op=OP_CROSSES_ABOVE,
        params={"right": FieldRef.literal(100.0), "lookback": 3},
        interval="5m",
    )
    cf = _cond_frame_for(c, root)
    kind, var = cf._param_widgets["lookback"]
    assert kind == "int"

    var.set("1.5")
    cf._commit_params()

    assert cf.cond.params["lookback"] == 3


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_float_scalar_param_rejects_non_finite_values(root, raw: str):
    c = Condition(
        left=FieldRef.builtin("close"),
        op=OP_WITHIN_PCT,
        params={"target": FieldRef.literal(100.0), "tolerance_pct": 2.5},
        interval="5m",
    )
    cf = _cond_frame_for(c, root)
    kind, var = cf._param_widgets["tolerance_pct"]
    assert kind == "float"

    var.set(raw)
    cf._commit_params()

    assert cf.cond.params["tolerance_pct"] == 2.5
    assert not math.isnan(cf.cond.params["tolerance_pct"])


# ---------------------------------------------------------------------------
# Fit-based / dynamic-resize behavior
# ---------------------------------------------------------------------------


def test_inline_estimate_for_simple_condition(root):
    """``close > 100`` inline estimate is well below typical widths."""
    from tradinglab.gui.scanner_block_editor import _estimate_condition_inline_width
    c = Condition(left=FieldRef.builtin("close"), op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    est = _estimate_condition_inline_width(c)
    # Empirical sanity bracket — exact value depends on font metrics.
    assert 600 < est < 1100, f"close > 100 estimate {est} px out of range"


def test_inline_estimate_for_rvol_condition(root):
    """RVOL's 6 trigger-relevant params push the estimate above
    any realistic dialog width — guarantees stacked layout."""
    from tradinglab.gui.scanner_block_editor import _estimate_condition_inline_width
    c = Condition(left=FieldRef.indicator("rvol"), op=OP_GT,
                  params={"right": FieldRef.literal(2.0)}, interval="5m")
    est = _estimate_condition_inline_width(c)
    assert est > 1500, f"rvol > 2 estimate {est} px should exceed realistic dialog widths"


def test_classify_uses_available_width_when_realized(root):
    """When the toplevel HAS a real width, classifier uses it (not the
    1200 px assumed default). EMA(20) > 100 (~1100 px estimate) goes
    inline in a 1500-wide window but stacked in a 700-wide one.
    """
    c = Condition(left=FieldRef.indicator("ema", params={"length": 20}),
                  op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    cf = _cond_frame_for(c, root)
    # Stub out _get_available_width to return controlled values.
    # Narrow → stacked.
    cf._get_available_width = lambda: 700  # type: ignore[method-assign]
    cf._current_layout = "inline"  # reset hysteresis state
    assert cf._classify_layout() == "stacked"
    # Wide → inline.
    cf._get_available_width = lambda: 1500  # type: ignore[method-assign]
    cf._current_layout = "stacked"
    assert cf._classify_layout() == "inline"


def test_hysteresis_prevents_thrashing_at_boundary(root):
    """Stacked → inline transition requires _HYSTERESIS_PX buffer."""
    from tradinglab.gui.scanner_block_editor import (
        _HYSTERESIS_PX,
        _estimate_condition_inline_width,
    )
    c = Condition(left=FieldRef.indicator("ema", params={"length": 20}),
                  op=OP_GT,
                  params={"right": FieldRef.literal(100.0)}, interval="5m")
    cf = _cond_frame_for(c, root)
    est = _estimate_condition_inline_width(c)
    # Exactly at the inline-fit boundary (est == available): if
    # currently stacked, we should STAY stacked until we have
    # _HYSTERESIS_PX of comfy room.
    cf._current_layout = "stacked"
    cf._get_available_width = lambda: est  # type: ignore[method-assign]
    assert cf._classify_layout() == "stacked"
    # Add just below the buffer — still stacked.
    cf._get_available_width = lambda: est + _HYSTERESIS_PX - 1  # type: ignore[method-assign]
    assert cf._classify_layout() == "stacked"
    # Cross the buffer — now flip to inline.
    cf._get_available_width = lambda: est + _HYSTERESIS_PX + 1  # type: ignore[method-assign]
    assert cf._classify_layout() == "inline"


def test_between_op_is_always_stacked_regardless_of_width(root):
    """OP_BETWEEN is a semantic override — always stacked even on a 4K window."""
    c = Condition(left=FieldRef.builtin("close"), op=OP_BETWEEN,
                  params={"low": FieldRef.literal(0.0),
                          "high": FieldRef.literal(10.0)}, interval="5m")
    cf = _cond_frame_for(c, root)
    cf._get_available_width = lambda: 4000  # type: ignore[method-assign]
    assert cf._classify_layout() == "stacked"
