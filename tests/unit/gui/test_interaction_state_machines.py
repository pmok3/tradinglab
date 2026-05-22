"""Headless integration tests for ``gui.interaction.InteractionMixin``.

The interaction mixin is a 1,200+ line state machine that owns pan,
zoom, hover, crosshair, click-to-type, drilldown-on-dblclick, and the
drawing drag-to-move gesture. It has been the source of multiple
real-world bugs:

* Alt+H typing 'H' into the chart (modifier-detection drift)
* Anchor-pick clicks bleeding into pan_begin
* Drill-down dblclick re-arming pan
* Drawing drag committing stale prices on release

These are NOT GUI tests — no real Tk, no real matplotlib backend.
Instead we mount the *method* on a minimal harness object exposing
only the attributes/collaborators the method reads. This is the same
pattern as ``tests/unit/test_pick_event_throttle.py`` which has been
in the suite since the pick-event-throttle audit.

The harness gives us coverage of behaviors that are hard to reach
from smoke tests because they require precise event sequencing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import numpy as np
import pytest

from tradinglab.drawings import DrawingStore, make_hline_drawing
from tradinglab.gui.interaction import InteractionMixin
from tradinglab.models import Candle


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class _FakeBBox:
    width: float = 800.0
    height: float = 600.0
    x0: float = 0.0
    y0: float = 0.0


@dataclass(eq=False)
class _FakeFigure:
    """Bare-bones figure stand-in for tests that touch ``_figure``.

    ``_pan_drag`` references ``self._figure.bbox`` to scope blits;
    ``_check_event_glyph_hit`` and ``_show_hover`` read
    ``_figure.bbox.width/height`` for the figure-fraction flip.
    """
    bbox: _FakeBBox = field(default_factory=lambda: _FakeBBox(width=800.0,
                                                              height=600.0))
    axes: list = field(default_factory=list)
    dpi: float = 96.0


@dataclass(eq=False)
class _FakeTransform:
    """Identity-ish transform for ``_drawing_drag_motion``. Maps a pixel
    y-coordinate straight through as the data y-coordinate, so a test
    can simply pass ``event.y = 150.25`` and assert the snapped price."""

    def transform(self, pt):
        return (pt[0], pt[1])


@dataclass(eq=False)
class _FakeTransData:
    """Identity transform with both ``.transform`` (forward) and
    ``.inverted()`` so the same fake works for ``pick_drawing``
    (data → display) and ``_drawing_drag_motion`` (display → data)."""

    def transform(self, pt):
        return (pt[0], pt[1])

    def inverted(self) -> _FakeTransform:
        return _FakeTransform()


@dataclass(eq=False)
class _FakeAxes:
    id_: int = 1
    bbox: _FakeBBox = field(default_factory=_FakeBBox)
    transData: _FakeTransData = field(default_factory=_FakeTransData)
    _xlim: tuple = (0.0, 100.0)
    _ylim: tuple = (90.0, 110.0)
    patches: list = field(default_factory=list)

    def get_xlim(self):
        return self._xlim

    def set_xlim(self, lo, hi):
        self._xlim = (float(lo), float(hi))

    def get_ylim(self):
        return self._ylim

    def add_patch(self, patch):
        self.patches.append(patch)
        try:
            patch.axes = self
        except Exception:
            pass
        return patch


@dataclass(eq=False)
class _FakeEvent:
    inaxes: Optional[_FakeAxes] = None
    x: Optional[float] = None
    y: Optional[float] = None
    xdata: Optional[float] = None
    ydata: Optional[float] = None
    button: int = 1
    dblclick: bool = False
    key: str = ""
    keysym: str = ""
    char: str = ""
    guiEvent: Any = None


class _FakeTkWidget:
    def __init__(self) -> None:
        self.cursor_history: list[str] = []

    def configure(self, **kwargs) -> None:
        if "cursor" in kwargs:
            self.cursor_history.append(kwargs["cursor"])


class _FakeCanvas:
    def __init__(self) -> None:
        self._widget = _FakeTkWidget()
        self.draw_idle_calls = 0
        self.draw_calls = 0
        self.restore_region_calls = 0
        self.blit_calls = 0
        self.copy_from_bbox_calls = 0

    def get_tk_widget(self) -> _FakeTkWidget:
        return self._widget

    def draw_idle(self) -> None:
        self.draw_idle_calls += 1

    def draw(self) -> None:
        self.draw_calls += 1

    def restore_region(self, bg) -> None:
        self.restore_region_calls += 1

    def blit(self, bbox) -> None:
        self.blit_calls += 1

    def copy_from_bbox(self, bbox):
        self.copy_from_bbox_calls += 1
        return object()


class _FakeVar:
    def __init__(self, initial: str) -> None:
        self._v = initial
        self.set_history: list[str] = []

    def get(self) -> str:
        return self._v

    def set(self, v: str) -> None:
        self._v = v
        self.set_history.append(v)


def _make_intraday_candles(n: int, start=datetime(2024, 3, 4, 9, 30),
                           base=100.0):
    """Generate ``n`` 5m intraday candles starting at ``start``."""
    from datetime import timedelta
    out = []
    for i in range(n):
        out.append(Candle(
            date=start + timedelta(minutes=5 * i),
            open=base + i * 0.1,
            high=base + i * 0.1 + 0.5,
            low=base + i * 0.1 - 0.5,
            close=base + i * 0.1 + 0.1,
            volume=1000,
            session="regular",
        ))
    return out


def _make_daily_candles(n: int, start=datetime(2024, 1, 2)):
    from datetime import timedelta
    out = []
    for i in range(n):
        out.append(Candle(
            date=start + timedelta(days=i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1_000_000,
            session="regular",
        ))
    return out


class _InteractionHarness:
    """Minimal stand-in exposing only the attributes the methods read.

    Methods from :class:`InteractionMixin` are pasted onto this class
    at module import time (see the bottom of this file), so calling
    ``harness._on_key_press(event)`` runs the real production code
    against fake collaborators.
    """

    def __init__(self, *,
                 ticker: str = "AAPL",
                 interval: str = "5m",
                 drawings: Optional[DrawingStore] = None,
                 candles=None) -> None:
        self._canvas = _FakeCanvas()
        self._drawings = drawings if drawings is not None else \
            DrawingStore(autosave=False)
        self.ticker_var = _FakeVar(ticker)
        self.compare_ticker_var = _FakeVar("")
        self.interval_var = _FakeVar(interval)

        self._price_ax = _FakeAxes(id_=1)
        self._vol_ax = _FakeAxes(id_=2)
        self._panel_state = {
            "primary": {
                "price_ax": self._price_ax,
                "vol_ax": self._vol_ax,
                "candles": candles or [],
            }
        }
        self._ax_candle_map = {}
        if candles:
            self._ax_candle_map[self._price_ax] = (candles, None, 0)

        self._typing_target = None
        self._typing_buffer = ""
        self._typing_preview_artists = {}
        self._last_clicked_slot = None
        self._theme = {"text": "#000000"}

        self._pan_state = None
        self._pan_animated = []
        self._pan_bg = None
        self._pan_redraw_job = None
        self._pan_anim_fingerprint = None
        self._blit_bg = None
        self._zoom_state = None
        self._drag_press = None
        self._drawing_drag_state = None
        self._anchor_pick_state = None
        self._pick_cache = None
        self._preserve_xlim_on_render = False
        self._slide_xlim_to_right_edge = False
        self._drawing_hover_cursor_active = False
        self._display_tz = ""
        self._scroll_zoom_invert = False
        self._figure = _FakeFigure(axes=[self._price_ax, self._vol_ax])
        self._ax_price = self._price_ax

        # Tracking
        self.schedule_reload_calls: list[int] = []
        self.zoom_drilldown_calls: list[Any] = []
        self.hide_overlays_calls = 0
        self.autoscale_calls = 0
        self.ensure_rendered_calls: list[str] = []
        self.rebind_animated_calls = 0
        self.pan_setup_blit_calls = 0
        self.pan_redraw_tick_calls = 0
        self.track_after_calls: list[tuple] = []
        self.drawing_context_menu_calls: list[tuple] = []
        self.canvas_menu_calls: list[tuple] = []
        self.open_drawing_dialog_calls: list[str] = []
        self._drilldown_return = True

    def _slot_symbol(self, slot_key):
        if slot_key == "primary":
            return self.ticker_var.get()
        if slot_key == "compare":
            return self.compare_ticker_var.get() or None
        return None

    def _zoom_5m_for_date(self, day):
        self.zoom_drilldown_calls.append(day)
        return self._drilldown_return

    def _schedule_reload(self, delay_ms: int = 0) -> None:
        self.schedule_reload_calls.append(delay_ms)

    def _hide_overlays(self) -> None:
        self.hide_overlays_calls += 1

    def _autoscale_y_to_visible(self) -> None:
        self.autoscale_calls += 1

    # Drawing drag commit calls this to pick up the slot's snap rules.
    def _compute_snapped_drawing_price(self, ax, slot_key, y_data, y_px):
        return round(float(y_data), 2)

    # ``_on_button_press`` dispatches to this when a B1 dblclick lands on
    # a horizontal line so the per-line edit dialog opens before the
    # drilldown gate. Default no-op so tests that don't exercise drawing
    # dblclick keep the original control flow.
    def _maybe_handle_drawing_dblclick(self, event) -> bool:
        return False

    def _maybe_handle_b3_click_menu(self, event) -> None:
        return None

    def _handle_anchor_pick_click(self, event) -> bool:
        return False

    # ---- pan/zoom collaborators (stubbed; methods walk into them) -----
    def _pan_setup_blit(self) -> None:
        self.pan_setup_blit_calls += 1

    def _pan_redraw_tick(self) -> None:
        self.pan_redraw_tick_calls += 1
        self._pan_redraw_job = None

    def _pan_rebind_animated_after_slice(self) -> None:
        self.rebind_animated_calls += 1

    def _ensure_rendered_for_view(self, slot_key) -> None:
        self.ensure_rendered_calls.append(slot_key)

    def _track_after(self, ms, cb):
        self.track_after_calls.append((ms, cb))
        return ("after_job", ms)

    # ---- right-click context menu sinks ------------------------------
    def _show_drawing_context_menu(self, drawing_id, x_root, y_root) -> None:
        self.drawing_context_menu_calls.append((drawing_id, x_root, y_root))

    def _show_chart_canvas_menu(self, slot_key, event, x_root, y_root) -> None:
        self.canvas_menu_calls.append((slot_key, event, x_root, y_root))

    def _open_drawing_dialog(self, drawing_id) -> None:
        self.open_drawing_dialog_calls.append(drawing_id)


# Borrow the real production methods onto the harness. This is the
# identical pattern used in tests/unit/test_pick_event_throttle.py.
for _name in (
    "_on_key_press",
    "_on_button_press",
    "_on_button_release",
    "_pan_begin",
    "_pan_drag",
    "_pan_end",
    "_zoom_begin",
    "_zoom_drag",
    "_zoom_end",
    "_on_scroll_zoom",
    "_maybe_handle_b3_click_menu",
    "_maybe_handle_drawing_dblclick",
    "_update_drawing_hover_cursor",
    "_reset_drawing_hover_cursor",
    "_begin_click_to_type",
    "_commit_click_to_type",
    "_cancel_click_to_type",
    "_refresh_typing_preview",
    "_maybe_handle_dblclick_drilldown",
    "_maybe_begin_drawing_drag",
    "_drawing_drag_motion",
    "_maybe_end_drawing_drag",
    "_pick_drawing_at_event",
    "_format_price_for_label",
    "_format_time_for_label",
):
    setattr(_InteractionHarness, _name, getattr(InteractionMixin, _name))

# Class attributes (tunable thresholds) read by ``_on_scroll_zoom``.
# These live on ``InteractionMixin`` itself, not on instances, so the
# borrow-by-setattr pattern misses them unless we copy explicitly.
for _attr in (
    "_SCROLL_ZOOM_PER_STEP",
    "_SCROLL_ZOOM_MIN_BARS",
    "_SCROLL_ZOOM_STEP_CLAMP",
):
    setattr(_InteractionHarness, _attr, getattr(InteractionMixin, _attr))


# ---------------------------------------------------------------------------
# 1. Key-press handler — typing-buffer state machine + modifier filtering
# ---------------------------------------------------------------------------


class TestKeyPressTypingBuffer:
    """The click-to-type buffer must accept letters, ignore digits and
    modifiers, and respect special keys (BackSpace / Return / Escape)."""

    def test_bare_letter_starts_typing_on_primary(self):
        h = _InteractionHarness()
        h._on_key_press(_FakeEvent(keysym="a", char="a"))
        assert h._typing_target == "primary"
        assert h._typing_buffer == "A"

    def test_letter_uppercase_normalisation(self):
        h = _InteractionHarness()
        for c in "msft":
            h._on_key_press(_FakeEvent(keysym=c, char=c))
        assert h._typing_buffer == "MSFT"

    def test_digit_ignored_when_no_active_buffer(self):
        """Digits must NOT start a phantom typing session — `_` only
        starts on letters/dot/dash (BRK.B class shares)."""
        h = _InteractionHarness()
        h._on_key_press(_FakeEvent(keysym="1", char="1"))
        assert h._typing_target is None
        assert h._typing_buffer == ""

    def test_dot_and_dash_allowed_for_class_shares(self):
        h = _InteractionHarness()
        for c in "brk.b":
            h._on_key_press(_FakeEvent(keysym=c, char=c))
        assert h._typing_buffer == "BRK.B"

    def test_backspace_pops_last_char(self):
        h = _InteractionHarness()
        for c in "abc":
            h._on_key_press(_FakeEvent(keysym=c, char=c))
        h._on_key_press(_FakeEvent(keysym="BackSpace", char=""))
        assert h._typing_buffer == "AB"

    def test_return_commits_and_clears(self):
        h = _InteractionHarness()
        for c in "tsla":
            h._on_key_press(_FakeEvent(keysym=c, char=c))
        h._on_key_press(_FakeEvent(keysym="Return", char="\r"))
        # Committed → ticker_var got set, buffer is cleared.
        assert h.ticker_var.get() == "TSLA"
        assert h._typing_target is None
        assert h._typing_buffer == ""
        assert h.schedule_reload_calls == [0]

    def test_escape_cancels_without_setting_ticker(self):
        h = _InteractionHarness()
        h._on_key_press(_FakeEvent(keysym="x", char="x"))
        h._on_key_press(_FakeEvent(keysym="Escape", char=""))
        assert h.ticker_var.get() == "AAPL"  # unchanged
        assert h._typing_target is None
        assert h._typing_buffer == ""
        # _cancel_click_to_type does NOT schedule a reload.
        assert h.schedule_reload_calls == []

    def test_space_keysym_is_ignored(self):
        """Space is reserved for watchlist cycling (bind_all at app
        level) — the canvas key handler must NOT consume it."""
        h = _InteractionHarness()
        h._on_key_press(_FakeEvent(keysym="space", char=" "))
        assert h._typing_target is None
        assert h._typing_buffer == ""

    @pytest.mark.parametrize("keysym", [
        "Alt_L", "Alt_R", "Control_L", "Control_R", "Shift_L", "Shift_R",
        "Meta_L", "Meta_R", "Super_L", "Super_R", "Caps_Lock", "Num_Lock",
    ])
    def test_bare_modifier_keys_filtered(self, keysym):
        """Pressing only Alt / Ctrl / Shift must not start a buffer.

        Regression: prior to the fix the bare-modifier press could
        leak a state change that interacted badly with Alt+H detection."""
        h = _InteractionHarness()
        h._on_key_press(_FakeEvent(keysym=keysym, char=""))
        assert h._typing_target is None
        assert h._typing_buffer == ""

    @pytest.mark.parametrize("mpl_key", [
        "alt+h", "ctrl+s", "shift+a", "ctrl+alt+x",
        "alt", "ctrl", "shift", "control", "meta",
    ])
    def test_mpl_modifier_combo_filtered(self, mpl_key):
        """When matplotlib's ``event.key`` reports a modifier combo we
        skip — the bound Tk shortcut already fired."""
        h = _InteractionHarness()
        h._on_key_press(_FakeEvent(keysym="h", char="h", key=mpl_key))
        assert h._typing_target is None

    def test_typing_with_last_clicked_compare_targets_compare(self):
        """When the user has clicked the compare panel last, typing
        starts on compare instead of primary."""
        h = _InteractionHarness()
        h._last_clicked_slot = "compare"
        for c in "qqq":
            h._on_key_press(_FakeEvent(keysym=c, char=c))
        h._on_key_press(_FakeEvent(keysym="Return", char="\r"))
        assert h.compare_ticker_var.get() == "QQQ"
        assert h.ticker_var.get() == "AAPL"  # untouched


# ---------------------------------------------------------------------------
# 2. Click-to-type lifecycle (no key-press path)
# ---------------------------------------------------------------------------


class TestClickToType:
    def test_begin_resolves_slot_from_axes(self):
        h = _InteractionHarness()
        h._begin_click_to_type(h._price_ax)
        assert h._typing_target == "primary"
        assert h._last_clicked_slot == "primary"
        assert h._typing_buffer == ""

    def test_begin_on_unknown_axes_falls_back_to_primary(self):
        h = _InteractionHarness()
        h._begin_click_to_type(_FakeAxes(id_=999))
        assert h._typing_target == "primary"

    def test_commit_with_empty_buffer_is_noop(self):
        h = _InteractionHarness()
        h._begin_click_to_type(h._price_ax)
        h._commit_click_to_type()
        # Empty commit clears state but does NOT set the ticker.
        assert h.ticker_var.get() == "AAPL"
        assert h._typing_target is None
        assert h.schedule_reload_calls == []

    def test_commit_strips_whitespace_and_uppercases(self):
        h = _InteractionHarness()
        h._begin_click_to_type(h._price_ax)
        h._typing_buffer = "  msft  "
        h._commit_click_to_type()
        assert h.ticker_var.get() == "MSFT"
        assert h.schedule_reload_calls == [0]

    def test_cancel_clears_without_committing(self):
        h = _InteractionHarness()
        h._begin_click_to_type(h._price_ax)
        h._typing_buffer = "ABC"
        h._cancel_click_to_type()
        assert h._typing_target is None
        assert h._typing_buffer == ""
        assert h.ticker_var.get() == "AAPL"


# ---------------------------------------------------------------------------
# 3. Double-click drilldown gate
# ---------------------------------------------------------------------------


class TestDblClickDrilldownGate:
    """1d dblclick on a real candle drills into 5m; everything else
    must early-return so a stray dblclick doesn't navigate."""

    def _setup(self, interval="1d", candles=None):
        candles = candles if candles is not None else _make_daily_candles(20)
        h = _InteractionHarness(interval=interval, candles=candles)
        return h, candles

    def test_drilldown_fires_for_1d_click_on_candle(self):
        h, candles = self._setup()
        ev = _FakeEvent(inaxes=h._price_ax, xdata=5.0, x=400.0, y=300.0)
        assert h._maybe_handle_dblclick_drilldown(ev) is True
        assert h.zoom_drilldown_calls == [candles[5].date.date()]

    def test_drilldown_blocked_on_5m_interval(self):
        h, _ = self._setup(interval="5m")
        ev = _FakeEvent(inaxes=h._price_ax, xdata=5.0, x=400.0, y=300.0)
        assert h._maybe_handle_dblclick_drilldown(ev) is False
        assert h.zoom_drilldown_calls == []

    def test_drilldown_blocked_for_click_far_before_first_bar(self):
        """A click that maps to a negative index (e.g. click far to the
        left of bar 0 after a pan) must early-return False."""
        h, _ = self._setup()
        ev = _FakeEvent(inaxes=h._price_ax, xdata=-3.0, x=10.0, y=300.0)
        assert h._maybe_handle_dblclick_drilldown(ev) is False

    def test_drilldown_blocked_with_no_inaxes(self):
        h, _ = self._setup()
        ev = _FakeEvent(inaxes=None, xdata=5.0, x=400.0, y=300.0)
        assert h._maybe_handle_dblclick_drilldown(ev) is False

    def test_drilldown_blocked_on_axes_with_no_candles(self):
        h, _ = self._setup(candles=[])
        ev = _FakeEvent(inaxes=h._price_ax, xdata=5.0, x=400.0, y=300.0)
        assert h._maybe_handle_dblclick_drilldown(ev) is False

    def test_drilldown_index_clamped_to_candle_range(self):
        """A click way past the last candle must return False, not
        index-out-of-range."""
        h, _ = self._setup()
        ev = _FakeEvent(inaxes=h._price_ax, xdata=100.0, x=700.0, y=300.0)
        assert h._maybe_handle_dblclick_drilldown(ev) is False

    def test_drilldown_skips_gap_candles(self):
        """Gap (synthetic) candles should not be drillable."""
        candles = _make_daily_candles(10)
        # Mark candle[3] as a gap.
        candles[3] = Candle.gap(candles[3].date)
        h, _ = self._setup(candles=candles)
        ev = _FakeEvent(inaxes=h._price_ax, xdata=3.0, x=300.0, y=300.0)
        assert h._maybe_handle_dblclick_drilldown(ev) is False


