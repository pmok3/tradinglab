# gui/exits_tab.py — Spec

## Purpose

Right-side notebook tab for exits operations: per-position attach
panel, trigger-status Treeview, audit-tail footer, and the PANIC
two-phase confirm button.

## Layout

```
┌─ Toolbar ───────────────────────────────────────────────────────────┐
│  [Edit Strategies…] [PANIC: Flatten All] [Refresh]   ⚠ broken badge │
├─ Open positions ────────────────────────────────────────────────────┤
│  Per-open-position row: [Symbol qty entry_px] [Attach …] [Detach]   │
│    (one _AttachRow widget per position)                              │
├─────────────────────────────────────────────────────────────────────┤
│  Status Treeview: symbol | side | qty | strategy | leg | trigger    │
│                   state | current | trigger px | distance            │
├─────────────────────────────────────────────────────────────────────┤
│  Audit tail (last N records from exits audit log)                   │
└─────────────────────────────────────────────────────────────────────┘
```

## Public API

```python
class ExitsTab(ttk.Frame):
    def __init__(self, master, *, tracker: PositionTracker,
                 evaluator: ExitEvaluator,
                 audit: AuditLog | None = None,
                 on_open_dialog: Callable[[], None] | None = None) -> None
    @property
    def library(self) -> tuple[ExitStrategy, ...]
    @property
    def broken_count(self) -> int
    def refresh(self) -> None
    def attach_for_position(position_id: str, strategy_id: str) -> None
    def _build_layout(self) -> None
    def _apply_theme(theme: dict[str, str]) -> None
    def _refresh_badge(self) -> None
    def _refresh_attach_panel(self) -> None
    def _refresh_status_tree(self) -> None
    def _format_state(position_id: str, leg_id: str, trigger_id: str) -> str
    def _format_trigger_price(pos: Position, leg: Any, trig: Any) -> str
    def _format_distance(current: float | None, trig_px: str) -> str
    def _refresh_audit_tail(self) -> None
    def _on_open_dialog_clicked(self) -> None
    def _on_panic_clicked(self) -> None
    def _disarm_panic(self) -> None
    def _do_panic_flatten(self) -> None
    def attach_strategy_for(position_id: str, strategy_id: str) -> None
    def detach_strategy_for(position_id: str) -> None

class _AttachRow(ttk.Frame):
    """One row per open position; holds summary, strategy combobox,
    Attach/Detach buttons, and warning label."""
```

## Two-phase PANIC

1. First click: opens `messagebox.askyesno`. If cancelled, no state
   changes. If confirmed, button text → "PANIC: Confirm" and a
   5-second auto-disarm timer starts.
2. Second click within the timer: calls `_do_panic_flatten()`, which
   loops over open positions and invokes `evaluator.panic_flatten_position`
   followed by `evaluator.submit_market_flatten` for each position.
3. Timer expires without the second click: button reverts to
   "PANIC: Flatten All", no action.

## Dependencies

- `..exits.{audit, evaluator, model, storage}`.
- `..positions.tracker.PositionTracker`, `..positions.model.Position`.
- `.exits_dialog.open_exits_dialog` for Edit Strategies fallback.
- `.colors` for muted and warning colours.

## Design Decisions

- **Refresh is host-driven**: the tab does not schedule its own tick;
  `ChartApp` calls `refresh` after sandbox ticks or library changes.
- **Toolbar refresh is manual**: the user can also click `Refresh` to
  force the same `refresh` path.
- **Status Treeview is trigger-keyed**, not strategy-keyed. Rows use
  `(position, leg, trigger)` ids and preserve selection across refresh.
- **`_AttachRow` is diff-updated** — one widget per open position;
  `_refresh_attach_panel` adds/removes rows and calls `update` in place.
- **Detach** disarms the strategy attached to the position. It does NOT
  flatten the position itself — that's the PANIC button's job.
- **Audit-tail footer** uses `audit.tail(100)` and expands evidence
  entries into indented child lines.

## Invariants

- All callbacks run on the Tk thread.
- Two-phase PANIC timer is single-flighted (clicking twice in the
  same frame doesn't double-fire).
- Status Treeview rows correspond 1:1 with triggers in strategies
  currently attached to open positions.

## See also

- Mirror: [`entries_tab.spec.md`](entries_tab.spec.md).
- Dialog: [`exits_dialog.spec.md`](exits_dialog.spec.md).
