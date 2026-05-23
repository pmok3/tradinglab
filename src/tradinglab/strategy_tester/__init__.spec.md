# strategy_tester/__init__.py — Spec

## Purpose
Public re-export surface for the Strategy Tester package. Lets callers `from tradinglab.strategy_tester import TestConfig, run, AcceptanceToken` without knowing the module structure.

## Re-exports
- From `acceptance`: `AcceptanceToken`, `RunCancelled`
- From `model`: `CURRENT_SCHEMA_VERSION`, `CostModel`, `DatePreset`, `RunStatus`, `TestConfig`, `TestRun`, `UniverseKind`, `UniverseSpec`, `make_run_id`, `validate_config`
- From `universe`: `PRESETS`, `PresetMissing`, `ResolvedUniverse`, `WatchlistMissing`, `list_presets`, `resolve_universe` (aliased from `resolve`)
- From `evaluator`: `UnsupportedTriggerKind`, `evaluate_symbol`
- From `runner`: `DEFAULT_MAX_WORKERS`, `RunResult`, `resolve_date_range`, `run`
- From `screenshot`: `ScreenshotSpec`, `render_trade_screenshot`, `select_window`, `trade_filename`
- From `report`: `AGGREGATE_FILENAME`, `BOOTSTRAP_SAMPLES_DEFAULT`, `ConfidenceInterval`, `PerSymbolStats`, `PerYearStats`, `RunAggregate`, `aggregate_run`, `bootstrap_ci`, `compute_aggregate`, `daily_sharpe`, `daily_sortino`, `expectancy`, `load_aggregate`, `max_drawdown`, `profit_factor`, `save_aggregate`, `wilson_score_ci`, `write_run_csv`
- From `export` (PR 5): `HTML_FILENAME`, `PDF_FILENAME`, `export_html`, `export_pdf`
- From `storage` (PR 5): `delete_run`, `list_runs`, `list_runs_with_paths`, `load_manifest`, `run_dir_for`, `runs_dir`

## Design Decisions
- **Single import boundary** — GUI / runner / tests all import from the package root, not from submodules. Makes future refactors cheaper.
- **Tk-free re-exports only** — the GUI integration layer (`gui/strategy_app.py`, PR 4) is kept separate so headless callers (smoke tests, batch jobs) never touch Tk.

## Testing
Covered by submodule tests; this file has no logic beyond imports.

## See also
- All sibling `.spec.md` files in this directory.
- `docs/SPEC_INDEX.md` — alphabetised catalog of every spec.