# ---------------------------------------------------------------------------
# 4. Drawing drag-to-move state machine
# ---------------------------------------------------------------------------


class TestDrawingDragStateMachine:
    """B1 press on a hline → motion → release moves the line. State
    must be initialised, mutated, and cleared correctly across the
    full press/motion/release sequence."""

    def _setup_with_line(self, price=100.0):
        store = DrawingStore(autosave=False)
        d = store.add(make_hline_drawing(ticker="AAPL", price=price))
        h = _InteractionHarness(drawings=store)
        return h, store, d

    def test_begin_misses_when_no_line_under_cursor(self):
        h, _, _ = self._setup_with_line()
        # Click on a clearly non-line location (y far from price=100).
        # Since our identity transform maps event.y → data y directly,
        # event.y=0 means y_data=0, line at price 100 → 100 px away,
        # >> 5 px tolerance, no hit.
        ev = _FakeEvent(inaxes=h._price_ax, x=400.0, y=0.0)
        assert h._maybe_begin_drawing_drag(ev) is False
        assert h._drawing_drag_state is None

    def test_begin_initialises_state_on_line_hit(self):
        h, _, line = self._setup_with_line(price=100.0)
        # Click directly on the line (y_data = 100 via identity).
        ev = _FakeEvent(inaxes=h._price_ax, x=400.0, y=100.0)
        assert h._maybe_begin_drawing_drag(ev) is True
        st = h._drawing_drag_state
        assert st is not None
        assert st["drawing"] is line
        assert st["ax"] is h._price_ax
        assert st["slot_key"] == "primary"
        assert st["start_price"] == 100.0

    def test_begin_sets_drag_cursor(self):
        h, _, _ = self._setup_with_line()
        ev = _FakeEvent(inaxes=h._price_ax, x=400.0, y=100.0)
        h._maybe_begin_drawing_drag(ev)
        assert "sb_v_double_arrow" in h._canvas.get_tk_widget().cursor_history

    def test_motion_updates_drawing_price_live(self):
        h, store, line = self._setup_with_line(price=100.0)
        h._maybe_begin_drawing_drag(
            _FakeEvent(inaxes=h._price_ax, x=400.0, y=100.0))
        # Move to y=105.37 — identity transform → data y=105.37 → snap to $0.01.
        h._drawing_drag_motion(
            _FakeEvent(inaxes=h._price_ax, x=400.0, y=105.37))
        assert store.list("AAPL")[0].price == pytest.approx(105.37)

    def test_motion_with_no_active_drag_is_noop(self):
        h, store, _ = self._setup_with_line(price=100.0)
        h._drawing_drag_motion(_FakeEvent(inaxes=h._price_ax, x=10, y=200))
        assert store.list("AAPL")[0].price == 100.0

    def test_release_commits_snapped_price_and_clears_state(self):
        h, store, line = self._setup_with_line(price=100.0)
        h._maybe_begin_drawing_drag(
            _FakeEvent(inaxes=h._price_ax, x=400.0, y=100.0))
        # Motion preview then release.
        h._drawing_drag_motion(
            _FakeEvent(inaxes=h._price_ax, x=400.0, y=110.789))
        assert h._maybe_end_drawing_drag(
            _FakeEvent(inaxes=h._price_ax, x=400.0, y=110.789)) is True
        # State cleared.
        assert h._drawing_drag_state is None
        # Snap to $0.01 (final commit uses _compute_snapped_drawing_price).
        assert store.list("AAPL")[0].price == pytest.approx(110.79)

    def test_release_with_no_active_drag_returns_false(self):
        h, _, _ = self._setup_with_line()
        ev = _FakeEvent(inaxes=h._price_ax, x=400.0, y=100.0)
        assert h._maybe_end_drawing_drag(ev) is False

    def test_release_restores_default_cursor(self):
        h, _, _ = self._setup_with_line(price=100.0)
        h._maybe_begin_drawing_drag(
            _FakeEvent(inaxes=h._price_ax, x=400.0, y=100.0))
        h._maybe_end_drawing_drag(
            _FakeEvent(inaxes=h._price_ax, x=400.0, y=100.5))
        # Last cursor configured must be the default (empty string).
        assert h._canvas.get_tk_widget().cursor_history[-1] == ""

    def test_release_with_no_event_y_still_clears_state(self):
        """Defensive: if matplotlib gives us a release with y=None,
        we still must clear the drag state to avoid a stuck drag."""
        h, _, _ = self._setup_with_line(price=100.0)
        h._maybe_begin_drawing_drag(
            _FakeEvent(inaxes=h._price_ax, x=400.0, y=100.0))
        # Force y=None on release.
        ev = _FakeEvent(inaxes=h._price_ax, x=400.0, y=None)
        assert h._maybe_end_drawing_drag(ev) is True
        assert h._drawing_drag_state is None


