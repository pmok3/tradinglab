# positions/tracker.py — Spec

## Purpose
Tk-thread-owned registry of open positions plus a re-entrancy-safe subscriber dispatch. Every mutation appends a `PositionEvent` to a per-tracker queue; the queue is drained synchronously after the mutator returns, so subscribers that call back into another mutator can't crash on list-mutation-during-iter and don't reorder events.

The tracker is the single source of truth for manual / sandbox open positions during a session. The exit evaluator, audit log, sandbox positions Treeview, and chart overlay subscribe; mutators come from sandbox fills, manual paper opens, and the entries-v1 paper engine.

## Public API
- `Subscriber = Callable[[PositionEvent, Position], None]`.
- `class PositionTracker`:
  - **Queries** (any thread): `get(position_id) -> Optional[Position]`, `list_open() -> List[Position]`, `list_open_for(symbol, side=None) -> List[Position]`, `__len__`.
  - **Subscriber API**: `subscribe(fn) -> unsub_callable` — registration may happen from any thread; event delivery still on Tk.
  - **Mutators** (all `@require_tk_thread`):
    - `open(*, symbol, side, qty, price, source, ts=None, strategy_id=None, extra=None, position_id=None) -> Position` — mint a manual / sandbox open. Hard error on dup `position_id`. Emits `OPEN`.
    - `open_from_fill(*, symbol, side, qty, price, ts=None, source="sandbox", strategy_id=None, position_id=None, fill_meta=None) -> Position` — entries-v1 counterpart for paper-engine entry fills. Same invariants as `open`; `fill_meta` is merged into the `OPEN` event's `meta` (with `side` / `source` always winning).
    - `apply_fill(*, position_id, qty, price, ts=None, meta=None) -> Position` — apply a CLOSE-direction fill (longs sell, shorts buy). Clamps `qty` to `qty_open`; emits `PARTIAL_CLOSE` (if remainder) or `CLOSE` (on full close, plus an auto `STRATEGY_UNBIND` if one was bound). Silent no-op when already flat.
    - `mark(symbol, price, ts=None, *, bar_close=False) -> List[Position]` — update `last_price` + watermarks for every open position on `symbol`. `bar_close=True` increments `bars_held`. Emits `MARK` per affected position.
    - `bind_strategy(position_id, strategy_id) -> Position`, `unbind_strategy(position_id, *, reason="manual") -> Position`.
    - `edit(position_id, *, qty_open=None, avg_entry_price=None, last_price=None, meta=None) -> Position` — manual-paper-only correction. Refuses sandbox positions.
    - `remove(position_id) -> Optional[Position]` — drop without notifying subscribers (session-end cleanup).
    - `clear() -> None` — drop everything; clears the pending-event queue too.

## Dependencies
- Internal: `..core.thread_guard.require_tk_thread`, `.model.{Position, PositionEvent, PositionEventKind, PositionSide, PositionSource}`.
- External: stdlib (`logging`, `uuid`, `collections.deque`, `datetime`).

## Design Decisions
- **Tk-thread-owned mutators**: every mutator carries `@require_tk_thread`. Stream-source / worker threads must marshal via `app._stream_queue` → `_drain_stream_queue`; the Tk main thread is the sole writer of position state. Tests bypass via `core.thread_guard.tk_thread_check_disabled()`.
- **Per-tracker event queue with re-entrancy guard**: mutators append `(event, position)` to `self._pending_events`, then call `_drain()`. `_drain()` walks a frozen-tuple snapshot of subscribers per event; nested mutator calls append to the same queue (the `_dispatching` flag suppresses nested drains) and return immediately. The outer `_drain` keeps consuming until empty. Guarantees: (a) no list-mutation-during-iter crashes, (b) stable per-event subscriber order, (c) nested events fire in emit-order after the outer event resolves.
- **`apply_fill` is close-only**: in the position's natural direction (long → sell, short → buy). Scaling INTO a position is intentionally not supported (v1 has no entry-add semantics); use `open_from_fill` to mint a fresh position instead. Avoids the weighted-avg-cost / flip-through-zero edge cases that live in `backtest.portfolio`.
- **Auto-strategy-unbind on full close**: emits a `STRATEGY_UNBIND` event with `meta.reason="position_closed"` right after the `CLOSE` event so the strategies tab / exit evaluator clear their per-position state without polling.
- **Subscriber exceptions are logged and swallowed**: a buggy subscriber must not halt the rest of the dispatch — the tracker is the single source of truth and one consumer's bad day shouldn't propagate to others.
- **`edit()` is manual-only**: editing a sandbox position would contradict the fills the engine has booked; the error message points the user at the right workflow. The before-state is captured in the `EDIT` event's `meta` so the audit log can reconstruct any change.
- **Symbol comparisons case-fold to upper**: `list_open_for("aapl")` matches a position stored as `"AAPL"`. Catches case-leakage from yfinance / user-typed tickers.
- **`clear()` does NOT notify subscribers**: session-end / app-shutdown — anything still bound is by definition stale.

## Invariants
- All mutator calls are on the Tk main thread (or via the test bypass).
- `_pending_events` is empty after every mutator returns (modulo nested re-entry, which the outer drain consumes).
- `_dispatching` flips True only during `_drain`; never observable from outside.
- `len(tracker)` counts every position (open + closed) until `remove` / `clear`. `list_open()` filters by `is_open`.
- `subscribe()` returns an idempotent unsubscribe — second call is a no-op (catches `ValueError` from the underlying list `.remove`).
- After `apply_fill` reduces `qty_open` to exactly 0, the emitted event kind is `CLOSE` (not `PARTIAL_CLOSE`).

## Testing
- Covered indirectly via sandbox smoke tests (`test_smoke_sandbox.py`) and manual-paper-positions exit-tab tests. Subscriber re-entrancy is exercised by the exit evaluator → tracker → audit log feedback loop.
