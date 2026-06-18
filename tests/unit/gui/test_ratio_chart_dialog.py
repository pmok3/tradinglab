"""Tests for the ratio chart composer dialog."""
from __future__ import annotations

from tradinglab.data import RATIO_PRESETS
from tradinglab.gui.ratio_chart_dialog import RatioChartDialog


def _make_dialog(root):
    submitted: list[str] = []
    dlg = RatioChartDialog(root, on_submit=submitted.append)
    return dlg, submitted


def test_preset_selection_populates_symbol_fields(root):
    dlg, _submitted = _make_dialog(root)
    try:
        num, den, description = RATIO_PRESETS[0]
        dlg._preset_var.set(description)
        dlg._on_preset_selected()
        assert dlg._num_var.get() == num
        assert dlg._den_var.get() == den
        assert f"{num} / {den}" in dlg._preview_var.get()
    finally:
        dlg.destroy()


def test_valid_input_submits_canonical_ratio(root):
    dlg, submitted = _make_dialog(root)
    try:
        dlg._num_var.set(" amd ")
        dlg._den_var.set(" nvda ")
        dlg._on_ok()
        assert submitted == ["AMD/NVDA"]
        assert not dlg.winfo_exists()
    except Exception:
        if dlg.winfo_exists():
            dlg.destroy()
        raise


def test_empty_leg_does_not_submit_and_surfaces_status(root):
    dlg, submitted = _make_dialog(root)
    try:
        dlg._num_var.set("AMD")
        dlg._den_var.set("")
        dlg._on_ok()
        assert submitted == []
        assert "numerator and denominator" in dlg._status_var.get().lower()
        assert dlg.winfo_exists()
    finally:
        if dlg.winfo_exists():
            dlg.destroy()


def test_nested_leg_does_not_submit_and_surfaces_status(root):
    dlg, submitted = _make_dialog(root)
    try:
        dlg._num_var.set("AMD/NVDA")
        dlg._den_var.set("SPY")
        dlg._on_ok()
        assert submitted == []
        assert "do not include '/'" in dlg._status_var.get().lower()
        assert dlg.winfo_exists()
    finally:
        if dlg.winfo_exists():
            dlg.destroy()
