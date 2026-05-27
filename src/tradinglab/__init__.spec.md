# __init__.py — Spec

## Purpose
Package entry point. Exposes `ChartApp` and `main()` LAZILY at the `tradinglab` namespace so consumers can write `from tradinglab import ChartApp` or `python -m tradinglab` without paying the cost of importing the full GUI stack (matplotlib + Tk + every subsystem `app.py` pulls in transitively) on bare `import tradinglab`.

## Public API
- `ChartApp` — loaded on first attribute access via PEP 562 `__getattr__` from `.app`. First reference triggers the import; subsequent accesses are cached on `globals()` so `hasattr(tradinglab, "ChartApp")` and repeated lookups don't pay the dispatch cost.
- `main` — same lazy treatment as `ChartApp`.
- `__version__` — eagerly re-exported from `._version` (the canonical version string from `_version.VERSION`). Cheap to import (no transitive deps), so kept eager.
- `version_string` — eagerly re-exported from `._version`. Returns a `"vX.Y.Z (git: <sha> built <date>)"` style descriptor; embeds the optional `_build_info` metadata when a frozen build wrote it.
- `__all__ = ["ChartApp", "main", "__version__", "version_string"]`.
- `__dir__()` returns `__all__ ∪ globals()` so `dir(tradinglab)` and auto-complete surface the lazy attributes the same way as eager ones.

## Dependencies
- Internal: `.app` (imported LAZILY only when `ChartApp` or `main` is first accessed; `._version` imported eagerly).
- External: none.

## Design Decisions
- Re-export at package root rather than requiring `from tradinglab.app import ChartApp` — gives a stable public surface even if internal file layout shifts.
- **Lazy via PEP 562 `__getattr__`** — `import tradinglab` and `from tradinglab import __version__` measure at ≈0ms; first access of `ChartApp` (or `main`) takes ~922ms as the full GUI stack initializes. Saves ~300-800ms on `--version` probes, test discovery, and non-GUI tooling. See audit `tradinglab-init-lazy`.

## Invariants
- `from tradinglab import ChartApp, main` must always work (PEP 562 makes this transparent).
- `import tradinglab` must NOT import `app.py` (the lazy invariant — pinned by the import-cost regression test).

## Testing
- `_smoke_full.py:check_00_import` imports the package at the top level and verifies no errors.

## Known limitations / Future work
None.

