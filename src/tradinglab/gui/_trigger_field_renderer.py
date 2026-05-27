"""Shared schema-driven trigger-params renderer.

Both :mod:`gui.entries_dialog` and :mod:`gui.exits_dialog_widgets`
need to render a per-``TriggerKind`` parameters row (a varying mix
of price entries, enum dropdowns, time strings, embedded block
editors). Historically the entries side was imperative
(``if kind == … else if …``) and the exits side was schema-driven
(``_FIELD_SPECS_BY_KIND`` + ``_render_field``). Audit item #8 lifts
the schema-driven primitives here so both dialogs share one
implementation and the per-kind taxonomy is declared once per side.

Design
------

* :class:`_FieldSpec` is a frozen dataclass describing one input
  widget (attribute name, visible label, kind, optional choices /
  width / leading separator glyph).
* :func:`render_field` renders ONE spec: creates the appropriate
  :class:`tk.Variable` subclass, the widget, wires the change
  callback. Returns ``(var, widget)``.
* :func:`render_kind_params` is the orchestrator: given a kind enum
  + a ``specs_by_kind`` registry, it iterates the matching specs and
  populates a caller-supplied ``vars_dict`` keyed by ``spec.attr``.

The shared renderer is intentionally STATELESS — the caller owns
the target object (the ``EntryTrigger`` or ``ExitTrigger``) and
passes a ``get_value`` reader + ``on_change`` writer pair. This
lets entries reuse its existing per-attribute setters and exits
keep direct ``setattr`` on its in-row ``self._trigger``.

Kind taxonomy
-------------

``_FieldSpec.kind`` supports the union of every kind needed by
both dialogs today:

* ``"float"`` — ``StringVar`` + ``ttk.Entry``. Empty string is
  written as ``None`` (lets the user type partial values like
  ``"1."`` without committing). The user's audit notes
  "DoubleVar + Spinbox" — we intentionally stayed with
  ``StringVar`` + ``Entry`` because every existing nullable
  price/offset field needs the empty-string sentinel that
  ``DoubleVar`` cannot represent.
* ``"int"`` — same shape as ``"float"`` but parses ``int``. Empty
  string is silently ignored (preserves the prior committed value).
* ``"str"`` — ``StringVar`` + ``ttk.Entry``; stored verbatim.
* ``"bool"`` — ``BooleanVar`` + ``ttk.Checkbutton``.
* ``"time_str"`` — ``StringVar`` + ``ttk.Entry`` with HH:MM
  validation (empty → ``None``; non-HH:MM → preserved on the var
  but ``on_change`` only fires for valid values).
* ``"enum"`` — ``StringVar`` + readonly ``ttk.Combobox`` over
  ``spec.choices`` (tuple of ``(value, label)`` pairs); the
  selected label is mapped back to its value before fire.
* ``"enum_with_none"`` — like ``"enum"`` but prefixed with a
  ``"(none)"`` choice that maps to ``None``.
* ``"enum_str"`` — readonly ``ttk.Combobox`` over a flat
  ``(str, ...)`` choice tuple, stored verbatim.
* ``"block_editor"`` — a placeholder kind that delegates to a
  caller-supplied ``block_editor_builder(parent, spec)`` factory.
  Lets the INDICATOR trigger embed a
  :class:`gui.scanner_block_editor.BlockEditor` uniformly across
  both dialogs without coupling this module to BlockEditor's many
  constructor knobs.

Registration pattern
--------------------

Each consumer dialog declares its own
``_TRIGGER_SPECS: dict[KindEnum, tuple[_FieldSpec, ...]]`` at
module scope and passes it via the ``specs_by_kind`` kwarg of
:func:`render_kind_params`. Unknown kinds resolve to an empty spec
tuple → no widgets rendered (the dialog can still draw its own
"no parameters" placeholder label).
"""
from __future__ import annotations

import re
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import ttk
from typing import Any

GetValue = Callable[[str], Any]
OnChange = Callable[[str, Any], None]
BlockEditorBuilder = Callable[[tk.Misc, "_FieldSpec"], tk.Widget]


_HHMM_RE = re.compile(r"^\d{1,2}:\d{2}$")


