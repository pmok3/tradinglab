from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest
from _pytest.outcomes import Failed

pytest.importorskip("tkinter")
import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

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


class _FakeDecisionSandboxController(_FakeSandboxController):
    def decision_logging_enabled(self) -> bool:
        return True

    def decisions_snapshot(self) -> list:
        return []


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
        assert panel._decision_btn is None
    finally:
        panel.destroy()


def test_sandbox_panel_decision_control_is_opt_in(dark_root: tk.Toplevel) -> None:
    panel = sandbox_panel.SandboxPanel(
        dark_root, _FakeDecisionSandboxController())
    try:
        assert panel._decision_btn is not None
        assert panel._decision_count_var.get() == "none logged"
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


def test_decision_log_note_uses_dark_theme(dark_root: tk.Toplevel) -> None:
    dlg = sandbox_review_dialog.DecisionLogDialog(
        dark_root, "AAPL", setup_tags=["Gap"])
    try:
        _assert_dark_text(dlg._note_text)
    finally:
        dlg.destroy()


def test_decision_log_validates_and_returns_payload(dark_root: tk.Toplevel) -> None:
    dlg = sandbox_review_dialog.DecisionLogDialog(
        dark_root, "AAPL", setup_tags=["Gap"])
    try:
        dlg._on_submit()
        assert dlg.result is None
        assert "Choose" in dlg._error_var.get()
        dlg._action_var.set("Pass")
        dlg._confidence_var.set(5)
        dlg._note_text.insert("1.0", "Breakout lacked volume")
        dlg._on_submit()
        assert dlg.result == {
            "action": "pass",
            "setup_tag": "Gap",
            "confidence": 5,
            "note": "Breakout lacked volume",
        }
    finally:
        with contextlib.suppress(tk.TclError):
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


def test_base_modal_dialog_paints_toplevel_dark(dark_root: tk.Toplevel) -> None:
    """``BaseModalDialog._finalize_modal`` paints the Toplevel's classic
    ``bg`` for EVERY subclass.

    ``ttk.Style`` never reaches a Toplevel's own background, so a dialog
    whose content does not cover the full window rendered the bright
    system default in dark mode. Hoisting the paint into the base class
    (rather than a third hand-rolled copy) is what makes the guarantee
    hold for dialogs nobody remembered to fix.
    """
    from tradinglab.gui._modal_base import BaseModalDialog

    dlg = BaseModalDialog(dark_root, title="probe", geometry_key=None)
    try:
        dlg._finalize_modal(grab=False)  # noqa: SLF001
        assert str(dlg.cget("background")) == DARK_THEME["win_bg"], (
            "BaseModalDialog must paint its Toplevel bg with win_bg so no "
            "bright system-default region shows in dark mode"
        )
    finally:
        with contextlib.suppress(tk.TclError):
            dlg.destroy()


def test_sandbox_start_dialog_toplevel_uses_dark_theme(dark_root: tk.Toplevel) -> None:
    """Start Sandbox Session must be dark across the whole window span.

    The dialog is resizable and its persisted ``dlg.sandbox_start``
    geometry can exceed the form's request size, so it needs BOTH halves
    of the fix: a painted Toplevel ``bg`` (the padding gutters) and grid
    weights so the themed ttk content frame stretches into the slack
    instead of leaving a bright region right/bottom.
    """
    import datetime as _dt

    from tradinglab.gui.sandbox_dialog import SandboxStartDialog

    dlg = SandboxStartDialog(
        dark_root,
        reference_symbol="SPY",
        intervals=["1m", "5m", "15m", "1h"],
        eligible_dates_provider=lambda _itv, _src: [_dt.date(2024, 6, 3)],
    )
    try:
        assert str(dlg.cget("background")) == DARK_THEME["win_bg"]
        assert int(dlg.grid_columnconfigure(0).get("weight", 0)) == 1
        assert int(dlg.grid_rowconfigure(0).get("weight", 0)) == 1
        # Teeth: the content frame must really be configured to grow into
        # the slack (weights alone could be set on the wrong cell).
        #
        # This previously measured pixels — `content.winfo_x() +
        # content.winfo_width() >= dlg.winfo_width() - 8`. Dialogs parented
        # to the withdrawn harness root are never mapped, so every value
        # was 1 and the assertion reduced to `1 >= -7`: it passed
        # regardless of the layout and could not have caught a regression
        # of the bug it was written for. The structural check is
        # falsifiable headlessly.
        _assert_no_light_gutter_after_resize(dlg, "SandboxStartDialog")
    finally:
        with contextlib.suppress(tk.TclError):
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
# Containers are walked too (``tk.Frame`` / ``tk.LabelFrame`` / the Toplevel
# itself). They are the specific cause of "the window was dark until I
# dragged it bigger, and the new strip came back grey": while the window is
# small the children cover the frame, so an unthemed container is invisible.
#
# Add a new combobox/listbox/text/canvas-bearing window to ``_DARK_WINDOWS``
# and it is protected automatically.
# ===========================================================================