# ---------------------------------------------------------------------------
# 5. Button-press dispatch routing
# ---------------------------------------------------------------------------


class TestButtonPressRouting:
    """``_on_button_press`` is the dispatcher — its routing decisions
    (pan vs. drilldown vs. drawing drag vs. anchor pick) are exactly
    the surface that hosted the recent regressions."""

    def test_click_outside_axes_short_circuits(self):
        """No inaxes ⇒ no state change, no pan_begin call."""
        h = _InteractionHarness()
        ev = _FakeEvent(inaxes=None, x=400.0, y=300.0, button=1)
        h._on_button_press(ev)
        assert h._pan_state is None

    def test_mouse_button_4_ignored(self):
        """Only buttons 1 and 3 trigger anything."""
        h = _InteractionHarness()
        ev = _FakeEvent(inaxes=h._price_ax, x=400.0, y=300.0, button=4)
        h._on_button_press(ev)
        assert h._pan_state is None
        assert h._zoom_state is None

    def test_b1_press_arms_pan_state(self):
        h = _InteractionHarness(candles=_make_intraday_candles(20))
        ev = _FakeEvent(inaxes=h._price_ax, x=400.0, y=300.0, button=1)
        h._on_button_press(ev)
        assert h._pan_state is not None
        assert h._pan_state["ax"] is h._price_ax

    def test_drilldown_dblclick_blocks_pan(self):
        """A 1d dblclick that drills down must NOT leave pan armed —
        regression for ``_pan_state`` lingering after the second click
        of a dblclick teleported to 5m."""
        candles = _make_daily_candles(10)
        h = _InteractionHarness(interval="1d", candles=candles)
        ev = _FakeEvent(inaxes=h._price_ax, xdata=5.0, x=400.0, y=300.0,
                        button=1, dblclick=True)
        h._on_button_press(ev)
        assert h._pan_state is None
        assert h._drag_press is None
        assert h.zoom_drilldown_calls == [candles[5].date.date()]

    def test_drawing_dblclick_consumed_before_drilldown(self):
        """B1 dblclick on a horizontal line opens the per-line edit
        dialog and consumes the event (gated before drilldown)."""
        store = DrawingStore(autosave=False)
        store.add(make_hline_drawing(ticker="AAPL", price=100.0))
        h = _InteractionHarness(interval="1d",
                                candles=_make_daily_candles(20),
                                drawings=store)
        # Record dblclick handler invocation.
        h._dblclick_dialog_calls = []

        def _stub(event):
            h._dblclick_dialog_calls.append(event)
            return True
        h._maybe_handle_drawing_dblclick = _stub
        # Click directly on the line (y=100).
        ev = _FakeEvent(inaxes=h._price_ax, xdata=5.0, x=400.0, y=100.0,
                        button=1, dblclick=True)
        h._on_button_press(ev)
        assert len(h._dblclick_dialog_calls) == 1
        # Drilldown was NOT called.
        assert h.zoom_drilldown_calls == []


