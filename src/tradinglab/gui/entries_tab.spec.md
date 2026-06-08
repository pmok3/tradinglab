# gui/entries_tab.py — Spec

## Purpose

Right-side notebook tab dedicated to entry-strategies management:
library list, arm/disarm controls, audit-tail footer, live stats.
Mirrors `ExitsTab` structurally; differs in that entries are
NOT bound to open positions (universe-driven).

## Layout

```
┌─ Library ─────────────────────────────────┬─ Toolbar ──────────────┐
│  Treeview: Name | Dir | Kind | Universe | │  [New] [Edit] [Delete] │
│            Enabled | Armed | Fires        │  [Duplicate] [Import]  │
│            ───────────────────────────    │  [Export] [Load tmpl…] │
│                                            │  ───────────────────── │
│                                            │  [Arm] [Disarm]        │
│                                            │  [Disarm All]          │
├────────────────────────────────────────────┴────────────────────────┤
│  Audit tail: last N entries from the entries audit log              │
├──────────────────────────────────────────────────────────────────────┤
│  Stats: fires / blocked / cooldowns / dedup / errors / bars         │
└──────────────────────────────────────────────────────────────────────┘
```

## Public API

```python
class EntriesTab(ttk.Frame):
    def __init__(self, master, *, app: "ChartApp") -> None
    def refresh(self) -> None  # full library + stats redraw
    def _refresh_audit_tail(self) -> None
    def _refresh_stats(self) -> None
    def _on_new(self) -> None
    def _on_edit(self) -> None
    def _on_delete(self) -> None
    def _on_duplicate(self) -> None
    def _on_import(self) -> None
    def _on_export(self) -> None
    def _on_arm(self) -> None
    def _on_disarm(self) -> None
    def _on_disarm_all(self) -> None
    def _on_load_template(self) -> None
```

## Dependencies

- `..entries.{model, storage, evaluator}` via `self._app`.
- `.entries_dialog.EntriesDialog` for New/Edit/Duplicate.
- `..core.thread_guard` — all mutators require Tk thread.

## Design Decisions

- **1-second `after()` refresh.** Cheap; the library + audit-tail
  read are O(N strategies + 20 audit lines). Heavy work
  (`load_all`) happens only on explicit toolbar actions.
- **Treeview, not Listbox.** Multiple sortable columns (Name / Dir /
  Kind / Universe / Enabled / Armed / Fires). Column widths tuned for
  ~6-character symbol lists. Selection state is single-row.
- **Mine | Templates | All filter** (audit `template-filter`). A radio
  segment above the Treeview filters the library *view*; it **defaults
  to "Mine" on every construction** (session-only `tk.StringVar`, NOT
  persisted) so the working list opens decluttered rather than buried
  under the ~21 bundled starter templates seeded into the library on
  first run. A strategy is a *bundled template* iff its `id` starts with
  `tmpl-` (`_is_template`) — NOT `created_with.template`, because a copy
  made via "Load template…" / Duplicate gets a fresh UUID id and belongs
  under "Mine" even though it is template-derived. `_refresh_tree`
  filters rows by the active view; the segment labels carry live counts
  (`Mine (n)` / `Templates (n)` / `All (n)`); an empty filtered view
  shows a muted hint. The filter is **display-only** — `refresh()` still
  feeds the FULL library to `evaluator.set_strategies`.
- **Toolbar Arm/Disarm guards.** Disabled when no row selected or
  the selected row is already in the requested state.
- **Intraday-interval arm guard.** `_on_arm` calls
  `strategy_tester.interval_compat.incompatible_arming_problems(strategy,
  available_intervals=…, fallback_interval=evaluator._default_interval)`
  BEFORE `evaluator.arm(sid)`. A non-empty result raises a
  `messagebox.showerror` and skips the arm — refusing to arm a strategy that
  can never fire: an intraday-only indicator (VWAP, RVOL cumulative, Prior-Day
  H/L) at a non-intraday condition interval, or (in a sandbox) a condition tree
  needing finer bars than the session serves. `available_intervals` comes from
  the optional `sandbox_intervals_provider` ctor callback (`None` ⇒ live, only
  the indicator check applies; a frozenset ⇒ the active sandbox's intervals).
  MARKET entries (no condition tree) are never blocked. Audit
  `intraday-interval-guard`.
- **Stats panel is read-only.** Hooks `evaluator.stats()` —
  numbers are display-only; resetting a counter requires
  `reset_session()` via a future menu item.
- **Audit-tail footer** shows last N (default 20) records via
  `audit_log.tail(N)`. Each row is `<ts> <kind> <symbol/strategy>
  <meta-blurb>` so it fits on one line in a Treeview row.
- **Import/Export** routes through `storage.import_from_path` /
  `storage.export_to_path` (with file dialog).
- **Dark-mode non-ttk chrome.** `_apply_theme(theme)` repaints the
  audit-tail and stats `tk.Text` panes, including the focus/highlight
  ring (`highlightbackground` / `highlightcolor`) plus flat/no-border
  chrome, because ttk styles do not reach classic `Text` widgets.
  Audit `watchlist-entries-full-dark`.

## Invariants

- All callbacks run on the Tk thread.
- Under the **All** filter, Library Treeview rows correspond 1:1 with the
  loaded library after each `refresh()`. **Mine** hides bundled-template
  (`tmpl-` id) rows; **Templates** shows only them. The view filter is
  display-only — the evaluator is always fed the full library.
- `Armed` column reflects `evaluator.is_armed(strategy_id)` at the
  moment of refresh.

## Testing

`tests/gui/test_entries_tab.py` — Treeview population, toolbar
arm/disarm flow, audit-tail rendering.

## See also

- Mirror: [`exits_tab.spec.md`](exits_tab.spec.md).
- Editor: [`entries_dialog.spec.md`](entries_dialog.spec.md).