#: Leaf widgets ``ttk.Style`` cannot reach.
_CLASSIC_TK_TYPES = (tk.Listbox, tk.Text, tk.Canvas)

#: Containers whose own background is revealed when a window grows.
_CONTAINER_TK_TYPES = (tk.Frame, tk.LabelFrame, tk.Toplevel)


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


def _themed_widgets(root: tk.Misc) -> list[tk.Widget]:
    """Every classic-Tk leaf AND container descendant, plus ``root`` itself.

    ``ttk`` widgets are skipped — the global ``ThemeController`` styles
    those. Widgets tagged ``_no_theme`` are skipped too (e.g. colour-swatch
    canvases whose background IS the data being shown).
    """
    watched = _CLASSIC_TK_TYPES + _CONTAINER_TK_TYPES
    out: list[tk.Widget] = []

    def _eligible(w: tk.Misc) -> bool:
        if getattr(w, "_no_theme", False):
            return False
        # ttk widgets subclass ttk.Widget, never the classic tk types.
        return isinstance(w, watched) and not isinstance(w, ttk.Widget)

    def _walk(w: tk.Misc) -> None:
        try:
            children = w.winfo_children()
        except tk.TclError:
            return
        for child in children:
            if _eligible(child):
                out.append(child)
            _walk(child)

    if _eligible(root):
        out.append(root)  # type: ignore[arg-type]
    _walk(root)
    return out


def _classic_widgets(root: tk.Misc) -> list[tk.Widget]:
    """Leaf-only view, kept for the narrow per-dialog assertions."""
    return [w for w in _themed_widgets(root)
            if isinstance(w, _CLASSIC_TK_TYPES)]


def _assert_window_classic_widgets_dark(dialog: tk.Misc, label: str) -> int:
    widgets = _themed_widgets(dialog)
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


def _assert_no_light_gutter_after_resize(dialog: tk.Misc, label: str) -> None:
    """Assert no region of the window can render unthemed when it grows.

    The second, independent cause of "expanding the window reveals a light
    strip": the content is themed correctly but does not *expand*, so
    whatever is behind it shows through the slack.

    There are two legitimate ways to be safe, and this accepts either:

    1. **A direct child expands in both axes** — the content covers the
       whole window, so nothing behind it is ever visible.
    2. **The Toplevel's own background is themed** — the slack is still
       painted, just by the window rather than the content.

    ``BaseModalDialog`` gives every dialog (2) for free via
    ``native_theme.apply_toplevel_theme``, which is why several fixed-size
    dialogs legitimately leave their content unexpanded: their
    ``default_geometry`` is larger than the form, and the leftover strip is
    the painted window background. Demanding (1) unconditionally would be
    asserting a layout preference, not the theme guarantee the user cares
    about. Failing **both** is the real bug — it is what shipped in the
    Prepare Universe and Start Sandbox dialogs.

    **Structural, not pixel-based, and deliberately so.** The obvious test
    is to call ``geometry("1100x850")`` and measure. That does not work
    here: dialogs parented to the withdrawn harness root are never mapped,
    so every ``winfo_width()`` is ``1`` and a measured assertion like
    ``reach >= width - 8`` reduces to ``1 >= -7``, passing no matter what
    the layout does. The hand-written check this generalises had exactly
    that hole. Geometry-manager configuration and widget colours are both
    readable while unmapped, so this form is falsifiable.
    """
    if not isinstance(dialog, tk.Wm):
        return  # embedded panel, not a window — nothing to resize
    try:
        children = dialog.winfo_children()
    except tk.TclError as exc:
        pytest.skip(f"{label}: children not enumerable ({exc})")
    if not children:
        pytest.skip(f"{label}: no children to check")

    reasons: list[str] = []
    for child in children:
        grows_x, grows_y, why = _child_expansion(dialog, child)
        if grows_x and grows_y:
            return  # (1) content covers the window
        reasons.append(f"{type(child).__name__}: {why}")

    if _bg_is_dark(dialog):  # type: ignore[arg-type]
        return  # (2) the exposed slack is the painted window background

    try:
        bg = str(dialog.cget("background"))
    except tk.TclError:
        bg = "<unreadable>"
    pytest.fail(
        f"{label}: no direct child expands in both axes AND the Toplevel's "
        f"own background is not themed (bg={bg!r}), so growing the window "
        f"exposes an unpainted strip down the right/bottom edge. Fix either "
        f"half: give the content `grid_rowconfigure/columnconfigure(weight=1)` "
        f"plus `sticky='nsew'` (or `pack(expand=True, fill='both')`), or let "
        f"`BaseModalDialog` paint the Toplevel via `apply_dark_theme=True`. "
        f"Children: " + "; ".join(reasons[:6])
    )


