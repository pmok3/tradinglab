# gui/scanner_tab.py — spec

> ⚠ **Tk-coupled module** — imports `tkinter`.

## Purpose

Self-contained right-side **Scanner** notebook tab. Toolbar (+ New /
Load / Rename / Close / Delete / Import / Export) plus a nested
`ttk.Notebook` of per-scan sub-tabs ordered by the user's open order.
Each sub-tab combines a `BlockEditor` with a result `Treeview` driven
by the runner's `ScanResult` stream.

## Public API

- `class ScannerTab(ttk.Frame)`:
  - `__init__(parent, *, library: Optional[Mapping[id, ScanDefinition]] = None,
    on_scan_saved: Optional[Callable[[ScanDefinition], None]] = None,
    on_scan_deleted: Optional[Callable[[str], None]] = None,
    on_row_action: Optional[Callable[[symbol, kind], None]] = None,
    new_scan_factory: Optional[Callable[[name], ScanDefinition]] = None,
    initial_open_ids: Optional[Iterable[str]] = None)`.
  - `set_library(scans) -> None` — replaces library; preserves open
    ids that survive; auto-opens 1 if none remain and library
    non-empty.
  - `get_library() -> Dict[id, ScanDefinition]`.
  - `get_active_scan_definitions() -> List[ScanDefinition]` — what
    the runner should evaluate = currently *open* sub-tabs only.
  - `open_scan(scan_id) -> bool` — load library scan as sub-tab and
    select. False if missing or already open.
  - `close_scan(scan_id) -> bool` — unload sub-tab; scan stays in
    library + on disk. Cancels pending debounced save.
  - `set_results(results: Mapping[scan_id, ScanResult]) -> None` —
    each open sub-tab diff-updates its Treeview.
  - `current_scan_id() -> Optional[str]` — id of the selected
    sub-tab, or None when the empty-state placeholder is showing.
  - `add_scan(scan)`, `delete_scan(id)` — programmatic library
    mutators (also callable from the toolbar).

## Library vs open tabs

Library (saved scans on disk) is decoupled from open sub-tabs so a
50-scan user doesn't get 50 sub-tabs (and 50 runner evaluations) at
startup.

- `_library: Dict[id, ScanDefinition]` — all on-disk scans.
- `_open_ids: List[str]` — ordered scan ids shown as sub-tabs.
  Runner evaluates only these.

### Default startup behavior

If `initial_open_ids` not supplied: empty library → no sub-tab
(empty-state); non-empty library → open the **single
most-recently-updated** scan (sort by `updated_at`, fallback
`created_at`, then `name`). `initial_open_ids` is an opt-in for tests.

### Toolbar buttons

| button   | action                                                                          |
| -------- | ------------------------------------------------------------------------------- |
| + New    | mints a fresh blank scan (via `new_scan_factory`), adds to library, opens it    |
| Load…    | pops the `_LoadScanDialog` listing library scans not currently open             |
| Rename   | renames the current sub-tab's scan                                              |
| Close    | unloads the current sub-tab (keeps in library + on disk)                        |
| Delete   | confirms then removes from library + disk; closes the tab if open               |
| Import…  | reads JSON via `ScanDefinition.from_dict`; auto-renames on collision            |
| Export…  | writes the current scan's JSON to a user-chosen path                            |

### `_LoadScanDialog`

Modal `Toplevel` with a sorted `Listbox` of library scans not in
`_open_ids`. Double-click / Enter loads; Escape cancels. Listbox
height = `min(15, max(5, n))`. Returns chosen scan id or `None`.
The classic Tk Listbox is explicitly themed from the active palette
(`tree_bg`, `tree_fg`, `spine`) because ttk.Style does not reach it.

### Right-click context menu on sub-tab strip

`Close tab (<name>)` / `Delete scan (<name>)…`. Identified via
`notebook.index(f"@{x},{y}")` guarded by `TclError`. Empty-state
placeholder no-ops.

`tests/unit/gui/test_native_widget_dark_theme.py` pins `_LoadScanDialog`'s Listbox dark-mode colors.
### `set_library` semantics

Replaces `_library`; filters `_open_ids` to surviving ids
(vanished scans auto-closed); if empty after filter AND library
non-empty, re-applies the most-recent-1 auto-open rule.

### `add_scan(scan)` / `delete_scan(scan_id)`

- `add_scan`: add to library, append to `_open_ids` if new,
  rebuild, select new tab, fire `on_scan_saved`.
- `delete_scan`: remove from library AND `_open_ids`, rebuild,
  fire `on_scan_deleted` (host routes to `storage.delete` +
  `runner.reset_history`).

## Per-sub-tab layout (`_ScanSubTab`)

1. **Header row**: `rank_by` preset combo, `▼/▲` rank-direction
   radios, primary-interval combo, "Show insufficient" checkbox.
