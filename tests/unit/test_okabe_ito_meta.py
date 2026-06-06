"""Meta-test: every *directional* color must follow the Okabe-Ito toggle.

The app's color-blind-safe palette swaps the bull/bear hues at runtime by
mutating ``constants.BULL_COLOR`` / ``constants.BEAR_COLOR`` (default
teal-green / coral-red ↔ Okabe-Ito orange / sky-blue). Every color that
encodes MARKET DIRECTION — bull/bear, up/down, gain/loss, rising/falling,
MFE/MAE, P&L sign, row-background tint — must route through that single
source of truth so the toggle reaches it. Status colors (error/warn/info/ok)
are a DIFFERENT semantic axis and are intentionally NOT covered here.

Two guarantees:

* **Part A — behavioural registry.** Each live directional resolver yields a
  different color under the default vs Okabe-Ito palette (and the Okabe-Ito
  value is no longer in the green/red family). This is the enforceable
  "modifiable by Okabe-Ito" contract; add a row when you wire a new live
  surface.
* **Part B — AST source guard.** The canonical green/red hex literals
  (`#26a69a`, `#ef5350`, `#b2dfdb`, `#ffcdd2`) appear as Python string
  constants ONLY in ``constants.py``. Any other hardcoded occurrence is a
  directional color that won't follow the toggle. AST exact-match ignores
  docstrings/comments (which contain the hex only as a substring).

Audit ``color-blind-palette-audit``.
"""
from __future__ import annotations

import ast
import colorsys
from contextlib import contextmanager
from pathlib import Path

import pytest

from tradinglab import constants as C

# Pre-import every module that exposes a live directional resolver so the
# registry below references real call-time lookups (not fresh imports that
# could bind to a sentinel set by the palette fixture).
from tradinglab import rendering as _rendering  # noqa: E402
from tradinglab.gui import colors as _colors  # noqa: E402
from tradinglab.gui.chartstack import render as _cs_render  # noqa: E402

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "tradinglab"


# ---------------------------------------------------------------------------
# Palette fixture
# ---------------------------------------------------------------------------

@contextmanager
def _palette(*, colorblind: bool):
    """Temporarily force the default or Okabe-Ito palette (restore after)."""
    saved = (C.BULL_COLOR, C.BEAR_COLOR)
    if colorblind:
        C.BULL_COLOR, C.BEAR_COLOR = (C._COLORBLIND_BULL_COLOR,
                                      C._COLORBLIND_BEAR_COLOR)
    else:
        C.BULL_COLOR, C.BEAR_COLOR = (C._DEFAULT_BULL_COLOR,
                                      C._DEFAULT_BEAR_COLOR)
    try:
        yield
    finally:
        C.BULL_COLOR, C.BEAR_COLOR = saved


def _hue_deg(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
               int(h[4:6], 16) / 255.0)
    hue, _, _ = colorsys.rgb_to_hls(r, g, b)
    return hue * 360.0


def _bull_candle():
    from datetime import datetime

    from tradinglab.models import Candle
    return Candle(date=datetime(2024, 1, 2, 10, 0), open=100.0, high=106.0,
                  low=99.0, close=105.0, volume=1000)


def _bear_candle():
    from datetime import datetime

    from tradinglab.models import Candle
    return Candle(date=datetime(2024, 1, 2, 10, 0), open=100.0, high=101.0,
                  low=94.0, close=95.0, volume=1000)


def _rgba_to_hex(rgba) -> str:
    return f"#{round(rgba[0] * 255):02x}{round(rgba[1] * 255):02x}{round(rgba[2] * 255):02x}"


# ---------------------------------------------------------------------------
# Part A — behavioural registry of LIVE directional color resolvers.
# Each returns a single hex string read at CALL time. Add a row when wiring
# a new live surface.
# ---------------------------------------------------------------------------

_LIVE_RESOLVERS: dict[str, callable] = {
    "constants.BULL_COLOR": lambda: C.BULL_COLOR,
    "constants.BEAR_COLOR": lambda: C.BEAR_COLOR,
    "constants.sentiment_recolor(bull tint)":
        lambda: C.sentiment_recolor(C._BULL_TINT_PALE, bullish=True),
    "constants.sentiment_recolor(bear tint)":
        lambda: C.sentiment_recolor(C._BEAR_TINT_PALE, bullish=False),
    "constants.bull_row_bg(LIGHT)": lambda: C.bull_row_bg(C.LIGHT_THEME),
    "constants.bear_row_bg(LIGHT)": lambda: C.bear_row_bg(C.LIGHT_THEME),
    "constants.bull_row_bg(DARK)": lambda: C.bull_row_bg(C.DARK_THEME),
    "constants.bear_row_bg(DARK)": lambda: C.bear_row_bg(C.DARK_THEME),
    "constants.macd_histogram_palette[0]":
        lambda: C.macd_histogram_palette()[0],
    "constants.macd_histogram_palette[1]":
        lambda: C.macd_histogram_palette()[1],
    "constants.macd_histogram_palette[2]":
        lambda: C.macd_histogram_palette()[2],
    "constants.macd_histogram_palette[3]":
        lambda: C.macd_histogram_palette()[3],
    "gui.colors.up_green()": lambda: _colors.up_green(),
    "gui.colors.down_red()": lambda: _colors.down_red(),
    "rendering._bar_rgba(bull)":
        lambda: _rgba_to_hex(_rendering._bar_rgba(_bull_candle())),
    "rendering._bar_rgba(bear)":
        lambda: _rgba_to_hex(_rendering._bar_rgba(_bear_candle())),
    "chartstack._direction_color(up)":
        lambda: _cs_render._direction_color(100.0, 105.0),
    "chartstack._direction_color(down)":
        lambda: _cs_render._direction_color(105.0, 100.0),
}


