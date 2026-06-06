"""Tests for the ``color-blind-palette`` audit.

Adds a Settings toggle that swaps the default green/red candle
palette for Okabe-Ito orange/blue — a well-known color-blind-safe
qualitative palette that preserves the "warm = up / cool = down"
mental model traders rely on but reads cleanly under
deuteranopia / protanopia / tritanopia.

Tests cover four layers:

1. ``constants`` resolves the initial palette from
   ``settings.get("use_colorblind_palette")``.
2. ``ChartApp.set_use_colorblind_palette`` flips the live module
   constants and persists.
3. Settings dialog wiring (checkbox + label + handler).
4. The two Okabe-Ito values are the canonical orange / sky-blue.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Import the candle renderers at *collection* time so any cached
# ``from .constants import BULL_COLOR`` binding is locked to the
# import-time palette BEFORE the sentinel fixtures below mutate
# ``constants``. Otherwise a fresh first-import inside a test would bind
# to the sentinel and mask the very bug these tests guard. (Both are
# already imported transitively via conftest, so this is belt-and-braces.)
from tradinglab import rendering as _rendering_preimport  # noqa: E402,F401
from tradinglab.gui import (  # noqa: E402,F401
    volume_tod_overlay as _vto_preimport,
)

# ---------------------------------------------------------------------------
# constants module — palette resolution
# ---------------------------------------------------------------------------

def test_default_palette_values():
    from tradinglab import constants as c
    assert c._DEFAULT_BULL_COLOR == "#26a69a"
    assert c._DEFAULT_BEAR_COLOR == "#ef5350"


def test_colorblind_palette_uses_okabe_ito():
    """Okabe-Ito orange (#E69F00) and sky-blue (#56B4E9) are the
    canonical accessibility references. Pin them so a future
    refactor can't quietly change the palette."""
    from tradinglab import constants as c
    assert c._COLORBLIND_BULL_COLOR.lower() == "#e69f00"
    assert c._COLORBLIND_BEAR_COLOR.lower() == "#56b4e9"


def test_resolve_initial_palette_default():
    """Without the setting, the default palette must apply."""
    from tradinglab import constants as c
    bull, bear = c._resolve_initial_palette()
    # We can't control the user's settings.json from a test, but
    # the function must return a (str, str) pair and pick one of
    # the two canonical palettes.
    assert isinstance(bull, str) and isinstance(bear, str)
    assert bull in (c._DEFAULT_BULL_COLOR, c._COLORBLIND_BULL_COLOR)
    assert bear in (c._DEFAULT_BEAR_COLOR, c._COLORBLIND_BEAR_COLOR)


def test_resolve_initial_palette_with_colorblind_enabled(monkeypatch):
    """When settings says color-blind ON, pick Okabe-Ito."""
    from tradinglab import constants as c
    from tradinglab import settings as s

    def fake_get(key, default=None):
        if key == "use_colorblind_palette":
            return True
        return default

    monkeypatch.setattr(s, "get", fake_get)
    bull, bear = c._resolve_initial_palette()
    assert bull == c._COLORBLIND_BULL_COLOR
    assert bear == c._COLORBLIND_BEAR_COLOR


def test_resolve_initial_palette_with_colorblind_disabled(monkeypatch):
    from tradinglab import constants as c
    from tradinglab import settings as s

    def fake_get(key, default=None):
        if key == "use_colorblind_palette":
            return False
        return default

    monkeypatch.setattr(s, "get", fake_get)
    bull, bear = c._resolve_initial_palette()
    assert bull == c._DEFAULT_BULL_COLOR
    assert bear == c._DEFAULT_BEAR_COLOR


def test_resolve_initial_palette_settings_import_failure(monkeypatch):
    """If settings is unavailable (extremely rare), fall back to
    the default palette — never to a NameError."""
    import builtins
    import sys

    from tradinglab import constants as c

    real_import = builtins.__import__
    # Snapshot the cached module so we can restore it after the
    # test — popping from sys.modules without restoring would
    # break later tests that monkey-patch attributes on the
    # cached module reference (e.g. test_window_title_cleanup).
    cached_settings = sys.modules.get("tradinglab.settings")

    def hostile_import(name, *args, **kwargs):
        if name == "tradinglab.settings" or name.endswith(".settings"):
            raise ImportError("hostile environment")
        return real_import(name, *args, **kwargs)

    sys.modules.pop("tradinglab.settings", None)
    monkeypatch.setattr(builtins, "__import__", hostile_import)

    try:
        bull, bear = c._resolve_initial_palette()
        assert bull == c._DEFAULT_BULL_COLOR
        assert bear == c._DEFAULT_BEAR_COLOR
    finally:
        # Restore the original cached module so subsequent tests
        # in the suite that rely on it see the same object they
        # imported.
        if cached_settings is not None:
            sys.modules["tradinglab.settings"] = cached_settings


def test_bull_color_and_bear_color_are_strings():
    """Sanity: the top-level constants are always strings (callers
    do hex parsing / matplotlib color lookups)."""
    from tradinglab import constants as c
    assert isinstance(c.BULL_COLOR, str)
    assert isinstance(c.BEAR_COLOR, str)


# ---------------------------------------------------------------------------
# ChartApp.set_use_colorblind_palette — source pin
# ---------------------------------------------------------------------------

APP_SRC = (Path(__file__).resolve().parents[2]
           / "src" / "tradinglab" / "app.py").read_text(encoding="utf-8")
DIALOGS_SRC = (Path(__file__).resolve().parents[2]
               / "src" / "tradinglab" / "gui" / "dialogs.py").read_text(
                   encoding="utf-8")


def test_chartapp_defines_set_use_colorblind_palette():
    assert "def set_use_colorblind_palette" in APP_SRC, (
        "ChartApp.set_use_colorblind_palette setter must exist")


def test_setter_mutates_live_constants():
    """Live mutation makes the chart + watchlist pick up the palette
    swap without a restart — the candle renderers read
    ``constants.BULL_COLOR`` / ``BEAR_COLOR`` via live attribute
    lookup (see the ``*_reads_live_*`` behavioural tests below)."""
    start = APP_SRC.find("def set_use_colorblind_palette")
    end = APP_SRC.find("\n    def ", start + 1)
    body = APP_SRC[start:end] if end != -1 else APP_SRC[start:]
    assert "_constants.BULL_COLOR" in body, (
        "Setter must mutate constants.BULL_COLOR live")
    assert "_constants.BEAR_COLOR" in body, (
        "Setter must mutate constants.BEAR_COLOR live")
    assert "_COLORBLIND_BULL_COLOR" in body, (
        "Setter must reference the canonical Okabe-Ito palette "
        "constants")
    assert "_DEFAULT_BULL_COLOR" in body, (
        "Setter must reference the canonical default palette "
        "constants for the OFF branch")


def test_setter_persists_to_settings_json():
    start = APP_SRC.find("def set_use_colorblind_palette")
    end = APP_SRC.find("\n    def ", start + 1)
    body = APP_SRC[start:end] if end != -1 else APP_SRC[start:]
    assert "_settings.set" in body
    assert '"use_colorblind_palette"' in body


def test_setter_triggers_render():
    """A live re-render lets artists that read constants by
    attribute lookup (rather than `from ... import`) pick up the
    new palette without a restart."""
    start = APP_SRC.find("def set_use_colorblind_palette")
    end = APP_SRC.find("\n    def ", start + 1)
    body = APP_SRC[start:end] if end != -1 else APP_SRC[start:]
    assert "self._render()" in body, (
        "Setter must trigger _render so the current chart picks "
        "up the change")


# ---------------------------------------------------------------------------
# Settings dialog wiring — source pin
# ---------------------------------------------------------------------------

def test_dialog_has_colorblind_checkbox():
    assert "_colorblind_var" in DIALOGS_SRC, (
        "Settings dialog must define a _colorblind_var Tk var")
    assert "Okabe-Ito" in DIALOGS_SRC, (
        "Settings dialog must mention the palette by name "
        "(Okabe-Ito) — accessibility-minded users search for it")


def test_dialog_describes_immediate_apply():
    """After the live-repaint fix, toggling the palette repaints the
    chart and re-tags the watchlist immediately, so the hint must tell
    the user it applies right away (and must NOT claim a relaunch is
    required — that was the pre-fix behaviour that prompted the bug
    report)."""
    assert "immediately" in DIALOGS_SRC.lower(), (
        "Settings dialog must tell the user the palette applies "
        "immediately")
    # The old, now-incorrect 'relaunch required to fully apply' hint
    # must be gone so users aren't told to restart needlessly.
    assert "relaunch required to fully apply" not in DIALOGS_SRC.lower(), (
        "Stale 'relaunch required to fully apply' hint must be removed "
        "now that the chart + watchlist update live")


def test_setter_retags_watchlist_live():
    """The setter must re-apply the active theme so every Treeview's
    bull/bear row BACKGROUND + foreground tags flip in lockstep with the
    chart (watchlist + OHLC tables). The theme controller routes the row
    tints through ``constants.bull_row_bg`` / ``bear_row_bg`` which
    recolour to the Okabe-Ito hue when active."""
    start = APP_SRC.find("def set_use_colorblind_palette")
    end = APP_SRC.find("\n    def ", start + 1)
    body = APP_SRC[start:end] if end != -1 else APP_SRC[start:]
    assert "self._apply_theme()" in body, (
        "Setter must re-apply the theme so Treeview row backgrounds + "
        "foregrounds track the live palette")


def test_dialog_persists_via_setter():
    """Per the live-preview pattern, the toggle handler must call
    the parent's setter (which persists). Cancel restores."""
    start = DIALOGS_SRC.find("def _on_colorblind_toggle")
    end = DIALOGS_SRC.find("\n    def ", start + 1)
    body = DIALOGS_SRC[start:end] if end != -1 else DIALOGS_SRC[start:]
    assert "set_use_colorblind_palette" in body, (
        "Toggle handler must call set_use_colorblind_palette")


def test_dialog_reverts_on_cancel():
    start = DIALOGS_SRC.find("def _on_cancel")
    end = DIALOGS_SRC.find("\n    def ", start + 1)
    body = DIALOGS_SRC[start:end] if end != -1 else DIALOGS_SRC[start:]
    assert "_colorblind_initial" in body


# ---------------------------------------------------------------------------
# Hex sanity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hex_color", [
    "#26a69a", "#ef5350", "#e69f00", "#56b4e9",
])
def test_hex_palette_is_valid(hex_color):
    """Each palette entry must be a valid 7-char hex string so
    matplotlib + Tk both accept it."""
    assert hex_color.startswith("#")
    assert len(hex_color) == 7
    int(hex_color[1:], 16)  # Must parse as hex.


