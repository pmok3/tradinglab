# backtest/portfolio.py — Spec

## Purpose
Cash + per-symbol position bookkeeping plus a mark-to-market equity curve. Sign-aware: opens / adds use weighted-average cost, reduces / closes realise P/L, and a flip through zero (e.g. selling more than the long quantity) splits cleanly into a close + new open at `fill_price`.

## Public API
- `@dataclass class Position` — `symbol: str`, `quantity: float = 0.0` (signed), `avg_cost: float = 0.0`, `realized_pnl: float = 0.0`. `is_flat` property returns `quantity == 0.0`.
- `@dataclass class Portfolio` — `cash: float`, `positions: Dict[str, Position]` (default empty), `equity_curve: List[Tuple[int, float]]` (default empty).
  - `get_or_create(symbol) -> Position`.
  - `apply_fill(fill)` — mutate cash + position for one fill.
  - `mark_to_market(ts, prices) -> float` — append `(ts, equity)` to the curve and return equity.
  - Callers read the latest equity directly off `equity_curve[-1]` (or fall back to `cash` when the curve is empty).

## Dependencies
- Internal: `.orders.Fill`, `.orders.Side`.

## Design Decisions
- **Sign-encoded quantity**: longs are positive, shorts are negative — one `Position` per symbol regardless of side. Saves a `Dict[str, Dict[Side, Position]]` layer that nothing in Phase 1 needs. Borrow / locate fees, hard-to-borrow rejections, and short-sale uptick rules are not modelled (Phase 2).
- **Weighted-avg cost on same-direction add**: the standard discretionary-trader convention. Avoids the FIFO-lot complexity that tax-aware accounting would require (explicitly out of scope).
- **Flip-through-zero is a close + open**: the closing leg realises P/L against the old `avg_cost`; the new leg's `avg_cost` is the fill price. Mirrors how brokerages report on a netting account.
- **MTM fallback to `avg_cost`** — equity = `cash + Σ(qty × price_or_avg_cost)`. If a symbol has no current price (halted, gap, missing bar), it is marked at `avg_cost` and contributes zero floating P/L for that bar. The equity curve stays continuous across data gaps but understates true exposure during halts.
- **MTM happens at bar close**, not continuously inside the bar — the engine drives one `mark_to_market` call per successful `tick` after fills are applied.

## Invariants
- `Position.is_flat` ⇔ `quantity == 0.0`.
- After a closing fill drops `quantity` to exactly zero, `avg_cost == 0.0`.
- `equity_curve` is append-only and strictly monotonically ordered by `ts` when driven by the engine (one append per `tick`).
- All monetary / quantity values are `float64`; sub-cent drift is accepted for paper-trading. No `Decimal`.

## Testing
- `check_f0_backtest_kernel` §D — cash flow, weighted-avg cost, realized P/L on close.
- Sign-flip path is exercised via `engine._apply_fill_with_tracking`'s flip branch in §E.