@dataclass(frozen=True)
class _FieldSpec:
    """Declarative description of one trigger-params input widget.

    Attributes
    ----------
    attr:
        Attribute name on the target trigger object — also used as
        the key under which the created :class:`tk.Variable` is
        stored in the caller's ``vars_dict``.
    label:
        Visible text drawn to the left of the widget. May be empty.
    kind:
        One of the strings documented in the module docstring.
    width:
        Hint passed to the widget constructor (``Entry`` /
        ``Combobox`` width). Default ``8``.
    choices:
        For ``"enum"`` / ``"enum_with_none"`` — tuple of
        ``(value, label)`` pairs. For ``"enum_str"`` — flat tuple
        of strings. Ignored for other kinds.
    separator:
        When True, the rendered label is prefixed with a ``"|"``
        glyph + extra leading padding to visually chunk the row
        (used by exits-side ``TRAILING_STOP`` etc.).
    """

    attr: str
    label: str
    kind: str
    width: int = 8
    choices: tuple[Any, ...] | None = None
    separator: bool = False


def _draw_label(parent: tk.Misc, spec: _FieldSpec) -> None:
    label_text = (
        f"| {spec.label}" if spec.separator and spec.label else spec.label
    )
    if label_text:
        ttk.Label(parent, text=label_text).pack(
            side="left", padx=((8 if spec.separator else 0), 0),
        )
    elif spec.separator:
        ttk.Label(parent, text="|").pack(side="left", padx=(8, 0))