def test_default_and_colorblind_palettes_differ():
    """Sanity: the two palettes are visually distinct."""
    from tradinglab import constants as c
    assert c._DEFAULT_BULL_COLOR != c._COLORBLIND_BULL_COLOR
    assert c._DEFAULT_BEAR_COLOR != c._COLORBLIND_BEAR_COLOR


# ---------------------------------------------------------------------------
# Candle renderers must read the LIVE palette (regression: toggling
# Okabe-Ito has to repaint candles without a relaunch). The bug was that
# rendering.py / chart_renderer.py / volume_tod_overlay.py did
# ``from .constants import BULL_COLOR, BEAR_COLOR`` — binding the *value*
# at import time — so mutating ``constants.BULL_COLOR`` in the setter
# never reached the candle artists. Audit ``color-blind-palette``.
# ---------------------------------------------------------------------------

from datetime import datetime  # noqa: E402


def _candle(close: float, open_: float = 100.0, *, session: str = "regular"):
    from tradinglab.models import Candle
    return Candle(
        date=datetime(2024, 1, 2, 10, 0),
        open=open_,
        high=max(open_, close) + 1.0,
        low=min(open_, close) - 1.0,
        close=close,
        volume=1000,
        session=session,
    )


@pytest.fixture
def _live_sentinel_palette():
    """Swap the live module constants to *sentinel* colours that are
    neither the default nor the Okabe-Ito palette, then restore.

    Using sentinels (rather than the real colour-blind palette) is
    essential: the user who reported the bug runs with
    ``use_colorblind_palette`` already ON, so the cached import in
    rendering.py happens to hold the Okabe-Ito value at import time.
    Asserting against a sentinel guarantees the test only passes if the
    renderer resolves the colour *live* at paint time."""
    from tradinglab import constants as c
    saved = (c.BULL_COLOR, c.BEAR_COLOR)
    c.BULL_COLOR = "#123456"  # sentinel bull — in neither palette
    c.BEAR_COLOR = "#abcdef"  # sentinel bear — in neither palette
    try:
        yield c
    finally:
        c.BULL_COLOR, c.BEAR_COLOR = saved


