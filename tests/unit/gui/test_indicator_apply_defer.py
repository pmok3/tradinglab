"""Meta-test: indicator-editing windows follow the deferred-"Apply" pattern.

The Manage Indicators dialog renders **live by default** (auto-apply ON);
the recent perf work (vectorized indicators + scanner + the live-tick
blit) retired the deferred "Apply" stopgap that existed for slow chart
loads. The deferred flow is still available (uncheck Auto-apply) and is
flushed on "Apply" / "Save and Close". This suite pins that contract
three ways:

1. **App gate** — the REAL ``ChartApp._on_indicator_event`` /
   ``_begin/_end/_flush`` methods are bound onto a lightweight probe and
   exercised directly: a deferral flag suppresses the render; Apply
   flushes exactly one.
2. **Dialog contract** — over a registry of indicator-editing dialogs
   (deferred-capable vs always-live), assert the dialog drives the app's
   defer API correctly: it opens live, can opt into deferral (Apply
   lights up on edit without rendering, flushes on Apply, balances on
   close); the per-overlay popup never defers.
3. **Structural guard** — EVERY ``gui/`` module that mutates the
   ``IndicatorManager`` must be classified here (a deferred-capable
   dialog, a live surface, or an allow-listed non-window path). A new
   unclassified indicator-editing window fails the test, forcing it to
   declare its render mode — i.e. "any window that edits an indicator
   follows this pattern".
"""
from __future__ import annotations

import re
import types
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("tkinter")
import tkinter as tk  # noqa: E402

import tradinglab.indicators  # noqa: F401,E402  -- register built-in indicators
from tradinglab.app import ChartApp  # noqa: E402
from tradinglab.gui.indicator_dialog import IndicatorDialog  # noqa: E402
from tradinglab.gui.per_indicator_dialog import _PerIndicatorDialog  # noqa: E402
from tradinglab.indicators.config import (  # noqa: E402
    IndicatorConfig,
    IndicatorManager,
)

_GUI_DIR = Path(__file__).resolve().parents[3] / "src" / "tradinglab" / "gui"


# ===========================================================================
# 1. App gate — the real _on_indicator_event / defer API in isolation
# ===========================================================================


class _GateProbe:
    """Minimal host for the REAL ChartApp indicator-render methods.

    Provides just the dependencies the methods touch so we can exercise
    the actual gate logic without constructing a full ChartApp.
    """

    def __init__(self) -> None:
        self._indicator_redraw_pending = False
        self._defer_indicator_render = 0
        self._indicator_render_count = 0
        self.render_calls = 0
        self._status = types.SimpleNamespace(warn=lambda *a, **k: None)

    def _render(self) -> None:
        self.render_calls += 1

    def after_idle(self, fn):  # synchronous for the test
        fn()
        return "after#0"

    def _materialize_blank_avwap_anchors(self) -> None:
        pass

    # Bind the real implementations under test.
    _on_indicator_event = ChartApp._on_indicator_event
    _begin_defer_indicator_render = ChartApp._begin_defer_indicator_render
    _end_defer_indicator_render = ChartApp._end_defer_indicator_render
    _flush_indicator_render = ChartApp._flush_indicator_render


def test_on_indicator_event_renders_when_not_deferred() -> None:
    p = _GateProbe()
    p._on_indicator_event("update", None)
    assert p.render_calls == 1
    assert p._indicator_render_count == 1


def test_on_indicator_event_suppresses_render_while_deferred() -> None:
    p = _GateProbe()
    p._begin_defer_indicator_render()
    for kind in ("add", "update", "remove", "reorder", "redraw"):
        p._on_indicator_event(kind, None)
    assert p.render_calls == 0, "deferred mode must suppress every render"
    assert p._indicator_render_count == 0


def test_flush_renders_exactly_once_even_while_deferred() -> None:
    p = _GateProbe()
    p._begin_defer_indicator_render()
    p._on_indicator_event("update", None)  # suppressed
    p._flush_indicator_render()
    assert p.render_calls == 1
    assert p._indicator_render_count == 1


def test_defer_counter_is_balanced_and_resumes() -> None:
    p = _GateProbe()
    p._begin_defer_indicator_render()
    p._begin_defer_indicator_render()  # nested
    p._on_indicator_event("update", None)
    assert p.render_calls == 0
    p._end_defer_indicator_render()
    p._on_indicator_event("update", None)
    assert p.render_calls == 0, "still deferred (counter > 0)"
    p._end_defer_indicator_render()
    p._on_indicator_event("update", None)
    assert p.render_calls == 1, "resumes once counter hits 0"


def test_chartapp_exposes_defer_api() -> None:
    for name in (
        "_begin_defer_indicator_render",
        "_end_defer_indicator_render",
        "_flush_indicator_render",
    ):
        assert callable(getattr(ChartApp, name, None)), name


# ===========================================================================
# 2. Dialog contract — registry of indicator-editing dialogs (defer vs live)
# ===========================================================================


