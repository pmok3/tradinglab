# `exits/evaluator.py` — live exit-strategy evaluator

## Purpose

Orchestrator that turns price/indicator data into broker instructions
for any open position with a bound exit strategy.

Tk-thread-only: every public mutator is `@require_tk_thread`. The
tracker subscriber delivers position events on the same thread, so
OCO / auto-detach mutations are non-reentrant.

## API

```python
class ExitEvaluator:
    def __init__(self, *,
                 tracker: PositionTracker,
                 sink: ExitSignalSink,
                 audit: Optional[AuditLog] = None,
                 bars_registry: Optional[BarsRegistry] = None,
                 session_close_time: time = time(16, 0),
                 clock: Callable[[], datetime] = utc_now,
                 default_interval: str = "1m") -> None: ...

    @require_tk_thread
    def attach_strategy(self, position_id: str, strategy: ExitStrategy) -> None
    @require_tk_thread
    def detach_strategy(self, position_id, *, reason="manual",
                        cancel_in_flight=True) -> bool
    @require_tk_thread
    def on_bar(self, position_id, bar, *, is_close=True,
               interval=None) -> List[ExitSignal]
    @require_tk_thread
    def panic_flatten_position(self, position_id) -> int
    @require_tk_thread
    def submit_market_flatten(self, position_id) -> Optional[ExitSignal]

    # Read-only (any thread)
    def attached_strategy(self, position_id) -> Optional[ExitStrategy]
    def is_attached(self, position_id) -> bool
    def attached_position_ids() -> List[str]
    def stats() -> EvaluatorStats
    def trigger_state(self, position_id, leg_id, trigger_id) -> Optional[_TriggerSlot]
    def close() -> None  # idempotent; unsubscribes from tracker
```

## Per-trigger state

`_TriggerSlot`: `armed` (single-fire), `state: TriggerState` (trail
HWM / activation / fire_count), `submitted_order_ids`,
`last_fire_bar_ts_ns` (bar dedup), `error_count`, `broken`.

States: **Armed → Fired (disarmed) → orders settled in sink**. Re-arm
requires a fresh `attach_strategy`.

## Trigger dispatch

All trigger fire decisions route through `exits.dispatch`.
`ExitEvaluator` owns live-only context construction (per-position
`TriggerState`, scanner `EvaluationContext`, bar-close/intrabar flag,
and `now`) then calls `dispatch.check_trigger_decision`.

| Kind             | Dispatch behavior                                      | Result kind |
|------------------|--------------------------------------------------------|-------------|
| `MARKET`         | `evaluate_market`                                      | MARKET      |
| `LIMIT`          | `evaluate_limit`                                       | LIMIT       |
| `STOP`           | `evaluate_stop`                                        | STOP        |
| `STOP_LIMIT`     | `evaluate_stop_limit`                                  | STOP_LIMIT  |
| `TRAILING_STOP`  | `update_trail_state` + `evaluate_trailing_stop`        | MARKET      |
| `TIME_OF_DAY`    | `evaluate_time_of_day` with `bar.date` / clock `now`   | MARKET      |
| `INDICATOR`      | `scanner.engine.evaluate_group` on caller-built ctx    | MARKET      |
| `CHANDELIER`     | `update_chandelier_state` + `evaluate_chandelier_stop` | MARKET      |

Trailing/TOD/indicator/chandelier collapse to MARKET on fire — the
evaluator owns the state machine; the sink only sees the resulting
market exit.

## Chandelier trigger

1. Activation detected via `tslot.state.chandelier_frozen_params is None`.
2. `update_chandelier_state(..., is_activation=True)` seeds the
   entry-anchored rolling extremum, initialises running ATR, and
   **freezes** `(lookback, atr_period, multiplier, ma_type)` into
   `state.chandelier_frozen_params`. Subsequent edits to the source
   trigger cannot change the stop math on this live attachment.
3. `update_chandelier_state(..., is_activation=False)` per subsequent
   bar — advances the rolling extremum (capped at `lookback`), ATR,
   ratchets the stop forward only.
4. `evaluate_chandelier_stop` fires on touch (long: `bar.low <= stop`;
   short: `bar.high >= stop`), fills at the stop level, surfaces
   realized gap slippage on `state.chandelier_realized_slippage`.
5. Audit `meta` includes `realized_slippage` (dollars/share) when the
   bar's open was worse than the stop; omitted otherwise.

## Indicator triggers

1. Skip if `is_close=False` and `trigger.evaluate_intrabar=False`.
2. Skip if `bars_registry is None` (logs no-fire reason).
3. Pull `BarsView` for `(symbol, trigger.interval or default_interval)`.
4. Build `EvaluationContext` via `make_context`, threading
   `bars_registry` so cross-interval `FieldRef`s resolve.
5. Pass that context to `exits.dispatch`, which calls
   `evaluate_group(condition, ctx)` and fires on True.

Scanner-pipeline failures mark the trigger broken + audit; never crash.

## OCO behavior

| `cancel_on`                | Behavior on first leg fire |
|----------------------------|----------------------------|
| `any_fire`                 | Cancel all siblings immediately |
| `full_closeout` (default)  | Mark siblings pending; cancel when tracker reports `qty_open == 0` |

`full_closeout` cancellation is tracker-driven: on `PositionEvent.kind
in (PARTIAL_CLOSE, CLOSE)` plus `pos.qty_open <= 0`, `_cancel_leg`
runs for every pending leg id then auto-detaches.

## EOD kill switch

When `bar.date >= session_close_time - eod_offset_min` and
`strategy.eod_kill_switch=True`:

1. Cancel every in-flight order via `sink.cancel_all_for_position`.
2. Disarm every trigger in every leg.
3. Submit a single MARKET signal for the full `pos.qty_open`.
4. Audit `eod_kill_switch_fired`.
5. Set `att.eod_fired=True` to prevent re-firing.

## Panic flatten (two-phase)

* **Phase 1 (sync):** `panic_flatten_position` — disarm + cancel
  in-flight. ~ms-fast; doesn't block on a hung sink.
* **Phase 2 (async, GUI):** `submit_market_flatten` — market exit for
  current `qty_open`. Safe to retry for residuals.

## Tracker subscription

Subscribes on construction via `tracker.subscribe(self._on_position_event)`:

1. **Full-closeout OCO drain:** on `qty_open == 0`, run pending
   cancels then auto-detach.
2. **Auto-detach on `STRATEGY_UNBIND`.**

Idempotent across re-entrancy (tracker queues nested events).

## Error handling

* Indicator exceptions → mark trigger broken + audit.
* Sink submit/cancel exceptions → log + audit; never crash.
* Tracker bind/unbind exceptions → debug-log only.

## Stats

`EvaluatorStats(fires, cancels, eod_fires, errors,
indicator_evaluations, bars_processed)` — read-only snapshot.
