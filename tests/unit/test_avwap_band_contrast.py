"""Regression test for the ``avwap-band-contrast`` audit.

The Anchored VWAP indicator draws an optional ±1σ / ±2σ band pair
around the AVWAP line. The previous default band color was
``#aec7e8`` — a very pale light blue that achieved only ~1.59:1
contrast against the chart's white background, well below the
WCAG-AA 3:1 minimum for non-text UI elements. On a busy chart
those bands effectively vanished against gridlines.

After the fix the default band color is ``#4393c3`` (ColorBrewer
Blues 7), which lands at ~3.39:1 on white and ~4.92:1 on the
dark-theme background ``#1e1e1e``. Both pass WCAG-AA non-text and
stay visually subordinate to the main AVWAP line (now ``#8c564b``
after the ``indicator-color-uniqueness`` audit moved the central
AVWAP color from blue to brown so it wouldn't collide with the
default SMA color).

These tests pin the specific hex AND verify the contrast math so
a future "make the bands subtler" tweak can't quietly slip below
the WCAG floor.
"""
from __future__ import annotations

from tradinglab.indicators.avwap import AnchoredVWAP


def _srgb_to_linear(channel: float) -> float:
    c = channel / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    rl = _srgb_to_linear(r)
    gl = _srgb_to_linear(g)
    bl = _srgb_to_linear(b)
    return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl


def _contrast_ratio(c1: str, c2: str) -> float:
    L1 = _relative_luminance(c1)
    L2 = _relative_luminance(c2)
    if L1 < L2:
        L1, L2 = L2, L1
    return (L1 + 0.05) / (L2 + 0.05)


_BAND_KEYS = ("upper1", "lower1", "upper2", "lower2")


def test_band_default_color_pinned_to_4393c3():
    """The four band style entries must share the new default hex."""
    for key in _BAND_KEYS:
        ls = AnchoredVWAP.default_style[key]
        assert ls.color.lower() == "#4393c3", (
            f"AVWAP band default {key}={ls.color!r} (expected '#4393c3'); "
            f"the pale '#aec7e8' fell below WCAG-AA non-text contrast.")


def test_band_default_color_meets_wcag_aa_on_white():
    """Contrast vs white background must be at least 3:1 (WCAG-AA non-text)."""
    color = AnchoredVWAP.default_style["upper1"].color
    ratio = _contrast_ratio(color, "#ffffff")
    assert ratio >= 3.0, (
        f"AVWAP band color {color!r} has contrast ratio {ratio:.2f}:1 "
        f"against white. WCAG-AA non-text requires at least 3.0:1.")


def test_band_default_color_meets_wcag_aa_on_dark_theme():
    """Contrast vs dark-theme bg ``#1e1e1e`` must also be at least 3:1."""
    color = AnchoredVWAP.default_style["upper1"].color
    ratio = _contrast_ratio(color, "#1e1e1e")
    assert ratio >= 3.0, (
        f"AVWAP band color {color!r} has contrast ratio {ratio:.2f}:1 "
        f"against the dark-theme background — must stay legible in "
        f"both themes.")


def test_band_default_visually_subordinate_to_avwap_line():
    """Band color must be lighter than the main AVWAP line color so
    it reads as secondary information, not a competing series."""
    main = AnchoredVWAP.default_style["avwap"].color
    band = AnchoredVWAP.default_style["upper1"].color
    main_L = _relative_luminance(main)
    band_L = _relative_luminance(band)
    assert band_L > main_L, (
        f"Band luminance ({band_L:.3f}) must be greater than the main "
        f"AVWAP line luminance ({main_L:.3f}) so the bands read as "
        f"subordinate, not competing, information.")


def test_previous_pale_band_color_is_gone():
    """The old default `#aec7e8` must not be reintroduced."""
    for key in _BAND_KEYS:
        ls = AnchoredVWAP.default_style[key]
        assert ls.color.lower() != "#aec7e8", (
            f"AVWAP band default {key} reintroduced the pale '#aec7e8' "
            f"that fell below WCAG-AA contrast — keep '#4393c3' or a "
            f"color that meets at least 3:1 against white.")


def test_band_pairs_share_same_color():
    """All four band keys (upper1/lower1/upper2/lower2) must share the
    same color — the ±1σ and ±2σ bands form a visual pair set, not
    four independent series."""
    colors = {AnchoredVWAP.default_style[k].color.lower() for k in _BAND_KEYS}
    assert len(colors) == 1, (
        f"AVWAP band default colors diverge across the four band keys "
        f"({colors!r}). All four must share one color so the band pair "
        f"reads as a single visual unit.")


def test_contrast_helper_self_check():
    """Sanity check the local WCAG math: black vs white = 21:1."""
    assert abs(_contrast_ratio("#000000", "#ffffff") - 21.0) < 0.05
