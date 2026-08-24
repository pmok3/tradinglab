# gui/quant_tab.py — Spec

## Purpose
Renders the **Quant** side-notebook tab: a grouped, double-clickable list of
market-internals series drawn from `quant/catalog.py`. The tab is a launcher —
double-clicking a row loads that quantity onto the chart exactly like a
watchlist ticker — plus a reference table showing each row's symbol, latest
daily value, and one-line meaning.

## Public API
- `class QuantTab(parent, *, catalog=QUANT_CATALOG, on_row_activate=None,
  on_unavailable=None)` — a `ttk.Frame` containing the Treeview.
- `QuantTab.tree` — the underlying `ttk.Treeview` (theming + tests).
- `QuantTab.symbols() -> list[str]` — every available symbol in display order.
- `QuantTab.set_last_values(values: dict[str, str])` — write the Last column
  from `{symbol: formatted_text}`; case-insensitive, partial updates allowed.
- `QuantTab.apply_theme(*, muted_fg, group_fg=None)` — re-tag for the palette.
- `TAG_UNAVAILABLE`, `TAG_GROUP` — Treeview tag names.

## Dependencies
- Internal: `quant/catalog.py`, `gui/tooltip.py`.
- External: `tkinter`, `tkinter.ttk`.

## Design Decisions
- **Name lives in the tree column (`#0`), not a data column.** That is what
  makes the two-level hierarchy read naturally: a group node is a name with
  children. Putting Name in a data column would leave every parent row with
  three blank cells and waste the indent guides.
- **Groups are real Treeview parents, expanded by default.** Collapsing is
  free, and a user who only watches credit can fold the rest away. Tk item
  ids are prefixed (`grp:` / `row:`) because groups and rows share one id
  namespace.
- **Double-click returns `"break"`.** Without it Tk's own handler also fires
  on a group node, collapsing and instantly re-expanding it.
- **Unavailable rows are rendered, not omitted.** `GEX` / `DIX` show an
  em-dash symbol, a muted foreground, and are inert on double-click — the
  host is told via `on_unavailable` so it can explain in the status bar. A
  market-internals panel that silently dropped them would mislead.
- **One retargeted tooltip, not one per row.** Tk tooltips attach to widgets
  and a Treeview is one widget, so `<Motion>` rewrites the single tooltip's
  text and calls `ToolTip.hide()` when the cursor leaves an unavailable row.
  The hover-item guard means the work is a no-op within a row.
- **The widget never fetches.** Last values arrive through
  `set_last_values`, so the tab is testable with no executor, no network, and
  no Tk-thread worker. Fetching lives in `quant_app.py`.
- **Per-tag foregrounds are re-applied on theme flip.** `ttk.Style` reaches
  the Treeview body but not tag colours, the same gap `native_theme.py`
  covers for classic widgets (AGENTS.md §7.31).

## Invariants
- Every catalog row appears exactly once; group order and row order match
  `QUANT_CATALOG`.
- `symbols()` returns only available rows and never an empty string.
- `set_last_values` leaves untouched any symbol absent from its argument, so
  a partial refresh never blanks a resolved cell.
- A double-click on a group node or empty space calls neither callback.
- `_items_by_symbol` maps upper-cased symbols to *lists*, so a symbol used by
  two rows updates both.

## Testing
`tests/unit/gui/test_quant_tab.py` — group/row structure, symbol ordering,
Last-column writes (including case-insensitivity and partial updates),
double-click activation for available rows, inert double-click plus
`on_unavailable` for `GEX`/`DIX`, group double-click doing nothing, and tag
assignment. Smoke: `check_g5_quant_tab` in `tests/smoke/test_smoke_full.py`.

## Known limitations / Future work
- Sorting is fixed to catalog order — these are curated groups, so a
  click-to-sort header would fight the grouping.
- No per-row Change / Change% column. Last plus the description was the
  chosen surface; adding Change means a second daily-bar derivation.

## Recent history
- Initial version alongside `quant/catalog.py` and `gui/quant_app.py`.
