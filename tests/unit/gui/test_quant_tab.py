"""Unit tests for :class:`~tradinglab.gui.quant_tab.QuantTab`.

Geometry is deliberately avoided. ``Treeview.identify_row`` needs a mapped,
laid-out widget, which a withdrawn Toplevel in a headless run does not
provide — so the double-click tests stub ``identify_row`` and drive the
handler directly. That keeps the interesting logic (which callback fires,
and whether the event is swallowed) under test on every platform.

See `gui/quant_tab.spec.md`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradinglab.gui.quant_tab import TAG_GROUP, TAG_UNAVAILABLE, QuantTab
from tradinglab.quant.catalog import (
    QUANT_CATALOG,
    UNAVAILABLE_SYMBOL_TEXT,
    QuantGroup,
    QuantRow,
    available_rows,
    iter_rows,
)


@pytest.fixture
def tab(root):
    activated: list[str] = []
    blocked: list[QuantRow] = []
    widget = QuantTab(
        root,
        on_row_activate=activated.append,
        on_unavailable=blocked.append,
    )
    widget.activated = activated
    widget.blocked = blocked
    yield widget
    try:
        widget.destroy()
    except Exception:  # noqa: BLE001
        pass


def _item_for(tab: QuantTab, key: str) -> str:
    return f"row:{key}"


# --- structure --------------------------------------------------------


def test_every_group_becomes_a_parent_node(tab):
    parents = tab.tree.get_children("")
    assert len(parents) == len(QUANT_CATALOG)
    labels = [tab.tree.item(p, "text") for p in parents]
    assert labels == [g.name for g in QUANT_CATALOG]


def test_every_row_becomes_a_child_in_catalog_order(tab):
    seen: list[str] = []
    for parent in tab.tree.get_children(""):
        for child in tab.tree.get_children(parent):
            seen.append(tab.tree.item(child, "text"))
    assert seen == [r.name for r in iter_rows()]


def test_group_nodes_carry_the_group_tag(tab):
    for parent in tab.tree.get_children(""):
        assert TAG_GROUP in tab.tree.item(parent, "tags")


def test_available_rows_have_symbol_and_description_cells(tab):
    for row in available_rows():
        item = _item_for(tab, row.key)
        assert tab.tree.set(item, "symbol") == row.symbol
        assert tab.tree.set(item, "description") == row.description
        assert tab.tree.set(item, "last") == ""
        assert TAG_UNAVAILABLE not in tab.tree.item(item, "tags")


def test_unavailable_rows_are_tagged_and_show_a_dash(tab):
    for row in iter_rows():
        if row.available:
            continue
        item = _item_for(tab, row.key)
        assert tab.tree.set(item, "symbol") == UNAVAILABLE_SYMBOL_TEXT
        assert TAG_UNAVAILABLE in tab.tree.item(item, "tags")


def test_symbols_returns_available_rows_in_display_order(tab):
    assert tab.symbols() == [r.symbol for r in available_rows()]
    assert "" not in tab.symbols()


# --- Last column ------------------------------------------------------


def test_set_last_values_writes_matching_rows(tab):
    tab.set_last_values({"VIX": "15.82", "TLT": "82.60"})
    assert tab.tree.set(_item_for(tab, "vix"), "last") == "15.82"
    assert tab.tree.set(_item_for(tab, "tlt"), "last") == "82.60"


def test_set_last_values_is_case_insensitive(tab):
    tab.set_last_values({"vix": "15.82"})
    assert tab.tree.set(_item_for(tab, "vix"), "last") == "15.82"


def test_set_last_values_leaves_absent_symbols_alone(tab):
    """A partial refresh must never blank a cell that already resolved."""
    tab.set_last_values({"VIX": "15.82", "TLT": "82.60"})
    tab.set_last_values({"TLT": "83.10"})
    assert tab.tree.set(_item_for(tab, "vix"), "last") == "15.82"
    assert tab.tree.set(_item_for(tab, "tlt"), "last") == "83.10"


def test_set_last_values_ignores_unknown_symbols(tab):
    tab.set_last_values({"NOPE": "1.00"})  # must not raise


def test_set_last_values_updates_every_row_sharing_a_symbol(root):
    """Two rows on one symbol must both update — the map holds lists."""
    catalog = (
        QuantGroup(
            key="g", name="G",
            rows=(
                QuantRow(key="a", name="A", symbol="SPY", description="d"),
                QuantRow(key="b", name="B", symbol="spy", description="d"),
            ),
        ),
    )
    widget = QuantTab(root, catalog=catalog)
    widget.set_last_values({"SPY": "763.77"})
    assert widget.tree.set("row:a", "last") == "763.77"
    assert widget.tree.set("row:b", "last") == "763.77"


# --- activation -------------------------------------------------------


def _double_click(tab: QuantTab, item: str):
    tab.tree.identify_row = lambda _y: item  # type: ignore[method-assign]
    return tab._on_double_click(SimpleNamespace(y=0))


def test_double_click_activates_an_available_row(tab):
    result = _double_click(tab, _item_for(tab, "vix"))
    assert tab.activated == ["VIX"]
    assert tab.blocked == []
    assert result == "break"


def test_double_click_on_a_ratio_row_passes_the_ratio_symbol(tab):
    _double_click(tab, _item_for(tab, "spy_em_1d"))
    assert tab.activated == ["VIX/15.87"]


def test_double_click_on_an_unavailable_row_does_not_activate(tab):
    result = _double_click(tab, _item_for(tab, "gex"))
    assert tab.activated == []
    assert [r.key for r in tab.blocked] == ["gex"]
    assert result == "break"


def test_double_click_on_a_group_node_does_nothing(tab):
    """Returning ``None`` lets Tk's own expand/collapse handler run."""
    group_item = tab.tree.get_children("")[0]
    result = _double_click(tab, group_item)
    assert tab.activated == []
    assert tab.blocked == []
    assert result is None


