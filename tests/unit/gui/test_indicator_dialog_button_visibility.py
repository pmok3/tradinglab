"""Regression tests: button bar visibility in IndicatorDialog.

Root cause: scroll_wrap was packed with fill="both", expand=True BEFORE the
bar frame. In Tkinter's pack layout, an expand=True widget consumes all
remaining height. At dialog heights smaller than the sum of content heights
(banner≈40 + canvas≈320 + bar≈33 ≈ 393px), the last-packed bar frame
received 0px and all four buttons became invisible.

Fix: pack the bar with side="bottom" FIRST, so it always claims its natural
height. The scrollable canvas fills the remaining space.
"""

from __future__ import annotations

import tkinter as tk
from unittest import mock

import pytest

from tradinglab.indicators.config import IndicatorManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def root():
    """Headless Tk root with stub attributes required by IndicatorDialog."""
    try:
        r = tk.Tk()
        r.withdraw()
    except tk.TclError:
        pytest.skip("No display available")
    mgr = IndicatorManager()
    r._indicator_manager = mgr  # type: ignore[attr-defined]
    r._indicator_dialog = None  # type: ignore[attr-defined]
    r._per_indicator_dialogs = {}  # type: ignore[attr-defined]
    r._theme = {"win_bg": "#ffffff"}  # type: ignore[attr-defined]
    r.interval_var = tk.StringVar(r, value="1d")  # type: ignore[attr-defined]
    r._on_menu_save_config = mock.MagicMock()  # type: ignore[attr-defined]
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------

def test_button_bar_packed_as_bottom_anchor(root):
    """Regression: button bar must be packed side='bottom' BEFORE the
    scrollable canvas.

    Previously the bar used the default side='top' and was packed AFTER
    scroll_wrap (which had expand=True). At short dialog heights the pack
    manager gave all remaining space to scroll_wrap and left the bar 0px.

    This test fails against the old code (side='top') and passes after the
    fix (side='bottom').
    """
    from tradinglab.gui.indicator_dialog import IndicatorDialog

    dlg = IndicatorDialog(root)
    root.update_idletasks()

    bar = dlg._save_close_btn.nametowidget(dlg._save_close_btn.winfo_parent())
    info = bar.pack_info()
    assert info["side"] == "bottom", (
        f"Button bar must be packed side='bottom' (got {info['side']!r}). "
        "Packing with side='top' after the canvas (expand=True) causes buttons "
        "to disappear when the dialog height is below ~393px."
    )


def test_button_bar_before_scroll_wrap_in_pack_order(root):
    """Regression: the button bar's pack sequence must come before the
    scrollable canvas in the outer frame's child list.

    ``pack_info()["in"]`` is the manager; child order in winfo_children()
    reflects pack sequence.  The bar must appear earlier (lower index) than
    scroll_wrap so that side='bottom' reservations happen before any
    expand=True widget is processed.
    """
    from tradinglab.gui.indicator_dialog import IndicatorDialog

    dlg = IndicatorDialog(root)
    root.update_idletasks()

    bar = dlg._save_close_btn.nametowidget(dlg._save_close_btn.winfo_parent())
    outer = bar.nametowidget(bar.winfo_parent())
    children = outer.winfo_children()
    names = [w.winfo_class() for w in children]

    bar_idx = children.index(bar)

    # scroll_wrap is the Frame that contains the canvas+scrollbar
    canvas = dlg._rows_canvas
    scroll_wrap = canvas.nametowidget(canvas.winfo_parent())
    scroll_idx = children.index(scroll_wrap)

    assert bar_idx < scroll_idx, (
        f"Button bar (child {bar_idx}) must come before scroll_wrap "
        f"(child {scroll_idx}) in pack order. Children: {names}"
    )


def test_button_bar_reqheight_positive(root):
    """Sanity: even without the window being visible, the button bar must
    have a positive requested height (its natural size based on content).
    """
    from tradinglab.gui.indicator_dialog import IndicatorDialog

    dlg = IndicatorDialog(root)
    root.update_idletasks()

    bar = dlg._save_close_btn.nametowidget(dlg._save_close_btn.winfo_parent())
    assert bar.winfo_reqheight() > 0, (
        f"Button bar reqheight={bar.winfo_reqheight()} — expected > 0."
    )
    assert dlg._add_button.winfo_reqheight() > 0
    assert dlg._save_close_btn.winfo_reqheight() > 0


def test_buttons_visible_at_tight_geometry(root):
    """Regression: all buttons must have positive allocated height when the
    dialog is shown at a height below the natural sum of content heights.

    Minsize is neutralised so the dialog can be forced to 880×370 (below the
    ~393px natural sum). With the old pack order the bar got 0px; with the
    fix (side='bottom' first) the bar always claims its reqheight.

    Requires a display; skipped automatically when Tk cannot map windows.
    """
    import sys

    from tradinglab.gui.indicator_dialog import IndicatorDialog

    # Neutralise the WM minsize so geometry("880x370") actually sticks.
    with mock.patch.object(IndicatorDialog, "minsize"):
        dlg = IndicatorDialog(root)

    # Force a height well below banner + canvas + bar natural sum.
    dlg.geometry("880x370")
    try:
        root.deiconify()
        dlg.deiconify()
        root.update()
        dlg.update()
        root.update_idletasks()
    except tk.TclError:
        pytest.skip("Tk window mapping not available on this runner")

    bar = dlg._save_close_btn.nametowidget(dlg._save_close_btn.winfo_parent())
    bar_h = bar.winfo_height()
    bar_req = bar.winfo_reqheight()

    assert bar_h >= bar_req, (
        f"Button bar allocated height={bar_h} is less than its requested "
        f"height={bar_req}. Buttons would be clipped or invisible."
    )
    assert bar_h > 0, f"Button bar height={bar_h}; buttons are invisible."

    for btn_name, btn in [
        ("Add Indicator", dlg._add_button),
        ("Save and Close", dlg._save_close_btn),
    ]:
        assert btn.winfo_height() > 0, (
            f"Button {btn_name!r} has height={btn.winfo_height()}; invisible."
        )
