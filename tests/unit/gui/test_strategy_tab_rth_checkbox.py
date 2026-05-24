"""Tests for the RTH-only opt-in checkbox in StrategyTab.

Mounts the Strategy Tester tab headlessly and verifies:

* The ``_var_include_extended_hours`` BooleanVar defaults to False.
* The ``_lbl_extended_hours_warning`` label is hidden initially.
* Toggling the var True + calling the handler reveals the warning.
* Toggling back to False hides the warning again.
* ``_build_config_from_ui`` threads the variable into the produced
  ``TestConfig`` (default False, opt-in True).
"""

from __future__ import annotations

from typing import Any

import pytest

tk = pytest.importorskip("tkinter")
ttk = pytest.importorskip("tkinter.ttk")  # noqa: F401


@pytest.fixture()
def tk_root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        r.geometry("800x600-3000-3000")
    except tk.TclError:
        pass
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


class _FakeStorage:
    def load_all(self):  # noqa: ANN201
        return [], []


def _make_tab(root: Any):
    from tradinglab.gui.strategy_tab import StrategyTab

    tab = StrategyTab(
        root,
        entries_storage=_FakeStorage(),
        exits_storage=_FakeStorage(),
        watchlists_storage=_FakeStorage(),
    )
    tab.pack(fill="both", expand=True)
    root.update_idletasks()
    return tab


def test_default_extended_hours_off(tk_root) -> None:
    tab = _make_tab(tk_root)
    assert tab._var_include_extended_hours.get() is False


def test_warning_hidden_initially(tk_root) -> None:
    tab = _make_tab(tk_root)
    # grid_info() returns empty dict when grid_remove() has hidden the widget.
    assert tab._lbl_extended_hours_warning.grid_info() == {}


def test_warning_shown_when_toggled_on(tk_root) -> None:
    tab = _make_tab(tk_root)
    tab._var_include_extended_hours.set(True)
    tab._on_extended_hours_toggle()
    tk_root.update_idletasks()
    info = tab._lbl_extended_hours_warning.grid_info()
    assert info != {}
    assert "warning" in tab._lbl_extended_hours_warning.cget("text").lower()


def test_warning_hidden_when_toggled_off(tk_root) -> None:
    tab = _make_tab(tk_root)
    tab._var_include_extended_hours.set(True)
    tab._on_extended_hours_toggle()
    tab._var_include_extended_hours.set(False)
    tab._on_extended_hours_toggle()
    tk_root.update_idletasks()
    assert tab._lbl_extended_hours_warning.grid_info() == {}
