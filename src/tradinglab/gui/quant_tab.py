"""The **Quant** side-notebook tab — a launcher for market-internals series.

Renders :data:`tradinglab.quant.catalog.QUANT_CATALOG` as a two-level
Treeview: catalog groups are collapsible parent nodes, rows are their
children. Double-clicking a row hands its symbol to the host app, which
loads it onto the chart exactly like a watchlist double-click.

Layout notes that are easy to get wrong:

- The **Name** lives in the tree column (``#0``), not in a data column.
  That is what makes the group hierarchy read naturally — a group node is
  just a name with children — and it keeps the indent guides meaningful
  instead of wasting a column on blank parent cells.
- Rows with no data source (``GEX``, ``DIX``) render greyed via the
  ``unavailable`` tag and are inert on double-click. They are still listed:
  a market-internals panel that silently omits them would be misleading.
- One :class:`~tradinglab.gui.tooltip.ToolTip` is retargeted on ``<Motion>``
  rather than one tooltip per row, since Tk tooltips attach to widgets and a
  Treeview is a single widget. Hovering an unavailable row explains why it
  is inert; hovering anything else hides the popup.

The widget owns no fetching. The host app pushes formatted Last values in
through :meth:`QuantTab.set_last_values`, so the tab stays testable without
Tk-thread workers or a network.
"""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from ..quant.catalog import (
    QUANT_CATALOG,
    UNAVAILABLE_SYMBOL_TEXT,
    QuantGroup,
    QuantRow,
)
from .tooltip import ToolTip

#: Treeview tag applied to rows with no data source.
TAG_UNAVAILABLE = "unavailable"
#: Treeview tag applied to group parent nodes.
TAG_GROUP = "group"

#: ``item id`` prefixes. Group and row ids share one namespace in Tk, so the
#: prefixes keep them apart and let ``_row_for_item`` stay a dict lookup.
_GROUP_PREFIX = "grp:"
_ROW_PREFIX = "row:"


class QuantTab(ttk.Frame):
    """Treeview of market-internals rows, grouped and double-clickable.

    ``on_row_activate(symbol)`` fires on a double-click of an available
    row. ``on_unavailable(row)`` fires instead for a row with no data
    source, so the host can explain itself in the status bar.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        catalog: tuple[QuantGroup, ...] = QUANT_CATALOG,
        on_row_activate: Callable[[str], None] | None = None,
        on_unavailable: Callable[[QuantRow], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._on_row_activate = on_row_activate
        self._on_unavailable = on_unavailable
        #: item id -> QuantRow, for row nodes only.
        self._rows_by_item: dict[str, QuantRow] = {}
        #: symbol (upper) -> list of item ids, since a symbol may repeat.
        self._items_by_symbol: dict[str, list[str]] = {}
        self._hovered_item: str | None = None

        self._tree = ttk.Treeview(
            self,
            columns=("symbol", "last", "description"),
            show="tree headings",
            height=20,
        )
        self._tree.heading("#0", text="Name")
        self._tree.heading("symbol", text="Symbol")
        self._tree.heading("last", text="Last")
        self._tree.heading("description", text="What it tells you")
        self._tree.column("#0", width=190, minwidth=120, stretch=False)
        self._tree.column("symbol", width=90, minwidth=60, anchor="w",
                          stretch=False)
        self._tree.column("last", width=80, minwidth=60, anchor="e",
                          stretch=False)
        self._tree.column("description", width=360, minwidth=140, anchor="w")

        vsb = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.bind("<Motion>", self._on_motion)
        self._tree.bind("<Leave>", self._on_leave)

        self._tooltip = ToolTip(self._tree, "")
        self._populate()

    # --- public ---------------------------------------------------------

    @property
    def tree(self) -> ttk.Treeview:
        """The underlying Treeview (tests + theming reach through this)."""
        return self._tree

    def symbols(self) -> list[str]:
        """Every available symbol currently rendered, in display order."""
        return [
            self._rows_by_item[i].symbol
            for i in self._ordered_row_items()
            if self._rows_by_item[i].available
        ]

    def set_last_values(self, values: dict[str, str]) -> None:
        """Write the Last column from ``{symbol: formatted_text}``.

        Lookup is case-insensitive. A symbol missing from ``values`` leaves
        its existing cell alone, so a partial refresh never blanks rows that
        already resolved.
        """
        normalised = {str(k).strip().upper(): v for k, v in values.items()}
        for symbol, items in self._items_by_symbol.items():
            text = normalised.get(symbol)
            if text is None:
                continue
            for item in items:
                try:
                    self._tree.set(item, "last", text)
                except tk.TclError:
                    pass

    def apply_theme(self, *, muted_fg: str, group_fg: str | None = None) -> None:
        """Re-tag rows for the active theme.

        ``ttk.Style`` reaches the Treeview body, but per-tag foregrounds are
        widget state and must be re-applied whenever the palette flips —
        otherwise a dark-mode disabled row keeps a light-mode grey.
        """
        try:
            self._tree.tag_configure(TAG_UNAVAILABLE, foreground=muted_fg)
            if group_fg:
                self._tree.tag_configure(TAG_GROUP, foreground=group_fg)
        except tk.TclError:
            pass

    # --- construction ---------------------------------------------------

    def _populate(self) -> None:
        for group in self._catalog:
            gid = self._tree.insert(
                "", "end", iid=f"{_GROUP_PREFIX}{group.key}",
                text=group.name, open=True, tags=(TAG_GROUP,),
            )
            for row in group.rows:
                tags: tuple[str, ...] = () if row.available else (TAG_UNAVAILABLE,)
                symbol_text = (
                    row.symbol if row.available else UNAVAILABLE_SYMBOL_TEXT
                )
                item = self._tree.insert(
                    gid, "end", iid=f"{_ROW_PREFIX}{row.key}",
                    text=row.name,
                    values=(symbol_text, "", row.description),
                    tags=tags,
                )
                self._rows_by_item[item] = row
                if row.available:
                    self._items_by_symbol.setdefault(
                        row.symbol.upper(), []).append(item)

    def _ordered_row_items(self) -> list[str]:
        out: list[str] = []
        for gid in self._tree.get_children(""):
            out.extend(self._tree.get_children(gid))
        return out

    # --- events ---------------------------------------------------------

    def _row_for_item(self, item: str | None) -> QuantRow | None:
        if not item:
            return None
        return self._rows_by_item.get(item)

    def _on_double_click(self, event: tk.Event) -> str | None:
        """Load the double-clicked row, or explain why it can't be loaded.

        Returns ``"break"`` on a group node so Tk's own double-click
        handler doesn't also toggle the node — a double-click on a group
        would otherwise collapse and immediately re-expand it.
        """
        item = self._tree.identify_row(event.y)
        row = self._row_for_item(item)
        if row is None:
            return None
        if not row.available:
            if self._on_unavailable is not None:
                self._on_unavailable(row)
            return "break"
        if self._on_row_activate is not None:
            self._on_row_activate(row.symbol)
        return "break"

    def _on_motion(self, event: tk.Event) -> None:
        item = self._tree.identify_row(event.y)
        if item == self._hovered_item:
            return
        self._hovered_item = item
        row = self._row_for_item(item)
        if row is not None and not row.available:
            self._tooltip.set_text(row.unavailable_reason)
        else:
            self._tooltip.set_text("")
            self._tooltip.hide()

    def _on_leave(self, _event: tk.Event | None = None) -> None:
        self._hovered_item = None
        self._tooltip.set_text("")


__all__ = ["TAG_GROUP", "TAG_UNAVAILABLE", "QuantTab"]
