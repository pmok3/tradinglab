"""Drawing-controller behavior: coalescing, visible snaps, and menu lifecycle."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from matplotlib.figure import Figure
from matplotlib.transforms import Affine2D

from tradinglab.drawings import DrawingStore, make_hline_drawing
from tradinglab.gui import drawings_app
from tradinglab.gui.dialog_manager import DialogManager
from tradinglab.gui.drawings_app import DrawingsAppMixin


class _App(DrawingsAppMixin):
    def __init__(self):
        self._drawing_redraw_pending = False
        self._last_drawing_color = "#123456"
        self._drawings = Mock(spec=["flush", "list", "get", "add", "clear_symbol"])
        self._status = Mock(spec=["warn", "error"])
        self._render = Mock()
        self._panel_state = {}
        self._canvas = Mock(spec=["draw_idle"])
        self._slot_symbol = Mock(return_value="AMD")
        self._drawing_dialogs = {}
        self._drawings_snap_to_ohlc = True
        self.idle = []
        self.after_idle = Mock(side_effect=self.idle.append)


def test_events_coalesce_but_latest_color_still_updates_while_pending():
    app = _App()
    app._repaint_drawings_only = Mock()
    app._on_drawing_event("ignored", "AMD", None)
    assert not app._drawing_redraw_pending
    assert not app.idle
    app._on_drawing_event("add", "AMD", SimpleNamespace(color="#ffffff"))
    assert app._last_drawing_color == "#123456"
    for event in ("remove", "clear_symbol", "clear_all", "loaded", "replaced"):
        app._on_drawing_event(event, "AMD", None)
    app._on_drawing_event("update", "AMD", SimpleNamespace(color="#ff0000"))
    app._on_drawing_event("update", "AMD", SimpleNamespace(color=""))
    assert app._last_drawing_color == "#ff0000"
    assert app._drawing_redraw_pending
    assert len(app.idle) == 1
    app._drawings.flush.assert_not_called()
    app.idle.pop()()
    assert not app._drawing_redraw_pending
    app._repaint_drawings_only.assert_called_once()
    app._drawings.flush.assert_called_once()
    app._render.assert_not_called()
    app._on_drawing_event("remove", "AMD", None)
    assert len(app.idle) == 1, "coalescer must rearm after the previous paint"


def test_fast_repaint_failure_warns_falls_back_and_still_flushes():
    app = _App()
    app._repaint_drawings_only = Mock(side_effect=RuntimeError("canvas unavailable"))
    app._on_drawing_event("update", "AMD", None)
    app.idle.pop()()
    assert not app._drawing_redraw_pending
    app._render.assert_called_once()
    app._drawings.flush.assert_called_once()
    assert "canvas unavailable" in app._status.warn.call_args.args[0]


def test_idle_scheduler_teardown_does_not_leave_pending_flag_stuck():
    app = _App()
    app.after_idle.side_effect = RuntimeError("Tk destroyed")
    app._repaint_drawings_only = Mock(side_effect=RuntimeError("no canvas"))
    app._on_drawing_event("remove", "AMD", None)
    assert not app._drawing_redraw_pending
    app._repaint_drawings_only.assert_called_once()
    app._render.assert_called_once()


def test_repaint_replaces_only_drawing_artists_and_clears_deleted_lines():
    app = _App()
    ax = Figure().subplots()
    indicator, = ax.plot([0, 1], [20, 21])
    note = ax.text(0, 0, "non-drawing overlay")
    store = DrawingStore(autosave=False)
    drawing = make_hline_drawing("AMD", 12, label="entry")
    store.add(drawing)
    app._drawings = store
    app._panel_state = {"primary": {"price_ax": ax}, "no-price-axis": {}}
    app._redraw_drawings_overlay()
    original = next(line for line in ax.lines if line.get_gid() == f"drawing:{drawing.id}")
    store.update(drawing.id, price=13, label="adjusted")
    app._repaint_drawings_only()
    replacement = next(line for line in ax.lines if line.get_gid() == f"drawing:{drawing.id}")
    assert replacement is not original
    assert list(replacement.get_ydata()) == [13, 13]
    assert [text.get_text() for text in ax.texts] == ["non-drawing overlay", "adjusted"]
    assert indicator in ax.lines and note in ax.texts
    store.remove(drawing.id)
    app._repaint_drawings_only()
    assert list(ax.lines) == [indicator]
    assert list(ax.texts) == [note]
    app._canvas.draw_idle.assert_has_calls([call(), call()])
    app._render.assert_not_called()


def test_overlay_failure_in_one_slot_does_not_suppress_the_other(monkeypatch):
    app = _App()
    primary, compare = object(), object()
    app._panel_state = {"primary": {"price_ax": primary}, "compare": {"price_ax": compare}}
    app._slot_symbol.side_effect = ["AMD", "SPY"]
    first, second = [object()], [object()]
    app._drawings.list.side_effect = [first, second]
    render = Mock(side_effect=[RuntimeError("bad primary axes"), None])
    monkeypatch.setattr(drawings_app, "_render_drawings", render)
    app._redraw_drawings_overlay()
    render.assert_has_calls([call(primary, first), call(compare, second)])


def test_missing_canvas_signals_full_render_fallback():
    app = _App()
    app._canvas = None
    with pytest.raises(RuntimeError, match="no canvas mounted"):
        app._repaint_drawings_only()


def test_visible_snap_candidates_exclude_offscreen_gap_and_missing_prices():
    app = _App()
    app._panel_state["primary"] = {
        "start": 1, "hi": 3,
        "candles": [
            SimpleNamespace(open=1, high=2, low=0, close=1),
            SimpleNamespace(open=10, high="11", low=None, close="bad"),
            SimpleNamespace(open=100, high=101, low=99, close=100, session="gap"),
            SimpleNamespace(open=20, high=21, low=19, close=20),
        ],
    }
    assert app._collect_visible_ohlc_for_slot("primary") == [10, 11]
    assert app._collect_visible_ohlc_for_slot("absent") == []


@pytest.mark.parametrize("enabled,pixel,expected", [
    (True, 1005, 100), (True, 1020, 100.46), (False, 1005, 100.46),
])
def test_snap_uses_display_distance_and_respects_opt_out(enabled, pixel, expected):
    app = _App()
    app._drawings_snap_to_ohlc = enabled
    app._collect_visible_ohlc_for_slot = Mock(
        return_value=[None, "bad", float("nan"), float("inf"), 100])
    ax = SimpleNamespace(get_ylim=lambda: (90, 110), transData=Affine2D().scale(1, 10))
    assert app._compute_snapped_drawing_price(ax, "primary", 100.456, pixel) == expected
    if not enabled:
        app._collect_visible_ohlc_for_slot.assert_not_called()


@pytest.mark.parametrize("pointer,expected", [
    ((120, 230), (20, 170)),
    ((99, 230), None), ((120, 199), None),
    ((400, 230), None), ((120, 350), None),
])
def test_cursor_fallback_flips_y_and_rejects_outside_widget(pointer, expected):
    app = _App()
    widget = SimpleNamespace(
        winfo_rootx=lambda: 100, winfo_rooty=lambda: 200,
        winfo_width=lambda: 300, winfo_height=lambda: 150,
    )
    app._canvas = SimpleNamespace(
        get_tk_widget=lambda: widget, figure=SimpleNamespace(bbox=SimpleNamespace(height=200)))
    app.winfo_pointerxy = lambda: pointer
    assert app._resolve_cursor_px_fallback() == expected


def test_drawing_dialog_reuses_live_singleton_and_reopens_after_close(monkeypatch):
    from tradinglab.gui import drawing_dialog

    app = _App()
    drawing = make_hline_drawing("AMD", 10)
    app._drawings.get.return_value = ("AMD", drawing)
    app._dialog_mgr = DialogManager(app)
    first = Mock(spec=["winfo_exists", "bind", "deiconify", "lift", "focus_set"])
    first.winfo_exists.return_value = True
    second = Mock(spec=["winfo_exists", "bind"])
    factory = Mock(side_effect=[first, second])
    monkeypatch.setattr(drawing_dialog, "DrawingDialog", factory)
    app._open_drawing_dialog(drawing.id)
    app._open_drawing_dialog(drawing.id)
    factory.assert_called_once()
    assert app._drawing_dialogs == {drawing.id: first}
    first.lift.assert_called_once()
    first.focus_set.assert_called_once()
    factory.call_args.kwargs["on_close"]()
    assert not app._drawing_dialogs
    first.winfo_exists.return_value = False
    app._open_drawing_dialog(drawing.id)
    assert factory.call_count == 2
    assert app._drawing_dialogs == {drawing.id: second}


def test_deleted_drawing_cannot_open_a_dialog():
    app = _App()
    app._drawings.get.return_value = None
    app._dialog_mgr = Mock(spec=["open_or_focus"])
    app._open_drawing_dialog("deleted")
    app._dialog_mgr.open_or_focus.assert_not_called()


@pytest.fixture
def canvas_menu(monkeypatch):
    menu = Mock(spec=["add_command", "add_separator", "tk_popup", "grab_release"])
    monkeypatch.setattr(drawings_app.tk, "Menu", Mock(return_value=menu))
    return menu


@pytest.mark.parametrize("answer", [True, False, RuntimeError("no window")])
def test_actual_clear_menu_callback_confirms_before_deleting(canvas_menu, monkeypatch, answer):
    from tkinter import messagebox

    app = _App()
    store = DrawingStore(autosave=False)
    store.add(make_hline_drawing("AMD", 10))
    store.add(make_hline_drawing("SPY", 20))
    app._drawings = store
    ask = Mock(side_effect=answer) if isinstance(answer, Exception) else Mock(return_value=answer)
    monkeypatch.setattr(messagebox, "askyesno", ask)
    app._show_chart_canvas_menu("primary", SimpleNamespace(), 25, 30)
    commands = {c.kwargs["label"]: c.kwargs["command"] for c in canvas_menu.add_command.call_args_list}
    commands["Clear All Drawings on AMD"]()
    assert len(store.list("AMD")) == (0 if answer is True else 1)
    assert len(store.list("SPY")) == 1
    ask.assert_called_once()
    assert "1 drawing on AMD" in ask.call_args.args[1]
    assert ask.call_args.kwargs["default"] == messagebox.NO
    assert ask.call_args.kwargs["parent"] is app
    canvas_menu.grab_release.assert_called_once()


def test_popup_failure_always_releases_menu_grab(canvas_menu):
    app = _App()
    canvas_menu.tk_popup.side_effect = RuntimeError("popup failed")
    with pytest.raises(RuntimeError, match="popup failed"):
        app._show_chart_canvas_menu("primary", SimpleNamespace(), 25, 30)
    canvas_menu.grab_release.assert_called_once()


@pytest.mark.parametrize("xdata,timestamp", [(40, "2026-06-10 10:05"), (39, "?")])
def test_copy_price_time_uses_virtual_slice_offset(canvas_menu, xdata, timestamp):
    from datetime import datetime

    app = _App()
    stamp = datetime(2026, 6, 10, 10, 5)
    app._panel_state = {"primary": {"offset": 40, "candles": [SimpleNamespace(date=stamp)]}}
    app.clipboard_clear = Mock()
    app.clipboard_append = Mock()
    app.update = Mock()
    app._show_chart_canvas_menu("primary", SimpleNamespace(xdata=xdata, ydata=10.126), 0, 0)
    commands = {c.kwargs["label"]: c.kwargs["command"] for c in canvas_menu.add_command.call_args_list}
    commands["Copy Price + Time"]()
    app.clipboard_append.assert_called_once_with(f"10.13 @ {timestamp}")
    app.clipboard_clear.assert_called_once()
    app.update.assert_called_once()


def test_save_errors_use_bounded_reason_without_path_and_tolerate_teardown(monkeypatch):
    app = _App()
    monkeypatch.setattr(drawings_app.time, "monotonic", lambda: 20)
    error = OSError(13, "access denied", r"C:\private\drawings.json")
    app._on_drawing_save_error(error)
    app._status.error.assert_called_once_with("Could not save drawings: access denied")
    assert app._drawing_save_error_last_ts == 20
    assert app._friendly_oserror(OSError("x" * 200)) == "x" * 157 + "..."
    assert app._friendly_oserror(OSError("  ")) == "I/O error"
    app._status.error.side_effect = RuntimeError("status destroyed")
    app._on_drawing_save_error(error)
