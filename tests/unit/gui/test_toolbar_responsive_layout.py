"""Responsive toolbar layout regression tests."""
from __future__ import annotations

import tkinter as tk

from tradinglab.gui.app_state import AppState
from tradinglab.gui.toolbar_controller import ToolbarController


class _Callbacks:
    def on_axis_change(self) -> None:
        pass

    def on_compare_toggle(self) -> None:
        pass

    def on_prepost_toggle(self) -> None:
        pass

    def on_reset_view(self) -> None:
        pass

    def on_open_settings(self) -> None:
        pass

    def on_open_watchlists(self) -> None:
        pass

    def on_theme_toggle(self) -> None:
        pass


def test_layout_classifier_uses_measured_fit() -> None:
    classify = ToolbarController._layout_for_width
    assert classify(1200, (350, 400, 300)) == "wide"
    assert classify(800, (350, 400, 300)) == "actions-row"
    assert classify(720, (500, 300, 400)) == "symbol-row"
    assert classify(600, (500, 350, 300)) == "stacked"


def test_narrow_toolbar_reflows_without_clipping(_tk_root: tk.Tk) -> None:
    host = tk.Toplevel(_tk_root)
    host.geometry("800x240")
    state = AppState(host, {})
    toolbar = ToolbarController(
        host,
        state,
        callbacks=_Callbacks(),
        intervals=("1m", "5m", "1d"),
        sources=("yfinance", "Auto"),
    )
    toolbar.frame.pack(fill="x")
    try:
        for _ in range(4):
            host.update_idletasks()
            host.update()
        assert toolbar.layout_mode != "wide"
        available = toolbar.frame.winfo_width()
        for group in toolbar._groups:
            assert group.winfo_x() + group.winfo_width() <= available + 2
    finally:
        host.destroy()
