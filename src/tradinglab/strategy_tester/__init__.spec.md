# strategy_tester/__init__.py — Spec

## Purpose
Public re-export surface for the Strategy Tester package. Lets callers `from tradinglab.strategy_tester import TestConfig, run, AcceptanceToken` without knowing the module structure.

## Re-exports
- From `acceptance`: `AcceptanceToken`, `RunCancelled`
- From `model`: `CURRENT_SCHEMA_VERSION`, `CostModel`, `DatePreset`, `RunStatus`, `TestConfig`, `TestRun`, `UniverseKind`, `UniverseSpec`, `make_run_id`, `validate_config`
- From `universe`: `PRESETS`, `PresetMissing`, `ResolvedUniverse`, `WatchlistMissing`, `list_presets`, `resolve_universe` (aliased from `resolve`)
- From `evaluator`: `UnsupportedTriggerKind`, `evaluate_symbol`
- From `runner`: `DEFAULT_MAX_WORKERS`, `RunResult`, `resolve_date_range`, `run`

## Design Decisions
- **Single import boundary** — GUI / runner / tests all import from the package root, not from submodules. Makes future refactors cheaper.
- **Tk-free re-exports only** — the GUI integration layer (`gui/strategy_app.py`, PR 4) is kept separate so headless callers (smoke tests, batch jobs) never touch Tk.

## Testing
Covered by submodule tests; this file has no logic beyond imports.

## See also
- All sibling `.spec.md` files in this directory.
- `docs/SPEC_INDEX.md` — alphabetised catalog of every spec.