# ---------------------------------------------------------------------------
# 6. Format helpers
# ---------------------------------------------------------------------------


class TestFormatHelpers:
    def test_format_price_uses_axis_formatter_when_available(self):
        from matplotlib.ticker import FuncFormatter

        @dataclass
        class _RealAxes:
            class _YAxis:
                def __init__(self):
                    self._fmt = FuncFormatter(lambda x, _: f"${x:.3f}")
                def get_major_formatter(self):
                    return self._fmt
            yaxis: Any = field(default_factory=_YAxis)

        h = _InteractionHarness()
        ax = _RealAxes()
        out = h._format_price_for_label(ax, 123.456)
        assert "123.4" in out or "$" in out

    def test_format_price_falls_back_on_formatter_failure(self):
        @dataclass
        class _BrokenAxes:
            class _YAxis:
                def get_major_formatter(self):
                    raise RuntimeError("boom")
            yaxis: Any = field(default_factory=_YAxis)

        h = _InteractionHarness()
        assert h._format_price_for_label(_BrokenAxes(), 99.5) == "99.50"

    def test_format_time_intraday(self):
        candles = _make_intraday_candles(10)
        h = _InteractionHarness(candles=candles)
        out = h._format_time_for_label(h._price_ax, 3.0)
        assert "2024-03-04" in out
        assert ":" in out  # has HH:MM component

    def test_format_time_out_of_range_returns_empty(self):
        candles = _make_intraday_candles(5)
        h = _InteractionHarness(candles=candles)
        assert h._format_time_for_label(h._price_ax, 99.0) == ""
        assert h._format_time_for_label(h._price_ax, -1.0) == ""

    def test_format_time_daily_omits_hhmm(self):
        candles = _make_daily_candles(10)
        h = _InteractionHarness(interval="1d", candles=candles)
        out = h._format_time_for_label(h._price_ax, 3.0)
        assert out.count(":") == 0  # YYYY-MM-DD format only
        assert "2024-01-05" == out


