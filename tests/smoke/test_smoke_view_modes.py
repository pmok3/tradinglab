"""Per-feature smoke subset: View modes (Heikin-Ashi + Highlight Key Bars + flat HA).

Covers the dedicated ``check_*`` functions for the View-menu visual
toggles. The handlers route through ``_repaint_visible_slot_glyphs``
(perf-3 optimization) so a glyph-only repaint replaces the full
``_render`` for the color-only toggles.
"""
from __future__ import annotations

import pytest

from tests.smoke.test_smoke_full import (
    check_b35b_highlight_ha_flat_overlay_toggle,
    check_b69_color_only_toggles_use_glyph_repaint,
)


_CHECKS = [
    check_b35b_highlight_ha_flat_overlay_toggle,
    check_b69_color_only_toggles_use_glyph_repaint,
]


@pytest.mark.parametrize("check", _CHECKS, ids=lambda c: c.__name__)
def test_view_modes(app, check) -> None:
    check(app)
