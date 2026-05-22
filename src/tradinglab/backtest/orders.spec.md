# backtest/orders.py — Spec

## Purpose
The `Order` and `Fill` dataclasses plus the `Side` enum that flow through the engine. Phase 1 is **market-orders-only** — limits, stops, and brackets are deferred to Phase 2 per locked decision Q2.

## Phase 1 order types (checklist)
- Phase 1 supports MARKET orders only.
- Stop, limit, stop-limit, trailing-stop, bracket, OCO, and time-in-force variants (GTC/IOC/FOK) are deferred to Phase 2.
- Buying power = current cash. No margin, no leverage, no PDT model.
- **No pre-trade cash check**: the engine fills any market order with a next-bar open and lets `Portfolio.apply_fill` decrement cash unconditionally. Cash may go negative; the engine does not reject, clip, or log. Caller responsible for sizing.
- Shorts notionally allowed via negative-direction fills; the SELL leg credits cash equal to proceeds. No haircut, no maintenance margin, no borrow rate.

## Public API
- `class Side(str, Enum)` — `BUY = "buy"`, `SELL = "sell"`. String-valued so JSON round-trip is trivial.
- `@dataclass(frozen=True) class Order` — `order_id`, `symbol`, `side`, `quantity` (float), `submitted_ts` (int).
- `@dataclass(frozen=True) class Fill` — `order_id`, `symbol`, `side`, `quantity`, `fill_price`, `fill_ts`, `slippage_bps`, `commission`.

## Dependencies
None beyond stdlib.

## Design Decisions
- **`order_id` is caller-assigned**, not UUID-generated. The `SandboxController` mints monotonically-increasing `ord-NNNN` ids; a Phase 2 batch runner can choose its own scheme. Deterministic ids feed reproducibility.
- **`Fill.fill_price` already includes slippage** in the worse-fill direction (BUY higher, SELL lower) — the fill model bakes it in so consumers (`Portfolio.apply_fill`) don't re-derive direction.
- **`commission` is per-fill currency, not bps**: matches how brokerage fees are quoted today and avoids double-converting through bps.
- **No `stop_price` / `limit_price` field**: keeping the dataclass shape minimal makes it impossible to forget a "fill must respect stop" branch in the engine. Phase 2 will introduce a richer order type rather than adding optional fields here.

## Invariants
- `Order` and `Fill` are frozen — engine logic must never mutate them.
- `Fill.fill_ts` is the *next* bar's ts after `Order.submitted_ts` (engine-side convention; not enforced by these dataclasses).
- No `tkinter` or `matplotlib` import.

## Testing
- `check_f0_backtest_kernel` §C — `apply_fills` slippage direction across BUY / SELL.
- `check_f1_session_reproducibility` — Fills round-trip via SessionResult JSON.