# ---------------------------------------------------------------------------
# 7. End-to-end-ish: full click-to-type via key handler
# ---------------------------------------------------------------------------


class TestEndToEndTypingFlow:
    """Click on chart, type a ticker, press Return — exercises the
    chain ``_begin_click_to_type → _on_key_press × N → _commit``."""

    def test_full_type_msft_via_key_press_path(self):
        h = _InteractionHarness()
        h._begin_click_to_type(h._price_ax)
        for c in "msft":
            h._on_key_press(_FakeEvent(keysym=c, char=c))
        h._on_key_press(_FakeEvent(keysym="Return", char="\r"))
        assert h.ticker_var.get() == "MSFT"
        assert h.schedule_reload_calls == [0]

    def test_backspace_then_retype(self):
        h = _InteractionHarness()
        h._begin_click_to_type(h._price_ax)
        for c in "tsla":
            h._on_key_press(_FakeEvent(keysym=c, char=c))
        for _ in range(2):
            h._on_key_press(_FakeEvent(keysym="BackSpace", char=""))
        for c in "ka":
            h._on_key_press(_FakeEvent(keysym=c, char=c))
        h._on_key_press(_FakeEvent(keysym="Return", char="\r"))
        assert h.ticker_var.get() == "TSKA"

    def test_escape_then_new_type(self):
        h = _InteractionHarness()
        h._begin_click_to_type(h._price_ax)
        for c in "abc":
            h._on_key_press(_FakeEvent(keysym=c, char=c))
        h._on_key_press(_FakeEvent(keysym="Escape", char=""))
        # Now type again from scratch.
        h._begin_click_to_type(h._price_ax)
        for c in "xyz":
            h._on_key_press(_FakeEvent(keysym=c, char=c))
        h._on_key_press(_FakeEvent(keysym="Return", char="\r"))
        assert h.ticker_var.get() == "XYZ"


# ===========================================================================
# Phase B add-on: pan / scroll-zoom / rubber-band / B3 menu / hover cursor
# ===========================================================================


