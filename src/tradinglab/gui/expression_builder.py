"""Visual "+" token-stacker for composing an Expression operand.

Builds a ``FieldRef(kind="expression")`` — an arithmetic stack of atoms
(fields / indicators — including *custom* ones — / constants) combined with
binary operators and parentheses. Each operand is picked with the shared
:class:`~tradinglab.gui.scanner_block_editor._FieldRefPicker` (so the full
categorized field / indicator surface, including custom indicators, is
available). Reused by the operand picker in Entries / Exits / Scanner.

Layout: a horizontal chip strip — one chip per token (operand chips carry
an edit + remove control; operator chips are a small readonly combobox) —
plus a trailing ``+`` menu that appends the next token, and a live preview
+ validity line beneath.
"""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Any

from ..scanner.model import (
    EXPR_BINARY_OPS,
    EXPR_OPERAND,
    EXPR_OPS,
    EXPR_PAREN_CLOSE,
    EXPR_PAREN_OPEN,
    FIELD_KIND_EXPRESSION,
    FIELD_KIND_INDICATOR,
    FIELD_KIND_LITERAL,
    ExprToken,
    FieldRef,
    validate_expression,
)
from ._modal_base import BaseModalDialog, protect_combobox_wheel
from .colors import ERROR_RED, MUTED_GREY, up_green
from .menu_theme import menu_theme_options
from .native_theme import current_theme

LOG = logging.getLogger(__name__)


def _fmt_num(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f):
        return str(int(f))
    return f"{f:g}"


def operand_summary(ref: FieldRef | None) -> str:
    """Short human label for an operand chip / the preview line."""
    if ref is None:
        return "?"
    if ref.kind == FIELD_KIND_LITERAL:
        return _fmt_num(ref.value)
    if ref.kind == FIELD_KIND_EXPRESSION:
        return "( … )"
    label = str(ref.id)
    if ref.kind == FIELD_KIND_INDICATOR and ref.params:
        nums = [_fmt_num(v) for v in ref.params.values()
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums:
            label = f"{ref.id}({','.join(nums)})"
    if getattr(ref, "output_key", ""):
        label += f".{ref.output_key}"
    if getattr(ref, "symbol", ""):
        label += f"@{ref.symbol}"
    return label


def expression_text(terms: list[ExprToken] | tuple[ExprToken, ...]) -> str:
    """Render the token list as a readable one-line expression."""
    parts: list[str] = []
    for t in terms:
        parts.append(operand_summary(t.operand) if t.kind == EXPR_OPERAND else t.op)
    return " ".join(parts)


class ExpressionBuilder(ttk.Frame):
    """The "+" token-stacker. ``get()`` returns a ``FieldRef(kind='expression')``."""

    def __init__(self, master: tk.Misc, *, ref: FieldRef | None = None,
                 on_change: Any = None, data_status_provider: Any = None) -> None:
        super().__init__(master)
        self._on_change = on_change
        self._data_status_provider = data_status_provider
        self._terms: list[ExprToken] = (
            list(ref.terms) if ref is not None
            and ref.kind == FIELD_KIND_EXPRESSION else []
        )
        self._chips = ttk.Frame(self)
        self._chips.pack(fill="x", anchor="w")
        status = ttk.Frame(self)
        status.pack(fill="x", anchor="w", pady=(4, 0))
        ttk.Label(status, text="=").pack(side="left")
        self._preview_var = tk.StringVar()
        ttk.Label(status, textvariable=self._preview_var,
                  foreground=MUTED_GREY).pack(side="left", padx=(4, 12))
        self._valid_var = tk.StringVar()
        self._valid_lbl = ttk.Label(status, textvariable=self._valid_var)
        self._valid_lbl.pack(side="left")
        self._render()

    # -- public API -----------------------------------------------------------

    def get(self) -> FieldRef:
        return FieldRef.expression(tuple(self._terms))

    def set(self, ref: FieldRef | None) -> None:
        self._terms = (
            list(ref.terms) if ref is not None
            and ref.kind == FIELD_KIND_EXPRESSION else []
        )
        self._render()

    def is_valid(self) -> bool:
        return validate_expression(tuple(self._terms))[0]

    # -- rendering ------------------------------------------------------------

    def _render(self) -> None:
        for w in self._chips.winfo_children():
            w.destroy()
        for i, tok in enumerate(self._terms):
            if tok.kind == EXPR_OPERAND:
                self._operand_chip(i, tok)
            else:
                self._op_chip(i, tok)
        add = ttk.Menubutton(self._chips, text="+", width=3)
        add["menu"] = self._add_menu(add)
        add.pack(side="left", padx=3, pady=2)
        self._refresh_status()

    def _operand_chip(self, i: int, tok: ExprToken) -> None:
        chip = ttk.Frame(self._chips, relief="solid", borderwidth=1, padding=(5, 2))
        chip.pack(side="left", padx=2, pady=2)
        ttk.Label(chip, text=operand_summary(tok.operand)).pack(side="left")
        ttk.Button(chip, text="\u270e", width=2,
                   command=lambda i=i: self._edit_operand(i)).pack(side="left", padx=(5, 0))
        ttk.Button(chip, text="\u2715", width=2,
                   command=lambda i=i: self._remove(i)).pack(side="left")

    def _op_chip(self, i: int, tok: ExprToken) -> None:
        chip = ttk.Frame(self._chips, padding=(1, 2))
        chip.pack(side="left", padx=1)
        var = tk.StringVar(value=tok.op)
        cb = ttk.Combobox(chip, textvariable=var, state="readonly", width=3,
                          values=list(EXPR_OPS))
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>",
                lambda _e, i=i, var=var: self._set_op(i, var.get()))
        ttk.Button(chip, text="\u2715", width=2,
                   command=lambda i=i: self._remove(i)).pack(side="left")

    def _add_menu(self, parent: tk.Misc) -> tk.Menu:
        opts = menu_theme_options(current_theme(self))
        m = tk.Menu(parent, tearoff=False, **opts)
        m.add_command(label="Value…", command=self._add_operand)
        opm = tk.Menu(m, tearoff=False, **opts)
        for o in EXPR_BINARY_OPS:
            opm.add_command(label=o, command=lambda o=o: self._add_op(o))
        opm.add_separator()
        opm.add_command(label="(  open group",
                        command=lambda: self._add_op(EXPR_PAREN_OPEN))
        opm.add_command(label=")  close group",
                        command=lambda: self._add_op(EXPR_PAREN_CLOSE))
        m.add_cascade(label="Operator", menu=opm)
        return m

    # -- mutations ------------------------------------------------------------

    def _add_operand(self) -> None:
        ref = self._pick_operand(FieldRef.builtin("close"))
        if ref is not None:
            self._terms.append(ExprToken.operand_token(ref))
            self._changed()

    def _edit_operand(self, i: int) -> None:
        ref = self._pick_operand(self._terms[i].operand)
        if ref is not None:
            self._terms[i] = ExprToken.operand_token(ref)
            self._changed()

    def _add_op(self, o: str) -> None:
        self._terms.append(ExprToken.op_token(o))
        self._changed()

    def _set_op(self, i: int, o: str) -> None:
        if o in EXPR_OPS and 0 <= i < len(self._terms):
            self._terms[i] = ExprToken.op_token(o)
            self._changed()

    def _remove(self, i: int) -> None:
        if 0 <= i < len(self._terms):
            del self._terms[i]
            self._changed()

    def _pick_operand(self, current: FieldRef | None) -> FieldRef | None:
        dlg = _OperandDialog(self, ref=current,
                             data_status_provider=self._data_status_provider)
        try:
            self.wait_window(dlg)
        except tk.TclError:
            return None
        return dlg.result

    def _changed(self) -> None:
        self._render()
        if self._on_change:
            try:
                self._on_change()
            except Exception:  # noqa: BLE001
                LOG.exception("ExpressionBuilder on_change raised")

    def _refresh_status(self) -> None:
        self._preview_var.set(expression_text(self._terms) or "(empty)")
        ok, msg = validate_expression(tuple(self._terms))
        if ok:
            self._valid_var.set("\u2713 valid")
            self._valid_lbl.configure(foreground=up_green())
        else:
            self._valid_var.set(f"\u2715 {msg}")
            self._valid_lbl.configure(foreground=ERROR_RED)


