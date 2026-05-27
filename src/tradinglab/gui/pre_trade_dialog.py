"""Mandatory pre-trade journal modal for the sandbox subsystem.

Extracted from :mod:`tradinglab.gui.sandbox_dialog` so the start-of-
session :class:`SandboxStartDialog` and the per-order
:class:`PreTradeFormDialog` live in independent files. The two dialogs
have no shared helpers; they were grouped purely because they both
belong to the sandbox feature.

The form refuses to submit unless `thesis` is non-empty and `size` is
a positive number. It also surfaces an optional ``notice`` string
(earnings/dividend proximity etc., supplied by the caller) inline at
the top of the form so the trader sees it before filling anything in.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from ._modal_base import BaseModalDialog, protect_combobox_wheel
from .colors import WARN_AMBER


class PreTradeFormDialog(BaseModalDialog):
    """Modal: capture mandatory pre-trade journal entry + side/size."""

    def __init__(
        self,
        app: Any,
        symbol: str,
        default_side: str = "buy",
        default_size: float = 1.0,
        setup_tags: list[str] | None = None,
        *,
        notice: str = "",
        suggested_tags: list[str] | None = None,
    ):
        super().__init__(
            app,
            title=f"Pre-Trade Form — {symbol}",
            geometry_key="dlg.pre_trade",
            default_geometry="380x420",
            resizable=(False, False),
        )
        self.app = app
        self.result: dict[str, Any] | None = None
        self._symbol = symbol

        pad = {"padx": 8, "pady": 4}
        frame = ttk.Frame(self)
        frame.grid(row=0, column=0, sticky="nsew", **pad)

        # Row 0 — passive earnings/dividend proximity notice (plan.md
        # decision 12). Inline at the very top of the form so the user
        # sees it before filling anything; no extra click. The notice is
        # built by :class:`SandboxPanel` from
        # :meth:`SandboxController._compute_event_proximity` — empty
        # string means "no proximity context to surface".
        row = 0
        if notice:
            notice_lbl = ttk.Label(
                frame, text=str(notice),
                foreground=WARN_AMBER,  # amber — neutral warning
                font=("TkDefaultFont", 9, "bold"),
                wraplength=320, justify="left",
            )
            notice_lbl.grid(row=row, column=0, columnspan=2,
                            sticky="w", **pad)
            row += 1

        ttk.Label(frame, text=f"Symbol: {symbol}", font=("TkDefaultFont", 10, "bold")) \
            .grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        row += 1

        ttk.Label(frame, text="Side:").grid(row=row, column=0, sticky="e", **pad)
        self._side_var = tk.StringVar(value=default_side.lower())
        side_cb = ttk.Combobox(frame, textvariable=self._side_var,
                               values=["buy", "sell"], state="readonly", width=10)
        side_cb.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        ttk.Label(frame, text="Size (units):").grid(row=row, column=0, sticky="e", **pad)
        self._size_var = tk.StringVar(value=str(default_size))
        ttk.Entry(frame, textvariable=self._size_var, width=12) \
            .grid(row=row, column=1, sticky="w", **pad)
        row += 1

        # The setup-tag combobox prepends any proximity-suggested tags
        # ahead of the user's custom list so they're a one-click choice.
        # Suggested tags are deduplicated against the existing setup_tags
        # so we don't double-render the same name.
        ttk.Label(frame, text="Setup tag:").grid(row=row, column=0, sticky="e", **pad)
        self._tag_var = tk.StringVar(value="")
        base_tags = list(setup_tags or [])
        sugg = [t for t in (suggested_tags or [])
                if t and t not in base_tags]
        combined_tags = sugg + base_tags
        tag_cb = ttk.Combobox(frame, textvariable=self._tag_var,
                              values=combined_tags, width=20)
        tag_cb.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        ttk.Label(frame, text="Thesis (mandatory):").grid(row=row, column=0, sticky="ne", **pad)
        self._thesis_text = tk.Text(frame, width=32, height=4)
        self._thesis_text.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        ttk.Label(frame, text="Conviction (1-5):").grid(row=row, column=0, sticky="e", **pad)
        self._conv_var = tk.IntVar(value=3)
        tk.Spinbox(frame, from_=1, to=5, textvariable=self._conv_var, width=6) \
            .grid(row=row, column=1, sticky="w", **pad)
        row += 1

        ttk.Label(frame, text="Target price (optional):").grid(row=row, column=0, sticky="e", **pad)
        self._target_var = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self._target_var, width=12) \
            .grid(row=row, column=1, sticky="w", **pad)
        row += 1

        ttk.Label(frame, text="Notes:").grid(row=row, column=0, sticky="ne", **pad)
        self._notes_text = tk.Text(frame, width=32, height=3)
        self._notes_text.grid(row=row, column=1, sticky="w", **pad)
        row += 1

        self._error_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._error_var, foreground="red") \
            .grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        row += 1

        btns = ttk.Frame(frame)
        btns.grid(row=row, column=0, columnspan=2, sticky="ew", **pad)
        # Windows dialog convention (audit ``button-order-windows``):
        # visual order ``[Submit] [Cancel]`` with the dismiss action
        # rightmost. ``side=tk.RIGHT`` reverses pack order, so pack
        # Cancel first so it lands rightmost.
        ttk.Button(btns, text="Cancel", command=self._on_cancel) \
            .pack(side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="Submit", command=self._on_submit) \
            .pack(side=tk.RIGHT, padx=4)

        self._thesis_text.focus_set()
        protect_combobox_wheel(self)
        self._finalize_modal(primary=self._on_submit, cancel=self._on_cancel)

    def _on_submit(self) -> None:
        side = self._side_var.get().strip().lower()
        if side not in ("buy", "sell"):
            self._error_var.set("Side must be buy or sell.")
            return
        try:
            size = float(self._size_var.get())
        except ValueError:
            self._error_var.set("Size must be numeric.")
            return
        if size <= 0:
            self._error_var.set("Size must be positive.")
            return
        thesis = self._thesis_text.get("1.0", "end").strip()
        if not thesis:
            self._error_var.set("Thesis is mandatory.")
            return
        target_raw = self._target_var.get().strip()
        target: float | None = None
        if target_raw:
            try:
                target = float(target_raw)
            except ValueError:
                self._error_var.set("Target must be numeric or blank.")
                return
        self.result = {
            "symbol": self._symbol,
            "side": side,
            "quantity": size,
            "pre_trade_data": {
                "setup_tag": self._tag_var.get().strip(),
                "thesis": thesis,
                "conviction": int(self._conv_var.get()),
                "size": size,
                "target": target,
                "notes": self._notes_text.get("1.0", "end").strip(),
            },
        }
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


__all__ = ("PreTradeFormDialog",)