@pytest.fixture()
def app_root():
    """Tk root with the stubs an IndicatorDialog reads, plus a RECORDING
    defer API so we can assert the dialog drives it correctly."""
    try:
        r = tk.Tk()
        r.withdraw()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    r._indicator_manager = IndicatorManager()  # type: ignore[attr-defined]
    r._indicator_dialog = None  # type: ignore[attr-defined]
    r._per_indicator_dialogs = {}  # type: ignore[attr-defined]
    r._theme = {"win_bg": "#ffffff", "text": "#000000"}  # type: ignore[attr-defined]
    r.interval_var = tk.StringVar(r, value="1d")  # type: ignore[attr-defined]
    r._on_menu_save_config = mock.MagicMock()  # type: ignore[attr-defined]
    calls = {"begin": 0, "end": 0, "flush": 0}
    r._defer_calls = calls  # type: ignore[attr-defined]
    r._defer_indicator_render = 0  # type: ignore[attr-defined]

    def _begin() -> None:
        calls["begin"] += 1
        r._defer_indicator_render += 1  # type: ignore[attr-defined]

    def _end() -> None:
        calls["end"] += 1
        if r._defer_indicator_render > 0:  # type: ignore[attr-defined]
            r._defer_indicator_render -= 1  # type: ignore[attr-defined]

    def _flush() -> None:
        calls["flush"] += 1

    r._begin_defer_indicator_render = _begin  # type: ignore[attr-defined]
    r._end_defer_indicator_render = _end  # type: ignore[attr-defined]
    r._flush_indicator_render = _flush  # type: ignore[attr-defined]
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


def _seed_bbands(root) -> IndicatorConfig:
    return root._indicator_manager.add(
        IndicatorConfig(kind_id="bbands", display_name="Bollinger Bands"),
    )


def _mutate_first_row(dlg) -> None:
    """Make a committing edit on the dialog's first row (toggle Compare)."""
    row = dlg._rows[-1]
    if row.compare_var is not None:
        row.compare_var.set(not bool(row.compare_var.get()))
    dlg._commit_now(row)


def test_dialog_opens_live_by_default(app_root) -> None:
    """Manage Indicators now renders live on open (auto-apply ON default).

    The deferred 'Apply' stopgap is opt-in (uncheck Auto-apply). Live
    rendering is what correctly spawns a new lower pane (e.g. RRVOL) the
    moment an indicator is added."""
    _seed_bbands(app_root)
    dlg = IndicatorDialog(app_root)
    try:
        assert dlg._defers_render is True  # the dialog CAN defer...
        assert bool(dlg._auto_apply_var.get()) is True  # ...but defaults live
        assert app_root._defer_calls["begin"] == 0, "must not defer on open"
        assert dlg._render_deferred_active is False
        # Apply button still exists (opt-in stopgap) but is disabled.
        assert dlg._apply_btn is not None
        assert str(dlg._apply_btn.cget("state")) == "disabled"
        # A live edit renders via the manager subscriber (no pending,
        # Apply stays disabled).
        _mutate_first_row(dlg)
        assert dlg._pending_dirty is False
        assert str(dlg._apply_btn.cget("state")) == "disabled"
    finally:
        dlg._on_cancel()
    assert app_root._defer_indicator_render == 0


def test_deferred_mode_defers_edits_and_flushes_on_apply(app_root) -> None:
    """Opt into deferred mode (uncheck Auto-apply): edits then wait for Apply."""
    _seed_bbands(app_root)
    dlg = IndicatorDialog(app_root)
    try:
        # Opt into the deferred stopgap.
        dlg._auto_apply_var.set(False)
        dlg._on_auto_apply_toggled()
        assert app_root._defer_calls["begin"] == 1
        assert dlg._render_deferred_active is True
        assert dlg._apply_btn is not None
        assert str(dlg._apply_btn.cget("state")) == "disabled"

        # An edit must NOT render — it only marks pending + lights Apply.
        _mutate_first_row(dlg)
        assert app_root._defer_calls["flush"] == 0, "edit must not render"
        assert dlg._pending_dirty is True
        assert str(dlg._apply_btn.cget("state")) == "normal"

        # Apply flushes exactly one render and clears pending.
        dlg._apply()
        assert app_root._defer_calls["flush"] == 1
        assert dlg._pending_dirty is False
        assert str(dlg._apply_btn.cget("state")) == "disabled"

        # A second Apply with nothing pending is a no-op (guarded).
        dlg._apply()
        assert app_root._defer_calls["flush"] == 1
    finally:
        dlg._on_cancel()  # real close path → _teardown → end deferral
    assert app_root._defer_calls["end"] >= 1, "deferral must be balanced on close"
    assert app_root._defer_indicator_render == 0


