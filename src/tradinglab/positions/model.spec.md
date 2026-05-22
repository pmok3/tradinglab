# positions/model.py — Spec

## Purpose
Pure-data dataclasses for one open / closed equity exposure (`Position`) and an immutable ledger entry describing a state change (`PositionEvent`). The tracker mutates `Position` in place by `id`; consumers persist or replay sessions by serialising the ledger via `to_dict` / `from_dict`.

## Public API
- `PositionSide = Literal["long", "short"]`.
- `PositionSource = Literal["sandbox", "manual"]`.
- `class PositionEventKind(str, Enum)` — `OPEN`, `PARTIAL_CLOSE`, `CLOSE`, `MARK`, `STRATEGY_BIND`, `STRATEGY_UNBIND`, `EDIT`. String values are persisted.
- `@dataclass class Position` — mutable; identity is `id` (UUID string).
  - Fields: `id`, `symbol`, `side`, `qty_initial`, `qty_open`, `avg_entry_price`, `entry_time`, `source`, `realized_pnl` (default 0), `high_watermark` (default 0), `low_watermark` (default 0), `last_price` (default 0), `bars_held` (default 0), `strategy_id` (Optional[str]), `extra: Dict[str, Any]`.
  - `is_open: bool` — `qty_open > 0`.
  - `signed_qty_open() -> float` — long positive, short negative.
  - `unrealized_pnl() -> float` — sign-aware mark-to-market PnL; `0.0` if any input is missing / non-positive.
  - `to_dict()` / `from_dict(d)` — JSON-stable round-trip.
- `@dataclass(frozen=True) class PositionEvent` — immutable ledger entry.
  - Fields: `position_id`, `kind`, `ts`, `qty` (default 0), `price` (default 0), `meta: Dict[str, Any]`.
  - `to_dict()` / `from_dict(d)` — JSON-stable round-trip; `kind` serialised by `.value`.

## Dependencies
- Standard library only (`dataclasses`, `datetime`, `enum`, `typing`).

## Design Decisions
- **`Position` is mutable, `PositionEvent` is frozen**: tracker applies fills / marks in place on the position; events are append-only history records. Mirrors the `Portfolio` (mutable) vs `Fill` (frozen) split in `backtest/`.
- **Watermarks track raw price, not signed-by-side**: `high_watermark` is the max `last_price` observed since open regardless of side. Consumers that care about R-multiples or trail anchors compute their own signed deltas. Avoids surprising "watermark went down" semantics for shorts.
- **`unrealized_pnl()` is signed by side**: longs profit when `last > entry`, shorts when `entry > last`. Returns `0.0` on missing data rather than raising — the price feed can be silent for a tick without crashing the panel render path.
- **ISO 8601 with explicit UTC offset for `entry_time` / `ts`**: naive datetimes are stamped UTC on serialise; the round-trip via `_parse_iso` accepts both naive (assumed UTC) and tz-aware ISO strings. Matches the "engine ts is UTC seconds, display tz applied at render" convention.
- **`extra: Dict[str, Any]` is the forward-compat slot**: future fields (R-multiple anchor, broker-side order id, strategy notes) land here without bumping the schema version.
- **`_validate_side` / `_validate_source` raise on bad input**: hard error at deserialise time so a corrupted JSON file fails loudly rather than silently coercing.

## Invariants
- `Position.qty_open >= 0` (zero is closed; negative is invalid).
- `Position.is_open ⇔ qty_open > 0`.
- `Position.to_dict() → from_dict → to_dict` is byte-stable when datetime tz is explicit.
- `PositionEvent` is immutable after construction (frozen dataclass).
- `_iso` always emits a tz-aware ISO string.

## Testing
- Covered indirectly via sandbox smoke tests (`test_smoke_sandbox.py`) and manual-paper-positions tests; `Position.to_dict / from_dict` round-trips on every storage save/load.

