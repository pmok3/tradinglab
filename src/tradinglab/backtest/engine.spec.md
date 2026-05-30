# backtest/engine.py — Spec

## Purpose
The `SandboxEngine` — headless replay kernel composing [`Clock`](clock.spec.md), [`Portfolio`](portfolio.spec.md), the pending-order queue, and the per-symbol MAE/MFE tracker. Drives the locked contract: market-only fills at the next bar's open ± slippage, multi-ticker / multi-position with all symbols advancing in lockstep, fully synchronous, every observable captured in [`SessionResult`](session.spec.md).

## Public API
- `@dataclass class SandboxEngine(spec: SessionSpec, bars_by_symbol: Dict[str, BarSeries], master_timeline: Optional[np.ndarray] = None)`.
- `tick() -> bool` — advance one bar. Returns `False` on exhausted clock.
- `submit_order(order, pre_trade=None)` — queue for the next tick; pre-trade entry is filed immediately.
- `register_bars(symbol, bars) -> bool` — make a symbol tradeable mid-session (open-universe). **Idempotent** on same-content fingerprint (returns `False`); **rejects** different-content re-register with `ValueError`.
- `flatten_all_at_close(last_bar_ts, prices, *, order_id_prefix="auto-flat") -> List[Fill]` — synthesize zero-slippage / zero-commission close fills for every open position; emits a `PostTradeReview` per close. Used by the auto-cycle path.
- `result() -> SessionResult` — snapshot current state.
- `run_to_completion() -> SessionResult` — drive `tick()` until exhausted (used by Phase 2 batch runners and the f1 reproducibility smoke).
- Engine state (read-only from the outside): `clock`, `portfolio`, `pending_orders`, `fills`, `pre_trades`, `post_trades`, `cash_adjustments`, `quantity_adjustments`. The two adjustment lists are populated by the corporate-action tick phase (between MAE/MFE roll and mark-to-market) when `register_corporate_actions(symbol, bundle)` has been called for a symbol; they surface unchanged on `result().cash_adjustments` / `result().quantity_adjustments` for downstream consumers (proximity rollup, save/load round-trip).
- `register_corporate_actions(symbol, actions) -> int` — register a per-symbol list of `CorporateAction` records so dividends / splits are applied automatically during the corporate-action phase. Engine-version-stable (`ENGINE_VERSION="sandbox-1d"` unchanged). Idempotent on same-content re-register; returns the count registered (0 on idempotent re-call).

## Dependencies
- Internal: [`actions`](actions.spec.md), [`bars`](bars.spec.md), [`clock`](clock.spec.md), [`fills`](fills.spec.md), [`journal`](journal.spec.md), [`orders`](orders.spec.md), [`portfolio`](portfolio.spec.md), [`session`](session.spec.md).
- External: `numpy`.

## Design Decisions
- **Master timeline is frozen at construction**. When `master_timeline` is supplied (open-universe path) the engine clock is anchored on it; symbols registered later via `register_bars` do **not** extend it. A mutating master timeline would invalidate `clock.index` mid-session and break reproducibility — explicit per the design critique. When `master_timeline=None` (legacy / f1 smoke path) the timeline is the sorted union of all `bars_by_symbol` ts.
- **`register_bars` is idempotent + immutable**. Same-content re-register is a no-op; different-content re-register raises. Rationale: replacing a `BarSeries` mid-session would retroactively change open-position MAE/MFE and break determinism. Caller-side fingerprint uses `(len, first_ts, last_ts, last_close)` — cheap, catches realistic re-fetch/cache cases.
- **Four-phase tick** in fixed order: (1) fills → (2) MAE/MFE roll → (3) corporate actions → (4) mark-to-market. A fill on this tick must contribute to MAE/MFE *from* this bar; opening the cursor before phase 2 makes that automatic. Corporate-action cash / quantity adjustments land before the equity point for that timestamp.
- **Open-trade cursor per symbol** tracks `(side, entry_ts, entry_price, mae_price, mfe_price, ref_pre_trade_id)`. Adds use weighted-avg entry (mirrors `Position.avg_cost`); flip-through-zero closes the original cursor and opens the new one at `fill_price`; partial reduce keeps the cursor with fresh quantity.
- **Symbols missing a bar at `ts` are silently skipped** for fills and excursion-rolling. Their pending orders re-queue for a later tick. Mark-to-market falls back to `avg_cost` for those symbols (delegated to `Portfolio.mark_to_market`).
- **`flatten_all_at_close` uses synthetic fills** (no slippage, no commission, prefixed `order_id` like `"auto-flat-AMD-1730000000"`). They route through the same `_apply_fill_with_tracking` machinery so a `PostTradeReview` is emitted with full MAE/MFE and the cursor's pre-trade link is preserved. Pending orders that never filled are dropped — their target bars no longer exist.
- **Auto-flatten falls back to `avg_cost`** — `flatten_all_at_close(last_bar_ts, prices)` closes each open position at the supplied close; symbols missing from the price map are closed at `avg_cost`, yielding zero realised P/L. Caller must provide a complete price map for accurate end-of-session realisation. Silent fallback intentional to avoid blocking session close.
- **No look-ahead — bar-close decision, next-open fill** — An order placed during bar `t` (after `bar_close[t-1]`, before `bar_close[t]`) fills at `open[t+1]`. Indicator values for bar `t` are only finalised at `bar_close[t]`.
- **`_OpenTradeCursor` is private**. One per active symbol, fields `(symbol, side, entry_ts, entry_price, quantity, mae_price, mfe_price, ref_pre_trade_id)` — see `backtest/engine.py` `_OpenTradeCursor`. Treated as opaque by anything outside the engine; not part of `SessionResult`.

## Invariants
- `len(equity_curve) == ticks_completed` (one MTM point per successful `tick`).
- After `register_bars(s, b)`, `s in bars_by_symbol`. Re-register with different content raises before any state mutation.
- For a fixed `(SessionSpec, bars_by_symbol)` and a fixed sequence of `submit_order` calls, two engines produce byte-identical `result().to_dict()` JSON.
- `master_timeline` length never changes after `__post_init__`.
- No `tkinter` or `matplotlib` import.

## Testing
- `check_f0_backtest_kernel` — composability (BarSeries / Clock / fills / Portfolio), MAE/MFE accounting against bar H/L, `PostTradeReview` on close.
- `check_f1_session_reproducibility` — byte-identical `SessionResult` JSON across two replays.
- `check_g0_sandbox_replay_integration` — `submit_order` queue + fill semantics through the controller.
- `check_g2_sandbox_open_universe` — `register_bars` idempotency + master-timeline freeze.
- `check_b6_sandbox_auto_cycle` — `flatten_all_at_close` and the synthetic-fill / cursor-routing path.