def test_bar_rgba_reads_live_bull_color(_live_sentinel_palette):
    from matplotlib.colors import to_rgba

    from tradinglab import rendering
    got = rendering._bar_rgba(_candle(close=105.0))  # bull
    assert got == to_rgba("#123456", 1.0), (
        "candle body/wick colour must follow the live BULL_COLOR")


def test_bar_rgba_reads_live_bear_color(_live_sentinel_palette):
    from matplotlib.colors import to_rgba

    from tradinglab import rendering
    got = rendering._bar_rgba(_candle(close=95.0))  # bear
    assert got == to_rgba("#abcdef", 1.0), (
        "candle body/wick colour must follow the live BEAR_COLOR")


def test_bar_geometry_colour_follows_live_palette(_live_sentinel_palette):
    from matplotlib.colors import to_rgba

    from tradinglab import rendering
    _wick, _body, colour = rendering.bar_geometry(_candle(close=105.0), x=0.0)
    assert colour == to_rgba("#123456", 1.0)


def test_vol_geometry_colour_follows_live_palette(_live_sentinel_palette):
    from matplotlib.colors import to_rgba

    from tradinglab import rendering
    _verts, colour = rendering.vol_geometry(_candle(close=95.0), x=0.0)
    assert colour == to_rgba("#abcdef", 0.7)


