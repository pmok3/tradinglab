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
    def __init__(self, master, *, evaluator: EntryEvaluator,
                 storage: Any = None, exit_storage: Any = None,
                 on_chart_focus: Callable[[str], None] | None = None,
                 templates_dir: Path | None = None,
                 sandbox_intervals_provider: Callable[[], frozenset[str] | None] | None = None) -> None
    @property
    def library(self) -> tuple[EntryStrategy, ...]
    @property
    def selected_strategy_id(self) -> str | None
    def refresh(self) -> None  # full library + stats redraw
    def load_template_from_path(path: Path) -> EntryStrategy
    def _refresh_tree(self) -> None
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

- `..entries.{model, storage, evaluator}` via injected evaluator/storage objects.
- `.entries_dialog.EntriesDialog` for New/Edit/Duplicate.
- `..strategy_tester.interval_compat` for the intraday arming guard.
- `..core.thread_guard` — all mutators require Tk thread.

## Design Decisions

- **1-second `after()` tick.** The tick refreshes audit/stats and
  patches the Armed/Fires columns in place for static views. It rebuilds
  the Treeview only while the Active filter is selected, because that
  view's membership depends on live arm state; full `load_all()` happens
  in `refresh()` and explicit library actions.
- **Treeview, not Listbox.** Multiple sortable columns (Name / Dir /
  Kind / Universe / Enabled / Armed / Fires). Column widths tuned for
  ~6-character symbol lists. Selection state is single-row.
- **Mine | Active | Templates | All filter** (audit `template-filter`). A
  radio segment above the Treeview filters the library *view*; it
  **defaults to "All" on every construction** (session-only
  `tk.StringVar`, NOT persisted) so the ~21 bundled starter templates
  seeded into the library on first run are visible alongside the user's
  own strategies (switch to "Mine" to declutter). A strategy is a
  *bundled template* iff its `id` starts with `tmpl-` (`_is_template`) —
  NOT `created_with.template`, because a copy made via "Load template…" /
  Duplicate gets a fresh UUID id and belongs under "Mine" even though it
  is template-derived. **"Active"** shows only strategies that are BOTH
  `enabled` AND armed (`id in evaluator.armed_strategies()`) — the live
  alerts, a decluttered slice of what's actually watching the market.
  `_refresh_tree` filters rows by the active view; the segment labels
  carry live counts (`Mine (n)` / `Active (n)` / `Templates (n)` /
  `All (n)`); an empty filtered view shows a muted hint (the "Active"
  hint points the user to arm a strategy). Because the "Active" set
  depends on live arm state, `_on_tick` does a full `_refresh_tree` (not
  just an in-place Armed-column patch) while that view is selected, so a
  strategy arming/disarming adds or removes its row within one tick. The
  filter is **display-only** — `refresh()` still feeds the FULL library to
  `evaluator.set_strategies`.
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
