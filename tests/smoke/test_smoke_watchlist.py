"""Per-feature smoke subset: watchlist tabs / pinning / Space-cycle / save."""
from __future__ import annotations

import pytest

from tests.smoke.test_smoke_full import (
    check_c0_watchlist_tab,
    check_c5_notebook,
    check_c6_bad_ticker,
    check_d12_companion_prefetch_warms_cache,
    check_d13_watchlist_pinned_subtabs,
    check_d15_pin_kicks_preload,
    check_d21_space_cycles_watchlist,
    check_d22_plus_button_adds_watchlist,
    check_d36_watchlist_explicit_save,
)

_CHECKS = [
    check_c0_watchlist_tab,
    check_c5_notebook,
    check_c6_bad_ticker,
    check_d12_companion_prefetch_warms_cache,
    check_d13_watchlist_pinned_subtabs,
    check_d15_pin_kicks_preload,
    check_d21_space_cycles_watchlist,
    check_d22_plus_button_adds_watchlist,
    check_d36_watchlist_explicit_save,
]


@pytest.mark.parametrize("check", _CHECKS, ids=lambda c: c.__name__)
def test_watchlist(app, check) -> None:
    check(app)
