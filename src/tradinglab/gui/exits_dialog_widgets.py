"""Internal widget classes for the Exit Strategies dialog.

Holds the dropdown-choice constants and the four ``ttk``
frames/Toplevels that the main :class:`ExitsDialog`
(in :mod:`tradinglab.gui.exits_dialog`) composes:

* :class:`_BracketDialog` — bracket-template prompt modal.
* :class:`_LegFrame` — per-leg editor card.
* :class:`_TriggerRow` — single trigger row inside a leg.
* :class:`_OCOGroupRow` — single OCO group row.

These are private (``_`` prefix) — callers should go through
:func:`tradinglab.gui.exits_dialog.open_exits_dialog`.
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any

from ..exits.model import (
    ActivationUnit,
    ExitLeg,
    ExitTrigger,
    OCOGroup,
    TrailBasis,
    TrailUnit,
    TriggerKind,
)
from ..scanner.model import Group as ConditionGroup
from ._modal_base import BaseModalDialog, protect_combobox_wheel
from .colors import ERROR_RED, MUTED_GREY
from .scanner_block_editor import BlockEditor

if TYPE_CHECKING:
    from .exits_dialog import ExitsDialog


# Constants / helpers
# ---------------------------------------------------------------------------


_TRIGGER_KIND_CHOICES: tuple[tuple[TriggerKind, str], ...] = (
    (TriggerKind.MARKET,        "Market"),
    (TriggerKind.LIMIT,         "Limit"),
    (TriggerKind.STOP,          "Stop"),
    (TriggerKind.STOP_LIMIT,    "Stop-Limit"),
    (TriggerKind.TRAILING_STOP, "Trailing Stop"),
    (TriggerKind.CHANDELIER,    "Chandelier Stop"),
    (TriggerKind.TIME_OF_DAY,   "Time of Day"),
    (TriggerKind.INDICATOR,     "Indicator"),
)

_TRIGGER_KIND_LABEL = {k: lbl for k, lbl in _TRIGGER_KIND_CHOICES}
_TRIGGER_KIND_BY_LABEL = {lbl: k for k, lbl in _TRIGGER_KIND_CHOICES}

_TRAIL_UNIT_CHOICES: tuple[tuple[TrailUnit, str], ...] = (
    (TrailUnit.PERCENT, "Percent"),
    (TrailUnit.DOLLAR,  "Dollar"),
    (TrailUnit.ATR,     "ATR"),
)

_ACTIVATION_UNIT_CHOICES: tuple[tuple[ActivationUnit, str], ...] = (
    (ActivationUnit.PERCENT,    "Percent"),
    (ActivationUnit.DOLLAR,     "Dollar"),
    (ActivationUnit.R_MULTIPLE, "R-multiple"),
)

_TRAIL_BASIS_CHOICES: tuple[tuple[TrailBasis, str], ...] = (
    (TrailBasis.INTRABAR, "Intrabar"),
    (TrailBasis.CLOSE,    "Close-only"),
)

_CHANDELIER_MA_TYPE_CHOICES: tuple[str, ...] = ("RMA", "SMA", "EMA", "WMA")

_INDICATOR_INTERVAL_CHOICES: tuple[str, ...] = (
    "(position)", "1m", "5m", "15m", "30m", "1h", "1d",
)

_OCO_CANCEL_ON_CHOICES: tuple[str, ...] = ("full_closeout", "any_fire")


# ---------------------------------------------------------------------------
# Trigger-row field schema (big-bet item #3)
# ---------------------------------------------------------------------------
#
# Each :class:`TriggerKind` is described by an ordered tuple of
# :class:`_FieldSpec` rows. ``_TriggerRow._render_params`` walks the
# schema and emits the appropriate widget per field; the dedicated
# per-kind render methods (``_render_price_or_offset``,
# ``_render_trailing``, ``_render_chandelier``, etc.) are gone, replaced
# by a single :py:meth:`_TriggerRow._render_field` dispatcher.
#
# Two kinds remain special-cased rather than schema-driven:
#
# * ``TriggerKind.MARKET`` — emits only a muted "no parameters" label.
# * ``TriggerKind.INDICATOR`` — needs to mount a nested ``BlockEditor``
#   (full-width, below the interval+intrabar bar) which doesn't fit the
#   one-row-of-widgets schema. Handled by ``_render_indicator``.
#
# ``kind`` values understood by ``_render_field``:
#
# * ``"float"`` — Entry; empty string → ``None`` on the trigger.
# * ``"int"``   — Entry; empty string is ignored (preserves the last
#                 committed value while the user is mid-typing).
# * ``"time_str"`` — Entry for ``HH:MM`` strings; empty → ``None``.
# * ``"enum"`` — readonly Combobox over ``(value, label)`` choices.
# * ``"enum_with_none"`` — readonly Combobox prefixed with
#                 ``"(none)"`` which maps to ``None`` on the trigger.
# * ``"enum_str"`` — readonly Combobox over a flat ``(str, ...)``
#                 choice tuple; stored verbatim on the trigger.
#
# ``separator=True`` prefixes a vertical ``"|"`` glyph + 8 px gap to
# preserve the visual chunking used in the legacy renderers
# (``"| limit:"``, ``"| activation:"``, ``"| basis:"``).


@dataclass(frozen=True)
class _FieldSpec:
    """One trigger-row input widget, declaratively described."""

    attr: str
    label: str
    kind: str
    width: int = 8
    choices: tuple[Any, ...] | None = None
    separator: bool = False


_FIELD_SPECS_BY_KIND: dict[TriggerKind, tuple[_FieldSpec, ...]] = {
    TriggerKind.LIMIT: (
        _FieldSpec("price",         "price:",   "float", width=10),
        _FieldSpec("offset_pct",    "offset%:", "float", width=8),
        _FieldSpec("offset_dollar", "offset$:", "float", width=8),
    ),
    TriggerKind.STOP: (
        _FieldSpec("price",         "price:",   "float", width=10),
        _FieldSpec("offset_pct",    "offset%:", "float", width=8),
        _FieldSpec("offset_dollar", "offset$:", "float", width=8),
    ),
    TriggerKind.STOP_LIMIT: (
        _FieldSpec("price",            "price:",   "float", width=10),
        _FieldSpec("offset_pct",       "offset%:", "float", width=8),
        _FieldSpec("offset_dollar",    "offset$:", "float", width=8),
        _FieldSpec("stop_limit_price", "limit:",   "float", width=10, separator=True),
    ),
    TriggerKind.TRAILING_STOP: (
        _FieldSpec("trail_unit",       "trail:",      "enum",           width=8,
                   choices=_TRAIL_UNIT_CHOICES),
        _FieldSpec("trail_value",      "",            "float",          width=8),
        _FieldSpec("activation_unit",  "activation:", "enum_with_none", width=12,
                   choices=_ACTIVATION_UNIT_CHOICES, separator=True),
        _FieldSpec("activation_value", "",            "float",          width=8),
        _FieldSpec("trail_basis",      "basis:",      "enum",           width=10,
                   choices=_TRAIL_BASIS_CHOICES, separator=True),
    ),
    TriggerKind.CHANDELIER: (
        _FieldSpec("chandelier_lookback",   "lookback:",   "int",      width=5),
        _FieldSpec("chandelier_atr_period", "ATR period:", "int",      width=5),
        _FieldSpec("chandelier_multiplier", "mult:",       "float",    width=6),
        _FieldSpec("chandelier_ma_type",    "MA:",         "enum_str", width=6,
                   choices=_CHANDELIER_MA_TYPE_CHOICES),
    ),
    TriggerKind.TIME_OF_DAY: (
        _FieldSpec("time_of_day", "HH:MM:", "time_str", width=8),
    ),
}


# ---------------------------------------------------------------------------
# Bracket-template prompt
# ---------------------------------------------------------------------------


class _BracketDialog(BaseModalDialog):
    """Tiny modal asking for target/stop unit+value + qty% allocation.

    Migrated to ``BaseModalDialog`` in commit ``audit-4-pilot`` —
    `BaseModalDialog` owns ``transient`` / ``grab_set`` / geometry
    persistence / ESC+Return keys via ``_finalize_modal``. We just
    build the body + footer; the wheel guard catches the two
    Comboboxes (target_unit, stop_unit) for free per CLAUDE.md §7.11.
    """

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(
            parent,
            title="Bracket template",
            geometry_key="dlg.bracket",
            default_geometry="340x260",
        )
        self.result: dict[str, Any] | None = None

        frm = ttk.Frame(self)
        frm.pack(padx=8, pady=8)

        self._target_unit_var = tk.StringVar(value="percent")
        self._target_value_var = tk.StringVar(value="2.0")
        self._stop_unit_var = tk.StringVar(value="percent")
        self._stop_value_var = tk.StringVar(value="1.0")
        self._qty_pct_var = tk.StringVar(value="100")
        self._name_var = tk.StringVar(value="Bracket")

        rows = [
            ("Name:",          self._name_var,         None),
            ("Target unit:",   self._target_unit_var,  ("percent", "dollar")),
            ("Target value:",  self._target_value_var, None),
            ("Stop unit:",     self._stop_unit_var,    ("percent", "dollar")),
            ("Stop value:",    self._stop_value_var,   None),
            ("Qty %:",         self._qty_pct_var,      None),
        ]
        for r, (lbl, var, choices) in enumerate(rows):
            ttk.Label(frm, text=lbl).grid(row=r, column=0, sticky="w", pady=2)
            if choices is None:
                ttk.Entry(frm, textvariable=var, width=14).grid(
                    row=r, column=1, sticky="ew", padx=(6, 0))
            else:
                ttk.Combobox(
                    frm, textvariable=var, values=choices,
                    state="readonly", width=12,
                ).grid(row=r, column=1, sticky="ew", padx=(6, 0))

        btnrow = ttk.Frame(self)
        btnrow.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btnrow, text="Cancel", command=self._cancel).pack(side="right", padx=(2, 0))
        ttk.Button(btnrow, text="Create", command=self._ok).pack(side="right")
        # CLAUDE.md §7.11 — must come AFTER all widgets exist so the
        # walker can find the two ``state="readonly"`` Comboboxes.
        protect_combobox_wheel(self)
        self._finalize_modal(primary=self._ok, cancel=self._cancel)

    def _on_cancel(self) -> None:
        """BaseModalDialog hook: ESC / WM_DELETE = treat as cancel."""
        self._cancel()

    def _ok(self) -> None:
        try:
            self.result = {
                "name":         self._name_var.get().strip() or "Bracket",
                "target_unit":  self._target_unit_var.get(),
                "target_value": float(self._target_value_var.get()),
                "stop_unit":    self._stop_unit_var.get(),
                "stop_value":   float(self._stop_value_var.get()),
                "qty_pct":      float(self._qty_pct_var.get()),
            }
        except ValueError as exc:
            messagebox.showerror("Bracket", f"Invalid number: {exc}", parent=self)
            return
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


# ---------------------------------------------------------------------------
# Per-leg editor frame
# ---------------------------------------------------------------------------


class _LegFrame(ttk.LabelFrame):
    """Editor for a single :class:`ExitLeg` — header + trigger rows."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        leg: ExitLeg,
        dialog: ExitsDialog,
    ) -> None:
        super().__init__(master, text=leg.label or f"leg {leg.id[:6]}")
        self._leg = leg
        self._dialog = dialog

        head = ttk.Frame(self)
        head.pack(fill="x", padx=4, pady=2)
        ttk.Label(head, text="Label:").pack(side="left")
        self._label_var = tk.StringVar(value=leg.label)
        e = ttk.Entry(head, textvariable=self._label_var, width=18)
        e.pack(side="left", padx=(2, 8))
        self._label_var.trace_add("write", lambda *_: self._on_label_changed())

        self._enabled_var = tk.BooleanVar(value=leg.enabled)
        ttk.Checkbutton(
            head, text="Enabled", variable=self._enabled_var,
            command=self._on_enabled_changed,
        ).pack(side="left")

        ttk.Button(
            head, text="× Delete leg",
            command=lambda: self._dialog.remove_leg(self._leg.id),
        ).pack(side="right")
        ttk.Button(
            head, text="+ Trigger",
            command=self._on_add_trigger,
        ).pack(side="right", padx=(0, 4))

        self._triggers_holder = ttk.Frame(self)
        self._triggers_holder.pack(fill="x", padx=4, pady=(0, 4))
        for t in leg.triggers:
            row = _TriggerRow(self._triggers_holder, trigger=t, leg_frame=self)
            row.pack(fill="x", pady=1)

    @property
    def leg(self) -> ExitLeg:
        return self._leg

    def _on_label_changed(self) -> None:
        self._leg.label = self._label_var.get()
        try:
            self.configure(text=self._leg.label or f"leg {self._leg.id[:6]}")
        except tk.TclError:
            pass

    def _on_enabled_changed(self) -> None:
        self._leg.enabled = bool(self._enabled_var.get())

    def _on_add_trigger(self) -> None:
        self._leg.triggers.append(ExitTrigger(kind=TriggerKind.MARKET))
        # Light rebuild — easier than wiring per-row tail-append.
        self._dialog._rebuild_editor()

    def remove_trigger(self, trigger_id: str) -> None:
        self._leg.triggers = [t for t in self._leg.triggers if t.id != trigger_id]
        self._dialog._rebuild_editor()


