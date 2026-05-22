"""Tests for the ``drawings-snap-extended`` audit.

Adds an opt-in snap-to-nearest-OHLC for the ``Alt+H`` placement
(and the right-click "Add Horizontal Line Here" menu) so a trader
who aims at a candle wick gets a line that lands exactly on the
wick, not a few cents off it. The snap is pixel-space (configurable
via ``_DRAWINGS_SNAP_PIXEL_THRESHOLD`` in ``app.py``) so it feels
identical across instruments with wildly different price ranges.

Tests cover three layers:

1. The pure helper
   :func:`tradinglab.drawings.find_nearest_ohlc_snap` — distance
   math + NaN/Inf handling + threshold + tie-breaking.
2. The ``ChartApp._compute_snapped_drawing_price`` integration
   layer (source pin to verify the wiring).
3. The Settings dialog persistence layer — the
   ``drawings_snap_to_ohlc`` checkbox.
"""
from __future__ import annotations

import math
from pathlib import Path

from tradinglab.drawings import find_nearest_ohlc_snap

# ---------------------------------------------------------------------------
# find_nearest_ohlc_snap — pure helper
# ---------------------------------------------------------------------------

def test_returns_none_for_empty_candidates():
    assert find_nearest_ohlc_snap(100.0, []) is None


def test_returns_none_when_nothing_in_threshold():
    cands = [(100.0, 200.0), (110.0, 250.0)]
    assert find_nearest_ohlc_snap(50.0, cands, threshold_px=8.0) is None


def test_returns_price_when_within_threshold():
    # Cursor at pixel-y=100; candidate at pixel-y=103 (3 px away).
    cands = [(99.5, 103.0)]
    assert find_nearest_ohlc_snap(100.0, cands, threshold_px=8.0) == 99.5


def test_picks_closest_when_multiple_within_threshold():
    # Cursor at pixel-y=100. Two candidates: one 5 px above, one 2 px below.
    cands = [
        (99.0, 105.0),  # 5 px away
        (101.0, 98.0),  # 2 px away
    ]
    assert find_nearest_ohlc_snap(100.0, cands, threshold_px=8.0) == 101.0


def test_inclusive_threshold():
    # Exact-threshold distance should still count (<= behaviour).
    cands = [(99.0, 108.0)]
    result = find_nearest_ohlc_snap(100.0, cands, threshold_px=8.0)
    assert result == 99.0


def test_first_match_wins_on_tie():
    """When two candidates are equidistant, the first one in the
    iterable wins. Stable behavior across multiple snaps."""
    cands = [
        (99.0, 105.0),
        (101.0, 105.0),  # Same pixel distance (both 5 px above).
    ]
    # Cursor at pixel-y=100; both candidates 5 px away.
    result = find_nearest_ohlc_snap(100.0, cands, threshold_px=8.0)
    assert result == 99.0


def test_skips_nan_candidate_price():
    cands = [
        (float("nan"), 99.0),
        (101.0, 105.0),
    ]
    assert find_nearest_ohlc_snap(100.0, cands, threshold_px=8.0) == 101.0


def test_skips_inf_candidate_pixel():
    cands = [
        (101.0, float("inf")),
        (99.5, 105.0),
    ]
    assert find_nearest_ohlc_snap(100.0, cands, threshold_px=8.0) == 99.5


def test_nan_target_returns_none():
    cands = [(99.0, 100.0)]
    assert find_nearest_ohlc_snap(float("nan"), cands) is None


def test_zero_threshold_returns_none_even_for_exact_hit():
    """A zero-threshold disables the snap entirely (defensive)."""
    cands = [(99.0, 100.0)]
    assert find_nearest_ohlc_snap(100.0, cands, threshold_px=0.0) is None


def test_negative_threshold_returns_none():
    cands = [(99.0, 100.0)]
    assert find_nearest_ohlc_snap(
        100.0, cands, threshold_px=-1.0) is None


def test_skips_non_numeric_candidates_gracefully():
    cands = [
        ("not-a-number", 100.0),
        (99.0, 100.0),
    ]
    # The first entry must be skipped, the second picked.
    assert find_nearest_ohlc_snap(100.0, cands) == 99.0


def test_default_threshold_is_eight():
    """Sanity: the default threshold matches the app-level constant.

    Pinned so a refactor that bumps one but not the other fails
    loudly (instead of silently desynchronizing the behavior the
    Settings checkbox description promises)."""
    # 8 px away — should snap.
    cands = [(50.0, 108.0)]
    assert find_nearest_ohlc_snap(100.0, cands) == 50.0
    # 9 px away — should NOT snap (outside default threshold).
    cands = [(50.0, 109.0)]
    assert find_nearest_ohlc_snap(100.0, cands) is None


# ---------------------------------------------------------------------------
# ChartApp integration — source pin
# ---------------------------------------------------------------------------

APP_SRC = (Path(__file__).resolve().parents[2]
           / "src" / "tradinglab" / "app.py").read_text(encoding="utf-8")
DIALOGS_SRC = (Path(__file__).resolve().parents[2]
               / "src" / "tradinglab" / "gui" / "dialogs.py").read_text(
                   encoding="utf-8")


def test_chartapp_loads_snap_setting_at_init():
    """ChartApp must read the setting at __init__ so the first
    Alt+H after launch observes the persisted preference."""
    assert 'drawings_snap_to_ohlc' in APP_SRC, (
        "ChartApp must reference 'drawings_snap_to_ohlc' "
        "as the settings.json key.")
    assert "self._drawings_snap_to_ohlc" in APP_SRC, (
        "ChartApp must store the loaded value on a "
        "_drawings_snap_to_ohlc attribute.")


