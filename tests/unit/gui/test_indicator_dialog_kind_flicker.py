"""Regression tests for the IndicatorDialog kind-combobox flicker.

The kind combobox binds ``<FocusOut>`` (so a typed-and-tabbed-away kind
name still commits). On Windows ttk *also* fires ``<FocusOut>`` when the
dropdown popdown is merely posted/dismissed, and a re-pick of the same
value fires ``<<ComboboxSelected>>`` — in both cases the resolved kind is
UNCHANGED. Pre-fix, ``_on_kind_changed`` unconditionally tore down +
rebuilt the row's param widgets and re-walked the whole dialog via
``_apply_theme`` on every such event, so clicking the dropdown made the
window flicker.

The fix tracks the rendered kind on ``_IndicatorRow.applied_kind_id`` and
short-circuits ``_on_kind_changed`` when the resolved kind is unchanged.
These tests pin:

* unchanged-kind events do NOT rebuild param widgets or re-theme;
* a genuine kind change still rebuilds exactly once and updates
  ``applied_kind_id``.
"""
from __future__ import annotations

from unittest import mock

import pytest

tk = pytest.importorskip("tkinter")

import tradinglab.indicators  # noqa: F401  -- registers built-in indicators
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager


@pytest.fixture()
def root():
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


def _open_dialog(root):
    from tradinglab.gui.indicator_dialog import IndicatorDialog
    return IndicatorDialog(root)


def _display_for(dlg, kind_id: str) -> str:
    display = next(
        (d for d, kid in dlg._kinds_by_display.items() if kid == kind_id),
        None,
    )
    if display is None:
        pytest.skip(f"{kind_id} indicator not registered")
    return display


def _two_registered_kinds(dlg) -> tuple[str, str]:
    """Pick two distinct registered kind_ids, preferring stable built-ins."""
    preferred = ["bbands", "macd", "atr", "adx", "keltner", "rsi", "vwap"]
    available = [
        k for k in preferred if k in set(dlg._kinds_by_display.values())
    ]
    if len(available) < 2:
        available = list(dict.fromkeys(dlg._kinds_by_display.values()))
    if len(available) < 2:
        pytest.skip("need at least two registered indicator kinds")
    return available[0], available[1]


# ---------------------------------------------------------------------------
# Unchanged-kind events must not rebuild / re-theme (the flicker fix)
# ---------------------------------------------------------------------------


def test_unchanged_kind_focusout_does_not_rebuild_or_retheme(root):
    """Repeated ``_on_kind_changed`` with the same kind is a no-op.

    Pre-fix this rebuilt the param widgets + re-walked the whole window
    via ``_apply_theme`` on every call — the flicker the user reported.
    """
    mgr = root._indicator_manager
    kind_a, _kind_b = _two_registered_kinds(_open_dialog(root))
    # Reopen fresh with a hydrated row of the chosen kind.
    mgr.add(IndicatorConfig(kind_id=kind_a, display_name=kind_a))
    dlg = _open_dialog(root)
    try:
        assert dlg._rows, "dialog should hydrate a row from the manager"
        row = dlg._rows[-1]
        assert row.applied_kind_id == kind_a

        build_spy = mock.patch.object(
            dlg, "_build_param_widgets",
            wraps=dlg._build_param_widgets,
        ).start()
        theme_spy = mock.patch.object(
            dlg, "_apply_theme", wraps=dlg._apply_theme,
        ).start()
        try:
            for _ in range(5):
                dlg._on_kind_changed(row)
            assert build_spy.call_count == 0, (
                "unchanged kind must NOT rebuild param widgets"
            )
            assert theme_spy.call_count == 0, (
                "unchanged kind must NOT re-walk/re-theme the window"
            )
        finally:
            mock.patch.stopall()
    finally:
        dlg.destroy()


def test_reselect_same_value_is_noop(root):
    """Re-setting the combobox to its current display is a no-op."""
    mgr = root._indicator_manager
    kind_a, _kind_b = _two_registered_kinds(_open_dialog(root))
    mgr.add(IndicatorConfig(kind_id=kind_a, display_name=kind_a))
    dlg = _open_dialog(root)
    try:
        row = dlg._rows[-1]
        build_spy = mock.patch.object(
            dlg, "_build_param_widgets",
            wraps=dlg._build_param_widgets,
        ).start()
        try:
            # Simulate <<ComboboxSelected>> re-picking the same display.
            row.kind_var.set(_display_for(dlg, kind_a))
            dlg._on_kind_changed(row)
            assert build_spy.call_count == 0
        finally:
            mock.patch.stopall()
    finally:
        dlg.destroy()


# ---------------------------------------------------------------------------
# A genuine kind change must still rebuild exactly once (no regression)
# ---------------------------------------------------------------------------


def test_genuine_kind_change_rebuilds_once_and_updates_applied(root):
    mgr = root._indicator_manager
    kind_a, kind_b = _two_registered_kinds(_open_dialog(root))
    mgr.add(IndicatorConfig(kind_id=kind_a, display_name=kind_a))
    dlg = _open_dialog(root)
    try:
        row = dlg._rows[-1]
        assert row.applied_kind_id == kind_a

        build_spy = mock.patch.object(
            dlg, "_build_param_widgets",
            wraps=dlg._build_param_widgets,
        ).start()
        try:
            row.kind_var.set(_display_for(dlg, kind_b))
            dlg._on_kind_changed(row)
            assert build_spy.call_count == 1, (
                "a genuine kind change must rebuild the param widgets once"
            )
            assert row.applied_kind_id == kind_b

            # And a follow-up unchanged event on the NEW kind is a no-op.
            build_spy.reset_mock()
            dlg._on_kind_changed(row)
            assert build_spy.call_count == 0
        finally:
            mock.patch.stopall()
    finally:
        dlg.destroy()


def test_applied_kind_id_seeded_on_hydrate(root):
    """A freshly hydrated row records its kind in ``applied_kind_id``."""
    mgr = root._indicator_manager
    kind_a, _kind_b = _two_registered_kinds(_open_dialog(root))
    mgr.add(IndicatorConfig(kind_id=kind_a, display_name=kind_a))
    dlg = _open_dialog(root)
    try:
        assert dlg._rows[-1].applied_kind_id == kind_a
    finally:
        dlg.destroy()