def _child_expansion(parent: tk.Misc, child: tk.Misc) -> tuple[bool, bool, str]:
    """Return ``(grows_x, grows_y, explanation)`` for ``child`` in ``parent``."""
    try:
        manager = child.winfo_manager()
    except tk.TclError:
        return (False, False, "geometry manager unreadable")

    if manager == "pack":
        try:
            info = child.pack_info()
        except tk.TclError:
            return (False, False, "pack_info unreadable")
        expand = str(info.get("expand", "0")) not in ("0", "false", "no", "")
        fill = str(info.get("fill", "none"))
        grows_x = expand and fill in ("x", "both")
        grows_y = expand and fill in ("y", "both")
        return (grows_x, grows_y,
                f"pack(expand={expand}, fill={fill!r})")

    if manager == "grid":
        try:
            info = child.grid_info()
            row = int(info.get("row", 0))
            col = int(info.get("column", 0))
            sticky = str(info.get("sticky", ""))
            row_w = int(parent.grid_rowconfigure(row).get("weight", 0) or 0)
            col_w = int(parent.grid_columnconfigure(col).get("weight", 0) or 0)
        except (tk.TclError, TypeError, ValueError):
            return (False, False, "grid info unreadable")
        grows_x = col_w >= 1 and "e" in sticky and "w" in sticky
        grows_y = row_w >= 1 and "n" in sticky and "s" in sticky
        return (grows_x, grows_y,
                f"grid(row={row} weight={row_w}, col={col} weight={col_w}, "
                f"sticky={sticky!r})")

    if manager == "place":
        return (False, False, "place() — absolute geometry, cannot infer")
    return (False, False, f"unhandled geometry manager {manager!r}")



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


def _build_decision_log(dark_root, _monkeypatch):
    return sandbox_review_dialog.DecisionLogDialog(
        dark_root, "AAPL", setup_tags=["Gap"])


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


# --- roster additions: the rest of the reachable dialogs -------------------
#
# These were previously covered only by the *static* rule in
# test_theme_invariant (a name-reference or a documented exemption), which
# cannot see an unthemed container. Building them here puts every reachable
# window through the same colour + gutter probe.


def _build_chartstack_settings(dark_root, _monkeypatch):
    from tradinglab.gui.chartstack_settings_dialog import ChartStackSettingsDialog
    return ChartStackSettingsDialog(dark_root)


def _build_credentials(dark_root, _monkeypatch):
    from tradinglab.gui.credentials_dialog import CredentialsDialog
    return CredentialsDialog(dark_root)


def _build_export_cache(dark_root, _monkeypatch):
    from tradinglab.gui.export_cache_dialog import ExportCacheDialog
    return ExportCacheDialog(dark_root)


def _build_schwab_connect(dark_root, _monkeypatch):
    from tradinglab.gui.schwab_connect_dialog import SchwabConnectDialog
    return SchwabConnectDialog(dark_root)


