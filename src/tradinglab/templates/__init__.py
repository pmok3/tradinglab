"""Starter-pack templates seeding.

This subpackage owns seeding the user-local strategy libraries with the
templates that ship in ``data/``:

- entry-strategy JSONs     → ``<cache_dir>/entry_strategies/``
- exit-strategy JSONs      → ``<cache_dir>/exit_strategies/``
- scanner-definition JSONs → ``<cache_dir>/scans/``

Seeding is **additive**: each bundled template is offered to the user
exactly once over the app's lifetime, tracked by filename in a JSON
ledger ``<cache_dir>/.templates_seeded``. This delivers newly-shipped
catalog templates to EXISTING users on upgrade — not just fresh installs
— while never clobbering edits or resurrecting deletions. Deleting the
ledger re-offers every bundled template; ``Tools → Restore Default
Templates`` force-copies them all (overwriting same-named files).

Indicator presets in ``data/indicator_presets/`` are ALSO seeded, but
into the single ``indicators.preset_store`` envelope (not per-file):
each bundled ``preset-*.json`` is translated from its compact starter
schema to canonical configs and merged under its display name, tracked
by the same ledger (kind ``indicator_presets``). They then appear in
Indicators → Load Preset.
"""

from .seed import (
    bundled_templates_dir,
    seed_default_templates,
    seed_default_templates_if_empty,
)

__all__ = [
    "bundled_templates_dir",
    "seed_default_templates",
    "seed_default_templates_if_empty",
]
