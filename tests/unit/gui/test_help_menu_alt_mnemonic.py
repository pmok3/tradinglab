"""Audit ``help-menu-alt-h-no-mnemonic`` — Help cascade is built with ``underline=-1``.

When the Help cascade is built without ``underline=-1``, Tk on
Windows assigns the default first-letter Alt mnemonic to "H".
Pressing Alt+H then opens the Help menu and highlights "About
TradingLab…" — silently swallowing the TradingView-style
horizontal-line drawing shortcut documented at
``app.spec.md`` §Horizontal-line drawings.

The fix sets ``underline=-1`` on the cascade so the Alt+H key
combo is free to fire ``_on_alt_h_placement`` instead.
"""
from __future__ import annotations

import inspect

from tradinglab.gui.help_menu import HelpMenuMixin


class TestHelpCascadeMnemonicDisabled:
    def test_build_help_menu_passes_underline_negative_one(self):
        # Inspect the source directly so we don't need a real Tk root.
        src = inspect.getsource(HelpMenuMixin._build_help_menu)
        assert "underline=-1" in src, (
            "Help cascade must be added with underline=-1 so the "
            "Alt+H mnemonic doesn't shadow the drawing-placement "
            "keystroke documented at app.spec.md.")

    def test_help_cascade_is_added_via_add_cascade(self):
        # Sanity-check that we still build a cascade — if the call
        # site changes the underline=-1 assertion would silently pass.
        src = inspect.getsource(HelpMenuMixin._build_help_menu)
        assert "add_cascade" in src
        assert 'label="Help"' in src


class TestAltHKeyBindingPresent:
    """``app.py`` must bind <Alt-h>/<Alt-H> to the placement handler."""

    def test_alt_h_bound_in_app(self):
        import tradinglab.app as app_mod
        src = inspect.getsource(app_mod.ChartApp)
        # Both case variants must be bound — Caps-Lock / Shift defence.
        assert '"<Alt-h>"' in src
        assert '"<Alt-H>"' in src
        # And the target must be _on_alt_h_placement — the same handler
        # Ctrl+H routes to.
        assert "_on_alt_h_placement" in src
