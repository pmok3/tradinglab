"""Regression tests for the ``british-spelling`` audit.

The reviewer flagged that the codebase mixed British and American
spellings in user-facing strings: ``"Anchor pick cancelled."`` next
to ``"settings.sanitised.json"`` while readme text and most other
strings used American spelling. The user is American and the
target customer base is American; standardizing on the American
form is less surprising and matches what Tk's built-in widgets
(``cancelbutton``, ``standardize``, …) use.

Scope: **user-facing strings only**. Code-internal API names
(``cancelled`` field in ``PreloadResult``, ``IntervalOutcome.status
== "cancelled"`` enum, paper-engine stats keys, etc.) stay because
they ARE the API contract — changing them would break saved
session JSON, scripted callers, and signal-event consumers.

These tests pin the user-facing strings to the American form and
the on-disk diagnostic-bundle filename to ``settings.sanitized.json``.
"""
from __future__ import annotations

from pathlib import Path

import tradinglab
import tradinglab.app
import tradinglab.diagnostics
import tradinglab.gui.exits_tab
import tradinglab.gui.help_menu
import tradinglab.gui.universe_prepare_dialog


def _read(mod) -> str:
    return Path(mod.__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Positive: required American forms
# ---------------------------------------------------------------------------

class TestAmericanSpellingPresent:

    def test_anchor_pick_canceled_status_message(self):
        src = _read(tradinglab.app)
        assert '"Anchor pick canceled"' in src, (
            "Anchor-pick cancel status message must use the American "
            "spelling 'canceled' (audit british-spelling).")

    def test_exits_detach_canceled_dialog(self):
        src = _read(tradinglab.gui.exits_tab)
        assert "Armed legs will be canceled." in src, (
            "Detach-strategy dialog body must use 'canceled' "
            "(audit british-spelling).")

    def test_universe_prepare_canceled_header(self):
        src = _read(tradinglab.gui.universe_prepare_dialog)
        assert '"Canceled" if result.cancelled else "Done"' in src, (
            "Universe-prepare dialog header must read 'Canceled' "
            "while keeping the internal API field name 'cancelled' "
            "(audit british-spelling).")

    def test_diagnostics_filename_is_sanitized(self):
        src = _read(tradinglab.diagnostics)
        assert '"settings.sanitized.json"' in src, (
            "Diagnostic bundle filename must be 'settings.sanitized.json' "
            "(audit british-spelling).")

    def test_diagnostics_readme_uses_sanitized(self):
        src = _read(tradinglab.diagnostics)
        assert "settings.sanitized.json - your settings" in src, (
            "Diagnostic bundle README must reference "
            "'settings.sanitized.json' (audit british-spelling).")

    def test_help_menu_summary_uses_sanitized(self):
        src = _read(tradinglab.gui.help_menu)
        assert "sanitized settings.json" in src, (
            "Help menu diagnostic-bundle summary must say "
            "'sanitized settings.json' (audit british-spelling).")


# ---------------------------------------------------------------------------
# Negative: forbidden British forms in user-facing strings
# ---------------------------------------------------------------------------

class TestBritishFormsAbsentFromUserFacing:

    def test_anchor_pick_does_not_use_cancelled(self):
        src = _read(tradinglab.app)
        assert '"Anchor pick cancelled' not in src, (
            "Anchor-pick status message still uses British 'cancelled' "
            "(audit british-spelling).")

    def test_exits_dialog_does_not_use_cancelled(self):
        src = _read(tradinglab.gui.exits_tab)
        assert "legs will be cancelled" not in src, (
            "Detach-strategy dialog still uses British 'cancelled' "
            "(audit british-spelling).")

    def test_diagnostics_no_sanitised_user_strings(self):
        src = _read(tradinglab.diagnostics)
        assert '"settings.sanitised.json"' not in src, (
            "Diagnostic bundle filename still uses British 'sanitised' "
            "(audit british-spelling).")
        assert "settings.sanitised.json - your settings" not in src, (
            "Diagnostic bundle README still uses British 'sanitised' "
            "(audit british-spelling).")

    def test_help_menu_no_sanitised_summary(self):
        src = _read(tradinglab.gui.help_menu)
        assert "sanitised settings.json" not in src, (
            "Help menu summary still uses British 'sanitised' "
            "(audit british-spelling).")


# ---------------------------------------------------------------------------
# Code-internal API contracts INTENTIONALLY keep British 'cancelled'
# (preload service status enum, paper-engine stats, signal events).
# Documented here so a future cleanup doesn't accidentally rip them
# out and break saved-session JSON or scripted callers.
# ---------------------------------------------------------------------------

class TestInternalApiContractsPreserved:

    def test_preload_service_status_enum_keeps_cancelled(self):
        from tradinglab.preload import service
        src = Path(service.__file__).read_text(encoding="utf-8")
        # The IntervalOutcome.status enum value 'cancelled' is part
        # of the public service API. Don't accidentally Americanize
        # it without coordinating with all consumers.
        assert '"cancelled"' in src, (
            "preload.service status enum 'cancelled' must stay — "
            "it's part of the public API contract. If you really "
            "want to rename, update PreloadResult consumers in lockstep.")

    def test_paper_engine_stats_key_keeps_cancelled(self):
        from tradinglab.exits import paper_engine
        src = Path(paper_engine.__file__).read_text(encoding="utf-8")
        assert '"cancelled"' in src, (
            "paper_engine stats dict key 'cancelled' must stay — "
            "it's part of the public stats() API contract.")
