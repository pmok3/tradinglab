# entries/signals.py — Spec

## Purpose

Broker-agnostic entry-signal protocol + concrete sinks. The `EntryEvaluator` decides *when* and *what* to enter; sinks deliver the resulting `EntrySignal` to a paper engine, manual notifier, or eventually a real broker.

## Pipeline

```
EntryEvaluator._try_fire(...)
        │  builds EntrySignal(kind, side, qty, price, limit_price, …)
        ▼
EntrySignalSink.submit(signal) → order_id
        │
        ├── EntryPaperSink   → PaperBrokerEngine.submit(PaperOrder, target_kind=PENDING_ENTRY)
        └── EntryManualSink  → EntryManualSignalEvent("submitted", …) → GUI banner
```

The evaluator records the order id so later cancellations (arm-window expiry, panic disarm, manual user action) can target individual pending orders.

## Public API

```python
class EntryOrderKind(str, Enum):  # MARKET / LIMIT / STOP / STOP_LIMIT
    # INDICATOR + SCANNER_ALERT collapse to MARKET when they fire.

@dataclass(frozen=True)
class EntrySignal:
    id: str
    strategy_id: str
    pending_position_id: str         # minted BEFORE submission
    symbol: str                      # required (no position_id yet)
    trigger_id: str
    kind: EntryOrderKind
    side: OrderSide                  # BUY (long-open) / SELL_SHORT
    position_side: Literal["long","short"]
    qty: float
    price: Optional[float] = None
    limit_price: Optional[float] = None
    on_fill_exit_ids: Tuple[str, ...] = ()
    label: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls, **kwargs) -> "EntrySignal"   # auto-id

class EntrySignalSink(Protocol):
    def submit(self, signal) -> str
    def cancel(self, order_id) -> bool
    def cancel_all_pending_for_symbol(self, symbol) -> int
    def working_order_ids_for_pending_position(self, pending_position_id) -> List[str]
    # concrete sinks also expose working_order_ids_for_symbol(symbol) -> List[str]

class EntryPaperSink:
    """Translates EntrySignal → PaperOrder(target_kind=PENDING_ENTRY)."""
    def __init__(self, engine: PaperBrokerEngine) -> None
    # Forward (signal_id → order_id), reverse, by-pending-position, by-symbol indexes.
    # `on_fill(order_id)` is the engine→sink hook that drops the id from indexes.

@dataclass(frozen=True)
class EntryManualSignalEvent:
    kind: str  # "submitted" / "cancelled" / "ack-fill"
    signal: Optional[EntrySignal]
    order_id: str

class EntryManualSink:
    def subscribe(cb) -> unsubscribe_fn
    def acknowledge_fill(order_id, *, fill_price=None, fill_qty=None) -> bool
```

## Dependencies

- `..exits.model.OrderSide` (shared enum).
- `..exits.paper_engine` (lazy import — avoids cycle) for `OrderTargetKind`, `PaperOrder`, `PaperOrderKind`.

## Design Decisions

- **No `position_id` on `EntrySignal`** — position doesn't exist yet. `pending_position_id` is the *future* id minted by the evaluator before submission; `open_from_fill` uses it on fill so the audit chain (signal → order → fill → position) correlates deterministically.
- **`symbol` is required** — no position_id to infer from.
- **`position_side` disambiguates** `OrderSide` — `BUY` means *open long*, not *cover short*.
- **`on_fill_exit_ids` propagates to the `PaperOrder`** so the pending fill metadata carries which exit strategies the upstream evaluator should attach on fill.
- **`EntryManualSink` is Tk-free** — subscribers marshal onto Tk before drawing.
- **`EntryPaperSink.on_fill(order_id)`** drops the id from local indexes so `cancel_all_pending_for_symbol` doesn't hit a filled order. Idempotent.

## Invariants

- `EntrySignal.id` is unique across the session (UUID hex).
- `submit` returns a non-empty order id; on engine failure the sink propagates (evaluator catches + audits).
- `cancel(unknown_id)` returns `False` — no-op contract.

## See also

- Mirror: [`exits/signals.spec.md`](../exits/signals.spec.md).
