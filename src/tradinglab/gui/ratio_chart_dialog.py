"""Discoverable ratio-chart composer dialog."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from ..data import (
    RATIO_DELIMITER,
    RATIO_PRESETS,
    canonical_ratio_symbol,
    is_ratio_symbol,
    ratio_display_label,
)
from ._modal_base import BaseModalDialog, protect_combobox_wheel
from .colors import MUTED_GREY


class RatioChartDialog(BaseModalDialog):
    """Compose and submit a ratio pseudo-symbol such as ``AMD/NVDA``."""

    def __init__(self, parent: tk.Misc, *, on_submit: Callable[[str], None]) -> None:
        super().__init__(
            parent,
            title="New Ratio Chart",
            geometry_key="dlg.ratio_chart",
            default_geometry="460x430",
            resizable=(False, False),
        )
        self._on_submit = on_submit
        self._preset_var = tk.StringVar(value="")
        self._num_var = tk.StringVar(value="")
        self._den_var = tk.StringVar(value="")
        self._preview_var = tk.StringVar(value="")
        self._status_var = tk.StringVar(value="")
        self._preset_by_description = {
            description: (num, den) for num, den, description in RATIO_PRESETS
        }

        self._build_widgets()
        self._num_var.trace_add("write", self._on_symbol_change)
        self._den_var.trace_add("write", self._on_symbol_change)
        self._refresh_preview()
        protect_combobox_wheel(self)
        self._finalize_modal(primary=self._on_ok, cancel=self._on_close)

    # ------------------------------------------------------------------ build
    def _build_widgets(self) -> None:
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(
            frm,
            text="Chart the ratio of two symbols, e.g. AMD / NVDA.",
            wraplength=420,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Label(frm, text="Presets").grid(row=1, column=0, sticky="w", pady=(0, 6))
        preset = ttk.Combobox(
            frm,
            textvariable=self._preset_var,
            values=[description for _, _, description in RATIO_PRESETS],
            state="readonly",
        )
        preset.grid(row=1, column=1, sticky="ew", pady=(0, 6))
        preset.bind("<<ComboboxSelected>>", self._on_preset_selected)
        self._preset_combo = preset

        ttk.Label(frm, text="Numerator").grid(row=2, column=0, sticky="w", pady=(8, 6))
        num_entry = ttk.Entry(frm, textvariable=self._num_var)
        num_entry.grid(row=2, column=1, sticky="ew", pady=(8, 6))
        self._num_entry = num_entry

        ttk.Label(frm, text="Denominator").grid(row=3, column=0, sticky="w", pady=(0, 6))
        den_entry = ttk.Entry(frm, textvariable=self._den_var)
        den_entry.grid(row=3, column=1, sticky="ew", pady=(0, 6))
        self._den_entry = den_entry

        ttk.Separator(frm, orient="horizontal").grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(12, 10)
        )

        ttk.Label(frm, text="Preview").grid(row=5, column=0, sticky="w", pady=(0, 4))
        ttk.Label(frm, textvariable=self._preview_var, font=("TkDefaultFont", 10, "bold")).grid(
            row=5, column=1, sticky="w", pady=(0, 4)
        )
        ttk.Label(
            frm,
            textvariable=self._status_var,
            foreground=MUTED_GREY,
            wraplength=420,
            justify="left",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))

        footer = ttk.Frame(frm)
        footer.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(20, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Button(footer, text="Cancel", command=self._on_close).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(footer, text="Chart Ratio", command=self._on_ok).grid(row=0, column=2)

    # ------------------------------------------------------------------ state
    def _on_preset_selected(self, _event: tk.Event | None = None) -> None:
        selected = self._preset_var.get()
        legs = self._preset_by_description.get(selected)
        if legs is None:
            return
        num, den = legs
        self._num_var.set(num)
        self._den_var.set(den)
        self._status_var.set("")

    def _on_symbol_change(self, *_args: object) -> None:
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        num = self._num_var.get().strip().upper()
        den = self._den_var.get().strip().upper()
        self._preview_var.set(ratio_display_label(f"{num}{RATIO_DELIMITER}{den}"))

    # ------------------------------------------------------------------ commit
    def _on_ok(self) -> None:
        num = self._num_var.get().strip().upper()
        den = self._den_var.get().strip().upper()
        if not num or not den:
            self._status_var.set("Enter both a numerator and denominator symbol.")
            return
        if RATIO_DELIMITER in num or RATIO_DELIMITER in den:
            self._status_var.set("Enter plain ticker symbols only; do not include '/' in either leg.")
            return

        raw = f"{num}{RATIO_DELIMITER}{den}"
        if not is_ratio_symbol(raw):
            self._status_var.set("Enter two plain ticker symbols that form a valid ratio.")
            return

        self._on_submit(canonical_ratio_symbol(raw))
        self._on_close()

    def _on_close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


def open_ratio_chart_dialog(
    parent: tk.Misc, *, on_submit: Callable[[str], None]
) -> RatioChartDialog | None:
    """Open the ratio chart composer as a modal child of ``parent``."""
    try:
        dlg = RatioChartDialog(parent, on_submit=on_submit)
        parent.wait_window(dlg)
        return dlg
    except tk.TclError:
        return None


__all__ = ["RatioChartDialog", "open_ratio_chart_dialog"]
