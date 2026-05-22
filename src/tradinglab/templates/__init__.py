"""Starter-pack templates seeding.

This subpackage owns the first-run seeding of the user-local
strategy libraries with the templates that ship in ``data/``:

- 5 entry-strategy JSONs   → ``<cache_dir>/entry_strategies/``
- 5 exit-strategy JSONs    → ``<cache_dir>/exit_strategies/``
- 5 scanner-definition JSONs → ``<cache_dir>/scans/``

A sentinel file ``<cache_dir>/.templates_seeded`` is written after
the first successful seed so subsequent launches are no-ops. The
user can force a re-seed by deleting the sentinel before relaunching,
or via a future Tools → Restore Default Templates affordance.

Indicator presets in ``data/indicator_presets/`` are bundled but NOT
auto-loaded — they're consumed by a future Indicators → Load preset
from file… UI affordance.
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