def _build_local_data(dark_root, _monkeypatch):
    from tradinglab.gui.local_data_dialog import LocalDataDialog
    return LocalDataDialog(dark_root)


def _build_bracket(dark_root, _monkeypatch):
    from tradinglab.gui.exits_dialog_widgets import _BracketDialog
    return _BracketDialog(dark_root)


def _build_watchlist_columns(dark_root, _monkeypatch):
    from tradinglab.gui.watchlist_columns_dialog import WatchlistColumnsDialog
    from tradinglab.watchlists.columns import default_columns
    return WatchlistColumnsDialog(
        dark_root,
        watchlist_name="Momentum",
        columns=list(default_columns()),
        on_apply=lambda _cols: None,
    )


def _build_operand(dark_root, _monkeypatch):
    from tradinglab.gui.expression_builder import _OperandDialog
    return _OperandDialog(dark_root, ref=None)


def _build_fieldref_param(dark_root, _monkeypatch):
    from tradinglab.gui.scanner_block_editor import _FieldRefParamDialog
    from tradinglab.scanner.model import FieldRef
    return _FieldRefParamDialog(
        dark_root, ref=FieldRef.indicator("rsi", params={"length": 14}))


def _build_entries(dark_root, _monkeypatch):
    from tradinglab.gui.entries_dialog import EntriesDialog
    return EntriesDialog(dark_root)


def _build_universe_prepare(dark_root, _monkeypatch):
    from tradinglab.gui.universe_prepare_dialog import UniversePrepareDialog
    return UniversePrepareDialog(
        dark_root, source_name="yfinance", fetcher=lambda _s, _i: [])


def _build_sandbox_start(dark_root, _monkeypatch):
    import datetime as _dt

    from tradinglab.gui.sandbox_dialog import SandboxStartDialog
    return SandboxStartDialog(
        dark_root,
        reference_symbol="SPY",
        intervals=["1m", "5m", "15m", "1h"],
        eligible_dates_provider=lambda _itv, _src: [_dt.date(2024, 6, 3)],
    )


def _build_drawing(dark_root, _monkeypatch):
    from tradinglab.drawings.model import make_hline_drawing
    from tradinglab.drawings.store import DrawingStore
    from tradinglab.gui.drawing_dialog import DrawingDialog

    store = DrawingStore(autosave=False)
    drawing = make_hline_drawing(ticker="AAPL", price=150.0, color="#2962ff")
    store.add(drawing)
    return DrawingDialog(dark_root, store=store, drawing=drawing)


def _build_indicator_dialog(dark_root, _monkeypatch):
    from tradinglab.gui.indicator_dialog import IndicatorDialog
    from tradinglab.indicators.config import IndicatorConfig, IndicatorManager

    mgr = IndicatorManager()
    mgr.add(IndicatorConfig(
        kind_id="sma", params={"length": 20}, display_name="SMA(20)"))
    dark_root._indicator_manager = mgr           # type: ignore[attr-defined]
    dark_root._indicator_dialog = None           # type: ignore[attr-defined]
    dark_root._per_indicator_dialogs = {}        # type: ignore[attr-defined]
    # This dialog resolves its palette from ``app._theme`` rather than the
    # shared ``_theme_ctrl`` seam (see ``IndicatorDialog._apply_theme``), so
    # the fixture's ``_theme_ctrl`` alone would leave it on the light default
    # and the probe would report a false positive.
    dark_root._theme = dict(DARK_THEME)          # type: ignore[attr-defined]
    if not hasattr(dark_root, "interval_var"):
        dark_root.interval_var = tk.StringVar(dark_root, value="1d")  # type: ignore[attr-defined]
    dark_root._on_menu_save_config = lambda *a, **k: None  # type: ignore[attr-defined]
    return IndicatorDialog(dark_root)


def _build_custom_indicator(dark_root, _monkeypatch):
    import tempfile
    from pathlib import Path

    from tradinglab.gui.custom_indicator_dialog import CustomIndicatorDialog
    directory = Path(tempfile.mkdtemp(prefix="tl_custom_ind_"))
    return CustomIndicatorDialog(dark_root, directory=directory)


