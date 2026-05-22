# entries/evaluator.py — live entry-strategy evaluator

## Purpose

Orchestrator that turns price / indicator / scanner-alert data into
broker instructions that mint **new** positions. Sits between the
bar/tick stream, optional `BarsRegistry` (cross-interval indicator
views), optional `ScanRunner`, an `EntrySignalSink`, optional
`RiskGate`, `AuditLog`, `PositionTracker` (delivers position-open
events for on-fill bracket binding), and — once a position is born —
the `ExitEvaluator` for declarative `on_fill_exit_ids`.

## Public API

```python
class EntryEvaluator:
    def __init__(self, *,
                 tracker: PositionTracker,
                 sink: EntrySignalSink,
                 audit: Optional[AuditLog] = None,
                 bars_registry: Optional[BarsRegistry] = None,
                 scan_runner: Optional[ScanRunner] = None,
                 exit_evaluator: Optional[ExitEvaluator] = None,
                 risk_gate: Optional[RiskGate] = None,
                 session_close_time: time = time(16, 0),
                 clock: Callable[[], datetime] = utc_now,
                 default_interval: str = "1m") -> None: ...

    # Library + arm state
    def set_strategies(self, strategies: Iterable[EntryStrategy]) -> None
    def get_strategy(self, strategy_id) -> Optional[EntryStrategy]
    def all_strategies() -> List[EntryStrategy]
    def is_armed(self, strategy_id) -> bool
    def armed_strategies() -> Set[str]
    def arm(self, strategy_id) -> None
    def disarm(self, strategy_id) -> None
    def disarm_all() -> None
    def reset_session() -> None  # rolls cooldown / max-fires counters

    # Per-tick evaluation
    def on_tick(self, candles_by_symbol, *, interval, tick_id,
                timestamp=None, is_close=True) -> List[EntrySignal]

    # GUI hooks
    def subscribe_modal_request(cb) -> unsubscribe_fn
    def pending_position_ids() -> Dict[str, str]  # pending_id → strategy_id
    def stats() -> EvaluatorStats
    def close() -> None  # idempotent

@dataclass
class EvaluatorStats:
    fires: int; blocked: int; cooldowns: int; dedup_skipped: int
    errors: int; indicator_evaluations: int; bars_processed: int
```

## Lifecycle gates (cheapest-first)

1. `enabled` (config).
2. `armed` (runtime).
3. `arm_window_start ≤ now ≤ arm_window_end` (ET).
4. `require_market_open` (RTH).
5. `position_already_open_policy == BLOCK` + open position exists for
   the (strategy, symbol).
6. Per-(strategy, symbol) cooldown.
7. `max_fires_per_session_per_symbol` / `max_fires_per_session_total`.
8. Dedup ring (`_DEDUP_LRU_SIZE = 1024`) keyed on
   `(strategy_id, symbol, bar_ts_ns)`.
9. Risk gate.

Refusals are audited (`entry_blocked` / `entry_cooldown` /
`entry_dedup_skipped`) with a reason.

## Trigger dispatch

| `TriggerKind`     | Logic                                                |
|-------------------|------------------------------------------------------|
| `MARKET`          | `entries.spec.should_fire_market` (next CLOSED bar)  |
| `LIMIT`           | `entries.spec.should_fire_limit` (touched-through)   |
| `STOP`            | `entries.spec.should_fire_stop`                      |
| `STOP_LIMIT`      | `entries.spec.should_fire_stop_limit`                |
| `INDICATOR`       | `scanner.engine.evaluate_group` over `BarsRegistry`  |
| `SCANNER_ALERT`   | `scan_runner` `new_rows` subscription                |

## SCANNER_ALERT path

Evaluator subscribes one adapter to `ScanRunner`. The real
`ScanRunner.subscribe` invokes `cb(scan_id, ScanResult)`;
`EntriesAppMixin` wraps it into `Dict[scan_id, ScanResult]`. For each
armed strategy with `trigger.kind == SCANNER_ALERT`, the evaluator
reads `results[trigger.scanner_id].new_rows` and routes each row
through the same fire path as `on_tick`. **`new_rows` is edge-filtered**
by `MatchHistory` — re-arming uses `disarm` + `arm` to reset.

## INDICATOR path

Cross-interval condition trees evaluate via `evaluate_group(condition,
ctx)` against an `EvaluationContext` built from the `BarsRegistry`
view for `(symbol, trigger.interval or default_interval)`. Failures
log via `entry_blocked`; never crash.

## On-fill bracket chain

Evaluator subscribes to `PositionTracker`. On `PositionEvent.kind ==
OPEN` for a `pending_position_id`:

1. Look up originating strategy.
2. For each id in `strategy.on_fill_exit_ids`, call
   `exit_evaluator.attach_strategy(pos.id, exit_strategy)`.
3. Missing/invalid ids → `entry_bind_failed` audit (does NOT block fill).
4. Empty `on_fill_exit_ids` → emit `request_attach_modal` (GUI prompt).

## Panic / disarm propagation

`disarm_all` is callable from the exits panic-flatten path. Audits
`entry_disarm_all` and clears the in-flight dedup ring.

## Error handling

- Indicator exceptions → audit `entry_blocked` + skip.
- Sink submit exceptions → audit `entry_blocked` (kind=`sink_error`).
- Tracker subscriber re-entrancy is safe (tracker uses its own queue).
- Position-event for an unknown pending id → debug log + ignore.

## Threading

Every public mutator `@require_tk_thread` (mirrors `ExitEvaluator`).
Read-only queries (`stats`, `armed_strategies`, `pending_position_ids`)
unrestricted. Subscribed ScanRunner / PositionTracker callbacks are
guarded so off-thread delivery fails fast with `TkThreadViolation`.

## See also

- Mirror: [`exits/evaluator.spec.md`](../exits/evaluator.spec.md).
- Sink: [`signals.spec.md`](signals.spec.md).
- Trigger maths: [`spec.spec.md`](spec.spec.md).
- Lifecycle: [`__init__.spec.md`](__init__.spec.md) §"On-fill bracket chain".