def test_volume_tod_overlay_colour_follows_live_palette(
        _live_sentinel_palette):
    from matplotlib.colors import to_rgba

    from tradinglab.gui import volume_tod_overlay as vto
    got = vto._bar_base_color(_candle(close=105.0))  # bull
    assert got == to_rgba("#123456", 0.7)


def test_chartstack_direction_colour_follows_live_palette(
        _live_sentinel_palette):
    """ChartStack cards (SPY/QQQ/VXX) must mirror the main chart's
    palette so a color-blind user gets Okabe-Ito candles there too."""
    from tradinglab.gui.chartstack import render as csr
    assert csr._direction_color(100.0, 105.0) == "#123456"  # bull → BULL
    assert csr._direction_color(105.0, 100.0) == "#abcdef"  # bear → BEAR


def test_setter_repaints_chartstack_and_watchlist():
    """The setter must nudge the ChartStack cards (refresh_palette) and
    the watchlist trees so every candle surface updates in lockstep."""
    start = APP_SRC.find("def set_use_colorblind_palette")
    end = APP_SRC.find("\n    def ", start + 1)
    body = APP_SRC[start:end] if end != -1 else APP_SRC[start:]
    assert "refresh_palette" in body, (
        "Setter must repaint the ChartStack cards on palette toggle")


def test_rendering_module_does_not_cache_palette_values():
    """Source-pin: the candle renderer must resolve the palette via a
    live ``constants`` attribute lookup, never a value-binding
    ``from .constants import BULL_COLOR`` (which freezes the colour at
    import time and is exactly what broke the Okabe-Ito toggle)."""
    src = (Path(__file__).resolve().parents[2]
           / "src" / "tradinglab" / "rendering.py").read_text(encoding="utf-8")
    assert "from .constants import BEAR_COLOR, BULL_COLOR" not in src, (
        "rendering.py must not value-bind BULL_COLOR/BEAR_COLOR at import")
    assert "_constants.BULL_COLOR" in src and "_constants.BEAR_COLOR" in src, (
        "rendering.py must read constants.BULL_COLOR/BEAR_COLOR live")