class TestPanStateMachine:
    """``_pan_begin`` / ``_pan_drag`` / ``_pan_end`` are the press-drag-
    release trio for chart panning. They're exercised here against a
    fake canvas/figure, with ``_pan_setup_blit`` stubbed so the test
    isn't tied to matplotlib's animated-artist machinery."""

    def test_pan_begin_with_no_inaxes_is_noop(self):
        h = _InteractionHarness()
        h._pan_begin(_FakeEvent(inaxes=None, x=100.0))
        assert h._pan_state is None
        assert h.hide_overlays_calls == 0

    def test_pan_begin_arms_state_and_hides_overlays(self):
        h = _InteractionHarness()
        ev = _FakeEvent(inaxes=h._price_ax, x=250.0)
        h._pan_begin(ev)
        assert h._pan_state is not None
        assert h._pan_state["press_x"] == 250.0
        assert h._pan_state["ax"] is h._price_ax
        assert h._pan_state["press_xlim"] == h._price_ax.get_xlim()
        assert h._pan_state["width_px"] == h._price_ax.bbox.width
        assert h.hide_overlays_calls == 1

    def test_pan_begin_swallows_axis_failure(self):
        """Backends occasionally raise from ``get_xlim`` mid-redraw —
        the early-return must protect against state corruption."""
        h = _InteractionHarness()

        class _Boom(_FakeAxes):
            def get_xlim(self):
                raise RuntimeError("backend transition")

        ev = _FakeEvent(inaxes=_Boom(), x=50.0)
        h._pan_begin(ev)
        assert h._pan_state is None

    def test_pan_drag_without_active_state_is_noop(self):
        h = _InteractionHarness()
        h._pan_drag(_FakeEvent(x=100.0))
        assert h._canvas.draw_idle_calls == 0
        assert h.pan_setup_blit_calls == 0

    def test_pan_drag_falls_back_to_redraw_when_blit_setup_fails(self):
        """When ``_pan_setup_blit`` doesn't produce a background snapshot
        (e.g. Agg backend in a headless test), the drag must schedule a
        full-redraw tick instead of attempting to blit."""
        h = _InteractionHarness()
        h._pan_begin(_FakeEvent(inaxes=h._price_ax, x=100.0))
        # Stubbed _pan_setup_blit leaves _pan_bg as None → fallback path.
        h._pan_drag(_FakeEvent(x=150.0))
        assert h.pan_setup_blit_calls == 1
        assert len(h.track_after_calls) == 1
        # Verify the scheduled callback is the redraw tick. Bound
        # methods don't compare identity-equal across access, so compare
        # by underlying function.
        ms, cb = h.track_after_calls[0]
        assert ms == 16  # _PAN_REDRAW_INTERVAL_MS
        assert getattr(cb, "__func__", cb) is _InteractionHarness._pan_redraw_tick

    def test_pan_drag_uses_blit_path_when_bg_available(self):
        h = _InteractionHarness()
        h._pan_begin(_FakeEvent(inaxes=h._price_ax, x=100.0))
        # Pretend _pan_setup_blit succeeded.
        h._pan_bg = object()
        h._pan_animated = []  # no artists, but exercise restore_region/blit
        h._pan_drag(_FakeEvent(x=200.0))
        assert h._canvas.restore_region_calls == 1
        assert h._canvas.blit_calls == 1
        # xlim slid in the negative-dx-data direction (cursor moved
        # right, so the data shifts left).
        new_lo, _ = h._price_ax.get_xlim()
        assert new_lo < 0.0

    def test_pan_drag_event_x_none_short_circuits(self):
        h = _InteractionHarness()
        h._pan_begin(_FakeEvent(inaxes=h._price_ax, x=100.0))
        h._pan_drag(_FakeEvent(x=None))
        # Nothing happened — no setup, no schedule, no canvas calls.
        assert h.pan_setup_blit_calls == 0
        assert h._canvas.draw_idle_calls == 0

    def test_pan_end_with_no_active_state_is_noop(self):
        h = _InteractionHarness()
        h._pan_end(_FakeEvent())
        assert h._preserve_xlim_on_render is False
        assert h._canvas.draw_idle_calls == 0

    def test_pan_end_clears_state_and_persists_view(self):
        h = _InteractionHarness()
        h._pan_begin(_FakeEvent(inaxes=h._price_ax, x=100.0))
        h._pan_animated = [object()]  # simulate a tracked artist
        h._pan_bg = object()
        h._blit_bg = object()
        h._pan_end(_FakeEvent())
        assert h._pan_state is None
        assert h._pan_animated == []
        assert h._pan_bg is None
        assert h._blit_bg is None
        assert h._preserve_xlim_on_render is True
        assert h._slide_xlim_to_right_edge is False
        assert h.autoscale_calls == 1
        assert h._canvas.draw_idle_calls == 1