def _build_theme_editor(dark_root, _monkeypatch):
    from tradinglab.gui.theme_editor import ThemeEditorDialog

    # The editor reaches into a small ChartApp-like surface; mirror the
    # stub in test_theme_editor.py rather than booting a real app.
    dark_root._theme_overrides = {"light": {}, "dark": {}}  # type: ignore[attr-defined]
    if not hasattr(dark_root, "dark_var"):
        dark_root.dark_var = tk.BooleanVar(master=dark_root, value=True)  # type: ignore[attr-defined]
    dark_root.set_theme_override = lambda *a, **k: None      # type: ignore[attr-defined]
    dark_root.clear_theme_overrides = lambda *a, **k: None   # type: ignore[attr-defined]
    dark_root.replace_theme_overrides = lambda *a, **k: None  # type: ignore[attr-defined]
    dark_root._apply_theme = lambda *a, **k: None            # type: ignore[attr-defined]
    return ThemeEditorDialog(dark_root)


#: Dialogs deliberately outside the live probe, with the reason. Enforced by
#: :func:`test_every_dialog_is_probed_or_exempt` so the roster cannot rot.
_PROBE_EXEMPTIONS: dict[str, str] = {
    "BaseModalDialog": "Abstract base — concrete subclasses carry the coverage.",
    "BaseEditorDialog": "Abstract base — concrete subclasses carry the coverage.",
    "PerformanceView": (
        "Needs a fully-populated SessionResult (fills, post-trades, equity "
        "curve) plus a matplotlib figure; constructing one here would "
        "duplicate tests/unit/backtest fixtures. Chrome is ttk + the "
        "BaseModalDialog painted Toplevel, both covered generically."
    ),
    "SandboxHeatmapWindow": (
        "Needs a live SandboxController AND a HeatmapProvider with "
        "point-in-time share counts; covered by tests/unit/gui/"
        "test_heatmap_live_quotes.py and the sandbox heatmap suites."
    ),
    "_SettingsDialog": (
        "Takes the real ChartApp (reads ~20 Tk vars and menu callbacks off "
        "it); a stub deep enough to build it would assert nothing about the "
        "shipped wiring. Covered by the smoke suite's settings round-trip."
    ),
}


_DARK_WINDOWS = {
    "DocViewerDialog": _build_doc_viewer,
    "_WatchlistDialog": _build_watchlist,
    "ExitsDialog": _build_exits,
    "SandboxPanel": _build_sandbox_panel,
    "DecisionLogDialog": _build_decision_log,
    "PostTradeReviewDialog": _build_post_trade_review,
    "TagsEditorDialog": _build_tags_editor,
    "_LoadScanDialog": _build_load_scan,
    "PreTradeFormDialog": _build_pre_trade,
    "ThemedColorChooser": _build_color_chooser,
    "ChartStackSettingsDialog": _build_chartstack_settings,
    "CredentialsDialog": _build_credentials,
    "ExportCacheDialog": _build_export_cache,
    "SchwabConnectDialog": _build_schwab_connect,
    "LocalDataDialog": _build_local_data,
    "_BracketDialog": _build_bracket,
    "WatchlistColumnsDialog": _build_watchlist_columns,
    "_OperandDialog": _build_operand,
    "_FieldRefParamDialog": _build_fieldref_param,
    "EntriesDialog": _build_entries,
    "UniversePrepareDialog": _build_universe_prepare,
    "SandboxStartDialog": _build_sandbox_start,
    "DrawingDialog": _build_drawing,
    "IndicatorDialog": _build_indicator_dialog,
    "CustomIndicatorDialog": _build_custom_indicator,
    "ThemeEditorDialog": _build_theme_editor,
}


