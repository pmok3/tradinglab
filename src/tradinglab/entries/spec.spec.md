# entries/spec.py — Spec

## Purpose

Pure-function trigger-evaluation helpers for native (non-INDICATOR,
non-SCANNER_ALERT) entry triggers. Stateless.

## Public API

```python
@dataclass
class BarLike:
    open: float
    high: float
    low: float
    close: float

def should_fire_market(trigger, bar, *, is_close: bool) -> bool
def should_fire_limit(trigger, bar, *, direction: Direction) -> bool
def should_fire_stop(trigger, bar, *, direction: Direction) -> bool
def should_fire_stop_limit(trigger, bar, *, direction,
                           stop_already_armed: bool = False) -> bool
def trigger_fill_price(trigger, bar, *, direction: Direction) -> Optional[float]
```

## Dependencies

- `entries.model.{Direction, EntryTrigger, TriggerKind}` only.
- No NumPy, no scanner imports, no Tk.

## Design Decisions

**Entries invert the exit stop/limit convention:**

| Trigger    | LONG fires when            | SHORT fires when           |
|------------|----------------------------|----------------------------|
| LIMIT      | `bar.low ≤ price`          | `bar.high ≥ price`         |
| STOP       | `bar.high ≥ stop_price`    | `bar.low ≤ stop_price`     |
| STOP_LIMIT | stop hit AND limit reachable (both on same bar by default) | same, inverted |

Exits use the *opposite* polarity (a long-position STOP fires on
`bar.low ≤ stop_price` because it's a protective stop *below* the
position).

- **`MARKET` enforces closed-bar invariant.** Fires only when
  `is_close=True`.
- **`STOP_LIMIT` is single-bar by default.**
  `stop_already_armed=True` means a prior bar armed the stop and
  this call checks only the limit half.
- **Fill prices are conservative.** LIMIT → trigger price; STOP →
  `stop_price`; MARKET / INDICATOR / SCANNER_ALERT → `bar.close`.
  Deterministic so sandbox runs are reproducible.

## Invariants

- `should_fire_*` returns `False` when `trigger.kind` doesn't match
  the function. (Wrong-kind defensiveness.)
- `should_fire_*` returns `False` when required prices are `None`.
- `trigger_fill_price` returns `None` when the required price field is
  missing or the kind is unknown; all six valid configured kinds resolve.
