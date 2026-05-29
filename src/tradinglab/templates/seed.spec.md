# tradinglab.templates

First-run seeding of bundled "starter pack" strategy templates into the
user-local libraries.

## Public API

- `seed_default_templates_if_empty(*, on_seed=None) -> dict` — call once
  per process at startup. Writes a sentinel
  `<cache_dir>/.templates_seeded` so the next launch is a no-op.
- `seed_default_templates(*, force=False, on_seed=None) -> dict` —
  unconditional. `force=True` overwrites existing files of the same
  name. Use this for an explicit "Restore Default Templates" action.
- `bundled_templates_dir(kind_subdir: str) -> Path` — resolves the
  bundled-templates dir under `data/`. Works in both source and frozen
  builds via `tradinglab._resources.resource_path`.

The return dict shape is
`{"copied": int, "skipped": int, "by_kind": {kind: (copied, skipped)}}`.

## What's seeded

| Bundled subdir under `data/` | User-local target dir | Kinds |
| --- | --- | --- |
| `entry_strategy_templates/` | `<cache_dir>/entry_strategies/` | 20+ entries |
| `exit_strategy_templates/` | `<cache_dir>/exit_strategies/` | 20+ exits |
| `scanner_templates/` | `<cache_dir>/scans/` | 5 scanners |

Indicator presets in `data/indicator_presets/` are bundled but NOT
auto-seeded; they're consumed by a future Indicators → Load preset
from file… affordance. Strategy-combination templates in
`data/strategy_combination_templates/` are also bundled but not
auto-seeded because strategy-tester runs do not yet have a user-local
template library.

## Guards

The seeder is conservative:

1. **Per-library "empty" guard.** A given user library is only seeded
   when its target dir has no visible `*.json` files (the `_index.json`
   meta-file is ignored). `force=True` bypasses the guard.
2. **Per-file existence guard.** Even with `force=False`, a destination
   that already exists is skipped (an explicit collision counts as
   "library not empty" but we double-check at copy time).
3. **Sentinel.** `seed_default_templates_if_empty` writes
   `<cache_dir>/.templates_seeded` after a successful seed (or no-op).
   Subsequent launches short-circuit. Delete the sentinel to force a
   re-seed; the empty-library guard still applies, so a re-seed of a
   non-empty library is a no-op.

## Owner contract

`tradinglab.app.ChartApp.__init__` calls
`seed_default_templates_if_empty()` after settings load and before any
GUI is constructed. Failures (e.g. OS permissions) are logged but
non-fatal: the app continues with whatever the user's library currently
contains.

## File structure

- `__init__.py` — package re-exports.
- `seed.py` — implementation.
- `seed.spec.md` — this file.
