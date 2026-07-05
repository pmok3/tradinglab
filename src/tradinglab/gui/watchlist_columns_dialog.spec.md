# gui/watchlist_columns_dialog.py — Spec

## Purpose
The per-watchlist **"Columns…" dialog** — the primary surface for
choosing which signal columns a watchlist shows. A right pane (reusing
the scanner
[`_FieldRefPicker`](scanner_block_editor.spec.md) for field + params,
plus a per-column interval + display-format) and an active-columns list
(left) with reorder / rename / remove / format, applied to one
watchlist. The tab's sub-tab right-click "Columns…" entry opens it. See
[`docs/WATCHLIST_COLUMNS.md`](../../../docs/WATCHLIST_COLUMNS.md).

## Public API
- `class WatchlistColumnsDialog(BaseModalDialog)`:
  - `__init__(parent, *, watchlist_name, columns, on_apply, **kwargs)` —
    build the editor for `watchlist_name`; `on_apply(ordered_columns)`
    fires on Apply with the validated list.
- `open_columns_dialog(app, watchlist_name) -> WatchlistColumnsDialog | None`
  — menu / header-menu entry point.

## Dependencies
- Internal: [`watchlists/columns`](../watchlists/columns.spec.md)
  (`WatchlistColumn`, `validate_columns`, `header_label`),
  [`gui/_modal_base`](_modal_base.spec.md) (`BaseModalDialog`,
  `protect_combobox_wheel`),
  [`gui/scanner_block_editor`](scanner_block_editor.spec.md)
  (`_FieldRefPicker` reuse), [`gui/native_theme`](native_theme.spec.md).
- External: `tkinter` / `tkinter.ttk`.

## Design Decisions
- **Dialog primary, header-menu secondary** (decision UI/UX). The dialog
  owns add / remove / reorder / edit / rename / format; a right-click
  column header offers quick Sort / Add / Edit / Remove / Move / Columns….
- **Reuse the scanner field-picker.** `_FieldRefPicker` already handles
  Built-in vs Indicator, params, and interval — wrap it in a
  watchlist-column editor rather than a scanner condition builder (the
  user is choosing a *value column*, not authoring a condition). v1 hides
  the cross-symbol `@` pin (relative columns are v2).
- **`ticker` locked + first.** Shown non-removable; `validate_columns`
  enforces it on Apply.
- **Combobox-wheel guard** (CLAUDE.md §7.11) applied after every partial
  rebuild; **dark-mode theming** via `gui/native_theme` for the classic
  Tk list widgets.
- **Drag-to-reorder deferred** — reorder via ↑/↓ + header "Move left/right".
- **Per-watchlist scope** with "copy from…" / "set as default" / "reset"
  affordances (global default + per-watchlist overrides).

## Invariants
- Apply returns a `validate_columns`-clean list (`ticker` first + locked,
  deduped).
- The picker round-trips a signal column's field / params / interval.
- Cancel leaves the watchlist's columns unchanged.

## Data Flow / Algorithm
```text
open_columns_dialog(app, name):
  cols = app._watchlists.columns_for(name)         # or default_columns()
  dlg  = WatchlistColumnsDialog(app, watchlist_name=name, columns=cols,
                                on_apply=_apply)
  _apply(cols): manager.set_columns(name, cols) → row-cache drop →
                _rebuild_watchlist_subtabs() → _preload_watchlist_signals()
OK → validate_columns → on_apply(cols); Cancel → result=None, no change
```

## Testing
- `tests/unit/gui/test_watchlist_columns_dialog.py` — Tk/Agg:
  lists scanner fields (built-in + indicator); picker preserves params /
  interval; add / remove / reorder; `ticker` not removable; Apply returns
  the ordered validated list; combobox-wheel guard applied.

## Known limitations / Future work
- v1 hides the cross-symbol `@` pin (relative / RS columns are v2). No
  per-column color-rule editor (per-cell heat is deferred). Drag-reorder
  and "add column from saved scan" are v2.

## Recent history
- **Implemented** — two-pane editor (active-columns list + reuse of
  `_FieldRefPicker` for adding signal columns with per-column interval +
  format); ↑/↓ reorder, remove, rename, live format combo, quick re-add of
  system columns, reset-to-defaults. `ticker` locked first. Wheel-guard
  re-applied after every picker rebuild (via the picker `on_change`), dark
  Listbox theming. `open_columns_dialog` persists via `set_columns` +
  rebuilds the sub-tab; wired into the tab sub-tab right-click "Columns…".
- **API skeleton** — class + entry point defined; bodies raised
  `NotImplementedError`. Encoded the v1 decisions in
  `docs/WATCHLIST_COLUMNS.md`.