class _OperandDialog(BaseModalDialog):
    """Modal wrapping a ``_FieldRefPicker`` to choose / edit one operand."""

    def __init__(self, parent: tk.Misc, *, ref: FieldRef | None,
                 data_status_provider: Any = None) -> None:
        super().__init__(parent, title="Choose value",
                         geometry_key="dlg.expr_operand",
                         default_geometry="760x300")
        self.result: FieldRef | None = None
        # Lazy import breaks the scanner_block_editor <-> expression_builder cycle.
        from .scanner_block_editor import _FieldRefPicker
        frame = ttk.Frame(self, padding=10)
        frame.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        ttk.Label(frame, text="Value (field, indicator, or constant):").pack(
            anchor="w")
        # An expression operand should be an atom — a nested expression is
        # reachable via parentheses, not by recursing the picker.
        picker_ref = ref if (ref is not None
                             and ref.kind != FIELD_KIND_EXPRESSION) else FieldRef.builtin("close")
        self._picker = _FieldRefPicker(
            frame, ref=picker_ref, data_status_provider=data_status_provider)
        self._picker.pack(fill="x", pady=8, anchor="w")
        bar = ttk.Frame(frame)
        bar.pack(fill="x", side="bottom")
        ttk.Button(bar, text="OK", command=self._ok).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(
            side="right", padx=6)
        protect_combobox_wheel(self)
        self._finalize_modal(primary=self._ok, cancel=self.destroy)

    def _ok(self) -> None:
        try:
            self.result = self._picker.get()
        except tk.TclError:
            self.result = None
        self.destroy()


__all__ = ("ExpressionBuilder", "operand_summary", "expression_text")