class TestScrollZoom:
    """Wheel-zoom is cursor-anchored: the bar under the cursor stays put
    while the surrounding range expands/contracts. The gate set lives at
    the top of ``_on_scroll_zoom`` and is the source of every "wheel
    doesn't work" support ticket."""

    def _arm(self, h):
        """Standard event: cursor at xdata=50, mid-axes."""
        return _FakeEvent(inaxes=h._price_ax, xdata=50.0, x=400.0, y=300.0,
                          button="down", key="")

    def test_no_inaxes_gate(self):
        h = _InteractionHarness()
        ev = _FakeEvent(inaxes=None, xdata=50.0, button="down")
        h._on_scroll_zoom(ev)
        assert h._price_ax.get_xlim() == (0.0, 100.0)

    def test_no_xdata_gate(self):
        h = _InteractionHarness()
        ev = _FakeEvent(inaxes=h._price_ax, xdata=None, button="down")
        h._on_scroll_zoom(ev)
        assert h._price_ax.get_xlim() == (0.0, 100.0)

    def test_active_pan_blocks_zoom(self):
        h = _InteractionHarness()
        h._pan_state = {"press_x": 0, "press_xlim": (0, 100),
                        "width_px": 800, "ax": h._price_ax}
        h._on_scroll_zoom(self._arm(h))
        assert h._price_ax.get_xlim() == (0.0, 100.0)

    def test_active_rubber_band_blocks_zoom(self):
        h = _InteractionHarness()
        h._zoom_state = {"ax": h._price_ax, "x0": 10, "y0": 100,
                         "rect": object()}
        h._on_scroll_zoom(self._arm(h))
        assert h._price_ax.get_xlim() == (0.0, 100.0)

    def test_zero_step_gate(self):
        h = _InteractionHarness()
        ev = _FakeEvent(inaxes=h._price_ax, xdata=50.0, x=400.0, y=300.0,
                        button="")
        # Force step=0 by zeroing event.step too
        ev.step = 0  # type: ignore[attr-defined]
        h._on_scroll_zoom(ev)
        assert h._price_ax.get_xlim() == (0.0, 100.0)

    def test_scroll_down_zooms_in(self):
        """Default mode: scroll DOWN → factor < 1 → window narrows."""
        h = _InteractionHarness()
        ev = self._arm(h)
        ev.step = -1.0  # type: ignore[attr-defined]
        h._on_scroll_zoom(ev)
        lo, hi = h._price_ax.get_xlim()
        assert (hi - lo) < 100.0
        assert h._preserve_xlim_on_render is True
        assert h._slide_xlim_to_right_edge is False
        assert "primary" in h.ensure_rendered_calls
        assert h.autoscale_calls == 1
        assert h._canvas.draw_idle_calls == 1

    def test_scroll_up_zooms_out(self):
        h = _InteractionHarness()
        ev = self._arm(h)
        ev.step = 1.0  # type: ignore[attr-defined]
        h._on_scroll_zoom(ev)
        lo, hi = h._price_ax.get_xlim()
        assert (hi - lo) > 100.0

    def test_invert_flips_direction(self):
        h = _InteractionHarness()
        h._scroll_zoom_invert = True
        ev = self._arm(h)
        ev.step = -1.0  # type: ignore[attr-defined]
        h._on_scroll_zoom(ev)
        lo, hi = h._price_ax.get_xlim()
        # With invert ON, step=-1 becomes +1 → zoom OUT.
        assert (hi - lo) > 100.0

    def test_step_magnitude_clamped(self):
        """A trackpad emitting step=20 must NOT collapse the chart —
        the handler caps |step| at _SCROLL_ZOOM_STEP_CLAMP."""
        h = _InteractionHarness()
        ev = self._arm(h)
        ev.step = -20.0  # type: ignore[attr-defined]
        h._on_scroll_zoom(ev)
        lo, hi = h._price_ax.get_xlim()
        # Width after clamped zoom-in is bounded below by the min-bars
        # floor and above by what a single clamped step produces.
        assert (hi - lo) > 0.0
        # Verify the cursor anchor is preserved: x=50 should still lie
        # in the new window.
        assert lo <= 50.0 <= hi

    def test_min_bars_floor_enforced(self):
        """Many consecutive zoom-ins must hit the floor, not collapse
        to a degenerate window."""
        h = _InteractionHarness()
        for _ in range(40):
            ev = self._arm(h)
            ev.step = -1.0  # type: ignore[attr-defined]
            h._on_scroll_zoom(ev)
        lo, hi = h._price_ax.get_xlim()
        # _SCROLL_ZOOM_MIN_BARS is the floor; the final window width
        # should be ≥ that floor.
        assert (hi - lo) >= h._SCROLL_ZOOM_MIN_BARS - 1e-6

    def test_cursor_anchored_at_axis_left_edge(self):
        """The bar under the cursor stays put in screen-space — even
        when the cursor is far from the center."""
        h = _InteractionHarness()
        # Cursor at xdata=5 (well left of center=50)
        ev = _FakeEvent(inaxes=h._price_ax, xdata=5.0, x=50.0, y=300.0,
                        button="down")
        ev.step = -1.0  # type: ignore[attr-defined]
        h._on_scroll_zoom(ev)
        lo, hi = h._price_ax.get_xlim()
        # 5 must still be in the visible range (and proportionally
        # closer to the left edge than to the right).
        assert lo <= 5.0 <= hi
        left = 5.0 - lo
        right = hi - 5.0
        assert left < right  # anchor near the left preserves L/R ratio


class TestRubberBandZoom:
    """Right-button drag draws a rectangle, release commits it as the
    new xlim. The state machine is begin → drag → end with a Rectangle
    artist threaded through the state dict."""

    def test_zoom_begin_with_no_inaxes_is_noop(self):
        h = _InteractionHarness()
        h._zoom_begin(_FakeEvent(inaxes=None))
        assert h._zoom_state is None

    def test_zoom_begin_arms_state_and_adds_patch(self):
        h = _InteractionHarness()
        h._theme = {"crosshair": "#ffffff", "text": "#000000"}
        ev = _FakeEvent(inaxes=h._price_ax, xdata=20.0, ydata=105.0)
        h._zoom_begin(ev)
        assert h._zoom_state is not None
        assert h._zoom_state["ax"] is h._price_ax
        assert h._zoom_state["x0"] == 20.0
        assert h._zoom_state["y0"] == 105.0
        # A Rectangle patch was added to the axes.
        assert len(h._price_ax.patches) == 1
        assert h.hide_overlays_calls == 1

    def test_zoom_drag_resizes_rectangle(self):
        h = _InteractionHarness()
        h._theme = {"crosshair": "#ffffff", "text": "#000000"}
        h._zoom_begin(_FakeEvent(inaxes=h._price_ax, xdata=20.0, ydata=105.0))
        rect = h._zoom_state["rect"]
        h._zoom_drag(_FakeEvent(xdata=35.0, ydata=110.0))
        # Rectangle was resized.
        assert rect.get_width() == 15.0
        assert rect.get_height() == 5.0

    def test_zoom_drag_with_no_state_is_noop(self):
        h = _InteractionHarness()
        h._zoom_drag(_FakeEvent(xdata=10.0, ydata=100.0))
        # No crash, no state appeared.
        assert h._zoom_state is None

    def test_zoom_end_commits_new_xlim(self):
        h = _InteractionHarness()
        h._theme = {"crosshair": "#ffffff", "text": "#000000"}
        h._zoom_begin(_FakeEvent(inaxes=h._price_ax, xdata=10.0, ydata=100.0))
        h._zoom_end(_FakeEvent(xdata=50.0, ydata=110.0))
        assert h._price_ax.get_xlim() == (10.0, 50.0)
        assert h._zoom_state is None
        assert h._preserve_xlim_on_render is True
        assert h._slide_xlim_to_right_edge is False
        assert h.autoscale_calls == 1

    def test_zoom_end_below_minimum_width_aborts(self):
        """A 0.0005 axis-unit rectangle is an accidental click, not a
        zoom — leave xlim alone."""
        h = _InteractionHarness()
        h._theme = {"crosshair": "#ffffff", "text": "#000000"}
        h._zoom_begin(_FakeEvent(inaxes=h._price_ax, xdata=10.0, ydata=100.0))
        prev = h._price_ax.get_xlim()
        h._zoom_end(_FakeEvent(xdata=10.0005, ydata=110.0))
        assert h._price_ax.get_xlim() == prev
        assert h._zoom_state is None

    def test_zoom_end_with_xdata_none_aborts_cleanly(self):
        h = _InteractionHarness()
        h._theme = {"crosshair": "#ffffff", "text": "#000000"}
        h._zoom_begin(_FakeEvent(inaxes=h._price_ax, xdata=10.0, ydata=100.0))
        prev = h._price_ax.get_xlim()
        h._zoom_end(_FakeEvent(xdata=None, ydata=None))
        assert h._price_ax.get_xlim() == prev
        assert h._zoom_state is None

    def test_zoom_end_with_no_state_is_noop(self):
        h = _InteractionHarness()
        h._zoom_end(_FakeEvent(xdata=10.0, ydata=100.0))
        assert h.autoscale_calls == 0


