# positions/spec.md — package design notes

## Purpose

Single source of truth for paper / sandbox open positions during a session.
Owned by the Tk main thread; persisted to `<cache_dir>/positions/` so a
manual paper position survives an app restart.

## Files

| File | Role |
| --- | --- |
| `__init__.py` | Public exports: `Position`, `PositionEvent`, `PositionEventKind`, `PositionSide`, `PositionTracker`, `Subscriber`. |
| `model.py` | Pure dataclasses. `Position` is mutable (tracker mutates in place); `PositionEvent` is frozen. |
| `tracker.py` | `PositionTracker` registry + subscriber dispatch with re-entrancy queue. |
| `storage.py` | Atomic JSON persistence for open positions + trail state. |

## Threading

Every public mutator on `PositionTracker` is decorated with
`tradinglab.core.thread_guard.require_tk_thread`. Stream-source / worker
threads must marshal via `app._stream_queue` → `_drain_stream_queue` and
the Tk main thread is the sole writer of position state.

Tests bypass the check via `tk_thread_check_disabled()` context manager.

## Subscriber re-entrancy

Subscribers receive events via a **per-tracker event queue**:

1. Mutators append `(event, position)` to `self._pending_events`, then
   call `_drain()`.
2. `_drain()` iterates a frozen tuple snapshot of `self._subscribers` for
   each event. If a subscriber's callback calls back into a mutator, the
   nested mutator appends to the queue and returns immediately — the
   outer `_drain()` keeps consuming until empty.
3. The `_dispatching: bool` flag prevents nested `_drain()` re-entry.

This guarantees:
- No list-mutation-during-iteration crashes.
- Stable per-event subscriber order.
- Nested events fire in emit-order after the outer event resolves.

## Position lifecycle

```
open(symbol, side, qty, price, source) -> Position(qty_open=qty)
mark(symbol, price)                    -> updates last_price + watermarks (per open position)
apply_fill(position_id, qty, price)    -> partial_close OR close (auto-detaches strategy on full close)
bind_strategy / unbind_strategy
edit (manual paper only)
```

`apply_fill` interprets the fill as a CLOSE in the position's natural
direction (longs sell, shorts buy). v1 has no entry support, so scaling
INTO a position is out-of-scope.

`unrealized_pnl()` is signed by side: long profit if `last > entry`, short
profit if `entry > last`.

## Persistence

`save_open_positions(list[Position])` → atomic write to `open.json`.
`load_open_positions()` is lenient: returns `[]` on any failure.

`save_trail_state(blob)` / `load_trail_state()` is an opaque dict the exit
evaluator uses for its `_TriggerState` snapshots. Stored separately so
position state and evaluator state can evolve independently.

Schema version 1; future migrations add a `migrate()` function and bump
the constant.

## Out of scope (explicit)

- Position-level audit log: that lives in `exits/audit.py`. The tracker
  emits domain events; persistence of the event stream is the audit log's
  job.
- Cross-broker reconciliation: manual paper positions are the user's
  responsibility to keep in sync with their broker; `edit()` exists for
  this.
- Position metrics / R-multiples: trader-agent recommended tagging
  positions with their initial-stop distance for R calculations. Out of
  scope for v1; `Position.extra` is the forward-compat slot.