def test_double_click_on_empty_space_does_nothing(tab):
    result = _double_click(tab, "")
    assert tab.activated == []
    assert tab.blocked == []
    assert result is None


def test_tab_without_callbacks_is_inert(root):
    widget = QuantTab(root)
    widget.tree.identify_row = lambda _y: "row:vix"  # type: ignore[method-assign]
    assert widget._on_double_click(SimpleNamespace(y=0)) == "break"


# --- hover tooltip ----------------------------------------------------


def test_motion_over_an_unavailable_row_sets_the_reason(tab):
    tab.tree.identify_row = lambda _y: _item_for(tab, "dix")  # type: ignore[method-assign]
    tab._on_motion(SimpleNamespace(y=0))
    assert tab._tooltip._text
    assert "data source" in tab._tooltip._text


def test_motion_over_an_available_row_clears_the_tooltip(tab):
    tab.tree.identify_row = lambda _y: _item_for(tab, "dix")  # type: ignore[method-assign]
    tab._on_motion(SimpleNamespace(y=0))
    tab.tree.identify_row = lambda _y: _item_for(tab, "vix")  # type: ignore[method-assign]
    tab._on_motion(SimpleNamespace(y=1))
    assert tab._tooltip._text == ""


def test_motion_within_the_same_row_is_a_no_op(tab):
    tab.tree.identify_row = lambda _y: _item_for(tab, "dix")  # type: ignore[method-assign]
    tab._on_motion(SimpleNamespace(y=0))
    first = tab._hovered_item
    tab._on_motion(SimpleNamespace(y=3))
    assert tab._hovered_item == first


# --- theming ----------------------------------------------------------


def test_apply_theme_sets_tag_foregrounds(tab):
    """Tk returns a Tcl color object here, so compare on its string form."""
    tab.apply_theme(muted_fg="#7a7a7a", group_fg="#d0d0d0")
    assert str(tab.tree.tag_configure(TAG_UNAVAILABLE, "foreground")) == "#7a7a7a"
    assert str(tab.tree.tag_configure(TAG_GROUP, "foreground")) == "#d0d0d0"
