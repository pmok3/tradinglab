"""Regression test: ``IndicatorDialog`` Combobox/Spinbox wheel-guard.

The dialog ``bind_all``s ``<MouseWheel>`` while the cursor is over
its rows-canvas so the canvas scrolls under the cursor. But ttk
Combobox / Spinbox widgets *consume* ``<MouseWheel>`` natively and
silently mutate their value on every wheel tick — same hazard as the
EMA 3/8 cross template corruption that motivated
``protect_combobox_wheel`` (see ``gui/_modal_base.py`` and CLAUDE.md
§7.11).

This test:

1. Builds an ``IndicatorDialog`` headlessly.
2. Adds two rows (the default is no rows on a fresh manager) so the
   tree contains real param Comboboxes/Spinboxes.
3. Snapshots every Combobox/Spinbox value in the dialog.
4. Wheel-bombs them all with 8 down-ticks + 8 up-ticks each.
5. Asserts the snapshot is byte-identical afterwards.

Also exercises the post-rebuild re-guard: after ``_on_click_add``,
``_on_kind_changed``, and ``_reconcile_from_manager`` the new widgets
must also be protected.
"""
from __future__ import annotations

from unittest import mock

import pytest

tk = pytest.importorskip("tkinter")

from tradinglab.indicators.config import IndicatorManager

from ._wheel_guard_helpers import (
    snapshot_combobox_spinbox_values,
    wheel_bomb_all,
)


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


def test_wheel_storm_does_not_mutate_indicator_params(root):
    """8 wheel-down + 8 wheel-up over every widget must not change a thing."""
    dlg = _open_dialog(root)
    try:
        # Seed with two rows so the form has real Combobox/Spinbox widgets.
        dlg._on_click_add()
        dlg._on_click_add()
        root.update_idletasks()

        before = snapshot_combobox_spinbox_values(dlg)
        assert before, (
            "expected at least one Combobox/Spinbox after adding rows — "
            "test would be vacuously true otherwise"
        )

        bombed = wheel_bomb_all(dlg, ticks=8)
        assert bombed == len(before), (
            f"walker mismatch: snapshot saw {len(before)} widgets, "
            f"bomber hit {bombed}"
        )

        after = snapshot_combobox_spinbox_values(dlg)
        assert after == before, (
            f"wheel-over silently mutated widget values:\n"
            f"  before={before}\n"
            f"  after ={after}"
        )
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_kind_combobox_filters_as_user_types(root):
    dlg = _open_dialog(root)
    try:
        dlg._on_click_add()
        row = dlg._rows[-1]
        row.kind_var.set("rrvol")
        dlg._on_kind_combo_keyrelease(row)
        values = tuple(row.kind_combo.cget("values"))
        assert "RRVOL" in values
        assert "EMA" not in values
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_kind_search_return_commits_single_match(root):
    dlg = _open_dialog(root)
    try:
        dlg._on_click_add()
        row = dlg._rows[-1]
        row.kind_var.set("rrvol")
        dlg._on_kind_changed(row)
        assert row.kind_var.get() == "RRVOL"
        assert row.last_good_params is not None
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_wheel_guard_reapplied_after_kind_change(root):
    """After ``_on_kind_changed`` rebuilds param widgets, the new
    Combobox/Spinbox widgets must also be guarded.

    Pre-fix, the first ``_protect_combobox_wheel`` call covered the
    initial widgets only; a kind swap (SMA → EMA → Bollinger) created
    a fresh schema's worth of spinboxes that fell through unprotected
    and would mutate on the next wheel-over.
    """
    dlg = _open_dialog(root)
    try:
        dlg._on_click_add()
        root.update_idletasks()
        row = dlg._rows[-1]

        # Pick a different kind to force the param-widgets rebuild.
        available = list(dlg._kinds_by_display.keys())
        if len(available) < 2:
            pytest.skip("need ≥2 registered indicator kinds to swap")
        current = row.kind_var.get()
        new_display = next((k for k in available if k != current), None)
        if new_display is None:
            pytest.skip("no alternate kind available to swap to")
        row.kind_var.set(new_display)
        dlg._on_kind_changed(row)
        root.update_idletasks()

        before = snapshot_combobox_spinbox_values(dlg)
        wheel_bomb_all(dlg, ticks=8)
        after = snapshot_combobox_spinbox_values(dlg)
        assert after == before, (
            f"post-kind-change widgets were not re-guarded:\n"
            f"  before={before}\n"
            f"  after ={after}"
        )
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass
