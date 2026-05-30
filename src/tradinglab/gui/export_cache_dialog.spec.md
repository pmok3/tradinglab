# gui/export_cache_dialog.py — Spec

## Purpose
The dialog opened by **Tools → Export Bars to CSV…**. Enumerates every
`(source, ticker, interval)` tuple currently in the disk cache and
writes the user's selected subset to a single zip archive
(`<SOURCE>/<TICKER>_<INTERVAL>.csv` as arcname) in the strict canonical
schema. Symmetric companion to `gui/local_data_dialog.py` — unzip the
archive and drop the resulting folder into Configure Local Data to
load back the same bars on another machine. Audit `local-export-zip`.

## Public API
- `ExportCacheDialog(parent)` — `BaseModalDialog` modal to `parent`.
  Loads the cache index at construction time and guards any combobox /
  spinbox descendants with `protect_combobox_wheel`.
- `open_export_cache_dialog(parent) -> ExportCacheDialog` — convenience
  opener used by the Tools menu callback.

## Module-level helpers
- `_load_cache_index() -> list[(source, ticker, interval)]` — thin
  wrapper around `tradinglab.disk_cache.list_entries()`. Extracted so
  unit tests can stub it without touching the real cache.
- `_load_cache_candles(source, ticker, interval) -> list[Candle] | None`
  — thin wrapper around `tradinglab.disk_cache.load(...)`. Also a stub
  seam for tests.

## Dependencies
- Internal: `tradinglab.disk_cache.list_entries`, `tradinglab.disk_cache.load`,
  `tradinglab.data.local_export.export_entries_zip`,
  `tradinglab.data.local_export.default_zip_filename`,
  `._modal_base.BaseModalDialog`,
  `._modal_base.protect_combobox_wheel`, `.colors.MUTED_GREY`.
- External: `tkinter`, `tkinter.ttk`, `tkinter.filedialog`,
  `tkinter.messagebox`.

## Design Decisions
- **All entries checked by default**. The most common workflow is
  "export everything so I can share my cache with another machine". The
  user expressly asked for an all-on default with un-check to skip;
  the Select None button is provided for the rare "I just want this
  one symbol" case.
- **Treeview with ☑/☐ checkbox glyph in column #1**. ttk lacks a native
  checked-tree widget; emulated via unicode glyph + click-to-toggle on
  the first column. Clicks outside the first column don't toggle, so
  the user can drag-scroll horizontally without flipping state.
- **Destination chosen via `filedialog.asksaveasfilename`**. Required
  before Export; if missing, the status row reports the error rather
  than silently failing. The default filename comes from
  `default_zip_filename()`, and a missing `.zip` suffix is added.
  Status row also reports "Nothing selected" when the user has clicked
  Select None.
- **Lazy candle loading**. The Treeview only stores `(source, ticker,
  interval)` triples — the bars themselves are not loaded into memory
  until the user clicks Export. Then `_iter()` materialises one entry
  at a time so memory stays bounded regardless of cache size.
- **`export_entries_zip` is the single bottleneck**. Per-entry failures
  don't abort the batch; the dialog tallies successes and surfaces
  up to 5 sample errors in the final messagebox.
- **Empty cache → friendly message + Close-only**. Refuses to open a
  blank Treeview; the user gets an explanation and a single Close
  button.
- **Zip write via `local_export.export_entries_zip`**. The dialog itself
  only chooses the destination and loads cache entries; archive layout
  and CSV serialization go through the same vetted exporter as the
  programmatic export API.

## Invariants
- Each row in the Treeview corresponds 1-to-1 with a key in `_selected`.
- The Treeview row iid is `f"{source}__{ticker}__{interval}"` (matches
  the `_key()` helper) and is used as the `_selected` lookup key.
- After Export, every entry with `_selected[key] == True` either lands
  in the destination zip at the expected member path OR appears in the
  per-entry results list with a non-None error message.
- Cancel never writes to disk.

## Testing
`tests/unit/gui/test_export_cache_dialog.py` — 10 tests covering:
- Default state: empty cache renders a friendly message; populated
  cache initialises `_selected` to all-True.
- Select All / Select None toggles flip every key in `_selected`.
- Export gating: refuses with "destination" message when destination
  is None; refuses with "nothing" message when no selection.
- End-to-end: selected entries land inside the destination zip as
  `<source>/<TICKER>_<INTERVAL>.csv`; unselected entries do NOT land.

## Known limitations
- **No incremental progress bar**. For a very large export the dialog
  appears unresponsive during the write; the final messagebox is the
  only feedback. A future progress widget is reserved.
- **No per-source / per-ticker filter UI**. Users with thousands of
  entries must scroll. A search/filter row is a future enhancement.
- **No "open destination in file manager" affordance after success**.
  The final messagebox names the destination but doesn't link to it.