2. **`Conditions` LabelFrame** with leaf-count summary label and
   `Edit conditions…` button. The button shows a modeless
   `Toplevel` (created at sub-tab init, withdrawn until first
   open, default 900×600, min 640×360) hosting the `BlockEditor`.
   Close hides via `withdraw` rather than destroys so widget state
   + `self._editor` refs remain valid; `destroy()` tears it down
   explicitly. Summary refreshes via `_refresh_cond_summary` on
   every editor `on_change`.
3. **View row**: `New` / `Active` radios + status label.
4. **Treeview**: `Symbol | Match | Rank | Tick | Time`. `iid =
   symbol` so selection persists across diff-update ticks.
   `selectmode = browse`. `Symbol` anchors west; `Match`,
   `Rank`, `Tick`, `Time` anchor center.

## Rank presets

The preset list is the union of:

1. A **curated** head of common ranks (kept stable so muscle memory
   doesn't break across releases):

   ```python
   _CURATED_RANK_PRESETS = (
       ("(none)", None),
       ("RVOL (cumulative)", FieldRef.indicator("rvol", params={"mode": "cumulative"})),
       ("RVOL (rolling)",    FieldRef.indicator("rvol", params={"mode": "simple"})),
       ("Volume",            FieldRef.builtin("volume")),
       ("Close",             FieldRef.builtin("close")),
       ("ATR(14)",           FieldRef.indicator("atr", params={"length": 14})),
       ("RSI(14)",           FieldRef.indicator("rsi", params={"length": 14})),
   )
   ```

2. **Every** scannable builtin / indicator returned by
   `tradinglab.scanner.fields.all_fields()` — projected via
   `_build_rank_presets()` with one preset per builtin and one
   preset per `(indicator, output_key)` pair. Indicator params come
   from each `ParamDef.default`. Items already represented in the
   curated head are skipped so the picker has no duplicates.

A `FieldRef` not matching any preset shows as `"custom"` and is
left untouched (editable via JSON export/import). Multi-output
indicators (Bollinger / MACD / ADX / SMI) compare `output_key` in
the reverse-lookup so an upper-band rank doesn't shadow-match the
middle / default preset.

Audit ID: `scanner-rank-presets-all-indicators`. Per the user's
request 2026-05-21, the picker exposes **every** registered
indicator (not just a curated subset) so a strategy author can
rank a candidate list by any indicator output already wired into
the scan-fields registry without leaving the dialog.

## Diff-based Treeview update

`_refresh_tree` does NOT clear-and-reinsert:

1. Snapshot current selection by symbol.
2. Compute target ordering (filtered by view + sorted, capped at
   `_MAX_VISIBLE_ROWS = 500`).
3. For each target row: update existing iid or insert with
   `iid=symbol`.
4. Delete iids that fell out.
5. `tree.move(iid, "", new_index)` to enforce ordering.
6. Restore selection (filtered to present symbols).

Cost O(visible rows) per tick. Selection persists across ticks
because iid keys on `symbol`.

## Sort

- Default: `rank` desc.
- Click header → toggle direction (or pick that column with a
  sensible default: `rank`/`tick` desc, `symbol`/`match` asc).
- Missing values (None) sort to bottom regardless of direction —
  same convention as watchlist tab.

## Save callback debouncing

`_on_subtab_change` (every `BlockEditor.on_change` fire) is
debounced 250 ms per scan id (replacing any pending one).

## Row interactions

`on_row_action(symbol, kind)`:

- **Double-click** → `kind = "primary"`. Falls back to current
  selection when the click misses a row.
- **Right-click** → context menu: `Set as primary` / `Add to
  compare` / `Add to watchlist` (passes
  `"primary"`/`"compare"`/`"watchlist"`).

Host wiring: see `app.spec.md` §"Scanner tab integration".

## Import / export

- **Export**: `scan.to_dict()` as pretty JSON to chosen path
  (filename pre-filled to `<scan.name>.json`).
- **Import**: parses via `ScanDefinition.from_dict`; invalid JSON
  or schema-too-new shows error and bails. Name collision (diff
  id) prompts for unique name via `simpledialog.askstring`.
  Source id preserved.

## Empty state

When `_open_ids` is empty (regardless of library size), a single
`(empty)` placeholder sub-tab with text "No scans yet. Click +
New to create one." Removed as soon as any sub-tab is opened.

## What we *don't* do here

- Run the scanner — `runner.ScanRunner` does that.
- Persist — host's `on_scan_saved`/`on_scan_deleted` callbacks.
- Semantic validation — `engine.validate_scan`; the tab will
  happily build a malformed scan; next tick surfaces error as
  `MatchRow(matched=None, error=…)`.

## See also

- [scanner_block_editor](scanner_block_editor.spec.md),
  [scanner/model](../scanner/model.spec.md),
  [scanner/runner](../scanner/runner.spec.md).
- App wiring: [`app.spec.md`](../app.spec.md) §"Scanner tab integration".
