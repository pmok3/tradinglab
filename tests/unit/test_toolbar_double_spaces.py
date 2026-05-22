"""Regression tests for audit ``toolbar-double-spaces``.

The toolbar's three right-side buttons (**Reset view**, **Settings**,
**Watchlists**) used to ship with **two** spaces between the label
and the parenthetical hotkey hint (e.g. ``"Reset view  (R)"``).
Two-space padding inside a button label reads as a missed-typo to
new users — Windows/macOS/Linux button labels conventionally use a
single space between the action verb and the parenthetical
shortcut. The fix tightens the padding to one space; this test
locks it in.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "tradinglab" / "app.py"
)

# Three known-bad patterns (post-fix none of them may appear in app.py).
DOUBLE_SPACE_LABEL_PATTERNS = [
    r"\"Reset view  \(R\)\"",
    r"\"Settings  \(Ctrl\+,\)\"",
    r"\"Watchlists  \(Ctrl\+L\)\"",
]

# Their fixed forms — the test also requires the fixed forms ARE
# present, so an accidental future rename doesn't silently regress
# the spacing.
SINGLE_SPACE_LABEL_PATTERNS = [
    r"\"Reset view \(R\)\"",
    r"\"Settings \(Ctrl\+,\)\"",
    r"\"Watchlists \(Ctrl\+L\)\"",
]


def test_no_double_space_before_paren_in_toolbar_labels() -> None:
    src = APP_PATH.read_text(encoding="utf-8")
    offenders = [p for p in DOUBLE_SPACE_LABEL_PATTERNS if re.search(p, src)]
    assert not offenders, (
        "toolbar-double-spaces regression: double-space-before-paren "
        "form is back on the toolbar. Offending patterns: "
        f"{offenders}. The buttons should be 'Reset view (R)' / "
        "'Settings (Ctrl+,)' / 'Watchlists (Ctrl+L)' (single space)."
    )


def test_single_space_form_present_in_toolbar_labels() -> None:
    src = APP_PATH.read_text(encoding="utf-8")
    missing = [p for p in SINGLE_SPACE_LABEL_PATTERNS if not re.search(p, src)]
    assert not missing, (
        "toolbar-double-spaces regression: one of the canonical "
        "single-space button labels is no longer present in app.py. "
        f"Missing patterns: {missing}. If the toolbar was restructured, "
        "update SINGLE_SPACE_LABEL_PATTERNS in this test."
    )


def test_no_triple_or_more_spaces_inside_any_app_label() -> None:
    """Catch-all guard: any string literal of the form
    ``"<chars>   <chars>"`` inside app.py is almost certainly a
    formatting bug. We allow doublespace as a known stylistic
    choice elsewhere (e.g. left-pad inside menu hint columns), but
    triple-space is never intentional."""
    src = APP_PATH.read_text(encoding="utf-8")
    bad = re.findall(r'"[^"\n]*   +[^"\n]*"', src)
    # Allow specific known-good multi-space literals if any ever
    # appear (currently none); empty allowlist defends against
    # surprise drift.
    allow_substrings = (
        # CLI ``--help`` output: column-padded option list. The
        # multi-space gap aligns the option flags with their
        # descriptions in monospaced terminal output.
        "--version",
        "--help",
    )
    bad = [s for s in bad if not any(a in s for a in allow_substrings)]
    assert not bad, (
        "Found string literals with 3+ consecutive spaces inside "
        f"app.py: {bad}. Almost certainly a typo / formatting bug — "
        "tighten the padding."
    )