# ---------------------------------------------------------------------------
# Per-trigger row
# ---------------------------------------------------------------------------


class _TriggerRow(ttk.Frame):
    """One trigger row inside a leg.

    ``kind`` dropdown drives a dynamic ``_params_frame`` that swaps
    widgets per kind.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        trigger: ExitTrigger,
        leg_frame: _LegFrame,
    ) -> None:
        super().__init__(master, padding=2, borderwidth=1, relief="solid")
        self._trigger = trigger
        self._leg_frame = leg_frame

        head = ttk.Frame(self)
        head.pack(fill="x")

        # kind dropdown
        ttk.Label(head, text="Kind:").pack(side="left")
        self._kind_var = tk.StringVar(value=_TRIGGER_KIND_LABEL[trigger.kind])
        cb = ttk.Combobox(
            head, textvariable=self._kind_var, state="readonly",
            values=[lbl for _, lbl in _TRIGGER_KIND_CHOICES], width=14,
        )
        cb.pack(side="left", padx=(2, 8))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._on_kind_changed())

        # qty%
        ttk.Label(head, text="qty%:").pack(side="left")
        self._qty_pct_var = tk.StringVar(value=f"{trigger.qty_pct:g}")
        ttk.Entry(head, textvariable=self._qty_pct_var, width=6).pack(side="left", padx=(2, 8))
        self._qty_pct_var.trace_add("write", lambda *_: self._on_qty_pct_changed())

        # enabled
        self._enabled_var = tk.BooleanVar(value=trigger.enabled)
        ttk.Checkbutton(
            head, text="Enabled", variable=self._enabled_var,
            command=self._on_enabled_changed,
        ).pack(side="left", padx=(0, 8))

        # label
        ttk.Label(head, text="Label:").pack(side="left")
        self._label_var = tk.StringVar(value=trigger.label)
        ttk.Entry(head, textvariable=self._label_var, width=14).pack(side="left", padx=(2, 4))
        self._label_var.trace_add("write", lambda *_: self._on_label_changed())

        # delete
        ttk.Button(
            head, text="× Trigger",
            command=lambda: self._leg_frame.remove_trigger(self._trigger.id),
        ).pack(side="right")

        # Per-kind param frame
        self._params_frame = ttk.Frame(self)
        self._params_frame.pack(fill="x", padx=2, pady=(2, 0))
        # Vars per kind (held to keep them alive)
        self._param_vars: dict[str, tk.Variable] = {}
        self._block_editor: BlockEditor | None = None
        self._render_params()

    @property
    def trigger(self) -> ExitTrigger:
        return self._trigger

    @property
    def block_editor(self) -> BlockEditor | None:
        return self._block_editor

    def _on_kind_changed(self) -> None:
        new = _TRIGGER_KIND_BY_LABEL.get(self._kind_var.get())
        if new is None:
            return
        self._trigger.kind = new
        self._render_params()

    def _on_qty_pct_changed(self) -> None:
        try:
            self._trigger.qty_pct = float(self._qty_pct_var.get())
        except (ValueError, tk.TclError):
            pass

    def _on_enabled_changed(self) -> None:
        self._trigger.enabled = bool(self._enabled_var.get())

    def _on_label_changed(self) -> None:
        self._trigger.label = self._label_var.get()

    # ----- Per-kind param rendering -----

    def _render_params(self) -> None:
        for child in list(self._params_frame.winfo_children()):
            child.destroy()
        self._param_vars.clear()
        self._block_editor = None
        kind = self._trigger.kind
        if kind == TriggerKind.MARKET:
            ttk.Label(
                self._params_frame,
                text="(fires immediately on arming — no parameters)",
                foreground=MUTED_GREY,
            ).pack(side="left")
            return
        if kind == TriggerKind.INDICATOR:
            self._render_indicator()
            return
        specs = _FIELD_SPECS_BY_KIND.get(kind, ())
        for spec in specs:
            self._render_field(spec)

    def _render_field(self, spec: _FieldSpec) -> None:
        """Render one schema-described field into ``_params_frame``."""
        label_text = (
            f"| {spec.label}" if spec.separator and spec.label else spec.label
        )
        if label_text:
            ttk.Label(self._params_frame, text=label_text).pack(
                side="left", padx=((8 if spec.separator else 0), 0),
            )
        elif spec.separator:
            ttk.Label(self._params_frame, text="|").pack(side="left", padx=(8, 0))

        kind = spec.kind
        attr = spec.attr
        if kind == "float":
            cur = getattr(self._trigger, attr)
            var = tk.StringVar(value="" if cur is None else f"{cur:g}")
            self._param_vars[attr] = var
            ttk.Entry(
                self._params_frame, textvariable=var, width=spec.width,
            ).pack(side="left", padx=(2, 6))
            var.trace_add(
                "write", lambda *_a, name=attr: self._set_float_attr(name))
        elif kind == "int":
            cur = getattr(self._trigger, attr)
            var = tk.StringVar(value=str(cur))
            self._param_vars[attr] = var
            ttk.Entry(
                self._params_frame, textvariable=var, width=spec.width,
            ).pack(side="left", padx=(2, 6))
            var.trace_add(
                "write", lambda *_a, name=attr: self._set_int_attr(name))
        elif kind == "time_str":
            cur = getattr(self._trigger, attr) or ""
            var = tk.StringVar(value=cur)
            self._param_vars[attr] = var
            ttk.Entry(
                self._params_frame, textvariable=var, width=spec.width,
            ).pack(side="left", padx=(2, 4))

            def _on_change(*_, name=attr, v=var):
                txt = v.get().strip()
                setattr(self._trigger, name, txt or None)
            var.trace_add("write", _on_change)
        elif kind == "enum":
            choices = spec.choices or ()
            labels = [lbl for _, lbl in choices]
            cur_label = next(
                (lbl for value, lbl in choices
                 if value == getattr(self._trigger, attr)),
                labels[0] if labels else "",
            )
            var = tk.StringVar(value=cur_label)
            self._param_vars[attr] = var
            cb = ttk.Combobox(
                self._params_frame, textvariable=var, state="readonly",
                values=labels, width=spec.width,
            )
            cb.pack(side="left", padx=(2, 4))
            cb.bind(
                "<<ComboboxSelected>>",
                lambda _e, name=attr, v=var, c=choices:
                    self._set_enum_attr(name, v, c),
            )
        elif kind == "enum_with_none":
            choices = spec.choices or ()
            labels = ["(none)"] + [lbl for _, lbl in choices]
            cur_value = getattr(self._trigger, attr)
            cur_label = next(
                (lbl for value, lbl in choices if value == cur_value),
                "(none)",
            )
            var = tk.StringVar(value=cur_label)
            self._param_vars[attr] = var
            cb = ttk.Combobox(
                self._params_frame, textvariable=var, state="readonly",
                values=labels, width=spec.width,
            )
            cb.pack(side="left", padx=(2, 4))
            cb.bind(
                "<<ComboboxSelected>>",
                lambda _e, name=attr, v=var, c=choices:
                    self._set_enum_with_none_attr(name, v, c),
            )
        elif kind == "enum_str":
            options = tuple(spec.choices or ())
            cur = (getattr(self._trigger, attr) or "").upper()
            if cur not in options:
                cur = options[0] if options else ""
            var = tk.StringVar(value=cur)
            self._param_vars[attr] = var
            cb = ttk.Combobox(
                self._params_frame, textvariable=var, state="readonly",
                values=list(options), width=spec.width,
            )
            cb.pack(side="left", padx=(2, 4))
            cb.bind(
                "<<ComboboxSelected>>",
                lambda _e, name=attr, v=var:
                    setattr(self._trigger, name, v.get()),
            )

    def _set_enum_with_none_attr(
        self,
        attr: str,
        var: tk.StringVar,
        choices: tuple[tuple[Any, str], ...],
    ) -> None:
        label = var.get()
        if label == "(none)":
            setattr(self._trigger, attr, None)
            return
        for value, lbl in choices:
            if lbl == label:
                setattr(self._trigger, attr, value)
                return

    def _render_indicator(self) -> None:
        # Interval picker
        bar = ttk.Frame(self._params_frame)
        bar.pack(fill="x")
        ttk.Label(bar, text="interval:").pack(side="left")
        cur = self._trigger.interval or "(position)"
        v_iv = tk.StringVar(value=cur if cur in _INDICATOR_INTERVAL_CHOICES else "(position)")
        self._param_vars["interval"] = v_iv
        cb_iv = ttk.Combobox(
            bar, textvariable=v_iv, state="readonly",
            values=list(_INDICATOR_INTERVAL_CHOICES), width=10,
        )
        cb_iv.pack(side="left", padx=(2, 8))
        cb_iv.bind("<<ComboboxSelected>>", lambda _e: self._set_indicator_interval(v_iv))

        # Intrabar checkbox
        v_ib = tk.BooleanVar(value=self._trigger.evaluate_intrabar)
        self._param_vars["evaluate_intrabar"] = v_ib
        ttk.Checkbutton(
            bar, text="Evaluate intrabar", variable=v_ib,
            command=lambda: setattr(self._trigger, "evaluate_intrabar", bool(v_ib.get())),
        ).pack(side="left")

        # Block editor
        cond = self._trigger.condition
        if cond is None:
            cond = ConditionGroup(combinator="and", children=[])
            self._trigger.condition = cond
        be = BlockEditor(
            self._params_frame, root=cond,
            on_change=self._on_indicator_changed,
            default_interval=(self._trigger.interval or "5m"),
        )
        be.pack(fill="x", expand=True, pady=(2, 0))
        self._block_editor = be

    def _on_indicator_changed(self) -> None:
        if self._block_editor is None:
            return
        # The BlockEditor mutates its root in place; we just need to
        # re-bind in case the user replaced the entire root.
        self._trigger.condition = self._block_editor.get_root()

    def _set_indicator_interval(self, var: tk.StringVar) -> None:
        v = var.get()
        if v == "(position)":
            self._trigger.interval = None
        else:
            self._trigger.interval = v

    # ----- Generic attribute setters -----

    def _set_float_attr(self, attr: str) -> None:
        var = self._param_vars.get(attr)
        if var is None:
            return
        raw = var.get().strip()
        if raw == "":
            setattr(self._trigger, attr, None)
            return
        try:
            setattr(self._trigger, attr, float(raw))
        except ValueError:
            pass  # silent — user is mid-typing

    def _set_int_attr(self, attr: str) -> None:
        var = self._param_vars.get(attr)
        if var is None:
            return
        raw = var.get().strip()
        if raw == "":
            return
        try:
            setattr(self._trigger, attr, int(raw))
        except ValueError:
            pass  # silent — user is mid-typing

    def _set_enum_attr(
        self,
        attr: str,
        var: tk.StringVar,
        choices: tuple[tuple[Any, str], ...],
    ) -> None:
        label = var.get()
        for value, lbl in choices:
            if lbl == label:
                setattr(self._trigger, attr, value)
                return


# ---------------------------------------------------------------------------
# OCO group row
# ---------------------------------------------------------------------------


class _OCOGroupRow(ttk.Frame):
    """One OCO group: leg-id chips + cancel_on dropdown + delete."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        oco: OCOGroup,
        dialog: ExitsDialog,
    ) -> None:
        super().__init__(master, padding=2, borderwidth=1, relief="solid")
        self._oco = oco
        self._dialog = dialog
        # Find this group's index in the draft for dispatch
        if dialog._draft is None:
            self._index = -1
        else:
            try:
                self._index = dialog._draft.oco_groups.index(oco)
            except ValueError:
                self._index = -1

        ttk.Label(self, text="Legs:").pack(side="left")
        self._chip_holder = ttk.Frame(self)
        self._chip_holder.pack(side="left", padx=(2, 8))
        self._render_chips()

        ttk.Label(self, text="cancel_on:").pack(side="left", padx=(4, 0))
        self._cancel_on_var = tk.StringVar(value=oco.cancel_on)
        cb = ttk.Combobox(
            self, textvariable=self._cancel_on_var, state="readonly",
            values=list(_OCO_CANCEL_ON_CHOICES), width=14,
        )
        cb.pack(side="left", padx=(2, 8))
        cb.bind("<<ComboboxSelected>>", lambda _e:
                self._dialog.set_oco_cancel_on(self._index, self._cancel_on_var.get()))

        ttk.Button(
            self, text="× Group",
            command=lambda: self._dialog.remove_oco_group(self._index),
        ).pack(side="right")

    def _render_chips(self) -> None:
        for child in list(self._chip_holder.winfo_children()):
            child.destroy()
        if self._dialog._draft is None:
            return
        dup_legs = self._dialog._oco_dup_legs
        for leg in self._dialog._draft.legs:
            on = leg.id in self._oco.leg_ids
            text = (leg.label or f"leg {leg.id[:6]}")
            if on:
                text = f"☑ {text}"
            else:
                text = f"☐ {text}"
            chip = tk.Button(
                self._chip_holder, text=text,
                relief=("sunken" if on else "raised"),
                command=lambda lid=leg.id: self._dialog.toggle_leg_in_group(self._index, lid),
            )
            if leg.id in dup_legs:
                # Disjoint validation: red border on duplicates
                try:
                    chip.configure(highlightbackground="red",
                                   highlightcolor="red",
                                   highlightthickness=2,
                                   foreground=ERROR_RED)
                except tk.TclError:
                    pass
            chip.pack(side="left", padx=1)
