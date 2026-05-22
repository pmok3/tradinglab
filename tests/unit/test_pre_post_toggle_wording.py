"""Regression tests for the ``pre-post-toggle-wording`` audit.

The user-facing label on the toolbar Extended-Hours checkbutton
was historically ``"Pre/Post"`` (slash-separated abbreviation).
The 1-star reviewer flagged this as cryptic — the abbreviation
doesn't decode without prior knowledge, and the surrounding
documentation/comments use a mix of "pre-market/post-market",
"extended hours", and "pre/post bars". The locked-in convention:

* **User-facing label**: ``"Extended Hours"`` (matches what
  brokers / exchanges call the 04:00–09:30 + 16:00–20:00 ET
  windows).
* **Internal code identifiers**: ``prepost_var``, ``prepost``
  parameter, etc. — stay because the vendor APIs use this term
  (yfinance: ``prepost=True``); renaming would churn the API
  layer without benefit.
* **Tooltip**: explains what extended hours means and gives the
  time windows so new users don't have to guess.

These tests pin the toolbar label, the tooltip wiring, and the
absence of the old ``"Pre/Post"`` user-facing string.
"""
from __future__ import annotations

import re
from pathlib import Path

import tradinglab.app


def _read_app_py() -> str:
    return Path(tradinglab.app.__file__).read_text(encoding="utf-8")


class TestExtendedHoursLabel:

    def test_toolbar_label_is_extended_hours(self):
        src = _read_app_py()
        assert 'text="Extended Hours"' in src, (
            "Toolbar checkbutton must use the user-facing label "
            "'Extended Hours' (audit pre-post-toggle-wording).")

    def test_old_pre_slash_post_label_absent(self):
        src = _read_app_py()
        # The old label was 'text="Pre/Post"'. It must not appear
        # in any ttk.Checkbutton / ttk.Button / ttk.Label call.
        # Code-internal comments may still mention "Pre/Post" for
        # historical context, but the literal 'text="Pre/Post"'
        # widget-text assignment is gone.
        assert 'text="Pre/Post"' not in src, (
            "The old user-facing label 'Pre/Post' is still wired "
            "into a widget (audit pre-post-toggle-wording).")


class TestExtendedHoursTooltip:

    def test_prepost_tooltip_attribute_present(self):
        src = _read_app_py()
        assert "_prepost_tooltip" in src, (
            "Extended-hours checkbutton should carry a ToolTip "
            "stored as ``self._prepost_tooltip`` (audit "
            "pre-post-toggle-wording).")

    def test_tooltip_explains_extended_hours_window(self):
        src = _read_app_py()
        # Locate the _prepost_tooltip assignment block; capture up
        # to the trailing "," that follows the second string
        # literal in the source. The text literals are split
        # across two lines via Python's adjacent-literal
        # concatenation, so a non-greedy ``.*?`` would stop at
        # the first ``)`` inside the text itself. Anchor on the
        # comment immediately following the call.
        idx = src.find("self._prepost_tooltip = _ToolTip(")
        assert idx != -1, "could not locate _prepost_tooltip assignment"
        block = src[idx:idx + 600]
        # The tooltip text must mention both pre- and after-hours,
        # plus at least one of the canonical hour markers.
        assert "pre-market" in block.lower(), (
            f"Tooltip must call out pre-market explicitly. Block: {block!r}")
        assert "after-hours" in block.lower() or "post-market" in block.lower(), (
            f"Tooltip must call out after-hours / post-market. Block: {block!r}")
        # At least one specific ET time so the user knows what
        # window we're talking about.
        assert any(t in block for t in ("04:00", "09:30", "16:00", "20:00")), (
            f"Tooltip should include at least one canonical ET time. Block: {block!r}")


class TestInternalIdentifiersUnchanged:
    """The fix is **user-facing-string-only**. The internal API
    surface (variables, parameters) keeps the ``prepost`` name so
    the vendor data layer stays unchanged."""

    def test_prepost_var_still_used(self):
        src = _read_app_py()
        assert "self.prepost_var" in src, (
            "Internal BooleanVar ``prepost_var`` must still exist "
            "— rename is user-facing-strings only.")

    def test_on_prepost_toggle_handler_still_exists(self):
        src = _read_app_py()
        assert "_on_prepost_toggle" in src, (
            "Handler ``_on_prepost_toggle`` must still exist — "
            "rename is user-facing-strings only.")
