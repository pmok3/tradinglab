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
- **Toolbar Arm/Disarm guards.** Disabled when no row selected or
  the selected row is already in the requested state.
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
- Library Treeview rows correspond 1:1 with `evaluator.all_strategies()`
  after each `refresh()`.
- `Armed` column reflects `evaluator.is_armed(strategy_id)` at the
  moment of refresh.

## Testing

`tests/gui/test_entries_tab.py` — Treeview population, toolbar
arm/disarm flow, audit-tail rendering.

## See also

- Mirror: [`exits_tab.spec.md`](exits_tab.spec.md).
- Editor: [`entries_dialog.spec.md`](entries_dialog.spec.md).
