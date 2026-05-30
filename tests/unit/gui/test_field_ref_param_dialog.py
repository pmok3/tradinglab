"""Headless tests for ``_FieldRefParamDialog`` (the compact-picker Edit popup).

Pins:
* Apply returns a new ``FieldRef`` with the edited params, preserving the
  ref's identity (id / output_key / cross-symbol ``symbol`` / interval).
* Cancel returns ``None`` (no mutation leaks).
* Invalid input blocks Apply and surfaces a validation message.
* Multi-output indicators expose an output-key selector that round-trips.
* The cross-symbol ``FieldRef.symbol`` pin is preserved untouched (NOT edited
  in this popup).
"""
from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")
pytest.importorskip("tkinter.ttk")

from tradinglab.gui import scanner_block_editor as sbe
from tradinglab.scanner.model import FieldRef


@pytest.fixture()
def root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        r.geometry("1x1-3000-3000")
    except tk.TclError:
        pass
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


def _mk(root, ref):
    dlg = sbe._FieldRefParamDialog(root, ref=ref)
    root.update_idletasks()
    return dlg


def test_apply_returns_updated_ref_preserving_identity(root):
    ref = FieldRef.indicator(
        "rrvol", params={"length": 20}, symbol="AAPL", interval="5m"
    )
    dlg = _mk(root, ref)
    dlg._param_vars["length"].set("30")
    dlg._param_vars["compare_symbol"].set("QQQ")
    dlg._on_primary()
    res = dlg.result
    assert res is not None
    assert res.id == "rrvol"
    assert res.params["length"] == 30
    assert res.params["compare_symbol"] == "QQQ"
    # cross-symbol pin + interval preserved untouched.
    assert res.symbol == "AAPL"
    assert res.interval == "5m"


def test_cancel_returns_none(root):
    ref = FieldRef.indicator("rrvol", params={"length": 20})
    dlg = _mk(root, ref)
    dlg._param_vars["length"].set("99")
    dlg._on_cancel()
    assert dlg.result is None


def test_invalid_blocks_apply_and_surfaces_message(root):
    ref = FieldRef.indicator("rvol", params={"length": 20})
    dlg = _mk(root, ref)
    dlg._param_vars["length"].set("not-a-number")
    dlg._on_primary()
    assert dlg.result is None
    assert dlg._error_var.get()  # non-empty validation message


def test_multi_output_round_trips_output_key(root):
    ref = FieldRef.indicator("smi", output_key="smi")
    dlg = _mk(root, ref)
    assert dlg._output_var is not None
    dlg._output_var.set("signal")
    dlg._on_primary()
    assert dlg.result is not None
    assert dlg.result.output_key == "signal"


def test_single_output_has_no_output_selector(root):
    ref = FieldRef.indicator("rvol", params={"length": 20})
    dlg = _mk(root, ref)
    assert dlg._output_var is None
    dlg._on_primary()
    assert dlg.result is not None


def test_param_vars_seeded_from_ref(root):
    ref = FieldRef.indicator("rvol", params={"length": 14})
    dlg = _mk(root, ref)
    assert dlg._param_vars["length"].get() in ("14", 14)
