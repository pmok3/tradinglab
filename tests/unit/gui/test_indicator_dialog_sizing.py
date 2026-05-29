"""Unit tests for the Manage Indicators dialog dynamic-sizing helpers.

Targets the helpers added in response to the user-reported clipping
bug ("in the manage indicators screen, some options are being cut
off because the window is too small horizontally"). These are pure
helper functions with no Tk side effects, so they exercise cleanly
in headless unit-test environments.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradinglab.gui.indicator_dialog import (
    _combo_width_for_choices,
    _filter_indicator_kind_displays,
    _spinbox_width_for,
)

# ---------------------------------------------------------------- combo widths


class TestComboWidthForChoices:
    def test_empty_choices_returns_legacy_default(self):
        assert _combo_width_for_choices(()) == 10
        assert _combo_width_for_choices([]) == 10
        assert _combo_width_for_choices(None) == 10

    def test_short_choices_clamped_to_floor(self):
        # Floor is 8; "a", "bb", "cccc" + 2 for arrow = 6, clamped up.
        assert _combo_width_for_choices(["a", "bb", "cccc"]) == 8

    def test_widest_choice_governs(self):
        # Longest = 22 chars + 2 = 24.
        choices = ["regular_only", "regular_plus_premarket"]
        assert _combo_width_for_choices(choices) == 24

    def test_long_choices_capped_at_30(self):
        # 50-char choice + 2 would be 52, capped at 30.
        big = "x" * 50
        assert _combo_width_for_choices([big]) == 30

    def test_atr_session_filter_choices_unclipped(self):
        # Repro of the actual ATR session_filter dropdown — must
        # fully fit ``regular_plus_premarket`` (22 chars).
        choices = (
            "regular_only",
            "regular_plus_premarket",
            "regular_plus_postmarket",
            "all_sessions",
        )
        width = _combo_width_for_choices(choices)
        # 23 chars + 2 = 25.
        assert width == 25

    def test_handles_non_string_iterables(self):
        # ChoiceParam.choices is often a list of strings, but
        # defensive: any iterable of stringifiable objects works.
        assert _combo_width_for_choices([1, 22, 333]) == 8


class TestFilterIndicatorKindDisplays:
    def test_searches_display_kind_id_and_tooltip_text(self):
        mapping = {
            "EMA": "ema",
            "RRVOL": "rrvol",
            "ATR": "atr",
        }
        assert _filter_indicator_kind_displays(mapping, "rrvol") == ("RRVOL",)
        assert _filter_indicator_kind_displays(mapping, "moving average") == ("EMA",)
        assert _filter_indicator_kind_displays(mapping, "atr") == ("ATR",)

    def test_empty_query_returns_all_displays(self):
        mapping = {"EMA": "ema", "RRVOL": "rrvol"}
        assert _filter_indicator_kind_displays(mapping, "") == ("EMA", "RRVOL")


# ------------------------------------------------------------- spinbox widths


class TestSpinboxWidthFor:
    def test_int_param_typical(self):
        pdef = SimpleNamespace(min=2, max=200, kind="int")
        # max(1, 3) + 2 = 5, clamped up to floor 6.
        assert _spinbox_width_for(pdef) == 6

    def test_int_param_large_max(self):
        pdef = SimpleNamespace(min=0, max=99999, kind="int")
        # 5 digits + 2 = 7.
        assert _spinbox_width_for(pdef) == 7

    def test_float_param_with_decimals(self):
        pdef = SimpleNamespace(min=0.5, max=8.0, kind="float")
        # ``str(8.0)`` = "8.0" (3 chars) + 2 = 5, clamped up to 6.
        assert _spinbox_width_for(pdef) == 6

    def test_unbounded_max_uses_safe_default(self):
        # When both min and max are None, the helper falls back to a
        # 6-char digit estimate each + 2 fudge = 8 (still inside the
        # [6, 14] clamp window). A reasonable default that's neither
        # too narrow to be useful nor wide enough to push the
        # neighbouring widget out of view.
        pdef = SimpleNamespace(min=None, max=None, kind="int")
        assert _spinbox_width_for(pdef) == 8

    def test_pathological_max_capped_at_14(self):
        pdef = SimpleNamespace(min=0, max=10**20, kind="int")
        assert _spinbox_width_for(pdef) == 14

    def test_negative_min_counts_minus_sign(self):
        # "-100" has 4 chars; max "100" has 3. max + 2 = 6.
        pdef = SimpleNamespace(min=-100, max=100, kind="int")
        # str("-100") len=4, str("100") len=3; max=4 + 2 = 6 (floor)
        assert _spinbox_width_for(pdef) == 6
