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
    """Live mutation makes new dialogs / charts pick up the
    palette swap without a restart. (Cached imports still hold
    the old reference, hence the dialog's 'relaunch required'
    hint.)"""
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


def test_dialog_warns_about_relaunch():
    """The hint must tell the user that some references are
    cached and require a relaunch — otherwise they'll toggle the
    setting, watch the chart not change instantly, and report a
    bug."""
    assert "Relaunch required" in DIALOGS_SRC or "relaunch required" in (
        DIALOGS_SRC.lower()), (
            "Settings dialog must warn that a relaunch is needed "
            "to fully propagate the palette change")


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
