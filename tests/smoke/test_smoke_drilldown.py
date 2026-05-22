"""Per-feature smoke subset: drill-down (double-click → 5m / context-aware ylim).

Imports check functions from ``test_smoke_full`` and runs them as
parametrized tests against the session-scoped ``app`` fixture defined
in ``conftest.py``. Use this file for fast iteration on drill-down
work; ``pytest tests/smoke/test_smoke_full.py`` remains the canonical
end-to-end gate.
"""
from __future__ import annotations

import pytest

from tests.smoke.test_smoke_full import (
    check_d17_double_click_drilldown_to_5m,
    check_d20_drilldown_persists_across_ticker_change,
    check_d30_drilldown_ylim_no_deferred_render_race,
    check_d34_compare_toggle_after_drilldown_ylim,
    check_d38_drilldown_race_and_coverage,
    check_d45_prepost_toggle_rescales_drilldown,
    check_d53_compare_off_during_drilldown_ylim,
    check_d60_drilldown_works_in_heikin_ashi_mode,
    check_d72_chartstack_promote_preserves_view,
)

_CHECKS = [
    check_d17_double_click_drilldown_to_5m,
    check_d20_drilldown_persists_across_ticker_change,
    check_d30_drilldown_ylim_no_deferred_render_race,
    check_d34_compare_toggle_after_drilldown_ylim,
    check_d38_drilldown_race_and_coverage,
    check_d45_prepost_toggle_rescales_drilldown,
    check_d53_compare_off_during_drilldown_ylim,
    check_d60_drilldown_works_in_heikin_ashi_mode,
    check_d72_chartstack_promote_preserves_view,
]


@pytest.mark.parametrize("check", _CHECKS, ids=lambda c: c.__name__)
def test_drilldown(app, check) -> None:
    check(app)
