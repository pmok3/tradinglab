# exits/spec.py — Spec

## Purpose

Pure-function trigger-evaluation helpers for native exit triggers (market, limit, stop, trailing-stop, chandelier, time-of-day) plus state-machine maths for trailing / chandelier. `update_*` mutates a small dataclass; `evaluate_*` reads it + the new bar. No Tk, no I/O.

## Public API

```python
@dataclass
class Bar:
    open: float; high: float; low: float; close: float
    volume: float = 0.0
    date: Optional[datetime] = None

@dataclass
class TriggerState:
    armed: bool = True
    hwm: Optional[float] = None
    lwm: Optional[float] = None
    activated: bool = False
    trail_price: Optional[float] = None
    chandelier_rolling_high: Optional[float] = None
    chandelier_rolling_low: Optional[float] = None
    chandelier_window_count: int = 0
    chandelier_stop: Optional[float] = None
    chandelier_atr_state: Optional[Dict[str, Any]] = None
    chandelier_frozen_params: Optional[Dict[str, Any]] = None
    chandelier_realized_slippage: float = 0.0
    last_evaluated_bar_ts: Optional[datetime] = None
    fire_count: int = 0

@dataclass
class Decision:
    fire: bool
    fire_price: float = 0.0
    qty: float = 0.0
    reason: str = ""
    limit_price: Optional[float] = None
    evidence: List[Any] = field(default_factory=list)

# Native evaluators
def resolve_price(trigger, position, *, use_stop_limit=False) -> Optional[float]
def evaluate_market(trigger, position, bar) -> Decision
def evaluate_limit(trigger, position, bar) -> Decision
def evaluate_stop(trigger, position, bar) -> Decision
def evaluate_stop_limit(trigger, position, bar) -> Decision

# Trailing stop
def update_trail_state(state, trigger, position, bar, *, is_close: bool,
                       atr_value=None, paired_stop_price=None) -> None
def evaluate_trailing_stop(state, trigger, position, bar) -> Decision
def recompute_hwm_from_history(state, trigger, position, bars, *,
                               atr_values=None, paired_stop_price=None) -> None

# Chandelier
def freeze_chandelier_params(trigger) -> Dict[str, Any]
def update_chandelier_state(state, trigger, position, bar, *, is_activation=False) -> None
def evaluate_chandelier_stop(state, trigger, position, bar) -> Decision

# Time-of-day
def evaluate_time_of_day(trigger, position, bar, *, now: datetime) -> Decision

# Sizing snapshots
def compute_qty_at_fire(trigger, position) -> float
def compute_initial_risk_per_share(position, paired_stop_price) -> Optional[float]
```

## Convention table (exit triggers — opposite of entries!)

| Trigger | LONG fires when                     | SHORT fires when                     |
|---------|-------------------------------------|--------------------------------------|
| LIMIT   | `bar.high ≥ limit_price`            | `bar.low ≤ limit_price`              |
| STOP    | `bar.low ≤ stop_price`              | `bar.high ≥ stop_price`              |
| TRAIL   | `bar.low ≤ state.trail_price`       | `bar.high ≥ state.trail_price`       |
| CHAND   | `bar.low ≤ state.chandelier_stop`   | `bar.high ≥ state.chandelier_stop`   |

A LONG **exit** stop fires *below*; a LONG **entry** stop fires *above* (breakout). Convention deliberate and locked.

## Dependencies

- `.model.{ActivationUnit, ExitTrigger, TrailBasis, TrailUnit, TriggerKind}`.
- `..positions.model.Position`.
- `..indicators.ma_kernels.apply_ma` for chandelier ATR smoothing.

## Design Decisions

- **State is supplied by the caller.** Evaluator owns one `TriggerState` per `(position_id, leg_id, trigger_id)`; this module mutates that explicit state but holds no module-global state.
- **`Decision.fire_price` is conservative.** TRAIL and CHANDELIER fire at the current stop level (not bar low/high); gap slippage is represented by stop/engine policy.
- **`update_*` mutates in place.** The caller persists or snapshots the supplied `TriggerState`; functions return `None`.
- **`freeze_chandelier_params` runs once at arm** — lookback, ATR period, multiple, and MA type are captured and re-used for the trigger's lifetime; protects against indicator-drift while a trail is live.
- **`evaluate_time_of_day`** treats `now` as authoritative; the evaluator passes a `datetime` already in ET.

## Invariants

- `evaluate_*(...)` returns `Decision(fire=False, reason="kind mismatch")` when the trigger kind doesn't match the function.
- `update_trail_state` is monotonic: HWM never decreases for LONG; LWM never increases for SHORT; `trail_price` never loosens after activation.
- `recompute_hwm_from_history` resets HWM/LWM/activation/trail price and replays the supplied bar sequence; an empty sequence leaves those state fields unset.

## See also

- Evaluator: [`evaluator.spec.md`](evaluator.spec.md) §"Trigger dispatch".
- Mirror: [`../entries/spec.spec.md`](../entries/spec.spec.md).
