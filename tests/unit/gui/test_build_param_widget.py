"""Tests for :func:`tradinglab.gui._param_widgets.build_param_widget`.

The shared helper underpins three dialog sites
(indicator_dialog, scanner_block_editor twice) — drift between them
was the audit-3 motivation. These tests pin the per-kind dispatcher,
the four CommitPolicy values, the choices_override + anchor_ts
specials, and the §7.19 ``pdef.description`` label contract.

Note: the ``root`` fixture comes from ``tests/conftest.py`` (a
``tk.Toplevel`` under the shared session ``_tk_root``). Earlier
revisions of this file defined a LOCAL ``root`` fixture that called
``tk.Tk()`` per test — that created a second Tcl interpreter and
broke the anchor_ts label-trace test on CI runners, because
``tk.StringVar(value=...)`` (no explicit master) registers itself
with ``tkinter._default_root.tk`` (the SESSION root), but the local
fixture's ``root.tk`` was a different interpreter. ``root.getvar()``
then raised ``can't read "PY_VARn": no such variable`` because the
two interpreters didn't share the variable namespace.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import pytest

from tradinglab.gui._param_widgets import (
    _format_anchor_label,
    build_param_widget,
    label_text_for,
    validate_param_value,
)
from tradinglab.indicators.base import ParamDef


# ---------------------------------------------------------------------------
# Per-kind widget + variable type
# ---------------------------------------------------------------------------


def test_bool_returns_booleanvar_and_checkbutton(root):
    pdef = ParamDef(name="flag", kind="bool", default=False)
    var, widget = build_param_widget(root, pdef, True)
    assert isinstance(var, tk.BooleanVar)
    assert isinstance(widget, ttk.Checkbutton)
    assert var.get() is True


def test_choice_returns_stringvar_and_combobox(root):
    pdef = ParamDef(name="ma_type", kind="choice", default="sma",
                    choices=("sma", "ema", "wma"))
    var, widget = build_param_widget(root, pdef, "ema")
    assert isinstance(var, tk.StringVar)
    assert isinstance(widget, ttk.Combobox)
    assert var.get() == "ema"
    assert str(widget.cget("state")) == "readonly"
    assert tuple(widget.cget("values")) == ("sma", "ema", "wma")


def test_int_returns_stringvar_and_spinbox(root):
    pdef = ParamDef(name="length", kind="int", default=14, min=1, max=200)
    var, widget = build_param_widget(root, pdef, 20)
    assert isinstance(var, tk.StringVar)
    assert isinstance(widget, ttk.Spinbox)
    assert var.get() == "20"


def test_float_returns_stringvar_and_spinbox(root):
    pdef = ParamDef(name="mult", kind="float", default=2.0)
    var, widget = build_param_widget(root, pdef, 1.5)
    assert isinstance(var, tk.StringVar)
    assert isinstance(widget, ttk.Spinbox)
    assert var.get() == "1.5"


def test_str_returns_stringvar_and_entry(root):
    pdef = ParamDef(name="label", kind="str", default="")
    var, widget = build_param_widget(root, pdef, "hi")
    assert isinstance(var, tk.StringVar)
    assert isinstance(widget, ttk.Entry)
    assert var.get() == "hi"


def test_str_with_choices_returns_editable_combobox(root):
    pdef = ParamDef(name="compare_symbol", kind="str", default="SPY",
                    choices=("SPY", "QQQ", "IWM"))
    var, widget = build_param_widget(root, pdef, "QQQ")
    assert isinstance(var, tk.StringVar)
    assert isinstance(widget, ttk.Combobox)
    assert str(widget.cget("state")) == "normal"
    assert var.get() == "QQQ"


# ---------------------------------------------------------------------------
# choices_override
# ---------------------------------------------------------------------------


def test_choices_override_swaps_choice_list(root):
    pdef = ParamDef(name="ma_type", kind="choice", default="sma",
                    choices=("sma", "ema"))
    _, widget = build_param_widget(
        root, pdef, "sma", choices_override=("wma", "hull"),
    )
    assert tuple(widget.cget("values")) == ("wma", "hull")


# ---------------------------------------------------------------------------
# CommitPolicy semantics
# ---------------------------------------------------------------------------


def test_eager_fires_on_every_write(root):
    pdef = ParamDef(name="n", kind="int", default=1)
    calls: list[int] = []
    var, _ = build_param_widget(
        root, pdef, 1,
        on_change=lambda: calls.append(1),
        commit_policy="eager",
    )
    var.set("2"); var.set("3"); var.set("4")
    root.update_idletasks()
    assert len(calls) == 3


def test_debounced_coalesces_rapid_writes(root):
    pdef = ParamDef(name="n", kind="int", default=1)
    calls: list[int] = []
    var, widget = build_param_widget(
        root, pdef, 1,
        on_change=lambda: calls.append(1),
        commit_policy="debounced",
        debounce_ms=30,
    )
    for v in range(5):
        var.set(str(v))
    assert calls == []  # nothing yet — debounce in flight
    # Wait past the debounce window by pumping the event loop.
    deadline = root.tk.call("clock", "milliseconds")
    target = int(deadline) + 200
    while int(root.tk.call("clock", "milliseconds")) < target:
        root.update()
        root.after(5)
    assert len(calls) == 1


def test_debounced_combobox_selection_fires_eagerly(root):
    pdef = ParamDef(name="ma_type", kind="choice", default="sma",
                    choices=("sma", "ema"))
    debounced: list[int] = []
    eager: list[int] = []
    _, widget = build_param_widget(
        root, pdef, "sma",
        on_change=lambda: debounced.append(1),
        on_commit_eager=lambda: eager.append(1),
        commit_policy="debounced",
        debounce_ms=30,
    )
    # Synthesize a <<ComboboxSelected>> event.
    widget.event_generate("<<ComboboxSelected>>")
    root.update()
    assert eager == [1]


def test_on_focus_out_fires_only_on_focusout(root):
    pdef = ParamDef(name="label", kind="str", default="")
    calls: list[int] = []
    var, widget = build_param_widget(
        root, pdef, "",
        on_change=lambda: calls.append(1),
        commit_policy="on_focus_out",
    )
    var.set("typed")
    root.update_idletasks()
    assert calls == []  # writes don't fire
    widget.event_generate("<FocusOut>")
    root.update()
    assert calls == [1]


def test_manual_never_fires(root):
    pdef = ParamDef(name="x", kind="int", default=1)
    calls: list[int] = []
    var, widget = build_param_widget(
        root, pdef, 1,
        on_change=lambda: calls.append(1),
        commit_policy="manual",
    )
    var.set("5")
    widget.event_generate("<FocusOut>")
    root.update()
    assert calls == []


# ---------------------------------------------------------------------------
# Param validation
# ---------------------------------------------------------------------------


def test_validate_int_rejects_below_minimum():
    pdef = ParamDef(name="length", kind="int", default=14, min=1, max=200,
                    description="Length")
    ok, value, message = validate_param_value(pdef, "0")
    assert ok is False
    assert value == 14
    assert message == "Enter Length greater than or equal to 1."


def test_validate_int_rejects_fractional_decimal_without_truncating():
    pdef = ParamDef(name="length", kind="int", default=14, min=1, max=200,
                    description="Length")
    ok, value, message = validate_param_value(pdef, "1.5")
    assert ok is False
    assert value == 14
    assert message == "Enter a whole number for Length."


def test_validate_int_accepts_whole_decimal_text():
    pdef = ParamDef(name="length", kind="int", default=14, min=1, max=200,
                    description="Length")
    ok, value, message = validate_param_value(pdef, "2.0")
    assert ok is True
    assert value == 2
    assert message == ""


def test_validate_float_accepts_value_in_range():
    pdef = ParamDef(name="mult", kind="float", default=2.0, min=0.1, max=10.0,
                    description="Multiplier")
    ok, value, message = validate_param_value(pdef, "1.5")
    assert ok is True
    assert value == 1.5
    assert message == ""


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_validate_float_rejects_non_finite_values(raw: str):
    pdef = ParamDef(name="mult", kind="float", default=2.0, min=0.1, max=10.0,
                    description="Multiplier")
    ok, value, message = validate_param_value(pdef, raw)
    assert ok is False
    assert value == 2.0
    assert message == "Enter a finite number for Multiplier."


def test_validate_choice_rejects_unknown_value():
    pdef = ParamDef(name="mode", kind="choice", default="simple",
                    choices=("simple", "time_of_day"),
                    description="Mode")
    ok, value, message = validate_param_value(pdef, "bogus")
    assert ok is False
    assert value == "simple"
    assert message == "Choose a valid Mode."


# ---------------------------------------------------------------------------
# anchor_ts special case
# ---------------------------------------------------------------------------


def test_anchor_ts_returns_frame_with_label_and_button(root):
    pdef = ParamDef(name="anchor_ts", kind="str", default="")
    calls: list[int] = []
    var, widget = build_param_widget(
        root, pdef, "",
        anchor_pick_callback=lambda: calls.append(1),
    )
    assert isinstance(var, tk.StringVar)
    assert isinstance(widget, ttk.Frame)
    # The frame contains a Label + a Button.
    kids = widget.winfo_children()
    assert any(isinstance(w, ttk.Label) for w in kids)
    btns = [w for w in kids if isinstance(w, ttk.Button)]
    assert len(btns) == 1
    btns[0].invoke()
    assert calls == [1]


def test_anchor_ts_var_trace_updates_label(root):
    pdef = ParamDef(name="anchor_ts", kind="str", default="")
    var, widget = build_param_widget(root, pdef, "")
    # Find the display Label.
    lbl = next(w for w in widget.winfo_children() if isinstance(w, ttk.Label))
    # The label is bound via ``textvariable``, not ``text``. On many
    # Tk builds ``cget("text")`` returns "" until a ``text=`` is
    # explicitly set; read the live value through the textvariable
    # name instead (this matches what the widget actually displays).
    tv_name = str(lbl.cget("textvariable"))
    assert root.getvar(tv_name) == _format_anchor_label("")  # "(first bar)"
    var.set("2024-01-15T09:30:00")
    root.update()
    assert "2024-01-15" in root.getvar(tv_name)


# ---------------------------------------------------------------------------
# §7.19 label contract
# ---------------------------------------------------------------------------


def test_label_text_uses_description_when_present():
    pdef = ParamDef(name="incl_curr", kind="bool", default=False,
                    description="Include current in denom")
    assert label_text_for(pdef) == "Include current in denom:"


def test_label_text_falls_back_to_name():
    pdef = ParamDef(name="length", kind="int", default=14)
    assert label_text_for(pdef) == "length:"


def test_label_text_handles_empty_description():
    pdef = ParamDef(name="length", kind="int", default=14, description="")
    assert label_text_for(pdef) == "length:"


# ---------------------------------------------------------------------------
# Width handling
# ---------------------------------------------------------------------------


def test_explicit_width_applied_to_combobox(root):
    pdef = ParamDef(name="x", kind="choice", default="a", choices=("a", "b"))
    _, widget = build_param_widget(root, pdef, "a", width=20)
    assert int(widget.cget("width")) == 20


def test_explicit_width_applied_to_spinbox(root):
    pdef = ParamDef(name="n", kind="int", default=1)
    _, widget = build_param_widget(root, pdef, 1, width=4)
    assert int(widget.cget("width")) == 4


def test_default_widths_per_kind(root):
    # choice defaults to 10, int/float to 6, str to 14.
    _, w_choice = build_param_widget(
        root, ParamDef(name="c", kind="choice", default="a", choices=("a",)), "a",
    )
    _, w_int = build_param_widget(
        root, ParamDef(name="i", kind="int", default=1), 1,
    )
    _, w_str = build_param_widget(
        root, ParamDef(name="s", kind="str", default=""), "",
    )
    assert int(w_choice.cget("width")) == 10
    assert int(w_int.cget("width")) == 6
    assert int(w_str.cget("width")) == 14


# ---------------------------------------------------------------------------
# Spinbox range / increment defaults
# ---------------------------------------------------------------------------


def test_spinbox_int_defaults_unbounded_with_step_1(root):
    pdef = ParamDef(name="n", kind="int", default=1)
    _, widget = build_param_widget(root, pdef, 1)
    assert float(widget.cget("from")) == -1e12
    assert float(widget.cget("to")) == 1e12
    assert float(widget.cget("increment")) == 1.0


def test_spinbox_float_defaults_step_point_one(root):
    pdef = ParamDef(name="x", kind="float", default=1.0)
    _, widget = build_param_widget(root, pdef, 1.0)
    assert float(widget.cget("increment")) == pytest.approx(0.1)


def test_spinbox_respects_explicit_bounds(root):
    pdef = ParamDef(name="n", kind="int", default=14, min=2, max=99, step=2)
    _, widget = build_param_widget(root, pdef, 14)
    assert float(widget.cget("from")) == 2
    assert float(widget.cget("to")) == 99
    assert float(widget.cget("increment")) == 2