def test_save_and_close_implicitly_applies(app_root) -> None:
    _seed_bbands(app_root)
    dlg = IndicatorDialog(app_root)
    closed = True
    try:
        # Opt into deferred mode so there is pending work to flush.
        dlg._auto_apply_var.set(False)
        dlg._on_auto_apply_toggled()
        _mutate_first_row(dlg)
        assert dlg._pending_dirty is True
        assert app_root._defer_calls["flush"] == 0
        dlg._on_save_close()  # validates, implicitly applies, tears down
        assert app_root._defer_calls["flush"] == 1, "Save and Close must apply"
        closed = True
    finally:
        if not closed:
            try:
                dlg._on_cancel()
            except tk.TclError:
                pass
    assert app_root._defer_indicator_render == 0


def test_auto_apply_toggle_switches_between_live_and_deferred(app_root) -> None:
    _seed_bbands(app_root)
    dlg = IndicatorDialog(app_root)
    try:
        # Opens live (default).
        assert dlg._render_deferred_active is False
        assert app_root._defer_calls["begin"] == 0
        # Flip auto-apply OFF → deferral begins.
        dlg._auto_apply_var.set(False)
        dlg._on_auto_apply_toggled()
        assert dlg._render_deferred_active is True
        assert app_root._defer_calls["begin"] == 1
        # A deferred edit marks pending.
        _mutate_first_row(dlg)
        assert dlg._pending_dirty is True
        # Flip auto-apply ON → deferral ends + one flush (apply current).
        dlg._auto_apply_var.set(True)
        dlg._on_auto_apply_toggled()
        assert dlg._render_deferred_active is False
        assert app_root._defer_calls["flush"] == 1
        # In live mode an edit does NOT mark pending (renders live).
        _mutate_first_row(dlg)
        assert dlg._pending_dirty is False
    finally:
        dlg._on_cancel()
    assert app_root._defer_indicator_render == 0


def test_per_indicator_popup_stays_live(app_root) -> None:
    cfg = _seed_bbands(app_root)
    dlg = _PerIndicatorDialog(app_root, cfg.id)
    try:
        # Live exception: never defers, no Apply button.
        assert dlg._defers_render is False
        assert app_root._defer_calls["begin"] == 0
        assert dlg._render_deferred_active is False
        assert dlg._apply_btn is None
        # Editing does not accumulate "pending" (renders live).
        _mutate_first_row(dlg)
        assert dlg._pending_dirty is False
    finally:
        dlg._on_cancel()
    assert app_root._defer_indicator_render == 0


# ===========================================================================
# 3. Structural guard — every manager-mutating gui module is classified
# ===========================================================================

#: Modules that mutate the IndicatorManager, each declared with its render
#: mode. Editing dialogs MUST declare ``_DEFERS_RENDER``; the others are
#: deliberate live/immediate surfaces (not deferred-Apply editors).
_CLASSIFIED_MUTATORS: dict[str, str] = {
    "indicator_dialog.py": "deferred-dialog (Apply button)",
    "per_indicator_dialog.py": "live-dialog (single-overlay quick edit)",
    "indicator_menu.py": "menu action (immediate, not a window)",
    "config_manager.py": "config load (immediate, programmatic)",
    "overlay_legend.py": "legend visibility toggle (immediate, single click)",
}

_MUTATION_RE = re.compile(
    r"(?:self\.)?_(?:indicator_)?manager\."
    r"(?:add|update|remove|reorder|clear|set_preset|load_dict)\s*\("
)


def _gui_modules_mutating_manager() -> set[str]:
    found: set[str] = set()
    for py in _GUI_DIR.rglob("*.py"):
        if py.name.endswith(".spec.md"):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        if _MUTATION_RE.search(text):
            found.add(py.name)
    return found


def test_every_indicator_editing_window_is_classified() -> None:
    """Any gui module that mutates the IndicatorManager must be classified.

    This is the enforcement the feature was asked for: a NEW window that
    edits an indicator can't silently bypass the deferred-render pattern —
    it has to be added here (declaring whether it defers + uses Apply, is
    a live surface, or is a non-window immediate path).
    """
    mutators = _gui_modules_mutating_manager()
    unclassified = mutators - set(_CLASSIFIED_MUTATORS)
    assert not unclassified, (
        "Unclassified gui module(s) mutate the IndicatorManager: "
        f"{sorted(unclassified)}.\n"
        "Every indicator-editing window must follow the deferred-render "
        "pattern. Classify it in _CLASSIFIED_MUTATORS (and, if it's an "
        "editing dialog, give it _DEFERS_RENDER + an Apply button)."
    )
    # And the registry must not rot: every classified module must still
    # actually mutate the manager.
    stale = set(_CLASSIFIED_MUTATORS) - mutators
    assert not stale, (
        f"_CLASSIFIED_MUTATORS lists module(s) that no longer mutate the "
        f"manager: {sorted(stale)} — remove them."
    )


def test_defer_flags_are_declared() -> None:
    assert IndicatorDialog._DEFERS_RENDER is True
    assert _PerIndicatorDialog._DEFERS_RENDER is False
