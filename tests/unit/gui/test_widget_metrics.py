"""Tests for ``gui/_widget_metrics.py`` runtime font-measured metrics.

Covers audit #9: hardcoded constants replaced with
``metrics_for(font_name)`` measurements + ``_ConstantProxy`` back-compat
aliases that look-and-act like ints in every existing call site.
"""
from __future__ import annotations

import tkinter as tk

import pytest

from tradinglab.gui._widget_metrics import (
    _CHAR_PX,
    _CHAR_PX_FALLBACK,
    _COMBO_OVERHEAD,
    _COMBO_OVERHEAD_FALLBACK,
    _ENTRY_OVERHEAD,
    _ENTRY_OVERHEAD_FALLBACK,
    _METRICS_CACHE,
    _SPINBOX_OVERHEAD,
    _SPINBOX_OVERHEAD_FALLBACK,
    invalidate_metrics_cache,
    metrics_for,
)


@pytest.fixture(autouse=True)
def _clean_metrics_cache():
    invalidate_metrics_cache()
    yield
    invalidate_metrics_cache()


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for Tk")
    root.withdraw()
    invalidate_metrics_cache()  # discard any pre-Tk fallback measurement
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass
    invalidate_metrics_cache()


def test_metrics_for_returns_four_keys(tk_root):
    m = metrics_for("TkDefaultFont")
    assert set(m.keys()) == {
        "char_px",
        "combo_overhead",
        "spinbox_overhead",
        "entry_overhead",
    }


def test_metrics_for_values_are_positive_ints(tk_root):
    m = metrics_for("TkDefaultFont")
    for key, value in m.items():
        assert isinstance(value, int), f"{key} is {type(value).__name__}"
        assert value > 0, f"{key} = {value}"


def test_metrics_for_is_cached_returns_same_instance(tk_root):
    m1 = metrics_for("TkDefaultFont")
    m2 = metrics_for("TkDefaultFont")
    assert m1 is m2


def test_invalidate_metrics_cache_clears(tk_root):
    metrics_for("TkDefaultFont")
    assert "TkDefaultFont" in _METRICS_CACHE
    invalidate_metrics_cache()
    assert "TkDefaultFont" not in _METRICS_CACHE


def test_metrics_for_unknown_font_falls_back():
    # No tk_root needed — the unknown font name guarantees TclError
    # regardless of Tk state, exercising the fallback path.
    invalidate_metrics_cache()
    m = metrics_for("this_font_definitely_does_not_exist_xyz")
    assert m == {
        "char_px":          _CHAR_PX_FALLBACK,
        "combo_overhead":   _COMBO_OVERHEAD_FALLBACK,
        "spinbox_overhead": _SPINBOX_OVERHEAD_FALLBACK,
        "entry_overhead":   _ENTRY_OVERHEAD_FALLBACK,
    }


def test_proxy_multiplication():
    invalidate_metrics_cache()
    expected = metrics_for()["char_px"]
    assert _CHAR_PX * 5 == expected * 5
    assert 5 * _CHAR_PX == 5 * expected


def test_proxy_addition():
    invalidate_metrics_cache()
    expected = metrics_for()["char_px"]
    assert _CHAR_PX + 10 == expected + 10
    assert 10 + _CHAR_PX == 10 + expected


def test_proxy_subtraction():
    invalidate_metrics_cache()
    expected = metrics_for()["combo_overhead"]
    assert _COMBO_OVERHEAD - 5 == expected - 5
    assert 100 - _COMBO_OVERHEAD == 100 - expected


def test_proxy_int_cast():
    invalidate_metrics_cache()
    assert int(_CHAR_PX) == metrics_for()["char_px"]
    assert int(_COMBO_OVERHEAD) == metrics_for()["combo_overhead"]
    assert int(_SPINBOX_OVERHEAD) == metrics_for()["spinbox_overhead"]
    assert int(_ENTRY_OVERHEAD) == metrics_for()["entry_overhead"]


def test_proxy_index_usage():
    # __index__ is required for e.g. range() and slicing
    invalidate_metrics_cache()
    expected = metrics_for()["char_px"]
    assert len(range(_CHAR_PX)) == expected


def test_proxy_floordiv():
    invalidate_metrics_cache()
    expected = metrics_for()["combo_overhead"]
    assert _COMBO_OVERHEAD // 2 == expected // 2


def test_proxy_equality_against_int():
    invalidate_metrics_cache()
    expected = metrics_for()["char_px"]
    assert _CHAR_PX == expected
    assert _CHAR_PX != expected + 1


def test_proxy_compound_expression_matches_int_form():
    # Mirrors the real usage pattern from scanner_block_editor.py:
    # label_px + 8 * _CHAR_PX + _COMBO_OVERHEAD
    invalidate_metrics_cache()
    m = metrics_for()
    via_proxy = 50 + 8 * _CHAR_PX + _COMBO_OVERHEAD
    via_dict = 50 + 8 * m["char_px"] + m["combo_overhead"]
    assert via_proxy == via_dict
