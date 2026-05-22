"""Unit tests for :class:`StatusHistoryWindow` level filter (F5b)."""
from __future__ import annotations

from datetime import datetime

import pytest

tk = pytest.importorskip("tkinter")

from tradinglab import status


@pytest.fixture
def _tk_root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk not available")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


def _make_log_with_entries(root, tmp_path) -> status.StatusLog:
    var = tk.StringVar(master=root)
    log = status.StatusLog(
        var,
        tk_root=root,
        log_dir=tmp_path,
        also_stdout=False,
        retention_days=0,  # skip prune entirely
    )
    log.info("info 1")
    log.warn("warn 1")
    log.error("error 1")
    log.info("info 2")
    log.warn("warn 2")
    return log


def test_level_filter_constants_cover_expected_labels():
    assert "All" in status.StatusHistoryWindow._LEVEL_FILTERS
    assert "WARN+" in status.StatusHistoryWindow._LEVEL_FILTERS
    assert "ERROR only" in status.StatusHistoryWindow._LEVEL_FILTERS
    assert status.StatusHistoryWindow._LEVEL_FILTERS["All"] is None
    assert "WARN" in status.StatusHistoryWindow._LEVEL_FILTERS["WARN+"]
    assert "ERROR" in status.StatusHistoryWindow._LEVEL_FILTERS["WARN+"]
    assert "INFO" not in status.StatusHistoryWindow._LEVEL_FILTERS["WARN+"]
    assert status.StatusHistoryWindow._LEVEL_FILTERS["ERROR only"] == frozenset({"ERROR"})


def test_history_window_renders_all_by_default(_tk_root, tmp_path):
    log = _make_log_with_entries(_tk_root, tmp_path)
    win = status.StatusHistoryWindow(_tk_root, log)
    try:
        _tk_root.update_idletasks()
        # 5 entries total.
        assert len(win._tree.get_children()) == 5
    finally:
        win._on_close()


def test_history_window_filters_warn_plus(_tk_root, tmp_path):
    log = _make_log_with_entries(_tk_root, tmp_path)
    win = status.StatusHistoryWindow(_tk_root, log)
    try:
        win._level_filter_var.set("WARN+")
        win._force_refresh()
        _tk_root.update_idletasks()
        # 2 warn + 1 error = 3 entries.
        assert len(win._tree.get_children()) == 3
    finally:
        win._on_close()


def test_history_window_filters_error_only(_tk_root, tmp_path):
    log = _make_log_with_entries(_tk_root, tmp_path)
    win = status.StatusHistoryWindow(_tk_root, log)
    try:
        win._level_filter_var.set("ERROR only")
        win._force_refresh()
        _tk_root.update_idletasks()
        assert len(win._tree.get_children()) == 1
    finally:
        win._on_close()


def test_filter_toggle_back_to_all_restores_full_view(_tk_root, tmp_path):
    log = _make_log_with_entries(_tk_root, tmp_path)
    win = status.StatusHistoryWindow(_tk_root, log)
    try:
        win._level_filter_var.set("ERROR only")
        win._force_refresh()
        _tk_root.update_idletasks()
        assert len(win._tree.get_children()) == 1
        win._level_filter_var.set("All")
        win._force_refresh()
        _tk_root.update_idletasks()
        assert len(win._tree.get_children()) == 5
    finally:
        win._on_close()


def test_selected_level_filter_returns_none_for_all(_tk_root, tmp_path):
    log = _make_log_with_entries(_tk_root, tmp_path)
    win = status.StatusHistoryWindow(_tk_root, log)
    try:
        win._level_filter_var.set("All")
        assert win._selected_level_filter() is None
    finally:
        win._on_close()
