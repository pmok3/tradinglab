"""Per-feature smoke subset: render / blit / pan invariants.

Covers the topology-preserving paint pipeline checks that aren't
specific to drill-down or compare-mode:

* ``check_d30`` — drill-down ylim no deferred-render race
* ``check_d31`` — pan-end invalidates blit background
* ``check_d32`` — multi-step interaction-sequence matrix
* ``check_d33`` — ``_blit_overlays`` invariant (never reduces candle count)
* ``check_d43`` — ``compute_layout`` preserves 3:1 ratio for no-indicators
* ``check_d44`` — ``_AdaptiveXLocator`` tolerates tz-mixed candles
* ``check_d46`` — pan slice change does NOT trigger ``canvas.draw()``

Imports from ``test_smoke_full``; runs as parametrised tests against
the session-scoped ``app`` fixture. ``pytest tests/smoke/test_smoke_full.py``
remains the canonical end-to-end gate.
"""
from __future__ import annotations

import pytest

from tests.smoke.test_smoke_full import (
    check_d30_drilldown_ylim_no_deferred_render_race,
    check_d31_pan_end_invalidates_blit_bg,
    check_d32_interaction_sequence_matrix,
    check_d33_blit_overlays_invariant,
    check_d43_compute_layout_preserves_3to1_ratio,
    check_d44_locator_handles_tz_mixed_candles,
    check_d46_pan_slice_change_no_draw_flash,
)

_CHECKS = [
    check_d30_drilldown_ylim_no_deferred_render_race,
    check_d31_pan_end_invalidates_blit_bg,
    check_d32_interaction_sequence_matrix,
    check_d33_blit_overlays_invariant,
    check_d43_compute_layout_preserves_3to1_ratio,
    check_d44_locator_handles_tz_mixed_candles,
    check_d46_pan_slice_change_no_draw_flash,
]


@pytest.mark.parametrize("check", _CHECKS, ids=lambda c: c.__name__)
def test_render(app, check) -> None:
    check(app)
