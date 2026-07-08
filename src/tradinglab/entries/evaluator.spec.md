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
                 risk_gate: Optional[RiskGate] = None,
                 bars_registry: Optional[BarsRegistry] = None,
                 scan_runner: Optional[ScanRunner] = None,
                 exit_evaluator: Optional[ExitEvaluator] = None,
                 exit_storage: Optional[module] = None,
                 get_active_symbol: Optional[Callable[[], str | None]] = None,
                 clock: Callable[[], datetime] = _utc_now,
                 default_interval: str = "1m",
                 session_close_time: time = time(16, 0)) -> None: ...

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
    def on_tick(self, bars_by_symbol, ts: datetime, *,
                last_bar_forming: bool = False) -> List[EntrySignal]

    # GUI hooks
    def subscribe_modal_request(cb) -> unsubscribe_fn
    def pending_position_ids() -> Dict[str, str]  # pending_id → strategy_id
    def stats() -> EvaluatorStats
    def close() -> None  # idempotent

@dataclass
class EvaluatorStats:
    fires: int; blocked: int; cooldowns: int; dedup_skips: int
    risk_blocks: int; on_fill_binds: int; on_fill_bind_failures: int
    indicator_evaluations: int; errors: int
```

## Lifecycle gates (cheapest-first)

1. `enabled` (config; enforced at `arm()`).
2. `armed` (runtime).
3. `arm_window_start ≤ now ≤ arm_window_end` (ET).
4. `position_already_open_policy == BLOCK` + open position exists for
   the (strategy, symbol).
5. Per-(strategy, symbol) cooldown.
6. `max_fires_per_session_per_symbol` / `max_fires_per_session_total`.
7. Dedup ring (`_DEDUP_LRU_SIZE = 1024`) keyed on
   `(strategy_id, symbol, bar_ts_ns)`.
8. Trigger evaluation, reference-price resolution, and sizing.
9. Risk gate (post-signal, pre-submit).

Refusals are audited (`entry_blocked` / `entry_cooldown` /
`entry_dedup_skipped`) with a reason.

## Trigger dispatch

**Source of truth is `entries/dispatch.py` (`_ENTRY_DISPATCH` registry).**
Both this live evaluator AND the mechanical
`strategy_tester/evaluator.py` delegate the per-bar fire decision to
the shared registry, so adding a new `TriggerKind` lights up both call
sites at once. See `entries/dispatch.spec.md` for the registry
contract.

`_evaluate_trigger` here builds a `TriggerContext` and calls
`check_trigger_fires(trigger, ctx)`. Kind-specific logic:

| `TriggerKind`     | Logic                                                |
|-------------------|------------------------------------------------------|
| `MARKET`          | `entries.spec.should_fire_market` (next CLOSED bar)  |
| `LIMIT`           | `entries.spec.should_fire_limit` (touched-through)   |
| `STOP`            | `entries.spec.should_fire_stop`                      |
| `STOP_LIMIT`      | `entries.spec.should_fire_stop_limit`                |
| `INDICATOR`       | `scanner.engine.evaluate_group` over `BarsRegistry`  |
| `SCANNER_ALERT`   | `scan_runner` `new_rows` subscription                |

`_reference_price` and `_signal_price_for_kind` are now thin staticmethod
wrappers around `dispatch.reference_price` / `dispatch.signal_price_for_kind`.

## SCANNER_ALERT path

`_on_scan_results` consumes `Dict[scan_id, ScanResult]`. The real
`ScanRunner.subscribe` invokes `cb(scan_id, ScanResult)`, so
`EntriesAppMixin` wraps it into the dict shape before calling the
evaluator. For each armed strategy with `trigger.kind == SCANNER_ALERT`,
the evaluator reads `results[trigger.scanner_id].new_rows` and routes
each row through the same fire path as `on_tick`. **`new_rows` is
edge-filtered** by `MatchHistory` — re-arming uses `disarm` + `arm` to
reset.

## INDICATOR path

`_build_indicator_context(symbol, trigger)` builds an
`EvaluationContext` from `BarsRegistry.get_view` for
`(symbol, trigger.interval or default_interval)` and bumps the
`indicator_evaluations` stat counter. The context is threaded to
`dispatch._h_indicator` via `TriggerContext.scanner_eval_ctx`; the
shared handler calls `evaluate_group(condition, ctx)` and returns the
evidence list. Failures log via `entry_blocked`; never crash.

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
