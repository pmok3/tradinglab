# `exits/signals.py` — exit-signal protocol + concrete sinks

## Purpose

The evaluator decides *when* and *what* to exit, hands a broker-agnostic `ExitSignal` to a sink that delivers the order. This module owns the protocol + the three v1 sinks.

## Pipeline

```
ExitEvaluator.fire(...)
        │  builds ExitSignal(kind, side, qty, price, limit_price, …)
        ▼
ExitSignalSink.submit(signal)
        │
        ├── PaperBrokerSink   → PaperBrokerEngine.submit(PaperOrder)
        ├── ManualPaperSink   → ManualSignalEvent("submitted", …) → GUI banner
        └── SchwabTraderSink  → SchwabTraderNotConfigured (v1 stub)
```

The evaluator records the order id from `submit` so later cancellations (OCO sibling cancel, panic flatten, EOD kill) can target individual orders.

## `ExitSignal`

Frozen dataclass:

```python
ExitSignal(
    id: str,             # uuid hex from .new() factory
    strategy_id: str,
    position_id: str,
    leg_id: str,
    trigger_id: str,
    kind: ExitOrderKind, # MARKET / LIMIT / STOP / STOP_LIMIT
    side: OrderSide,     # closing side, derived from position direction
    qty: float,          # resolved at fire time against position.qty_open
    price: Optional[float] = None,
    limit_price: Optional[float] = None,
    label: str = "",
    extra: Dict[str, Any] = field(default_factory=dict),
)
```

`ExitOrderKind` is intentionally narrower than `TriggerKind` — trailing / TOD / indicator triggers flatten to MARKET on fire. The evaluator owns trail HWM and indicator state; the sink only sees the resulting order kind.

## `ExitSignalSink` Protocol

```python
class ExitSignalSink(Protocol):
    def submit(self, signal: ExitSignal) -> str
    def cancel(self, order_id: str) -> bool
    def cancel_all_for_position(self, position_id: str) -> int
    def working_order_ids_for_position(self, position_id: str) -> List[str]
```

All three concrete sinks enforce `@require_tk_thread` on mutators. Read-only `working_order_ids_for_position` has no thread restriction.

## `PaperBrokerSink`

Translates `ExitSignal` → `PaperOrder` (mapping `ExitOrderKind` → `PaperOrderKind`) and delegates to `PaperBrokerEngine.submit`. Holds forward (signal_id → order_id), reverse, and per-position working-id maps so cancellations work via either id. Auto-fill paper trading (the engine fills against bar data).

## `ManualPaperSink`

Surfaces signals via callback rather than filling — for users who paper-trade by mirroring on a real broker. Working set keyed by synthetic `manual-<uuid>` ids; cleared via `acknowledge_fill` or `cancel`. `subscribe(cb) -> unsubscribe_fn` registers listeners; sink emits `ManualSignalEvent("submitted"/"cancelled"/"ack-fill", signal, order_id)`. Mutators are Tk-thread guarded; subscriber callbacks are otherwise plain Python callbacks that GUI listeners marshal onto Tk before drawing.

## `SchwabTraderSink`

Explicit stub. `submit` audits then raises `SchwabTraderNotConfigured`; cancel mutators raise the same typed error. Lets the GUI list the option without silently dropping signals.

## Error semantics

* `cancel(unknown_id) -> False` is the no-op contract (not an error) — OCO cancel loop relies on it for siblings already filled.
* `PaperBrokerSink.submit` propagates `ValueError` from the engine (unknown position, missing prices). The evaluator catches, audits an `error` meta-row, marks the leg broken.
* `ManualPaperSink.acknowledge_fill(unknown_id) -> False`.
