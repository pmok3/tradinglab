"""Regression test for visible outlines on clickable input widgets.

Commit ``536fe6c`` ("complete dark-mode coverage for Watchlists + Entries
tabs") accidentally collapsed the 3D bevel on ``TEntry`` / ``TCombobox``
/ ``TSpinbox`` to a single ``spine``-colored line, making input outlines
nearly invisible against the field background. This test pins the
**post-fix** invariant so the regression can't recur:

1. Both palettes expose an ``input_border`` key.
2. The ``input_border`` color is visibly distinct from ``ax_bg`` (the
   input field background) in both palettes — measured via simple RGB
   distance.
3. The ``TEntry`` / ``TCombobox`` / ``TSpinbox`` style specs use
   ``input_border`` for ``bordercolor`` / ``lightcolor`` / ``darkcolor``,
   NOT ``spine`` (which is for chart axis lines and is often subtler).
4. The styles set ``borderwidth=1`` and ``relief="solid"`` so the
   outline reads as a clean 1px frame.
"""
from __future__ import annotations

from tradinglab.constants import (
    DARK_THEME,
    LIGHT_THEME,
    build_ttk_style_spec,
)


def _hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _rgb_distance(a: str, b: str) -> int:
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    return abs(ar - br) + abs(ag - bg) + abs(ab - bb)


# Minimum RGB-distance between input_border and ax_bg for the outline
# to be visibly readable. 60 is conservative — at distance < 30 the
# outline starts to blend into the field background.
MIN_BORDER_CONTRAST = 60


class TestInputBorderPalette:
    """Both themes must expose a visibly contrasting ``input_border``."""

    def test_light_has_input_border(self) -> None:
        assert "input_border" in LIGHT_THEME, (
            "LIGHT_THEME must define 'input_border' — input widget "
            "outlines depend on this key"
        )

    def test_dark_has_input_border(self) -> None:
        assert "input_border" in DARK_THEME, (
            "DARK_THEME must define 'input_border' — input widget "
            "outlines depend on this key"
        )

    def test_light_input_border_contrast(self) -> None:
        d = _rgb_distance(LIGHT_THEME["input_border"], LIGHT_THEME["ax_bg"])
        assert d >= MIN_BORDER_CONTRAST, (
            f"LIGHT_THEME input_border={LIGHT_THEME['input_border']} "
            f"too close to ax_bg={LIGHT_THEME['ax_bg']} "
            f"(RGB distance {d} < {MIN_BORDER_CONTRAST}); outline would "
            f"blend into the field background"
        )

    def test_dark_input_border_contrast(self) -> None:
        d = _rgb_distance(DARK_THEME["input_border"], DARK_THEME["ax_bg"])
        assert d >= MIN_BORDER_CONTRAST, (
            f"DARK_THEME input_border={DARK_THEME['input_border']} "
            f"too close to ax_bg={DARK_THEME['ax_bg']} "
            f"(RGB distance {d} < {MIN_BORDER_CONTRAST}); outline would "
            f"blend into the field background"
        )


class TestInputWidgetStyles:
    """TEntry / TCombobox / TSpinbox must use input_border (not spine)
    for the border-paint layers, with a visible 1px solid frame."""

    INPUT_STYLE_NAMES = frozenset({"TEntry", "TCombobox", "TSpinbox"})

    def _spec_by_name(self, theme: dict) -> dict[str, tuple[dict, dict]]:
        return {
            name: (cfg, mp) for (name, cfg, mp) in build_ttk_style_spec(theme)
        }

    def _assert_input_style_uses_visible_border(
        self, theme: dict, style_name: str
    ) -> None:
        spec = self._spec_by_name(theme)
        assert style_name in spec, (
            f"build_ttk_style_spec missing entry for {style_name}"
        )
        cfg, _mp = spec[style_name]
        expected_border = theme["input_border"]
        for key in ("bordercolor", "lightcolor", "darkcolor"):
            assert cfg.get(key) == expected_border, (
                f"{style_name}.{key} must be input_border "
                f"({expected_border!r}); got {cfg.get(key)!r}. "
                f"Don't use 'spine' for input widget borders — spine is "
                f"for chart axis lines and is too subtle for tap targets."
            )
        assert cfg.get("borderwidth") == 1, (
            f"{style_name} must set borderwidth=1 (got {cfg.get('borderwidth')!r})"
        )
        assert cfg.get("relief") == "solid", (
            f"{style_name} must set relief='solid' for a clean 1px frame "
            f"(got {cfg.get('relief')!r})"
        )
        # Fieldbackground must still be ax_bg (preserve dark-mode coverage).
        assert cfg.get("fieldbackground") == theme["ax_bg"], (
            f"{style_name} fieldbackground regressed — must still be ax_bg"
        )

    def test_light_tentry_visible_border(self) -> None:
        self._assert_input_style_uses_visible_border(LIGHT_THEME, "TEntry")

    def test_light_tcombobox_visible_border(self) -> None:
        self._assert_input_style_uses_visible_border(LIGHT_THEME, "TCombobox")

    def test_light_tspinbox_visible_border(self) -> None:
        self._assert_input_style_uses_visible_border(LIGHT_THEME, "TSpinbox")

    def test_dark_tentry_visible_border(self) -> None:
        self._assert_input_style_uses_visible_border(DARK_THEME, "TEntry")

    def test_dark_tcombobox_visible_border(self) -> None:
        self._assert_input_style_uses_visible_border(DARK_THEME, "TCombobox")

    def test_dark_tspinbox_visible_border(self) -> None:
        self._assert_input_style_uses_visible_border(DARK_THEME, "TSpinbox")


class TestBackCompat:
    """Themes missing the ``input_border`` key must still produce a
    valid style spec (falls back to ``spine``)."""

    def test_back_compat_falls_back_to_spine(self) -> None:
        partial_theme = dict(LIGHT_THEME)
        partial_theme.pop("input_border")
        spec = dict(
            (name, cfg) for (name, cfg, _mp) in build_ttk_style_spec(partial_theme)
        )
        assert "TEntry" in spec
        # Falls back to spine — back-compat for older theme palettes.
        assert spec["TEntry"]["bordercolor"] == partial_theme["spine"]
