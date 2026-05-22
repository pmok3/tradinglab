"""Regression test for the ``font-default-config`` audit.

The reviewer noted that without explicit configuration Tk's
``TkDefaultFont`` / ``TkTextFont`` / ``TkMenuFont`` named fonts
fall back to whatever the underlying display server (or stripped
Tk build) decides. On legacy Windows installs or some container
images that's a bitmap fallback that makes the app look like a
1990s shareware demo.

After the fix :func:`tradinglab.gui.named_fonts.configure_named_fonts`
runs from :meth:`ChartApp.__init__` immediately after ``super().__init__()``,
pinning every named font to a known proportional sans family
(Segoe UI 9 on Windows; DejaVu Sans 9 on Linux/BSD; macOS left
alone so Aqua's system font path keeps working) and the fixed
font to a known monospace family.

These tests verify the public API, the family/size selection
logic, and the wiring into ``ChartApp.__init__``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tradinglab.gui import named_fonts as nf
from tradinglab.gui.named_fonts import configure_named_fonts

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

def test_default_size_is_modern_baseline():
    """Default size must be ≥ 8 so the chrome is legible on
    high-DPI displays where Tk wouldn't otherwise scale."""
    assert nf.DEFAULT_SIZE >= 8
    assert nf.DEFAULT_SIZE <= 12, (
        "DEFAULT_SIZE is too large — UI chrome will balloon and break "
        "the carefully sized dialogs.")


def test_fixed_size_is_modern_baseline():
    assert nf.FIXED_SIZE >= 9
    assert nf.FIXED_SIZE <= 13


# ---------------------------------------------------------------------------
# Platform-specific family selection
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not sys.platform.startswith("win"),
                    reason="Windows-specific family selection")
def test_windows_uses_segoe_ui_and_consolas():
    assert nf._PROPORTIONAL_FAMILY == "Segoe UI"
    assert nf._FIXED_FAMILY == "Consolas"


@pytest.mark.skipif(sys.platform != "darwin",
                    reason="macOS-specific family selection")
def test_macos_leaves_fonts_alone():
    assert nf._PROPORTIONAL_FAMILY == ""
    assert nf._FIXED_FAMILY == ""


@pytest.mark.skipif(sys.platform.startswith("win")
                    or sys.platform == "darwin",
                    reason="Linux/BSD-specific family selection")
def test_linux_uses_dejavu_sans():
    assert nf._PROPORTIONAL_FAMILY == "DejaVu Sans"
    assert nf._FIXED_FAMILY == "DejaVu Sans Mono"


# ---------------------------------------------------------------------------
# Named-font coverage
# ---------------------------------------------------------------------------

def test_proportional_named_fonts_includes_tk_default_font():
    """``TkDefaultFont`` is the most-referenced named font in our
    codebase (every ``font=("TkDefaultFont", N, "bold")`` literal
    falls back to it); it must be configured."""
    assert "TkDefaultFont" in nf._PROPORTIONAL_NAMED_FONTS


def test_proportional_named_fonts_includes_menu_and_tooltip():
    """The menu bar, context menus, and the per-widget ToolTip helper
    all derive from these named fonts. Any miss leaves a single Tk
    surface looking out of place."""
    expected = {
        "TkDefaultFont", "TkTextFont", "TkMenuFont",
        "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont",
        "TkIconFont", "TkTooltipFont",
    }
    assert expected.issubset(set(nf._PROPORTIONAL_NAMED_FONTS))


def test_fixed_named_fonts_includes_tk_fixed_font():
    assert "TkFixedFont" in nf._FIXED_NAMED_FONTS


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_configure_named_fonts_is_idempotent(monkeypatch):
    """A second call must not throw, even when the first didn't have
    a real Tk root. Idempotency is enforced by the module-level
    ``_CONFIGURED`` flag."""
    nf._reset_for_tests()
    calls = {"count": 0}

    class FakeFont:
        def cget(self, *_):  # noqa: ANN001
            return "normal"

        def configure(self, **_):  # noqa: ANN001
            calls["count"] += 1

    def fake_nametofont(name, root=None):  # noqa: ARG001
        return FakeFont()

    import tkinter.font as tkfont
    monkeypatch.setattr(tkfont, "nametofont", fake_nametofont)

    # On macOS the function short-circuits without calling
    # nametofont at all, which is the documented behavior; only
    # assert call counts when we expect configuration to happen.
    expected_first = (
        len(nf._PROPORTIONAL_NAMED_FONTS) + len(nf._FIXED_NAMED_FONTS)
        if nf._PROPORTIONAL_FAMILY else 0
    )

    configure_named_fonts(root=None)  # type: ignore[arg-type]
    assert calls["count"] == expected_first
    configure_named_fonts(root=None)  # type: ignore[arg-type]
    assert calls["count"] == expected_first, (
        "Second call to configure_named_fonts re-applied the font "
        "config; the idempotency guard must short-circuit it.")


def test_swallows_tclerror_from_missing_named_font(monkeypatch):
    """Tk builds that lack a named font (very old or stripped builds)
    must not crash the whole app."""
    import tkinter as tk
    nf._reset_for_tests()

    def boom(name, root=None):  # noqa: ARG001
        raise tk.TclError(f"named font {name!r} is unknown")

    import tkinter.font as tkfont
    monkeypatch.setattr(tkfont, "nametofont", boom)

    configure_named_fonts(root=None)  # type: ignore[arg-type]
    # No exception leaked.


# ---------------------------------------------------------------------------
# Wiring into ChartApp.__init__
# ---------------------------------------------------------------------------

def test_app_imports_configure_named_fonts():
    """ChartApp must import the configurator so the baseline runs
    before any widget construction."""
    from tradinglab import app as app_mod
    src = Path(app_mod.__file__).read_text(encoding="utf-8")
    assert "configure_named_fonts" in src and "from .gui.named_fonts" in src, (
        "app.py must import configure_named_fonts (likely via "
        "`from .gui.named_fonts import ...`) so it can run "
        "the font baseline immediately after super().__init__().")


def test_chartapp_init_calls_configure_named_fonts():
    """The wiring must be inside ChartApp.__init__ near the top."""
    from tradinglab import app as app_mod
    src = Path(app_mod.__file__).read_text(encoding="utf-8")
    needle = (
        "def __init__(self, *, splash: Optional[SplashController] = None) -> None:\n"
        "        super().__init__()\n"
    )
    assert needle in src, (
        "ChartApp.__init__ signature changed in an unexpected way; "
        "update the test if intentional.")
    # The configure call must appear shortly after super().__init__().
    # Accept either the old `configure_named_fonts(self)` shape or the
    # newer `configure_named_fonts(self, scale=...)` (audit `font-scaling`).
    head_idx = src.find(needle)
    window = src[head_idx: head_idx + 1500]
    assert "configure_named_fonts(self" in window, (
        "ChartApp.__init__ must call configure_named_fonts(self...) "
        "shortly after super().__init__() so the named-font baseline "
        "is in place before any widget is constructed.")


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

def test_module_exports():
    # font-scaling (audit) added UI_SCALES / DEFAULT_UI_SCALE / clamp_ui_scale
    # / current_ui_scale alongside the original three.
    expected = {
        "DEFAULT_SIZE",
        "DEFAULT_UI_SCALE",
        "FIXED_SIZE",
        "UI_SCALES",
        "clamp_ui_scale",
        "configure_named_fonts",
        "current_ui_scale",
    }
    assert set(nf.__all__) == expected
