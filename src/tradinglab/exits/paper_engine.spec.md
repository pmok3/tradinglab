# exits/paper_engine.spec.md — `PaperBrokerEngine`

## Purpose

Live in-app paper broker that fills exit orders against incoming bar
data. Owns the working-order set, applies fills to `PositionTracker`,
reports synthetic `Fill` records.

## Distinction from `SandboxEngine`

| Engine | Role |
| --- | --- |
| `backtest.engine.SandboxEngine` | Replay engine for *historical* backtests. Operates on a frozen master timeline; deterministic. |
| `exits.paper_engine.PaperBrokerEngine` | Live broker for *paper-trading* exits. Each `on_bar` processes one live (or simulated-live) bar; orders resolve FIFO. |

## Order kinds

Handles only the four kinds with deterministic OHLC-based fill
prices: `MARKET`, `LIMIT`, `STOP`, `STOP_LIMIT`. `TRAILING_STOP`,
`TIME_OF_DAY`, `INDICATOR`, and `CHANDELIER` triggers are evaluated
*upstream* by `ExitEvaluator`; on fire the evaluator submits a plain
`MARKET` `PaperOrder`. Engine stores **no** trail/HWM/indicator state
— pure order-fill machine.

## Target kinds

`PaperOrder.target_kind` distinguishes existing-position exit orders
from pending-entry orders. `EXISTING_POSITION` is the default and
requires an already-open `position_id`; fills call
`PositionTracker.apply_fill`. `PENDING_ENTRY` requires `symbol`,
`pending_position_id`, and `position_side`; fills are processed via
`on_bar_for_pending` and call `PositionTracker.open_from_fill` to mint
the new position.

## Fill priority — FIFO

Working orders processed in **submission order** (Python dict
insertion order). Each `on_bar` snapshots relevant order ids first,
then walks them in order.

## Slippage convention

`slippage_bps` is a fixed deterministic offset, in basis points (1
bp = 0.01%), applied **against the trader**:

- `MARKET` SELL exit: `fill = bar.close * (1 − bps/10000)` (lower).
- `MARKET` BUY exit: `fill = bar.close * (1 + bps/10000)` (higher).
- `STOP` fills: same direction as MARKET, applied to
  `min(stop, bar.open)` (SELL) or `max(stop, bar.open)` (BUY) so a
  gap-through bar fills at the worse of the two.
- `LIMIT` and the limit body of `STOP_LIMIT` receive **no slippage**.

## Touched-through detection

- `LIMIT` SELL fills if `bar.high >= limit_price`.
- `LIMIT` BUY fills if `bar.low <= limit_price`.
- `STOP` SELL fills if `bar.low <= stop_price`.
- `STOP` BUY fills if `bar.high >= stop_price`.
- `STOP_LIMIT` requires the stop touched **and** the limit reachable
  on the same bar (SELL: `bar.high >= limit_price`; BUY: `bar.low <=
  limit_price`). Gap past limit (e.g. SELL `stop=180, limit=179.5`,
  bar opens 178) → remains working.

## Evaluate-but-clamp-to-zero on mid-bar close

Multiple working orders on the same bar all evaluate, even if an
earlier order fully closes the position. `PositionTracker.apply_fill`
clamps applied qty to remaining `qty_open` (0 once closed), so later
attempts produce a `Fill` with `qty=0.0`. No-op fills are emitted so
callers see the full event trace, but `filled` stats only increment
on non-zero qty. **Cancelling siblings on full close is the
`ExitEvaluator`'s job**, not the engine's.

## Threading invariant

Every public mutator (`submit`, `cancel`, `cancel_all_for_position`,
`cancel_all_pending_for_symbol`, `on_bar`, `on_bar_for_pending`) is
`@require_tk_thread`. Tests bypass via `tk_thread_check_disabled()`.

## Stats

`working_orders_for_position` returns only `EXISTING_POSITION`
orders. `pending_orders_for_symbol` returns pending-entry orders keyed
by uppercased symbol. `stats()` returns `working` (current in-flight),
`submitted`, `filled`, `cancelled`, `rejected` (lifetime counters).
