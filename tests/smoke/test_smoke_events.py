"""Per-feature smoke subset: events & corporate actions.

Imports check functions from ``test_smoke_full`` and runs them as
parametrised tests against the session-scoped ``app`` fixture defined
in ``conftest.py``. Use this file for fast iteration on the events
subsystem (protocol registry, corporate-action ticks, blind redaction,
master-timeline freezing, save/load round-trip, on-disk cache,
provider-drift determinism, volume time-of-day shading).

``pytest tests/smoke/test_smoke_full.py`` remains the canonical
end-to-end gate (now per-check parametrised so per-feature subset
files are primarily an iteration-speed tool).
"""
from __future__ import annotations

import pytest

from tests.smoke.test_smoke_full import (
    check_b60_events_protocol_registry,
    check_b61_engine_corporate_action_phase,
    check_b62_events_blind_redaction,
    check_b63_events_master_timeline_frozen,
    check_b64_save_load_proximity_and_adjustments,
    check_b65_events_cache_disk_roundtrip,
    check_b66_events_cycle_clears,
    check_b67_events_provider_drift_determinism,
    check_b68_volume_tod_shading,
)

_CHECKS = [
    check_b60_events_protocol_registry,
    check_b61_engine_corporate_action_phase,
    check_b62_events_blind_redaction,
    check_b63_events_master_timeline_frozen,
    check_b64_save_load_proximity_and_adjustments,
    check_b65_events_cache_disk_roundtrip,
    check_b66_events_cycle_clears,
    check_b67_events_provider_drift_determinism,
    check_b68_volume_tod_shading,
]


@pytest.mark.parametrize("check", _CHECKS, ids=lambda c: c.__name__)
def test_events(app, check) -> None:
    check(app)
