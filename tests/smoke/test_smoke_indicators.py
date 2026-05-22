"""Per-feature smoke subset: indicators (catalog + dialog + render integration)."""
from __future__ import annotations

import pytest

from tests.smoke.test_smoke_full import (
    check_b27_indicator_dialog_dark_mode,
    check_b28_sandbox_indicator_survives_tick,
    check_b41_indicator_intervals_per_instance,
    check_b42_indicator_color_palette,
    check_b43_bollinger_bands_ema,
    check_b44_bollinger_separate_std_window,
    check_b45_vwap_session_anchored,
    check_b46_smi_stochastic_momentum_index,
    check_b47_indicator_pane_yfit_after_click,
    check_b48_adx_average_directional_index,
    check_b49_atr_average_true_range,
    check_b50_lrsi_laguerre_rsi,
    check_b70_keltner_channels,
    check_b71_macd,
    check_b72_chandelier_stops,
    check_b73_per_indicator_popup,
    check_d39_indicators_phase1,
    check_d41_indicator_menu_add_routes,
    check_d42_indicator_scope_picker,
    check_d48_indicator_dialog,
    check_d49_indicator_render_integration,
    check_d50_indicators_menu_wiring,
    check_d51_hover_indicator_readout,
    check_d54_indicator_reorder,
    check_d55_indicator_preset_menu,
    check_d56_ema_seeding_alignment,
    check_d57_performance_view_equity_csv_export,
    check_d58_anchored_vwap,
    check_d59_relative_volume,
)

_CHECKS = [
    check_d39_indicators_phase1,
    check_d41_indicator_menu_add_routes,
    check_d42_indicator_scope_picker,
    check_d48_indicator_dialog,
    check_d49_indicator_render_integration,
    check_d50_indicators_menu_wiring,
    check_d51_hover_indicator_readout,
    check_d54_indicator_reorder,
    check_d55_indicator_preset_menu,
    check_d56_ema_seeding_alignment,
    check_d57_performance_view_equity_csv_export,
    check_d58_anchored_vwap,
    check_d59_relative_volume,
    check_b27_indicator_dialog_dark_mode,
    check_b28_sandbox_indicator_survives_tick,
    check_b41_indicator_intervals_per_instance,
    check_b42_indicator_color_palette,
    check_b43_bollinger_bands_ema,
    check_b44_bollinger_separate_std_window,
    check_b45_vwap_session_anchored,
    check_b46_smi_stochastic_momentum_index,
    check_b47_indicator_pane_yfit_after_click,
    check_b48_adx_average_directional_index,
    check_b49_atr_average_true_range,
    check_b50_lrsi_laguerre_rsi,
    check_b70_keltner_channels,
    check_b71_macd,
    check_b72_chandelier_stops,
    check_b73_per_indicator_popup,
]


@pytest.mark.parametrize("check", _CHECKS, ids=lambda c: c.__name__)
def test_indicators(app, check) -> None:
    check(app)
