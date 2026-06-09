# tradinglab.templates

First-run seeding of bundled "starter pack" strategy templates into the
user-local libraries.

## Public API

- `seed_default_templates_if_empty(*, on_seed=None) -> dict` — call at
  every startup. **Additive:** offers each bundled template to the user
  exactly once over the app's lifetime, tracked by filename in a JSON
  ledger `<cache_dir>/.templates_seeded`. New catalog templates shipped
  in a later build therefore reach EXISTING users on upgrade (not just
  fresh installs); already-offered templates (incl. ones the user
  deleted) are never re-offered, and existing files are never clobbered.
  (Name kept for back-compat — it predates the ledger, when it was a
  one-shot "seed an empty library then write a sentinel".)
- `seed_default_templates(*, force=False, on_seed=None) -> dict` —
  unconditional. `force=True` overwrites existing files of the same
  name. Backs the explicit "Restore Default Templates" menu action.
  Does NOT touch the ledger (the next additive run reconciles it).
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

## Seeding model

`seed_default_templates_if_empty` (the startup path) is **additive and
idempotent per template**:

1. **JSON ledger.** `<cache_dir>/.templates_seeded` is
   `{"version": 1, "seeded": {kind: [filenames]}}` recording every
   bundled template already offered. `_load_ledger()` returns an empty
   mapping when the file is missing, corrupt, or in the **legacy
   plain-text format** (pre-ledger sentinel) — so the first launch after
   upgrading treats every currently-bundled template as a candidate.
2. **Offer-once.** A bundled file is copied only when its name is NOT in
   the ledger for that kind. After consideration its name is folded into
   the ledger whether it was copied, skipped, or already present — so it
   is never reconsidered. Deleting a seeded template makes it stay
   deleted.
3. **Never clobber.** If a same-named file already exists in the target
   library (user edit, or seeded by a pre-ledger build), it is recorded
   but NOT overwritten.
4. **Cheap + best-effort.** Runs every launch (globs a few dozen bundled
   JSONs + reads the ledger). The ledger is rewritten only when it
   changed or is still in legacy format. A write failure just means the
   next launch re-offers (the per-file existence check keeps it safe).

`seed_default_templates` (the **Restore Default Templates** path) is the
unconditional primitive and keeps the older guards:

- **Per-library "empty" guard** (`force=False`): a library is only
  seeded when its target dir has no visible `*.json` (`_index.json`
  ignored). `force=True` bypasses it.
- **Per-file existence guard** (`force=False`): an existing destination
  is skipped; `force=True` overwrites.

### Why additive (the bug this fixes)

v0.1–v0.2 shipped 5 entry templates; the catalog grew to 20 from v0.3.0.
The old binary sentinel + empty-library guard meant a user who installed
an early build (5 seeded + sentinel) never received the 15 new templates
on upgrade — the seeder short-circuited on the sentinel. The frozen
`.exe` always bundled all 20 (`TradingLab.spec` ships the whole
`data/entry_strategy_templates/` dir); only the runtime seeder gated
them. The ledger delivers the new ones additively.

## Owner contract

`tradinglab.app.ChartApp.__init__` schedules
`seed_default_templates_if_empty()` via `after_idle` (so first paint
isn't blocked on first-run file I/O). Failures (e.g. OS permissions) are
logged but non-fatal: the app continues with whatever the user's library
currently contains.

## File structure

- `__init__.py` — package re-exports.
- `seed.py` — implementation.
- `seed.spec.md` — this file.
