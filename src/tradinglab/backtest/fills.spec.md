# backtest/fills.py — Spec

## Purpose
Pure-function fill model. Given a list of pending market orders and the next bar's open price for each symbol, return the resulting fills with slippage applied in the worse direction (BUY pays more, SELL receives less).

## Public API
- `apply_fills(orders, next_bar_opens, next_bar_ts, slippage_bps, commission, commission_per_share=0.0) -> List[Fill]` — build fills for every order whose symbol has a next-bar open in `next_bar_opens`. Orders for absent symbols are silently skipped (the engine re-queues them). Per-fill commission is `commission + commission_per_share * abs(quantity)`.

## Dependencies
- Internal: `.orders` (`Side`, `Order`, `Fill`).

## Phase 1 fill model (checklist)
- Market-only at the next bar's `open`.
- Full requested size, no partial fills.
- No commission by default. Optional flat + per-share commission when callers pass non-zero values. No spread. Slippage = 0 by default.
- Fill price is `open[t+1]` exactly when slippage is zero.
- P/L is best-case; do not interpret backtest equity as net-of-costs.

## Design Decisions
- **Pure function, no shared state**. Slippage / flat commission / per-share commission live on the engine's `SessionSpec` and are passed in per call; the function itself has no globals. Required for the determinism contract.
- **Slippage is `slippage_bps / 10_000` of the open**, applied additively in price units in the worse-fill direction. BUY pays `open + slip`, SELL receives `open − slip`. Note divergence from pure-percentage / fixed-bps models that some references use.
- **No partial fills**. Phase 1's bar-replay model has no order book — every order either fills in full at the next open or stays queued for a future tick.

## Invariants
- For a fixed `(orders, next_bar_opens, next_bar_ts, slippage_bps, commission, commission_per_share)` tuple the output `Fill` list is byte-identical across calls (Q-12 reproducibility commitment).
- `Fill.fill_price` for a BUY is `>= open`; for a SELL is `<= open` (equal only when `slippage_bps == 0`).
- Symbols absent from `next_bar_opens` produce zero fills — the engine's `_process_fills` re-queues those orders.
- No `tkinter` or `matplotlib` import.

## Testing
- `check_f0_backtest_kernel` §C — slippage direction for both sides; absent-symbol no-op.

## See also
- [orders](orders.spec.md), [engine](engine.spec.md).
