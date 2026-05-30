"""Phase 1c sandbox dialogs: post-trade review + tag taxonomy editor.

* :class:`PostTradeReviewDialog` — modal that pops every time a
  sandbox position closes. Mandatory free-form review text; cannot be
  dismissed without typing at least one character (per the locked
  decision: every closed trade must be journaled).
* :class:`TagsEditorDialog` — small list editor for the sandbox setup
  tag taxonomy. Add / Remove / OK round-trip into a :class:`TagStore`.
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime, timezone
from tkinter import ttk
from typing import Any

from ._modal_base import BaseModalDialog, protect_combobox_wheel
from .colors import DOWN_RED, UP_GREEN
from .native_theme import apply_listbox_theme, apply_text_theme, current_theme


class PostTradeReviewDialog(BaseModalDialog):
    """Modal: capture mandatory user review for a closed trade."""

    def __init__(self, app: Any, post_trade: Any):
        side = post_trade.side.upper()
        super().__init__(
            app,
            title=f"Post-Trade Review — {post_trade.symbol} {side}",
            geometry_key="dlg.post_trade_review",
            default_geometry="440x420",
            resizable=(False, False),
        )
        self.app = app
        self.post_trade = post_trade
        self.result: str | None = None

        self._build()

        self._review_text.focus_set()
        protect_combobox_wheel(self)
        # WM_DELETE / X-button MUST refuse to dismiss until a review is
        # entered — route close gestures through ``_on_attempted_close``.
        # The base class's _finalize_modal binds both ESC and the close
        # protocol to the supplied cancel callback. To preserve the
        # legacy "ESC is intentionally NOT bound" contract we unbind
        # ESC explicitly after finalize.
        self._finalize_modal(
            primary=self._on_submit,
            cancel=self._on_attempted_close,
        )
        try:
            self.unbind("<Escape>")
        except tk.TclError:
            pass

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        frame = ttk.Frame(self)
        frame.grid(row=0, column=0, sticky="nsew", **pad)

        p = self.post_trade
        try:
            entry_dt = datetime.fromtimestamp(int(p.entry_ts), tz=timezone.utc) \
                .strftime("%Y-%m-%d %H:%M UTC")
            exit_dt = datetime.fromtimestamp(int(p.exit_ts), tz=timezone.utc) \
                .strftime("%Y-%m-%d %H:%M UTC")
        except Exception:  # noqa: BLE001
            entry_dt = str(p.entry_ts)
            exit_dt = str(p.exit_ts)

        pnl_color = UP_GREEN if p.pnl >= 0 else DOWN_RED
        sign = "+" if p.pnl >= 0 else ""

        ttk.Label(frame, text=f"{p.symbol}  {p.side.upper()}  ×  {p.quantity:g}",
                  font=("TkDefaultFont", 12, "bold")) \
            .grid(row=0, column=0, columnspan=2, sticky="w", **pad)

        rows = [
            ("Entry:", f"{entry_dt}  @  ${p.entry_price:,.4f}"),
            ("Exit:",  f"{exit_dt}  @  ${p.exit_price:,.4f}"),
            ("PnL:",   f"{sign}${p.pnl:,.2f}  ({sign}{p.pnl_pct * 100:.2f}%)"),
            ("MAE:",   f"${p.mae:,.2f}  ({p.mae_pct * 100:.2f}%)"),
            ("MFE:",   f"${p.mfe:,.2f}  ({p.mfe_pct * 100:.2f}%)"),
        ]
        for i, (label, val) in enumerate(rows, start=1):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="e", **pad)
            kw = {"foreground": pnl_color} if label == "PnL:" else {}
            ttk.Label(frame, text=val, **kw) \
                .grid(row=i, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Review (mandatory) — what did you learn?") \
            .grid(row=6, column=0, columnspan=2, sticky="w", **pad)
        self._review_text = tk.Text(frame, width=44, height=5)
        apply_text_theme(self._review_text, current_theme(self.app))
        self._review_text.grid(row=7, column=0, columnspan=2, sticky="ew", **pad)

        self._error_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._error_var, foreground="red") \
            .grid(row=8, column=0, columnspan=2, sticky="w", **pad)

        btns = ttk.Frame(frame)
        btns.grid(row=9, column=0, columnspan=2, sticky="ew", **pad)
        ttk.Button(btns, text="Submit", command=self._on_submit) \
            .pack(side=tk.RIGHT, padx=4)

    def _on_attempted_close(self) -> None:
        # Mandatory: nudge the user instead of dismissing.
        self._error_var.set("Review is mandatory — type at least one character.")

    def _on_submit(self) -> None:
        text = self._review_text.get("1.0", "end").strip()
        if not text:
            self._error_var.set("Review cannot be empty.")
            return
        self.result = text
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


class TagsEditorDialog(BaseModalDialog):
    """Modal: add / remove sandbox setup tags."""

    def __init__(self, app: Any, tag_store: Any):
        super().__init__(
            app,
            title="Sandbox Setup Tags",
            geometry_key="dlg.tags_editor",
            default_geometry="420x360",
            resizable=(False, False),
        )
        self.app = app
        self.tag_store = tag_store
        self.result: bool | None = None  # True if applied; False/None if cancelled

        self._build()
        self._refresh_listbox()

        self._new_var_entry.focus_set()
        protect_combobox_wheel(self)
        self._finalize_modal(primary=self._on_ok, cancel=self._on_cancel)

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        frame = ttk.Frame(self)
        frame.grid(row=0, column=0, sticky="nsew", **pad)

        ttk.Label(frame, text="Setup tags (used by the pre-trade form):") \
            .grid(row=0, column=0, columnspan=2, sticky="w", **pad)

        self._listbox = tk.Listbox(frame, height=8, exportselection=False)
        apply_listbox_theme(self._listbox, current_theme(self.app))
        self._listbox.grid(row=1, column=0, sticky="ew", **pad)
        ttk.Button(frame, text="Remove", command=self._on_remove) \
            .grid(row=1, column=1, sticky="ne", **pad)

        ttk.Label(frame, text="New tag:") \
            .grid(row=2, column=0, sticky="e", **pad)
        new_row = ttk.Frame(frame)
        new_row.grid(row=2, column=1, sticky="w", **pad)
        self._new_var = tk.StringVar(value="")
        self._new_var_entry = ttk.Entry(new_row, textvariable=self._new_var, width=18)
        self._new_var_entry.pack(side=tk.LEFT)
        self._new_var_entry.bind("<Return>", lambda _e: self._on_add())
        ttk.Button(new_row, text="Add", command=self._on_add) \
            .pack(side=tk.LEFT, padx=4)
        ttk.Button(new_row, text="Sort A→Z", command=self._on_sort_az) \
            .pack(side=tk.LEFT, padx=4)

        self._error_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._error_var, foreground="red") \
            .grid(row=3, column=0, columnspan=2, sticky="w", **pad)

        btns = ttk.Frame(frame)
        btns.grid(row=4, column=0, columnspan=2, sticky="ew", **pad)
        # Windows dialog convention (audit ``button-order-windows``):
        # visual order ``[OK] [Cancel]`` with the dismiss action
        # rightmost. ``side=tk.RIGHT`` reverses pack order, so pack
        # Cancel first so it lands rightmost.
        ttk.Button(btns, text="Cancel", command=self._on_cancel) \
            .pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="OK", command=self._on_ok) \
            .pack(side=tk.RIGHT, padx=4)

    def _refresh_listbox(self) -> None:
        self._listbox.delete(0, tk.END)
        for t in self.tag_store.list():
            self._listbox.insert(tk.END, t)

    def _on_add(self) -> None:
        new = self._new_var.get().strip()
        if not new:
            return
        if not self.tag_store.add(new):
            self._error_var.set(f"Tag '{new}' already exists (case-insensitive).")
            return
        self._error_var.set("")
        self._new_var.set("")
        self._refresh_listbox()

    def _on_remove(self) -> None:
        sel = self._listbox.curselection()
        if not sel:
            return
        tag = self._listbox.get(sel[0])
        self.tag_store.remove(tag)
        self._refresh_listbox()

    def _on_sort_az(self) -> None:
        """Sort tags alphabetically in place.

        Reads the current list, sorts case-insensitively, and writes
        back through ``TagStore.replace`` so the order persists in the
        backing store (used by the pre-trade form's Combobox).
        """
        current = self.tag_store.list()
        ordered = sorted(current, key=lambda t: t.casefold())
        if ordered == current:
            return
        self.tag_store.replace(ordered)
        self._refresh_listbox()

    def _on_ok(self) -> None:
        self.result = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = False
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


__all__ = ("PostTradeReviewDialog", "TagsEditorDialog")
