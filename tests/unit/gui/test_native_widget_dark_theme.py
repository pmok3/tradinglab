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


def test_watchlist_columns_dialog_listbox_uses_dark_theme(dark_root: tk.Toplevel) -> None:
    from tradinglab.gui.watchlist_columns_dialog import WatchlistColumnsDialog
    from tradinglab.watchlists.columns import default_columns

    dlg = WatchlistColumnsDialog(
        dark_root,
        watchlist_name="Test",
        columns=default_columns(),
        on_apply=lambda _cols: None,
    )
    try:
        _assert_dark_listbox(dlg._listbox)
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


def test_apply_toplevel_theme_paints_window_bg(root: tk.Toplevel) -> None:
    """The ``apply_toplevel_theme`` helper paints a Toplevel's classic
    ``bg`` with the theme's ``win_bg`` (ttk.Style does not reach it)."""
    from tradinglab.gui.native_theme import apply_toplevel_theme
    top = tk.Toplevel(root)
    try:
        apply_toplevel_theme(top, DARK_THEME)
        assert str(top.cget("background")) == DARK_THEME["win_bg"]
    finally:
        top.destroy()


def test_universe_prepare_dialog_toplevel_uses_dark_theme(dark_root: tk.Toplevel) -> None:
    """The Download Replay Data (Prepare Universe) dialog must paint its
    Toplevel background with the dark window colour AND let its themed
    ttk content frame fill the whole window (grid weights) — so no bright
    system-default background shows on the right/bottom in dark mode (the
    reported "right half is all white" bug). The dialog has no classic Tk
    widgets, so it is tested here rather than on the meta-test roster.
    """
    from tradinglab.gui.universe_prepare_dialog import UniversePrepareDialog
    dlg = UniversePrepareDialog(
        dark_root, source_name="yfinance",
        fetcher=lambda _sym, _interval: None,
    )
    try:
        assert str(dlg.cget("background")) == DARK_THEME["win_bg"]
        # Themed content frame fills the Toplevel (no unthemed gap right/bottom).
        assert int(dlg.grid_columnconfigure(0).get("weight", 0)) == 1
        assert int(dlg.grid_rowconfigure(0).get("weight", 0)) == 1
    finally:
        dlg.destroy()


# ===========================================================================
# Meta-test: every window's classic Tk widgets are linked to the dark theme.
#
# ``ttk.Style`` does not reach ``tk.Listbox`` / ``tk.Text`` / ``tk.Canvas``;
# a dialog that forgets to theme them shows bright white chrome in dark mode
# (the reported Documentation-viewer bug). Rather than rely solely on the
# per-dialog exact-colour tests above, this generic probe constructs each
# registered window under a dark ``_theme_ctrl`` and asserts that EVERY
# classic Tk widget resolves to a dark background — catching any window that
# isn't colour-linked, regardless of which exact dark palette it uses.
#
# Add a new combobox/listbox/text/canvas-bearing window to ``_DARK_WINDOWS``
# and it is protected automatically.
# ===========================================================================

_CLASSIC_TK_TYPES = (tk.Listbox, tk.Text, tk.Canvas)


def _bg_is_dark(widget: tk.Widget) -> bool:
    """True if ``widget``'s resolved background is a dark shade.

    Resolves hex AND named/system colours via ``winfo_rgb`` (0..65535 per
    channel) so an unthemed widget left on its system default (white-ish
    on a light-mode host) is correctly flagged.
    """
    try:
        bg = str(widget.cget("background"))
        r, g, b = widget.winfo_rgb(bg)
    except tk.TclError:
        return False
    luma = (0.299 * r + 0.587 * g + 0.114 * b) / 65535.0
    return luma < 0.5


def _classic_widgets(root: tk.Misc) -> list[tk.Widget]:
    """Every ``tk.Listbox`` / ``tk.Text`` / ``tk.Canvas`` descendant.

    Skips widgets explicitly tagged theme-exempt (``_no_theme``) — e.g.
    colour-swatch canvases whose background IS the data being shown.
    """
    out: list[tk.Widget] = []

    def _walk(w: tk.Misc) -> None:
        try:
            children = w.winfo_children()
        except tk.TclError:
            return
        for child in children:
            if isinstance(child, _CLASSIC_TK_TYPES) and not getattr(
                child, "_no_theme", False
            ):
                out.append(child)
            _walk(child)

    _walk(root)
    return out