class TestB3ContextMenu:
    """Right-click release → either the per-line edit menu or the
    canvas menu, gated by the drawing hit-test."""

    def test_no_inaxes_short_circuits(self):
        h = _InteractionHarness()
        ev = _FakeEvent(inaxes=None)
        ev.guiEvent = type("GE", (), {"x_root": 100, "y_root": 200})()
        h._maybe_handle_b3_click_menu(ev)
        assert h.drawing_context_menu_calls == []
        assert h.canvas_menu_calls == []

    def test_unrecognised_axes_short_circuits(self):
        """B3 release inside an axes that isn't in ``_panel_state`` (e.g.
        an indicator pane or volume pane) must NOT pop a menu."""
        h = _InteractionHarness()
        stranger = _FakeAxes(id_=99)
        ev = _FakeEvent(inaxes=stranger)
        ev.guiEvent = type("GE", (), {"x_root": 100, "y_root": 200})()
        h._maybe_handle_b3_click_menu(ev)
        assert h.canvas_menu_calls == []

    def test_b3_on_blank_canvas_pops_canvas_menu(self):
        h = _InteractionHarness()
        ev = _FakeEvent(inaxes=h._price_ax, xdata=10.0, x=100.0, y=300.0)
        ev.guiEvent = type("GE", (), {"x_root": 500, "y_root": 400})()
        h._maybe_handle_b3_click_menu(ev)
        assert len(h.canvas_menu_calls) == 1
        slot_key, _e, x_r, y_r = h.canvas_menu_calls[0]
        assert slot_key == "primary"
        assert (x_r, y_r) == (500, 400)
        assert h.drawing_context_menu_calls == []

    def test_b3_on_drawing_pops_drawing_menu(self):
        h = _InteractionHarness()
        # Place a horizontal line and click within tolerance.
        drawing = make_hline_drawing(ticker="AAPL", price=105.0, color="#fff")
        h._drawings.add(drawing)
        ev = _FakeEvent(inaxes=h._price_ax, xdata=50.0, x=400.0, y=105.0)
        ev.guiEvent = type("GE", (), {"x_root": 700, "y_root": 250})()
        h._maybe_handle_b3_click_menu(ev)
        assert len(h.drawing_context_menu_calls) == 1
        assert h.drawing_context_menu_calls[0][0] == drawing.id
        assert h.drawing_context_menu_calls[0][1:] == (700, 250)
        assert h.canvas_menu_calls == []

    def test_b3_with_bad_gui_event_swallows_attribute_error(self):
        """guiEvent without x_root attribute must NOT raise."""
        h = _InteractionHarness()
        ev = _FakeEvent(inaxes=h._price_ax)
        ev.guiEvent = object()
        # Should not raise.
        h._maybe_handle_b3_click_menu(ev)
        assert h.drawing_context_menu_calls == []
        assert h.canvas_menu_calls == []


class TestDrawingHoverCursor:
    """Hovering a horizontal line swaps the cursor to a vertical
    double-arrow so the user knows the line is grab-able. Moving away
    restores it to the default."""

    def test_hover_over_line_sets_drag_cursor(self):
        h = _InteractionHarness()
        drawing = make_hline_drawing(ticker="AAPL", price=105.0, color="#fff")
        h._drawings.add(drawing)
        ev = _FakeEvent(inaxes=h._price_ax, xdata=50.0, x=400.0, y=105.0)
        h._update_drawing_hover_cursor(ev)
        assert h._drawing_hover_cursor_active is True
        assert "sb_v_double_arrow" in h._canvas._widget.cursor_history

    def test_hover_away_restores_default(self):
        h = _InteractionHarness()
        drawing = make_hline_drawing(ticker="AAPL", price=105.0, color="#fff")
        h._drawings.add(drawing)
        # First arm the drag cursor.
        on = _FakeEvent(inaxes=h._price_ax, xdata=50.0, x=400.0, y=105.0)
        h._update_drawing_hover_cursor(on)
        # Now hover far away — well outside the 8 px tolerance.
        off = _FakeEvent(inaxes=h._price_ax, xdata=50.0, x=400.0, y=1000.0)
        h._update_drawing_hover_cursor(off)
        assert h._drawing_hover_cursor_active is False
        assert h._canvas._widget.cursor_history[-1] == ""

    def test_hover_is_idempotent_when_already_on_line(self):
        """Two consecutive hovers on the same line must NOT spam
        ``widget.configure`` (which would queue redundant cursor
        updates on the Tk event loop)."""
        h = _InteractionHarness()
        drawing = make_hline_drawing(ticker="AAPL", price=105.0, color="#fff")
        h._drawings.add(drawing)
        ev = _FakeEvent(inaxes=h._price_ax, xdata=50.0, x=400.0, y=105.0)
        h._update_drawing_hover_cursor(ev)
        first = len(h._canvas._widget.cursor_history)
        h._update_drawing_hover_cursor(ev)
        # Should not have configured the cursor again.
        assert len(h._canvas._widget.cursor_history) == first

    def test_hover_with_no_store_short_circuits(self):
        h = _InteractionHarness()
        h._drawings = None  # type: ignore[assignment]
        ev = _FakeEvent(inaxes=h._price_ax, xdata=50.0, x=400.0, y=105.0)
        h._update_drawing_hover_cursor(ev)
        assert h._drawing_hover_cursor_active is False

    def test_reset_when_active_clears_cursor(self):
        h = _InteractionHarness()
        h._drawing_hover_cursor_active = True
        h._reset_drawing_hover_cursor()
        assert h._drawing_hover_cursor_active is False
        assert h._canvas._widget.cursor_history[-1] == ""

    def test_reset_when_inactive_is_noop(self):
        h = _InteractionHarness()
        h._drawing_hover_cursor_active = False
        h._reset_drawing_hover_cursor()
        # No cursor write because the flag wasn't set.
        assert h._canvas._widget.cursor_history == []


class TestDrawingDblclickEditDialog:
    """``_maybe_handle_drawing_dblclick`` opens the per-line edit dialog
    when a B1 dblclick lands on a horizontal line. This is the same
    routing used by ``_on_button_press`` to suppress the drilldown gate
    when the user is editing, not drilling-down."""

    def test_dblclick_off_line_returns_false(self):
        h = _InteractionHarness()
        ev = _FakeEvent(inaxes=h._price_ax, xdata=50.0, x=400.0, y=300.0)
        # Borrow the real method so it actually pick-tests.
        real = InteractionMixin._maybe_handle_drawing_dblclick.__get__(h)
        assert real(ev) is False
        assert h.open_drawing_dialog_calls == []

    def test_dblclick_on_line_opens_dialog_and_returns_true(self):
        h = _InteractionHarness()
        drawing = make_hline_drawing(ticker="AAPL", price=105.0, color="#fff")
        h._drawings.add(drawing)
        ev = _FakeEvent(inaxes=h._price_ax, xdata=50.0, x=400.0, y=105.0)
        real = InteractionMixin._maybe_handle_drawing_dblclick.__get__(h)
        assert real(ev) is True
        assert h.open_drawing_dialog_calls == [drawing.id]
