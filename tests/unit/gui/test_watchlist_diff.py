"""Watchlist Treeview incremental diff (qw-watchlist-diff).

Pins `_diff_watchlist_rows`: ticker-keyed iids, in-place cell updates when
the ordered row set is unchanged, full rebuild on add/remove/reorder,
selection preserved across incremental refreshes, and the duplicate-ticker
fallback.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import pytest

from tradinglab.gui.watchlist_tab import WatchlistTabMixin

_COLS = ("ticker", "last", "change", "change_pct", "next_earn")


class _Stub:
    """Minimal carrier of the one attribute `_diff_watchlist_rows` reads."""

    def __init__(self) -> None:
        self._watchlist_row_cache: dict[str, dict[tuple, tuple]] = {}


@pytest.fixture(scope="module")
def _root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no Tk display available")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except Exception:
        pass


@pytest.fixture()
def tree(_root):
    t = ttk.Treeview(_root, columns=_COLS, show="headings")
    yield t
    try:
        t.destroy()
    except Exception:
        pass


def _row(ticker, last, tag="bull"):
    return (ticker, (ticker, last, "+0.10", "+1.00%", ""), (tag,))


def _diff(stub, tree, rows, name="W"):
    WatchlistTabMixin._diff_watchlist_rows(stub, name, tree, rows)


def test_initial_populate_uses_ticker_iids(tree):
    stub = _Stub()
    rows = [_row("AAPL", "1.00"), _row("MSFT", "2.00", "bear")]
    _diff(stub, tree, rows)
    assert list(tree.get_children()) == ["AAPL", "MSFT"]
    assert tree.item("AAPL", "values")[1] == "1.00"
    assert tree.item("MSFT", "tags")[0] == "bear"
    assert set(stub._watchlist_row_cache["W"]) == {"AAPL", "MSFT"}


def test_incremental_updates_only_changed_rows(tree):
    stub = _Stub()
    _diff(stub, tree, [_row("AAPL", "1.00"), _row("MSFT", "2.00")])

    # Spy on writes (tree.item with kwargs).
    written: list[str] = []
    orig = tree.item

    def spy(iid, *a, **kw):
        if a or kw:
            written.append(iid)
        return orig(iid, *a, **kw)

    tree.item = spy  # type: ignore[assignment]
    # AAPL price moves; MSFT unchanged.
    _diff(stub, tree, [_row("AAPL", "9.99"), _row("MSFT", "2.00")])
    assert written == ["AAPL"]
    assert tree.item("AAPL", "values")[1] == "9.99"


def test_no_change_writes_nothing(tree):
    stub = _Stub()
    rows = [_row("AAPL", "1.00"), _row("MSFT", "2.00")]
    _diff(stub, tree, rows)
    written: list[str] = []
    orig = tree.item

    def spy(iid, *a, **kw):
        if a or kw:
            written.append(iid)
        return orig(iid, *a, **kw)

    tree.item = spy  # type: ignore[assignment]
    _diff(stub, tree, rows)  # identical
    assert written == []


def test_reorder_rebuilds_in_new_order(tree):
    stub = _Stub()
    _diff(stub, tree, [_row("AAPL", "1.00"), _row("MSFT", "2.00")])
    _diff(stub, tree, [_row("MSFT", "2.00"), _row("AAPL", "1.00")])
    assert list(tree.get_children()) == ["MSFT", "AAPL"]


def test_add_and_remove_ticker(tree):
    stub = _Stub()
    _diff(stub, tree, [_row("AAPL", "1.00")])
    _diff(stub, tree, [_row("AAPL", "1.00"), _row("NVDA", "3.00")])
    assert list(tree.get_children()) == ["AAPL", "NVDA"]
    _diff(stub, tree, [_row("NVDA", "3.00")])
    assert list(tree.get_children()) == ["NVDA"]
    assert set(stub._watchlist_row_cache["W"]) == {"NVDA"}


def test_selection_preserved_across_incremental_update(tree):
    stub = _Stub()
    _diff(stub, tree, [_row("AAPL", "1.00"), _row("MSFT", "2.00")])
    tree.selection_set("MSFT")
    # A live-price refresh that only changes AAPL must not drop the
    # MSFT selection (the legacy delete-all+reinsert did).
    _diff(stub, tree, [_row("AAPL", "1.50"), _row("MSFT", "2.00")])
    assert tree.selection() == ("MSFT",)


def test_duplicate_tickers_fall_back_to_auto_iids(tree):
    stub = _Stub()
    rows = [_row("AAPL", "1.00"), _row("AAPL", "1.00")]
    _diff(stub, tree, rows)
    # Both rows present (auto iids), and no per-ticker cache kept.
    assert len(tree.get_children()) == 2
    assert "W" not in stub._watchlist_row_cache


def test_empty_after_nonempty_clears_tree(tree):
    stub = _Stub()
    _diff(stub, tree, [_row("AAPL", "1.00")])
    _diff(stub, tree, [])
    assert tree.get_children() == ()
    assert stub._watchlist_row_cache["W"] == {}