@pytest.mark.parametrize("window_name", sorted(_DARK_WINDOWS))
def test_window_classic_widgets_linked_to_dark_theme(
    window_name, dark_root, monkeypatch,
) -> None:
    """Every classic Tk widget in the window resolves to a dark background.

    Fails for any window that leaves a ``tk.Listbox`` / ``tk.Text`` /
    ``tk.Canvas`` — or a ``tk.Frame`` / ``tk.LabelFrame`` / the Toplevel
    itself — on its (light) system default in dark mode. The leaf case is
    the Documentation-viewer dark-mode bug; the container case is the
    "expanded the window and the new strip is grey" bug, invisible until
    the window grows past its children.
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


@pytest.mark.parametrize("window_name", sorted(_DARK_WINDOWS))
def test_window_has_no_light_gutter_after_resize(
    window_name, dark_root, monkeypatch,
) -> None:
    """Growing the window must not reveal an unpainted strip.

    The colour probe above cannot catch this: the content can be themed
    perfectly and still fail to *expand*, leaving the parent's background
    visible down one edge. Generalises the hand-written check that landed
    with the Start Sandbox dark-mode fix — and, unlike it, is falsifiable
    headlessly (see :func:`_assert_no_light_gutter_after_resize`).
    """
    builder = _DARK_WINDOWS[window_name]
    try:
        dlg = builder(dark_root, monkeypatch)
    except tk.TclError as exc:
        pytest.skip(f"{window_name} could not open headlessly: {exc}")
    try:
        _assert_no_light_gutter_after_resize(dlg, window_name)
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


def test_probe_flags_unthemed_container(dark_root) -> None:
    """A light ``tk.Frame`` is flagged even when every child is dark.

    This is the case the leaf-only probe could not see: while the window
    is small the children cover the frame, so it only shows once the
    window grows.
    """
    top = tk.Toplevel(dark_root)
    try:
        top.configure(background=DARK_THEME["win_bg"])
        frame = tk.Frame(top)  # left on the light system default
        frame.pack()
        lb = tk.Listbox(frame)
        lb.configure(background=DARK_THEME["tree_bg"])
        lb.pack()
        with pytest.raises(AssertionError, match="NOT linked to the dark"):
            _assert_window_classic_widgets_dark(top, "synthetic-light-frame")
    finally:
        with contextlib.suppress(tk.TclError):
            top.destroy()


def test_gutter_probe_flags_non_expanding_content(dark_root) -> None:
    """Teeth: non-expanding content over an unpainted Toplevel fails.

    Both safety nets missing at once — the shape that shipped in the
    Prepare Universe and Start Sandbox dialogs.
    """
    top = tk.Toplevel(dark_root)
    try:
        # Toplevel left on the light system default AND content pinned to
        # its natural size via a bare ``pack()``.
        inner = tk.Frame(top, width=120, height=80,
                         background=DARK_THEME["win_bg"])
        inner.pack()
        with pytest.raises(Failed, match="exposes an unpainted strip"):
            _assert_no_light_gutter_after_resize(top, "synthetic-gutter")
    finally:
        with contextlib.suppress(tk.TclError):
            top.destroy()


def test_gutter_probe_flags_grid_without_weight(dark_root) -> None:
    """`sticky='nsew'` without a row/column weight still leaves a gutter."""
    top = tk.Toplevel(dark_root)
    try:
        inner = tk.Frame(top, background=DARK_THEME["win_bg"])
        inner.grid(row=0, column=0, sticky="nsew")
        # Deliberately NOT calling grid_rowconfigure/columnconfigure, and
        # deliberately leaving the Toplevel unpainted.
        with pytest.raises(Failed, match="exposes an unpainted strip"):
            _assert_no_light_gutter_after_resize(top, "synthetic-no-weight")
    finally:
        with contextlib.suppress(tk.TclError):
            top.destroy()


def test_gutter_probe_accepts_expanding_content(dark_root) -> None:
    """Safety net 1: content that fills the window needs no painted bg."""
    top = tk.Toplevel(dark_root)
    try:
        top.grid_rowconfigure(0, weight=1)
        top.grid_columnconfigure(0, weight=1)
        tk.Frame(top, background=DARK_THEME["win_bg"]).grid(
            row=0, column=0, sticky="nsew")
        _assert_no_light_gutter_after_resize(top, "synthetic-ok-grid")
    finally:
        with contextlib.suppress(tk.TclError):
            top.destroy()

    other = tk.Toplevel(dark_root)
    try:
        tk.Frame(other, background=DARK_THEME["win_bg"]).pack(
            expand=True, fill="both")
        _assert_no_light_gutter_after_resize(other, "synthetic-ok-pack")
    finally:
        with contextlib.suppress(tk.TclError):
            other.destroy()


def test_gutter_probe_accepts_painted_toplevel(dark_root) -> None:
    """Safety net 2: a fixed-size form over a painted window is fine.

    This is the shape several real dialogs use — ``BaseModalDialog``
    forces a ``default_geometry`` larger than the form and paints the
    Toplevel, so the leftover strip is themed even though the content
    never expands.
    """
    top = tk.Toplevel(dark_root)
    try:
        top.configure(background=DARK_THEME["win_bg"])
        tk.Frame(top, width=120, height=80,
                 background=DARK_THEME["win_bg"]).pack()
        _assert_no_light_gutter_after_resize(top, "synthetic-painted-toplevel")
    finally:
        with contextlib.suppress(tk.TclError):
            top.destroy()


# ===========================================================================
# Roster meta-test: no dialog may escape the live probe unnoticed.
#
# ``test_theme_invariant`` already requires every Toplevel subclass to be
# *mentioned* in this file. Mentioning is weaker than probing: a dialog can
# have a narrow "its listbox is dark" test and still ship an unthemed
# container. This ties the static discovery to the live roster.
# ===========================================================================


def _discover_dialog_class_names() -> set[str]:
    """Every Toplevel/BaseModalDialog subclass under ``gui/`` (AST walk)."""
    import ast
    from pathlib import Path

    gui_dir = Path(__file__).resolve().parents[3] / "src" / "tradinglab" / "gui"
    found: set[str] = set()
    for py in sorted(gui_dir.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases: list[str] = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(base.attr)
            if any(b in {"BaseModalDialog", "BaseEditorDialog", "Toplevel"}
                   for b in bases):
                found.add(node.name)
    return found


def test_every_dialog_is_probed_or_exempt() -> None:
    """Every dialog class is either in ``_DARK_WINDOWS`` or exempt.

    Adding a new Toplevel without a builder fails here with the class
    name, so the theme probe grows with the codebase instead of quietly
    covering an ever-smaller share of it.
    """
    discovered = _discover_dialog_class_names()
    assert discovered, "AST walk found no dialog classes — discovery broken"
    missing = sorted(
        name for name in discovered
        if name not in _DARK_WINDOWS and name not in _PROBE_EXEMPTIONS
    )
    assert not missing, (
        "Dialog classes not exercised by the live dark-theme probe:\n  - "
        + "\n  - ".join(missing)
        + "\n\nAdd a builder to _DARK_WINDOWS in "
        "tests/unit/gui/test_native_widget_dark_theme.py (preferred — it "
        "gets both the colour and gutter checks for free), or document why "
        "not in _PROBE_EXEMPTIONS."
    )


def test_probe_exemptions_are_real_dialog_classes() -> None:
    """Exemptions must name a class that still exists (no dead entries)."""
    discovered = _discover_dialog_class_names()
    stale = sorted(set(_PROBE_EXEMPTIONS) - discovered)
    assert not stale, (
        "_PROBE_EXEMPTIONS names classes that no longer exist (or were "
        f"renamed): {stale}. Drop the stale entries."
    )


def test_probe_roster_names_real_dialog_classes() -> None:
    """Roster keys must match real class names, so discovery lines up.

    ``SandboxPanel`` is the one deliberate non-dialog: an embedded
    ``ttk.Frame`` that still carries classic Tk widgets worth probing.
    """
    discovered = _discover_dialog_class_names()
    unknown = sorted(set(_DARK_WINDOWS) - discovered - {"SandboxPanel"})
    assert not unknown, (
        f"_DARK_WINDOWS keys that match no dialog class: {unknown}. Use the "
        "exact class name (including any leading underscore) so "
        "test_every_dialog_is_probed_or_exempt can pair them up."
    )


def test_probe_roster_and_exemptions_do_not_overlap() -> None:
    """A dialog is either probed or exempt — never both."""
    overlap = sorted(set(_DARK_WINDOWS) & set(_PROBE_EXEMPTIONS))
    assert not overlap, (
        f"Dialogs listed as both probed and exempt: {overlap}. Remove the "
        "exemption — the builder already covers them."
    )