def _assert_window_classic_widgets_dark(dialog: tk.Misc, label: str) -> int:
    widgets = _classic_widgets(dialog)
    assert widgets, f"{label}: no classic Tk widget found to check"
    light = [w for w in widgets if not _bg_is_dark(w)]
    assert not light, (
        f"{label}: {len(light)} classic Tk widget(s) NOT linked to the dark "
        f"theme (white/light background under dark mode). Theme them via "
        f"gui/native_theme.py (or the window's own dark palette). Offenders: "
        + ", ".join(
            f"{type(w).__name__}={str(w.cget('background'))}" for w in light[:6]
        )
    )
    return len(widgets)


# --- window registry -------------------------------------------------------


def _build_doc_viewer(dark_root, _monkeypatch):
    from tradinglab.gui.doc_viewer import DocViewerDialog
    return DocViewerDialog(dark_root)


def _build_watchlist(dark_root, _monkeypatch):
    dark_root._watchlists = _FakeWatchlists()  # type: ignore[attr-defined]
    return dialogs._WatchlistDialog(dark_root)  # noqa: SLF001


def _build_exits(dark_root, monkeypatch):
    monkeypatch.setattr(exits_dialog._exits_storage, "load_all", lambda: ([], []))
    return exits_dialog.ExitsDialog(dark_root)


def _build_sandbox_panel(dark_root, _monkeypatch):
    return sandbox_panel.SandboxPanel(dark_root, _FakeSandboxController())


def _build_post_trade_review(dark_root, _monkeypatch):
    post = SimpleNamespace(
        side="long", symbol="AAPL", quantity=1.0,
        entry_ts=1_700_000_000, exit_ts=1_700_000_060,
        entry_price=100.0, exit_price=101.0, pnl=1.0, pnl_pct=0.01,
        mae=0.5, mae_pct=0.005, mfe=1.5, mfe_pct=0.015,
    )
    return sandbox_review_dialog.PostTradeReviewDialog(dark_root, post)


def _build_tags_editor(dark_root, _monkeypatch):
    return sandbox_review_dialog.TagsEditorDialog(dark_root, _FakeTagStore())


def _build_load_scan(dark_root, _monkeypatch):
    return scanner_tab._LoadScanDialog(  # noqa: SLF001
        dark_root, [("scan-1", SimpleNamespace(name="Breakout"))],
    )


def _build_pre_trade(dark_root, _monkeypatch):
    return pre_trade_dialog.PreTradeFormDialog(dark_root, "AAPL", setup_tags=["Gap"])


def _build_color_chooser(dark_root, _monkeypatch):
    from tradinglab.gui.color_palette import ThemedColorChooser
    return ThemedColorChooser(dark_root, initial="#1f77b4")


_DARK_WINDOWS = {
    "DocViewerDialog": _build_doc_viewer,
    "WatchlistDialog": _build_watchlist,
    "ExitsDialog": _build_exits,
    "SandboxPanel": _build_sandbox_panel,
    "PostTradeReviewDialog": _build_post_trade_review,
    "TagsEditorDialog": _build_tags_editor,
    "LoadScanDialog": _build_load_scan,
    "PreTradeFormDialog": _build_pre_trade,
    "ThemedColorChooser": _build_color_chooser,
}


@pytest.mark.parametrize("window_name", sorted(_DARK_WINDOWS))
def test_window_classic_widgets_linked_to_dark_theme(
    window_name, dark_root, monkeypatch,
) -> None:
    """Every classic Tk widget in the window resolves to a dark background.

    Fails for any window that leaves a ``tk.Listbox`` / ``tk.Text`` /
    ``tk.Canvas`` on its (light) system default in dark mode — the
    Documentation-viewer dark-mode bug, generalised across the roster.
    """
    builder = _DARK_WINDOWS[window_name]
    try:
        dlg = builder(dark_root, monkeypatch)
    except tk.TclError as exc:
        pytest.skip(f"{window_name} could not open headlessly: {exc}")
    try:
        _assert_window_classic_widgets_dark(dlg, window_name)
    finally:
        with contextlib.suppress(tk.TclError):
            dlg.destroy()


def test_probe_flags_unthemed_classic_widget(dark_root) -> None:
    """The probe has teeth: an unthemed Listbox/Text/Canvas is flagged."""
    top = tk.Toplevel(dark_root)
    try:
        tk.Listbox(top).pack()  # left on the light system default
        with pytest.raises(AssertionError, match="NOT linked to the dark"):
            _assert_window_classic_widgets_dark(top, "synthetic-unthemed")
    finally:
        with contextlib.suppress(tk.TclError):
            top.destroy()
