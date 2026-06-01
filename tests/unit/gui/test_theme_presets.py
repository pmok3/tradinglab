"""Built-in preset themes — registry + invariants.

The repo historically shipped 3 themes: light, dark, Bloomberg. This
sprint adds 6 popular community palettes (Solarized, Nord, Dracula,
Gruvbox, Monokai, Material) so users have more out-of-box variety
without having to hand-tune every slot.

Each preset is registered in ``constants.PRESET_THEMES`` as a
``ThemePreset`` dataclass with:

* ``label`` — UI display name (e.g. "Dracula").
* ``mode`` — ``"light"`` or ``"dark"`` (controls whether the active
  mode flips when the preset is applied).
* ``overrides`` — dict of ``CUSTOMIZABLE_THEME_KEYS`` colour values.

Tests in this file pin the contract so a future palette-tweak can't
accidentally:

* break the keys allow-list (every override key must be in
  ``CUSTOMIZABLE_THEME_KEYS``);
* drop the "Default Light" / "Default Dark" / "Bloomberg" entries
  the existing UI tests reference;
* pick a label that collides with another preset.
"""
from __future__ import annotations

import pytest

from tradinglab.constants import (
    CUSTOMIZABLE_THEME_KEYS,
    PRESET_THEMES,
    ThemePreset,
)


def _allowed_keys() -> set[str]:
    return {k for k, _ in CUSTOMIZABLE_THEME_KEYS}


class TestPresetRegistryShape:
    def test_preset_themes_is_a_tuple_of_themepreset(self):
        assert isinstance(PRESET_THEMES, tuple)
        assert PRESET_THEMES, "must register at least one preset"
        for entry in PRESET_THEMES:
            assert isinstance(entry, ThemePreset), (
                f"every PRESET_THEMES entry must be a ThemePreset; "
                f"got {type(entry).__name__}"
            )

    def test_every_preset_has_unique_label(self):
        labels = [p.label for p in PRESET_THEMES]
        assert len(labels) == len(set(labels)), (
            f"preset labels must be unique; got duplicates in {labels}"
        )

    def test_every_preset_targets_a_valid_mode(self):
        for p in PRESET_THEMES:
            assert p.mode in ("light", "dark"), (
                f"preset {p.label!r} mode must be 'light' or 'dark'; "
                f"got {p.mode!r}"
            )

    def test_every_preset_override_key_is_customizable(self):
        allowed = _allowed_keys()
        for p in PRESET_THEMES:
            extra = set(p.overrides) - allowed
            assert not extra, (
                f"preset {p.label!r} has overrides for unknown keys: "
                f"{extra}. Only CUSTOMIZABLE_THEME_KEYS are honoured."
            )

    def test_every_override_value_is_a_hex_colour(self):
        import re

        rx = re.compile(r"^#[0-9a-fA-F]{6}$")
        for p in PRESET_THEMES:
            for key, val in p.overrides.items():
                assert isinstance(val, str) and rx.match(val), (
                    f"preset {p.label!r}: {key!r} must be a "
                    f"6-digit hex colour like '#aabbcc'; got {val!r}"
                )


class TestRequiredPresetsStillPresent:
    """Existing UI tests depend on these labels being available."""

    def test_default_light_present(self):
        labels = {p.label for p in PRESET_THEMES}
        assert "Default Light" in labels

    def test_default_dark_present(self):
        labels = {p.label for p in PRESET_THEMES}
        assert "Default Dark" in labels

    def test_bloomberg_present(self):
        labels = {p.label for p in PRESET_THEMES}
        assert "Bloomberg" in labels


class TestNewBuiltInPresets:
    """The six new built-in presets added by this sprint."""

    @pytest.mark.parametrize(
        "label", [
            "Solarized Light",
            "Solarized Dark",
            "Nord",
            "Dracula",
            "Gruvbox Dark",
            "Monokai",
            "Material Ocean",
        ],
    )
    def test_named_preset_registered(self, label):
        labels = {p.label for p in PRESET_THEMES}
        assert label in labels, (
            f"this sprint adds {label!r} as a built-in preset; "
            f"available: {sorted(labels)}"
        )

    def test_presets_cover_six_canonical_slots(self):
        """Every new preset must define all 6 customizable slots.

        A preset that only overrides win_bg + text would leave the
        chart background + gridlines untouched and look broken
        against the *previous* preset's values. Forcing every new
        preset to cover the full ``CUSTOMIZABLE_THEME_KEYS`` set
        means switching presets is always a complete repaint.
        """
        allowed = _allowed_keys()
        new_labels = {
            "Solarized Light", "Solarized Dark", "Nord", "Dracula",
            "Gruvbox Dark", "Monokai", "Material Ocean",
        }
        for p in PRESET_THEMES:
            if p.label not in new_labels:
                continue
            missing = allowed - set(p.overrides)
            assert not missing, (
                f"preset {p.label!r} is missing required slots: {missing}"
            )

    def test_at_least_two_light_and_seven_dark_presets(self):
        """Variety check — users should have meaningful choice in both modes."""
        light = [p for p in PRESET_THEMES if p.mode == "light"]
        dark = [p for p in PRESET_THEMES if p.mode == "dark"]
        # 2 light = Default Light + Solarized Light (minimum bar)
        assert len(light) >= 2, (
            f"expected at least 2 light-mode presets; got {len(light)}: "
            f"{[p.label for p in light]}"
        )
        # 7 dark = Default Dark + Bloomberg + Solarized Dark + Nord +
        #         Dracula + Gruvbox Dark + Monokai + Material Ocean
        assert len(dark) >= 7, (
            f"expected at least 7 dark-mode presets; got {len(dark)}: "
            f"{[p.label for p in dark]}"
        )
