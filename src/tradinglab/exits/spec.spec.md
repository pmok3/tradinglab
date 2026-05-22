# exits/spec.py — Spec

## Purpose

Pure-function trigger-evaluation helpers for native exit triggers (market, limit, stop, trailing-stop, chandelier, time-of-day) plus state-machine maths for trailing / chandelier. `update_*` mutates a small dataclass; `evaluate_*` reads it + the new bar. No Tk, no I/O.

## Public API

```python
@dataclass
class Bar:
    open: float; high: float; low: float; close: float
    timestamp: Optional[datetime] = None

@dataclass
class TriggerState:
    """Per-trigger evaluator-owned state slot (trailing HWM, etc.)."""
    hwm: Optional[float] = None
    lwm: Optional[float] = None
    last_stop: Optional[float] = None
    armed_at_bar_ts: Optional[datetime] = None
    extra: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Decision:
    should_fire: bool
    fill_price: Optional[float]
    reason: str = ""

# Native evaluators
def evaluate_market(trigger, bar, *, is_close: bool) -> Decision
def evaluate_limit(trigger, bar, *, direction: Direction) -> Decision
def evaluate_stop(trigger, bar, *, direction: Direction) -> Decision
def evaluate_stop_limit(trigger, bar, *, direction, stop_already_armed=False) -> Decision

# Trailing stop
def update_trail_state(state, bar, *, direction, trail_pct, trail_amount) -> TriggerState
def evaluate_trailing_stop(state, bar, *, direction) -> Decision
def recompute_hwm_from_history(bars, *, direction) -> Tuple[float, float]  # (hwm, lwm) for restart

# Chandelier
def freeze_chandelier_params(strategy, *, bars_at_arm) -> Dict[str, Any]  # captured at arm; placed in TriggerState.extra
def update_chandelier_state(state, bar, *, direction) -> TriggerState
def evaluate_chandelier_stop(state, bar, *, direction) -> Decision

# Time-of-day
def evaluate_time_of_day(trigger, bar, *, now_et: datetime) -> Decision

# Sizing snapshots
def compute_qty_at_fire(strategy, *, position, ref_price) -> float
def compute_initial_risk_per_share(strategy, *, entry_price) -> float
```

## Convention table (exit triggers — opposite of entries!)

| Trigger | LONG fires when                     | SHORT fires when                     |
|---------|-------------------------------------|--------------------------------------|
| LIMIT   | `bar.high ≥ limit_price`            | `bar.low ≤ limit_price`              |
| STOP    | `bar.low ≤ stop_price`              | `bar.high ≥ stop_price`              |
| TRAIL   | `bar.low ≤ state.hwm·(1-trail_pct)` | `bar.high ≥ state.lwm·(1+trail_pct)` |
| CHAND   | `bar.low ≤ frozen.hwm - n·ATR`      | `bar.high ≥ frozen.lwm + n·ATR`      |

A LONG **exit** stop fires *below*; a LONG **entry** stop fires *above* (breakout). Convention deliberate and locked.

## Dependencies

- `.model.{Direction, ExitStrategy, ExitTrigger, TriggerKind}`.
- `..core.indicator_atr` for chandelier ATR.

## Design Decisions

- **State is supplied by the caller.** Evaluator owns one `TriggerState` per `(position_id, leg_id, trigger_id)`; this module reads / writes but doesn't hold it.
- **`Decision.fill_price` is conservative.** TRAIL fires at the current stop level (not bar low) — slippage lives in the paper engine.
- **`update_*` always returns a NEW dataclass** (value-typed).
- **`freeze_chandelier_params` runs once at arm** — ATR(n) and the multiple are captured and re-used for the trigger's lifetime; protects against indicator-drift while a trail is live.
- **`evaluate_time_of_day`** treats `now_et` as authoritative; the evaluator passes a `datetime` already in ET.

## Invariants

- `evaluate_*(trigger, …, direction)` returns `should_fire=False` when the trigger kind doesn't match the function.
- `update_trail_state` is monotonic: HWM never decreases for LONG; LWM never increases for SHORT.
- `recompute_hwm_from_history([])` returns `(nan, nan)` — callers must skip arming a trail in that case.

## See also

- Evaluator: [`evaluator.spec.md`](evaluator.spec.md) §"Trigger dispatch".
- Mirror: [`../entries/spec.spec.md`](../entries/spec.spec.md).