@pytest.mark.parametrize("name", sorted(_LIVE_RESOLVERS))
def test_live_directional_color_changes_with_palette(name):
    """Each live directional resolver yields a DIFFERENT color under the
    Okabe-Ito palette than under the default palette."""
    resolver = _LIVE_RESOLVERS[name]
    with _palette(colorblind=False):
        default_val = resolver()
    with _palette(colorblind=True):
        okabe_val = resolver()
    assert default_val.lower() != okabe_val.lower(), (
        f"{name} did not change when the Okabe-Ito palette was toggled "
        f"(stayed {default_val!r}) — it is not routed through "
        f"constants.BULL_COLOR/BEAR_COLOR")


# The default green hues live ~ 0°(red)/175°(teal); Okabe-Ito orange ≈ 37°,
# sky-blue ≈ 201°. After toggling, bullish surfaces must read orange-ish and
# bearish surfaces blue-ish — proving the swap actually happened (not just
# "some other color").
_BULLISH_RESOLVERS = (
    "constants.BULL_COLOR", "constants.bull_row_bg(LIGHT)",
    "constants.bull_row_bg(DARK)", "constants.macd_histogram_palette[0]",
    "gui.colors.up_green()", "rendering._bar_rgba(bull)",
    "chartstack._direction_color(up)",
)
_BEARISH_RESOLVERS = (
    "constants.BEAR_COLOR", "constants.bear_row_bg(LIGHT)",
    "constants.bear_row_bg(DARK)", "constants.macd_histogram_palette[3]",
    "gui.colors.down_red()", "rendering._bar_rgba(bear)",
    "chartstack._direction_color(down)",
)


@pytest.mark.parametrize("name", _BULLISH_RESOLVERS)
def test_bullish_surface_becomes_orange_under_okabe(name):
    with _palette(colorblind=True):
        hue = _hue_deg(_LIVE_RESOLVERS[name]())
    # Okabe-Ito orange hue ≈ 37°; allow a generous window for tints.
    assert 20.0 <= hue <= 60.0, (
        f"{name} hue {hue:.0f}° is not in the Okabe-Ito orange band "
        f"(20–60°) under the color-blind palette")


@pytest.mark.parametrize("name", _BEARISH_RESOLVERS)
def test_bearish_surface_becomes_blue_under_okabe(name):
    with _palette(colorblind=True):
        hue = _hue_deg(_LIVE_RESOLVERS[name]())
    # Okabe-Ito sky-blue hue ≈ 201°.
    assert 185.0 <= hue <= 220.0, (
        f"{name} hue {hue:.0f}° is not in the Okabe-Ito blue band "
        f"(185–220°) under the color-blind palette")


def test_default_palette_is_pixel_exact_passthrough():
    """Under the default palette, sentiment_recolor / row-bg helpers return
    the original theme tints UNCHANGED (no rounding drift) so the default
    look is preserved exactly."""
    with _palette(colorblind=False):
        assert C.bull_row_bg(C.LIGHT_THEME) == C.LIGHT_THEME["bull_row_bg"]
        assert C.bear_row_bg(C.LIGHT_THEME) == C.LIGHT_THEME["bear_row_bg"]
        assert C.bull_row_bg(C.DARK_THEME) == C.DARK_THEME["bull_row_bg"]
        assert C.bear_row_bg(C.DARK_THEME) == C.DARK_THEME["bear_row_bg"]
        assert C.sentiment_recolor("#123456", bullish=True) == "#123456"


# ---------------------------------------------------------------------------
# Part B — AST source guard: canonical directional hexes live ONLY in
# constants.py. Exact-match on string constants ignores docstrings/comments.
# ---------------------------------------------------------------------------

_BANNED_HEXES = {"#26a69a", "#ef5350", "#b2dfdb", "#ffcdd2"}


def _iter_src_py():
    for py in _SRC_ROOT.rglob("*.py"):
        if py.name == "constants.py":
            continue
        yield py


def test_no_hardcoded_directional_hex_outside_constants():
    offenders: list[str] = []
    for py in _iter_src_py():
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.strip().lower() in _BANNED_HEXES:
                    rel = py.relative_to(_SRC_ROOT)
                    offenders.append(f"{rel}:{node.lineno} -> {node.value!r}")
    assert not offenders, (
        "Canonical bull/bear green/red hex literals must live ONLY in "
        "constants.py (route every directional color through "
        "constants.BULL_COLOR / BEAR_COLOR / sentiment_recolor / "
        "macd_histogram_palette). Offending literals:\n  "
        + "\n  ".join(offenders))


def test_banned_hexes_are_actually_defined_in_constants():
    """Sanity: the guard's banned set really is the canonical palette so
    the test can't silently pass on a typo."""
    assert C._DEFAULT_BULL_COLOR.lower() in _BANNED_HEXES
    assert C._DEFAULT_BEAR_COLOR.lower() in _BANNED_HEXES
    assert C._BULL_TINT_PALE.lower() in _BANNED_HEXES
    assert C._BEAR_TINT_PALE.lower() in _BANNED_HEXES
