# __init__.py — Spec

## Purpose
Package entry point. Exposes `ChartApp` and `main()` at the `tradinglab` namespace so consumers can write `from tradinglab import ChartApp` or `python -m tradinglab`.

## Public API
- `ChartApp` — re-export from `.app` (the Tkinter application class).
- `main` — re-export from `.app` (convenience launcher).
- `__version__` — re-export from `._version` (the canonical version string from `_version.VERSION`).
- `version_string` — re-export from `._version`. Returns a `"vX.Y.Z (git: <sha> built <date>)"` style descriptor; embeds the optional `_build_info` metadata when a frozen build wrote it.
- `__all__ = ["ChartApp", "main", "__version__", "version_string"]`.

## Dependencies
- Internal: `.app` (the only import — everything else is transitive).
- External: none.

## Design Decisions
- Re-export at package root rather than requiring `from tradinglab.app import ChartApp` — gives a stable public surface even if internal file layout shifts.
- Keep this file trivial so importing the package has minimal cost; heavy wiring happens lazily inside `ChartApp.__init__`.

## Invariants
- `from tradinglab import ChartApp, main` must always work.

## Testing
- `_smoke_full.py:check_00_import` imports the package at the top level and verifies no errors.

## Known limitations / Future work
None.

