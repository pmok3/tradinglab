# entries/model.py — Spec

## Purpose

Pure-data model for one saved entry strategy. Tk-free, side-effect-
free. Round-trips through JSON; structural validation runs as a
separate `validate_strategy` pass (NOT in `__post_init__`) so the GUI
can build half-edited drafts in-flight.

## Aggregates

```
EntryStrategy
├── universe   : Universe          # symbols / scanner_id / chart-attached
├── trigger    : EntryTrigger      # exactly one trigger (no leg/OCO)
├── sizing     : SizingRule        # FIXED_QTY or FIXED_NOTIONAL
└── on_fill_exit_ids : Tuple[str]  # exits to bind on the resulting position
```

## Public API

- `class TriggerKind(str, Enum)` — `MARKET / LIMIT / STOP /
  STOP_LIMIT / INDICATOR / SCANNER_ALERT` (deliberately narrower
  than `exits.model.TriggerKind`).
- `class Direction(str, Enum)` — `LONG` / `SHORT`.
- `class SizingKind(str, Enum)` — `FIXED_QTY` / `FIXED_NOTIONAL`.
- `class ShareRounding(str, Enum)` — `DOWN` / `NEAREST`.
- `class TimeInForce(str, Enum)` — `DAY` / `GTC`.
- `class OrderSide(str, Enum)` — `BUY` (long-open) / `SELL_SHORT`
  (short-open). Disambiguation on `EntrySignal.position_side`.
- `class PositionAlreadyOpenPolicy(str, Enum)` — `BLOCK` (skip +
  audit) / `STACK` (open a second independent position). Per-strategy.
- `@dataclass SizingRule(kind, qty, notional, share_rounding)` —
  tagged-union; only one of `qty`/`notional` meaningful per kind.
- `@dataclass Universe(symbols, scanner_id, from_attached_chart)` —
  XOR enforced by `validate_strategy`. `__post_init__` uppercases
  symbols deterministically.
- `@dataclass EntryTrigger(id, kind, price, stop_price, condition,
  interval, evaluate_intrabar, scanner_id, time_in_force, label)` —
  tagged-union on `kind`; `condition` is a `scanner.model.Group`.
- `@dataclass EntryStrategy(id, name, direction, universe, trigger,
  sizing, on_fill_exit_ids, enabled, cooldown_secs,
  max_fires_per_session_per_symbol, max_fires_per_session_total,
  position_already_open_policy, arm_window_start, arm_window_end,
  require_market_open, schema_version, created_with, created_at,
  updated_at, extra)`.
- `@dataclass CreatedWith(app, version, template)` — provenance.
- `validate_strategy(strategy) -> List[str]` — human-readable errors
  (empty = valid). Storage and arming both call this.
- `migrate(d, *, from_version) -> Dict[str, Any]` — forward-only.
- `CURRENT_SCHEMA_VERSION = 1`.

## Dependencies

- `scanner.model.Group` (for `EntryTrigger.condition`).
- `re`, `uuid`, `time`, `dataclasses`, `enum`, `typing`.

## Design Decisions

- **Arm state is NOT persistent.** `enabled` is a config flag; the
  runtime *armed* flag lives on `EntryEvaluator` and is wiped on
  construction. App restart wipes arm state by design.
- **One trigger per strategy.** No legs, no OCO — entries fire once
  and a position is born.
- **Validation is a separate pass.** `__post_init__` does cheap
  normalisation only (symbols → uppercase, list → tuple); GUI builds
  partial drafts without raising.
- **`arm_window_*` are ET HH:MM strings** (matches exits TIME_OF_DAY).
- **`on_fill_exit_ids` is a tuple** of exit-strategy ids; missing
  ids are logged but do not block the entry fill.

## Invariants

- `validate_strategy(d) == []` iff every tagged-union slot is
  internally consistent (LIMIT requires `price`; STOP requires
  `stop_price`; INDICATOR requires `condition`; SCANNER_ALERT
  requires `scanner_id`; Universe has exactly one of symbols /
  scanner_id / from_attached_chart).
- `EntryStrategy.from_dict(d.to_dict()).to_dict() == d.to_dict()`
  for every well-formed strategy.
- `schema_version > CURRENT_SCHEMA_VERSION` is refused at load time.