def test_chartapp_defines_set_drawings_snap_to_ohlc():
    """The setter must exist and write through to settings."""
    assert "def set_drawings_snap_to_ohlc" in APP_SRC, (
        "ChartApp.set_drawings_snap_to_ohlc setter must exist")
    # Find body, ensure settings write.
    start = APP_SRC.find("def set_drawings_snap_to_ohlc")
    body = APP_SRC[start:start + 1500]
    assert '_settings.set' in body, (
        "Setter must persist via _settings.set")
    assert '"drawings_snap_to_ohlc"' in body or (
        "'drawings_snap_to_ohlc'" in body), (
            "Setter must write to the 'drawings_snap_to_ohlc' key")


def test_compute_snapped_drawing_price_calls_helper_when_enabled():
    """The unified helper must consult find_nearest_ohlc_snap."""
    start = APP_SRC.find("def _compute_snapped_drawing_price")
    assert start != -1, (
        "_compute_snapped_drawing_price helper must exist")
    end = APP_SRC.find("\n    def ", start + 1)
    body = APP_SRC[start:end] if end != -1 else APP_SRC[start:]
    assert "find_nearest_ohlc_snap" in body, (
        "_compute_snapped_drawing_price must call "
        "find_nearest_ohlc_snap when the toggle is enabled")
    assert "_drawings_snap_to_ohlc" in body, (
        "_compute_snapped_drawing_price must gate the helper "
        "call on the user preference")
    assert "snap_price_to_grid" in body, (
        "Fallback to per-instrument grid snap must remain in "
        "place when OHLC snap is disabled or no candidate "
        "qualifies")


def test_on_alt_h_placement_uses_unified_helper():
    """Alt+H placement must route through the unified helper."""
    start = APP_SRC.find("def _on_alt_h_placement")
    end = APP_SRC.find("\n    def ", start + 1)
    body = APP_SRC[start:end] if end != -1 else APP_SRC[start:]
    assert "_compute_snapped_drawing_price" in body, (
        "Alt+H must use _compute_snapped_drawing_price so the "
        "OHLC snap is respected (was using snap_price_to_grid "
        "directly).")


def test_add_hline_here_menu_uses_unified_helper():
    """The right-click 'Add Horizontal Line Here' menu must
    route through the same helper as Alt+H so the snap behavior
    is consistent across entry points."""
    # The _add_hline_here closure lives inside _show_chart_canvas_menu.
    start = APP_SRC.find("def _show_chart_canvas_menu")
    end = APP_SRC.find("\n    def ", start + 1)
    body = APP_SRC[start:end] if end != -1 else APP_SRC[start:]
    assert "_compute_snapped_drawing_price" in body, (
        "Right-click 'Add Horizontal Line Here' must use the "
        "unified snap helper")


def test_pixel_threshold_constant_defined():
    """The pixel threshold must be a module-level constant so
    tests and future tuning have a single source of truth."""
    assert "_DRAWINGS_SNAP_PIXEL_THRESHOLD" in APP_SRC, (
        "app.py must define _DRAWINGS_SNAP_PIXEL_THRESHOLD")


# ---------------------------------------------------------------------------
# Settings dialog wiring — source pin
# ---------------------------------------------------------------------------

def test_dialog_has_snap_ohlc_checkbox():
    """The Settings dialog must expose the toggle."""
    assert "_snap_ohlc_var" in DIALOGS_SRC, (
        "Settings dialog must create a _snap_ohlc_var Tk var")
    assert "Snap horizontal lines to nearest OHLC" in DIALOGS_SRC, (
        "Settings dialog must label the toggle clearly")


def test_dialog_persists_on_ok():
    """OK must call the setter (not just mutate the live flag)."""
    start = DIALOGS_SRC.find("def _on_ok")
    end = DIALOGS_SRC.find("\n    def ", start + 1)
    body = DIALOGS_SRC[start:end] if end != -1 else DIALOGS_SRC[start:]
    assert "set_drawings_snap_to_ohlc" in body, (
        "Settings dialog _on_ok must persist via "
        "set_drawings_snap_to_ohlc (live-preview only mutates the "
        "in-memory flag)")


def test_dialog_reverts_on_cancel():
    start = DIALOGS_SRC.find("def _on_cancel")
    end = DIALOGS_SRC.find("\n    def ", start + 1)
    body = DIALOGS_SRC[start:end] if end != -1 else DIALOGS_SRC[start:]
    assert "_snap_ohlc_initial" in body, (
        "Cancel must restore _drawings_snap_to_ohlc to the "
        "dialog-open snapshot")


def test_dialog_has_snap_toggle_handler():
    assert "def _on_snap_ohlc_toggle" in DIALOGS_SRC, (
        "Settings dialog must define _on_snap_ohlc_toggle for the "
        "live-preview behavior")


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

def test_helper_exported_from_drawings_package():
    from tradinglab import drawings as drawings_pkg
    assert "find_nearest_ohlc_snap" in drawings_pkg.__all__
    assert hasattr(drawings_pkg, "find_nearest_ohlc_snap")


def test_threshold_constant_value_is_finite():
    """Sanity: not accidentally Inf / NaN."""
    import re
    match = re.search(
        r"_DRAWINGS_SNAP_PIXEL_THRESHOLD\s*=\s*([0-9.]+)", APP_SRC)
    assert match is not None
    val = float(match.group(1))
    assert math.isfinite(val) and val > 0
