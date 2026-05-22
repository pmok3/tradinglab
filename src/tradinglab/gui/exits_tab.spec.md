# gui/exits_tab.py — Spec

## Purpose

Right-side notebook tab for exits operations: per-position attach
panel, status Treeview of armed legs / pending orders, audit-tail
footer, and the PANIC two-phase confirm button.

## Layout

```
┌─ Attach ────────────────────────────────────────────────────────────┐
│  Per-open-position row: [Symbol qty entry_px] [Attach …] [Detach]   │
│    (one _AttachRow widget per position)                              │
├─────────────────────────────────────────────────────────────────────┤
│  Status Treeview: position | leg | trigger-kind | armed-at | state  │
│                   ──────────────────────────────────────────         │
├─────────────────────────────────────────────────────────────────────┤
│  [PANIC]   ← red, two-phase: first click arms, second flattens      │
├─────────────────────────────────────────────────────────────────────┤
│  Audit tail (last N records from exits audit log)                   │
└─────────────────────────────────────────────────────────────────────┘
```

## Public API

```python
class ExitsTab(ttk.Frame):
    def __init__(self, master, *, app: "ChartApp") -> None
    def refresh(self) -> None
    def _refresh_attach_rows(self) -> None
    def _refresh_status_tree(self) -> None
    def _refresh_audit_tail(self) -> None
    def _on_panic_click(self) -> None  # two-phase
    def _on_panic_timeout(self) -> None
    def _on_position_event(self, event: PositionEvent) -> None

class _AttachRow(ttk.Frame):
    """One row per open position; holds Attach/Detach buttons + a
    summary label."""
```

## Two-phase PANIC

1. First click: button text → "Confirm PANIC", red background, 5-second
   timer starts.
2. Second click within the timer: invokes
   `app._on_panic_flatten()` — cancels all working orders, then
   market-flattens every open position. Audited via
   `panic_flatten_request` and `panic_flatten_complete`.
3. Timer expires without confirm: button reverts to "PANIC", no action.

## Dependencies

- `..exits.{audit, evaluator}` via `self._app`.
- `..core.positions.PositionTracker`.
- `.exits_dialog.open_exits_dialog` for Attach.

## Design Decisions

- **1-second `after()` refresh** for attach panel + status tree; the
  audit tail piggy-backs on the same tick. Cheap reads.
- **Status Treeview is leg-keyed**, not strategy-keyed. A single
  bracket strategy can show as 2 rows (target / stop).
- **`_AttachRow` is a small widget class** — one per open position.
  Rebuilt on every `_refresh_attach_rows` (cheap; O(open-positions)).
- **Detach** disarms every leg attached to the position and cancels
  the position's pending exit orders. It does NOT flatten the
  position itself — that's the PANIC button's job.
- **Audit-tail footer** uses `audit_log.tail(20)`; one line per record,
  truncated.

## Invariants

- All callbacks run on the Tk thread.
- Two-phase PANIC timer is single-flighted (clicking twice in the
  same frame doesn't double-fire).
- Status Treeview rows correspond 1:1 with armed legs known to
  `_exit_evaluator.attached_legs()`.

## See also

- Mirror: [`entries_tab.spec.md`](entries_tab.spec.md).
- Dialog: [`exits_dialog.spec.md`](exits_dialog.spec.md).
