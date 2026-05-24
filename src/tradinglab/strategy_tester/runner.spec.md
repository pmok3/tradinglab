# strategy_tester/runner.py — Spec

## Purpose
Top-level orchestrator for a Strategy Tester Run. Fans out per-symbol workers on a `ThreadPoolExecutor`, integrates results, mutates the on-disk `manifest.json` after every completion so the GUI can poll progress, and finalises status as `DONE` / `CANCELLED` / `FAILED`.

## Public API
- `DEFAULT_MAX_WORKERS` — resolved at import time. Precedence: (1) persisted `worker_count` tunable from `defaults.get("worker_count")` (if > 0, clamped to 64); (2) auto-detect `os.cpu_count() - 1`, clamped to `[1, 64]`. The previous hard cap of 4 is removed so users who configure e.g. 12 workers in **Settings → Workers** actually get 12 strategy-tester threads.
- `RunResult(test_run, run_dir, universe, outcomes)` — what `run()` returns.
- `run(cfg, *, cancel_token=None, progress=None, max_workers=None, today=None, candles_fetcher=None, entry_loader=None, exit_loader=None, screenshot_spec=None) -> RunResult` — entry point.
- `resolve_date_range(cfg, *, today=None) -> tuple[date, date]` — preset → concrete UTC dates.
- `load_entry_strategy(id) -> EntryStrategy` / `load_exit_strategy(id) -> ExitStrategy` — default loaders (test-overridable).
- `fetch_candles_for_symbol(sym, interval) -> list[Candle]` — default fetcher routing through `DATA_SOURCES["yfinance"]` so the smoke `_stub_yfinance` intercepts cleanly.

## Dependencies
- `concurrent.futures.ThreadPoolExecutor` (stdlib)
- `acceptance.AcceptanceToken`
- `evaluator.evaluate_symbol`
- `model.{TestConfig, TestRun, RunStatus, DatePreset, make_run_id}`
- `universe.resolve`
- `storage.{run_dir_for, save_config, save_manifest, save_session_result_for_symbol}`
- `data.base.DATA_SOURCES` (lazy import — kept off the critical import path)
- `entries.storage.load` / `exits.storage.load` (lazy via `load_entry_strategy` / `load_exit_strategy`)

## Design Decisions
- **Per-symbol independent capital** is implicit in the design: each worker builds its own `SandboxEngine` via `evaluate_symbol`. Workers never share engine state.
- **ThreadPoolExecutor cap** — removed the old hard cap of 4. `DEFAULT_MAX_WORKERS` now reads the persisted `worker_count` tunable (if > 0, clamped to 64) or falls back to `max(1, min(cpu_count-1, 64))`. The GUI passes `max_workers=app._worker_count` from `StrategyTab._on_run_clicked` so the user-configured count is always honoured. Override-able via `max_workers` for tests.
- **Cancellation is checked at submission boundary + on every result integration** — per-symbol evaluation is uninterruptible (bounded), so polling inside is unnecessary.
- **`progress` callback runs on the orchestrator thread, not workers** — GUI integrators thread-marshal it via `app.after_idle(...)`. Smoke tests can use it directly.
- **All exceptions in workers are captured into `_SymbolOutcome.error`** — one bad symbol never aborts the Run. The orchestrator integrates outcomes into a final-status decision.
- **Final-status rules:**
  - If cancel was tripped → `CANCELLED` (partial results preserved).
  - If every symbol errored → `FAILED` (with first error message).
  - If at least one symbol succeeded → `DONE` (even if some had errors).
- **Override-able loaders + fetcher** — keeps the runner unit-testable without hitting disk or network. Smoke tests pass closures returning in-memory strategies + synthetic candles.
- **Manifest writes after every completion** — atomic via `atomic_write_json`. Cheap (< 1 KB per write). Lets the GUI poll without blocking.
- **`run_id` is `make_run_id(cfg, engine_version=ENGINE_VERSION)` + ISO timestamp suffix** — re-running an identical config produces the same `run_id` but a distinct on-disk directory, per the locked design.
- **`screenshot_spec` opt-in:** pass an explicit `ScreenshotSpec` to render one PNG per closed trade into `<run_dir>/screenshots/<SYM>_<id>_post.png`. The filename's `<id>` segment is resolved via a fallback chain: `row.pre.order_id → row.post.ref_pre_trade_id → f"t{entry_ts}"`. The mechanical evaluator emits no `PreTradeEntry` records, so `row.pre` is always `None`; before the fallback existed every trade collapsed onto `<SYM>_unknown_post.png` and runs with 60+ trades per symbol produced exactly 1 PNG per symbol. The default `None` disables screenshots entirely (smoke checks use this to stay fast); the GUI passes `ScreenshotSpec()` so production runs always include images. Screenshot failures are logged but never abort a worker — `SessionResult` correctness is the gating artifact, screenshots are complementary. The worker also threads the loaded `entry_strategy` and `exit_strategy` into `_render_screenshots_for_symbol` and then `render_trade_screenshot` so each PNG can overlay the indicators referenced by the strategy (EMA cross gets EMA(3) + EMA(8) lines on the price pane, etc.). See `screenshot.spec.md` "Indicator overlays" for the walking + colour-cycle rules.
- **RTH-only filter (default):** the `_worker` slices candles to the configured date range, then — when `cfg.include_extended_hours is False` (the default) — drops every bar outside US-equity Regular Trading Hours via `_filter_rth_only`. RTH = Mon-Fri AND 09:30 ≤ ET time ≤ 16:00, using `_is_regular_session` / `_bar_ts_to_et` imported from `evaluator`. Indicators (EMA, SMA, RSI, VWAP, ...) computed inside the evaluator therefore see only RTH bars by default and aren't skewed by premarket / postmarket prints. Opt in via `TestConfig.include_extended_hours=True` (GUI checkbox in `strategy_tab.py`). Saturday-only inputs collapse to an empty candle list — the evaluator handles this without crashing.

## Invariants
- `run()` never raises; errors become `RunStatus.FAILED` on the manifest.
- Every `RunResult.test_run.status` is a terminal state (`DONE` / `CANCELLED` / `FAILED`) — never `PENDING` / `RUNNING`.
- `manifest.json` is consistent with the in-memory `TestRun` at function exit.

## Testing
- `tests/unit/strategy_tester/test_runner.py` — happy path (3 symbols, all DONE), cancellation mid-run (CANCELLED + partial outcomes), worker error isolation (one bad symbol doesn't fail the others), `resolve_date_range` for every `DatePreset` value, custom date range round-trip.
- `tests/unit/strategy_tester/test_worker_scaling.py` — `_default_max_workers` scales above 4 on high-core machines, respects persisted setting, clamps to 64, minimum 1; `runner.run` forwards `max_workers` to `ThreadPoolExecutor`.
- `tests/smoke/test_smoke_strategy.py::check_st0_kernel_only` — full pipeline under `_stub_yfinance`.

## See also
- [acceptance](acceptance.spec.md), [evaluator](evaluator.spec.md), [model](model.spec.md), [storage](storage.spec.md), [universe](universe.spec.md)
- `scanner/runner.spec.md` — sibling ThreadPoolExecutor pattern.
