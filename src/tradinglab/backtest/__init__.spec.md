# backtest/__init__.py — Spec

## Purpose
Aggregates the headless sandbox kernel and re-exports its public surface. The kernel is a deterministic, synchronous, dependency-free (no Tk, no matplotlib) bar-replay engine: feed it a `SessionSpec` plus per-symbol `BarSeries` and the same input always produces a byte-identical `SessionResult`. The Tk-side controller (`replay.SandboxController`) and the GUI dialogs / panels live elsewhere.

## Public API (re-exports)
From `.bars`: `BarSeries`, `from_candles`.
From `.clock`: `Clock`.
From `.orders`: `Side`, `Order`, `Fill`.
From `.fills`: `apply_fills`.
From `.portfolio`: `Position`, `Portfolio`.
From `.journal`: `PreTradeEntry`, `PostTradeReview`.
From `.session`: `SessionSpec`, `SessionResult`, `ENGINE_VERSION`.
From `.engine`: `SandboxEngine`.

Not re-exported (callers import directly):
- `backtest/deck.py` — eligible-day enumeration + seeded shuffle.
- `backtest/tags.py` — `TagStore` setup-tag taxonomy.
- `backtest/aggregation.py` — primary→higher-timeframe candle bucketing.
- `backtest/replay.py` — `SandboxController` (Tk-coupled).
- `backtest/persistence.py` — `save_session` / `load_session`.
- `backtest/performance.py` — `build_trade_rows`, `build_setup_aggregates`.

## Dependencies
- Internal: `..models.Candle` (only as input to `from_candles`).
- External: `numpy`.
- **No Tk, no matplotlib** at the kernel layer — these only appear under `replay`, and only the Tk-coupled subset of `gui/` reaches into `replay`.

## Design Decisions
- **Per-field ndarrays, not list-of-Candle**: see `bars.spec.md`. Locks the data layout shared by sandbox replay and the Strategy Tester evaluator.
- **Deterministic round-trip is the contract**: every output observable from the engine is captured in `SessionResult.to_dict()`. Reproducibility check (`check_f1`) compares two replays byte-for-byte.
- **No Tk imports at the kernel layer**: keeps the engine importable from headless smoke checks and lets the Strategy Tester instantiate it from worker threads.
- **`replay` / `tags` / `persistence` / `performance` not auto-imported** — they pull `dataclasses`, `pathlib`, `tkinter`, etc. that the pure-kernel consumer doesn't need. Callers import explicitly.

## Invariants
- After `import tradinglab.backtest`, every symbol in the public-API list above resolves.
- `import tradinglab.backtest` succeeds in a headless environment (no display, no Tk runtime). `replay` is intentionally NOT auto-imported.
- `ENGINE_VERSION` is the only mutable surface that breaks SessionResult-from-disk back-compat. Bumping it is intentional and visible.

## Testing
- `check_f0_backtest_kernel` — `BarSeries` shape, `Clock` exhaustion, `apply_fills` slippage direction, `Portfolio` cash flow + weighted-avg cost + realized P/L on close, MAE/MFE accounting against bar H/L, `PostTradeReview` emitted on close.
- `check_f1_session_reproducibility` — same `SessionSpec` replayed twice on identical input bars produces byte-identical `SessionResult` JSON.

## Known limitations
- No stops / limits / bracket orders (Phase 2). No automated `on_bar` strategy hook (Phase 2).
- Shorts ARE supported via negative quantity. Borrow / locate fees are not modelled (Phase 2). Multi-currency, options pricing not supported.
