"""Tests for the shared :mod:`gui._trigger_field_renderer`.

Audit item #8 lifted the schema-driven trigger-params renderer from
``gui.exits_dialog_widgets`` into ``gui._trigger_field_renderer`` so
the entries and exits dialogs can drive the same primitive from
per-side ``_FIELD_SPECS_BY_KIND`` registries. These tests pin:

* Each taxonomy ``kind`` produces the right widget class + Var
  subclass.
* :func:`render_kind_params` orchestrator routes ``specs_by_kind``
  correctly (empty for MARKET, multi-field for STOP_LIMIT,
  ``time_str`` for the exits TIME_OF_DAY kind).
* The ``block_editor`` kind delegates to the caller-supplied
  builder.
* ``enum_with_none`` maps the ``"(none)"`` choice to ``None``.
"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

import pytest

from tradinglab.entries.model import TriggerKind as EntryTriggerKind
from tradinglab.exits.model import TriggerKind as ExitTriggerKind
from tradinglab.gui._trigger_field_renderer import (
    _FieldSpec,
    render_field,
    render_kind_params,
)
from tradinglab.gui.entries_dialog import _ENTRY_TRIGGER_SPECS
from tradinglab.gui.exits_dialog_widgets import _FIELD_SPECS_BY_KIND
from tradinglab.gui.scanner_block_editor import BlockEditor
from tradinglab.scanner.model import Group as ConditionGroup

# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


@pytest.fixture
def root() -> tk.Tk:
    if sys.platform == "linux" and "DISPLAY" not in __import__("os").environ:
        pytest.skip("No display for headless Linux without xvfb")
    r = tk.Tk()
    r.withdraw()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


class _Target:
    """Tiny stub that mimics a trigger object for get/set callbacks."""

    def __init__(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


def _writer(target: _Target):
    def _on_change(attr: str, value: object) -> None:
        setattr(target, attr, value)
    return _on_change


def _reader(target: _Target):
    def _get(attr: str) -> object:
        return getattr(target, attr, None)
    return _get


# ---------------------------------------------------------------------------
# render_field — per-kind widget/var taxonomy
# ---------------------------------------------------------------------------


def test_float_kind_produces_string_var_and_entry(root: tk.Tk) -> None:
    """``float`` uses StringVar + Entry to preserve empty→None.

    NOTE: the audit-#8 task description originally suggested
    ``DoubleVar + Spinbox`` for ``float``, but every nullable
    price/offset field in the codebase needs the empty-string
    sentinel that ``DoubleVar`` cannot represent, so the lifted
    renderer intentionally stays with ``StringVar + Entry``.
    """
    target = _Target(price=12.5)
    frame = ttk.Frame(root)
    spec = _FieldSpec("price", "Price:", "float", width=10)
    var, widget = render_field(
        frame, spec,
        get_value=_reader(target), on_change=_writer(target),
    )
    assert isinstance(var, tk.StringVar)
    assert isinstance(widget, ttk.Entry)
    assert var.get() == "12.5"


def test_int_kind_produces_string_var_and_entry(root: tk.Tk) -> None:
    target = _Target(count=7)
    frame = ttk.Frame(root)
    var, widget = render_field(
        frame, _FieldSpec("count", "N:", "int", width=4),
        get_value=_reader(target), on_change=_writer(target),
    )
    assert isinstance(var, tk.StringVar)
    assert isinstance(widget, ttk.Entry)
    assert var.get() == "7"


def test_str_kind(root: tk.Tk) -> None:
    target = _Target(scanner_id="my_scan")
    frame = ttk.Frame(root)
    var, widget = render_field(
        frame, _FieldSpec("scanner_id", "Scanner id:", "str", width=30),
        get_value=_reader(target), on_change=_writer(target),
    )
    assert isinstance(var, tk.StringVar)
    assert isinstance(widget, ttk.Entry)
    assert var.get() == "my_scan"
    var.set("changed")
    root.update_idletasks()
    assert target.scanner_id == "changed"


def test_bool_kind(root: tk.Tk) -> None:
    target = _Target(enabled=True)
    frame = ttk.Frame(root)
    var, widget = render_field(
        frame, _FieldSpec("enabled", "Enabled:", "bool"),
        get_value=_reader(target), on_change=_writer(target),
    )
    assert isinstance(var, tk.BooleanVar)
    assert isinstance(widget, ttk.Checkbutton)
    assert var.get() is True


def test_time_str_kind_validates_hhmm(root: tk.Tk) -> None:
    target = _Target(time_of_day="09:30")
    frame = ttk.Frame(root)
    var, widget = render_field(
        frame, _FieldSpec("time_of_day", "HH:MM:", "time_str", width=8),
        get_value=_reader(target), on_change=_writer(target),
    )
    assert isinstance(var, tk.StringVar)
    assert isinstance(widget, ttk.Entry)
    assert var.get() == "09:30"
    # Valid HH:MM fires
    var.set("15:55")
    root.update_idletasks()
    assert target.time_of_day == "15:55"
    # Empty → None
    var.set("")
    root.update_idletasks()
    assert target.time_of_day is None
    # Invalid mid-typing → no fire (target preserved)
    var.set("xx")
    root.update_idletasks()
    assert target.time_of_day is None


def test_enum_kind(root: tk.Tk) -> None:
    target = _Target(choice="A")
    frame = ttk.Frame(root)
    choices = (("A", "Alpha"), ("B", "Beta"))
    var, widget = render_field(
        frame,
        _FieldSpec("choice", "Choice:", "enum", width=10, choices=choices),
        get_value=_reader(target), on_change=_writer(target),
    )
    assert isinstance(var, tk.StringVar)
    assert isinstance(widget, ttk.Combobox)
    assert str(widget["state"]) == "readonly"
    assert list(widget["values"]) == ["Alpha", "Beta"]
    assert var.get() == "Alpha"
    var.set("Beta")
    widget.event_generate("<<ComboboxSelected>>")
    assert target.choice == "B"


def test_enum_with_none_maps_none(root: tk.Tk) -> None:
    target = _Target(choice="A")
    frame = ttk.Frame(root)
    choices = (("A", "Alpha"), ("B", "Beta"))
    var, widget = render_field(
        frame,
        _FieldSpec("choice", "Choice:", "enum_with_none",
                   width=10, choices=choices),
        get_value=_reader(target), on_change=_writer(target),
    )
    assert isinstance(var, tk.StringVar)
    assert isinstance(widget, ttk.Combobox)
    assert list(widget["values"]) == ["(none)", "Alpha", "Beta"]
    var.set("(none)")
    widget.event_generate("<<ComboboxSelected>>")
    assert target.choice is None
    var.set("Beta")
    widget.event_generate("<<ComboboxSelected>>")
    assert target.choice == "B"


def test_enum_str_kind(root: tk.Tk) -> None:
    target = _Target(ma_type="EMA")
    frame = ttk.Frame(root)
    options = ("RMA", "SMA", "EMA", "WMA")
    var, widget = render_field(
        frame,
        _FieldSpec("ma_type", "MA:", "enum_str", width=6, choices=options),
        get_value=_reader(target), on_change=_writer(target),
    )
    assert isinstance(var, tk.StringVar)
    assert isinstance(widget, ttk.Combobox)
    assert var.get() == "EMA"
    var.set("SMA")
    widget.event_generate("<<ComboboxSelected>>")
    assert target.ma_type == "SMA"


def test_block_editor_delegates_to_builder(root: tk.Tk) -> None:
    target = _Target()
    frame = ttk.Frame(root)
    built: list[BlockEditor] = []

    def _builder(parent: tk.Misc, spec: _FieldSpec) -> tk.Widget:
        be = BlockEditor(
            parent,
            root=ConditionGroup(combinator="and", children=[]),
            on_change=lambda: None,
            default_interval="1m",
        )
        be.pack(fill="both", expand=True)
        built.append(be)
        return be

    var, widget = render_field(
        frame, _FieldSpec("__indicator__", "", "block_editor"),
        get_value=_reader(target),
        on_change=_writer(target),
        block_editor_builder=_builder,
    )
    assert var is None
    assert isinstance(widget, BlockEditor)
    assert built and built[0] is widget


def test_block_editor_without_builder_is_noop(root: tk.Tk) -> None:
    target = _Target()
    frame = ttk.Frame(root)
    var, widget = render_field(
        frame, _FieldSpec("x", "", "block_editor"),
        get_value=_reader(target), on_change=_writer(target),
    )
    assert var is None
    assert widget is None


def test_unknown_kind_silent(root: tk.Tk) -> None:
    target = _Target()
    frame = ttk.Frame(root)
    var, widget = render_field(
        frame, _FieldSpec("x", "", "nonsense_kind"),
        get_value=_reader(target), on_change=_writer(target),
    )
    assert var is None
    assert widget is None


# ---------------------------------------------------------------------------
# render_kind_params — orchestrator semantics
# ---------------------------------------------------------------------------


def test_render_kind_params_market_renders_nothing(root: tk.Tk) -> None:
    target = _Target()
    frame = ttk.Frame(root)
    vars_dict: dict[str, tk.Variable] = {}
    widgets = render_kind_params(
        frame, EntryTriggerKind.MARKET, vars_dict,
        specs_by_kind=_ENTRY_TRIGGER_SPECS,
        get_value=_reader(target), on_change=_writer(target),
    )
    assert widgets == []
    assert vars_dict == {}


def test_render_kind_params_stop_limit_two_fields(root: tk.Tk) -> None:
    target = _Target(stop_price=10.0, price=11.0)
    frame = ttk.Frame(root)
    vars_dict: dict[str, tk.Variable] = {}
    widgets = render_kind_params(
        frame, EntryTriggerKind.STOP_LIMIT, vars_dict,
        specs_by_kind=_ENTRY_TRIGGER_SPECS,
        get_value=_reader(target), on_change=_writer(target),
    )
    assert len(widgets) == 2
    assert set(vars_dict.keys()) == {"stop_price", "price"}
    assert all(isinstance(v, tk.StringVar) for v in vars_dict.values())


def test_render_kind_params_exits_time_of_day(root: tk.Tk) -> None:
    target = _Target(time_of_day="15:55")
    frame = ttk.Frame(root)
    vars_dict: dict[str, tk.Variable] = {}
    widgets = render_kind_params(
        frame, ExitTriggerKind.TIME_OF_DAY, vars_dict,
        specs_by_kind=_FIELD_SPECS_BY_KIND,
        get_value=_reader(target), on_change=_writer(target),
    )
    assert len(widgets) == 1
    assert isinstance(widgets[0], ttk.Entry)
    assert "time_of_day" in vars_dict
    assert isinstance(vars_dict["time_of_day"], tk.StringVar)


def test_render_kind_params_unknown_kind_empty(root: tk.Tk) -> None:
    target = _Target()
    frame = ttk.Frame(root)
    vars_dict: dict[str, tk.Variable] = {}
    widgets = render_kind_params(
        frame, "NOT_A_KIND", vars_dict,
        specs_by_kind=_ENTRY_TRIGGER_SPECS,
        get_value=_reader(target), on_change=_writer(target),
    )
    assert widgets == []
    assert vars_dict == {}
