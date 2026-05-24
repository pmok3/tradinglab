# strategy_tester/runner.py — Spec

## Purpose
Top-level orchestrator for a Strategy Tester Run. Fans out per-symbol workers on a `ThreadPoolExecutor`, integrates results, mutates the on-disk `manifest.json` after every completion so the GUI can poll progress, and finalises status as `DONE` / `CANCELLED` / `FAILED`.

## Public API
- `DEFAULT_MAX_WORKERS` — `min(cpu_count-1, 4)` matching `scanner/runner.py`.
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
- **ThreadPoolExecutor cap = `min(cpu_count-1, 4)`** — copied from `scanner/runner.py` for consistency. Override-able via `max_workers` for stress tests.
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
- **`screenshot_spec` opt-in:** pass an explicit `ScreenshotSpec` to render one PNG per closed trade into `<run_dir>/screenshots/<SYM>_<id>_post.png`. The filename's `<id>` segment is resolved via a fallback chain: `row.pre.order_id → row.post.ref_pre_trade_id → f"t{entry_ts}"`. The mechanical evaluator emits no `PreTradeEntry` records, so `row.pre` is always `None`; before the fallback existed every trade collapsed onto `<SYM>_unknown_post.png` and runs with 60+ trades per symbol produced exactly 1 PNG per symbol. The default `None` disables screenshots entirely (smoke checks use this to stay fast); the GUI passes `ScreenshotSpec()` so production runs always include images. Screenshot failures are logged but never abort a worker — `SessionResult` correctness is the gating artifact, screenshots are complementary.

## Invariants
- `run()` never raises; errors become `RunStatus.FAILED` on the manifest.
- Every `RunResult.test_run.status` is a terminal state (`DONE` / `CANCELLED` / `FAILED`) — never `PENDING` / `RUNNING`.
- `manifest.json` is consistent with the in-memory `TestRun` at function exit.

## Testing
- `tests/unit/strategy_tester/test_runner.py` — happy path (3 symbols, all DONE), cancellation mid-run (CANCELLED + partial outcomes), worker error isolation (one bad symbol doesn't fail the others), `resolve_date_range` for every `DatePreset` value, custom date range round-trip.
- `tests/smoke/test_smoke_strategy.py::check_st0_kernel_only` — full pipeline under `_stub_yfinance`.

## See also
- [acceptance](acceptance.spec.md), [evaluator](evaluator.spec.md), [model](model.spec.md), [storage](storage.spec.md), [universe](universe.spec.md)
- `scanner/runner.spec.md` — sibling ThreadPoolExecutor pattern.
