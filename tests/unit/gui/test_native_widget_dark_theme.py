from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

pytest.importorskip("tkinter")
import tkinter as tk  # noqa: E402

from tradinglab.constants import DARK_THEME
from tradinglab.gui import (
    dialogs,
    exits_dialog,
    pre_trade_dialog,
    sandbox_panel,
    sandbox_review_dialog,
    scanner_tab,
)


class _FakeWatchlists:
    MAX_PINNED = 5

    def __init__(self) -> None:
        self._wl = SimpleNamespace(tickers=["AAPL", "MSFT"])

    def list_names(self) -> list[str]:
        return ["Momentum"]

    def pinned_names(self) -> list[str]:
        return []

    def get(self, _name: str):
        return self._wl


class _FakeSandboxController:
    app = SimpleNamespace(_display_tz="", ticker_var=None)
    focus_symbol = "AAPL"
    blind = False

    def set_post_trade_callback(self, _callback) -> None:
        return None

    def clock_ts(self) -> int:
        return 1_700_000_000

    def cash(self) -> float:
        return 100_000.0

    def is_active(self) -> bool:
        return True

    def tickers(self) -> list[str]:
        return ["AAPL", "MSFT"]

    def positions_snapshot(self) -> list[dict[str, object]]:
        return []


class _FakeTagStore:
    def list(self) -> list[str]:
        return ["Gap", "Pullback"]


@pytest.fixture()
def dark_root(root: tk.Toplevel):
    root._theme_ctrl = SimpleNamespace(theme=DARK_THEME)  # type: ignore[attr-defined]
    yield root
    with contextlib.suppress(AttributeError):
        delattr(root, "_theme_ctrl")


def _assert_dark_listbox(lb: tk.Listbox) -> None:
    assert str(lb.cget("background")) == DARK_THEME["tree_bg"]
    assert str(lb.cget("foreground")) == DARK_THEME["tree_fg"]
    assert str(lb.cget("selectbackground")) == DARK_THEME["spine"]
    assert str(lb.cget("selectforeground")) == DARK_THEME["tree_fg"]
    assert str(lb.cget("highlightbackground")) == DARK_THEME["spine"]
    assert str(lb.cget("highlightcolor")) == DARK_THEME["spine"]
    assert str(lb.cget("highlightthickness")) == "1"
    assert str(lb.cget("borderwidth")) == "0"
    assert str(lb.cget("relief")) == "flat"


def _assert_dark_text(txt: tk.Text) -> None:
    assert str(txt.cget("background")) == DARK_THEME["ax_bg"]
    assert str(txt.cget("foreground")) == DARK_THEME["text"]
    assert str(txt.cget("insertbackground")) == DARK_THEME["text"]
    assert str(txt.cget("selectbackground")) == DARK_THEME["spine"]
    assert str(txt.cget("selectforeground")) == DARK_THEME["text"]
    assert str(txt.cget("highlightbackground")) == DARK_THEME["spine"]
    assert str(txt.cget("highlightcolor")) == DARK_THEME["spine"]
    assert str(txt.cget("highlightthickness")) == "1"
    assert str(txt.cget("borderwidth")) == "0"
    assert str(txt.cget("relief")) == "flat"


def test_watchlist_dialog_tickers_listbox_uses_dark_theme(dark_root: tk.Toplevel) -> None:
    dark_root._watchlists = _FakeWatchlists()  # type: ignore[attr-defined]
    dlg = dialogs._WatchlistDialog(dark_root)  # noqa: SLF001
    try:
        _assert_dark_listbox(dlg._tickers)
    finally:
        dlg.destroy()


def test_exits_dialog_library_listbox_uses_dark_theme(dark_root: tk.Toplevel, monkeypatch) -> None:
    monkeypatch.setattr(exits_dialog._exits_storage, "load_all", lambda: ([], []))
    dlg = exits_dialog.ExitsDialog(dark_root)
    try:
        _assert_dark_listbox(dlg._library_lb)
    finally:
        dlg.destroy()


def test_sandbox_panel_focus_listbox_uses_dark_theme(dark_root: tk.Toplevel) -> None:
    panel = sandbox_panel.SandboxPanel(dark_root, _FakeSandboxController())
    try:
        _assert_dark_listbox(panel._focus_lb)
    finally:
        panel.destroy()


def test_post_trade_review_text_uses_dark_theme(dark_root: tk.Toplevel) -> None:
    post = SimpleNamespace(
        side="long",
        symbol="AAPL",
        quantity=1.0,
        entry_ts=1_700_000_000,
        exit_ts=1_700_000_060,
        entry_price=100.0,
        exit_price=101.0,
        pnl=1.0,
        pnl_pct=0.01,
        mae=0.5,
        mae_pct=0.005,
        mfe=1.5,
        mfe_pct=0.015,
    )
    dlg = sandbox_review_dialog.PostTradeReviewDialog(dark_root, post)
    try:
        _assert_dark_text(dlg._review_text)
    finally:
        dlg.destroy()


def test_tags_editor_listbox_uses_dark_theme(dark_root: tk.Toplevel) -> None:
    dlg = sandbox_review_dialog.TagsEditorDialog(dark_root, _FakeTagStore())
    try:
        _assert_dark_listbox(dlg._listbox)
    finally:
        dlg.destroy()


def test_load_scan_dialog_listbox_uses_dark_theme(dark_root: tk.Toplevel) -> None:
    dlg = scanner_tab._LoadScanDialog(  # noqa: SLF001
        dark_root,
        [("scan-1", SimpleNamespace(name="Breakout"))],
    )
    try:
        _assert_dark_listbox(dlg._listbox)
    finally:
        dlg.destroy()


def test_pre_trade_dialog_text_widgets_use_dark_theme(dark_root: tk.Toplevel) -> None:
    dlg = pre_trade_dialog.PreTradeFormDialog(dark_root, "AAPL", setup_tags=["Gap"])
    try:
        _assert_dark_text(dlg._thesis_text)
        _assert_dark_text(dlg._notes_text)
    finally:
        dlg.destroy()


def test_color_palette_canvas_uses_dark_theme(dark_root: tk.Toplevel) -> None:
    """The themed ``ThemedColorChooser`` (audit
    ``themed-color-chooser``) must paint its four ``tk.Canvas``
    chrome backgrounds with the active dark theme — the rendered
    swatch + gradient pixels stay as the colours being displayed.

    Detailed per-canvas + per-label dark-theme assertions live in
    `tests/unit/gui/test_themed_color_chooser.py`; this test pins
    that the dialog appears on the dark-themed-dialog audit roster.
    """
    from tradinglab.gui.color_palette import ThemedColorChooser
    dlg = ThemedColorChooser(dark_root, initial="#1f77b4")
    try:
        win_bg = DARK_THEME["win_bg"]
        assert str(dlg.cget("background")) == win_bg
        for canvas in (dlg._basic_canvas, dlg._custom_canvas,
                       dlg._pad_canvas, dlg._slider_canvas):
            assert str(canvas.cget("background")) == win_bg, (
                f"canvas {canvas} bg is not dark"
            )
    finally:
        dlg.destroy()