def _format_float(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return ""


def _format_int(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return ""


def render_field(
    parent: tk.Misc,
    spec: _FieldSpec,
    *,
    get_value: GetValue,
    on_change: OnChange,
    block_editor_builder: BlockEditorBuilder | None = None,
) -> tuple[tk.Variable | None, tk.Widget | None]:
    """Render one ``_FieldSpec`` into ``parent`` and wire callbacks.

    Returns ``(var, widget)``. ``var`` is ``None`` when the kind
    has no associated :class:`tk.Variable` (currently only
    ``"block_editor"``); ``widget`` is the primary widget created
    (``None`` for ``block_editor`` when no builder is supplied).
    """
    kind = spec.kind
    attr = spec.attr

    # block_editor is special: no Var, no label drawing here — the
    # caller's builder owns the full layout (typically a row with
    # interval picker + intrabar checkbox + the editor itself).
    if kind == "block_editor":
        if block_editor_builder is None:
            return None, None
        widget = block_editor_builder(parent, spec)
        return None, widget

    _draw_label(parent, spec)

    if kind == "float":
        cur = get_value(attr)
        var = tk.StringVar(value=_format_float(cur))
        widget = ttk.Entry(parent, textvariable=var, width=spec.width)
        widget.pack(side="left", padx=(2, 6))

        def _on_write(*_a: object, _v: tk.StringVar = var) -> None:
            raw = _v.get().strip()
            if raw == "":
                on_change(attr, None)
                return
            try:
                on_change(attr, float(raw))
            except ValueError:
                pass  # mid-typing — silent

        var.trace_add("write", _on_write)
        return var, widget

    if kind == "int":
        cur = get_value(attr)
        var = tk.StringVar(value=_format_int(cur))
        widget = ttk.Entry(parent, textvariable=var, width=spec.width)
        widget.pack(side="left", padx=(2, 6))

        def _on_write(*_a: object, _v: tk.StringVar = var) -> None:
            raw = _v.get().strip()
            if raw == "":
                return  # preserve last committed value
            try:
                on_change(attr, int(raw))
            except ValueError:
                pass

        var.trace_add("write", _on_write)
        return var, widget

    if kind == "str":
        cur = get_value(attr)
        var = tk.StringVar(value="" if cur is None else str(cur))
        widget = ttk.Entry(parent, textvariable=var, width=spec.width)
        widget.pack(side="left", padx=(2, 6))

        def _on_write(*_a: object, _v: tk.StringVar = var) -> None:
            on_change(attr, _v.get())

        var.trace_add("write", _on_write)
        return var, widget

    if kind == "bool":
        cur = get_value(attr)
        var = tk.BooleanVar(value=bool(cur))
        widget = ttk.Checkbutton(
            parent, variable=var,
            command=lambda _v=var: on_change(attr, bool(_v.get())),
        )
        widget.pack(side="left", padx=(2, 6))
        return var, widget

    if kind == "time_str":
        cur = get_value(attr) or ""
        var = tk.StringVar(value=str(cur))
        widget = ttk.Entry(parent, textvariable=var, width=spec.width)
        widget.pack(side="left", padx=(2, 4))

        def _on_write(*_a: object, _v: tk.StringVar = var) -> None:
            txt = _v.get().strip()
            if txt == "":
                on_change(attr, None)
                return
            if _HHMM_RE.match(txt):
                on_change(attr, txt)
            # else: invalid mid-typing → don't fire (preserves last good)

        var.trace_add("write", _on_write)
        return var, widget

    if kind == "enum":
        choices = spec.choices or ()
        labels = [lbl for _, lbl in choices]
        cur_value = get_value(attr)
        cur_label = next(
            (lbl for value, lbl in choices if value == cur_value),
            labels[0] if labels else "",
        )
        var = tk.StringVar(value=cur_label)
        cb = ttk.Combobox(
            parent, textvariable=var, state="readonly",
            values=labels, width=spec.width,
        )
        cb.pack(side="left", padx=(2, 4))

        def _on_select(_e: object, _v: tk.StringVar = var,
                       _c: tuple[Any, ...] = choices) -> None:
            label = _v.get()
            for value, lbl in _c:
                if lbl == label:
                    on_change(attr, value)
                    return

        cb.bind("<<ComboboxSelected>>", _on_select)
        return var, cb

    if kind == "enum_with_none":
        choices = spec.choices or ()
        labels = ["(none)"] + [lbl for _, lbl in choices]
        cur_value = get_value(attr)
        cur_label = next(
            (lbl for value, lbl in choices if value == cur_value),
            "(none)",
        )
        var = tk.StringVar(value=cur_label)
        cb = ttk.Combobox(
            parent, textvariable=var, state="readonly",
            values=labels, width=spec.width,
        )
        cb.pack(side="left", padx=(2, 4))

        def _on_select(_e: object, _v: tk.StringVar = var,
                       _c: tuple[Any, ...] = choices) -> None:
            label = _v.get()
            if label == "(none)":
                on_change(attr, None)
                return
            for value, lbl in _c:
                if lbl == label:
                    on_change(attr, value)
                    return

        cb.bind("<<ComboboxSelected>>", _on_select)
        return var, cb

    if kind == "enum_str":
        options = tuple(spec.choices or ())
        cur = get_value(attr)
        cur_str = (str(cur).upper() if cur is not None else "")
        if cur_str not in options:
            cur_str = options[0] if options else ""
        var = tk.StringVar(value=cur_str)
        cb = ttk.Combobox(
            parent, textvariable=var, state="readonly",
            values=list(options), width=spec.width,
        )
        cb.pack(side="left", padx=(2, 4))

        def _on_select(_e: object, _v: tk.StringVar = var) -> None:
            on_change(attr, _v.get())

        cb.bind("<<ComboboxSelected>>", _on_select)
        return var, cb

    # Unknown kind — silently ignore (don't crash the dialog).
    return None, None


def render_kind_params(
    parent: tk.Misc,
    kind: Any,
    vars_dict: dict[str, tk.Variable],
    *,
    specs_by_kind: dict[Any, tuple[_FieldSpec, ...]],
    get_value: GetValue,
    on_change: OnChange,
    block_editor_builder: BlockEditorBuilder | None = None,
) -> list[tk.Widget]:
    """Render every field for ``kind`` into ``parent``.

    For each spec in ``specs_by_kind[kind]`` calls
    :func:`render_field` and stores the resulting variable under
    ``vars_dict[spec.attr]`` (when non-None). Returns the list of
    widgets created (in declaration order) for callers that need
    to apply additional layout / styling.

    Missing kinds resolve to an empty tuple → empty widget list.
    """
    widgets: list[tk.Widget] = []
    specs = specs_by_kind.get(kind, ())
    for spec in specs:
        var, widget = render_field(
            parent, spec,
            get_value=get_value,
            on_change=on_change,
            block_editor_builder=block_editor_builder,
        )
        if var is not None:
            vars_dict[spec.attr] = var
        if widget is not None:
            widgets.append(widget)
    return widgets
