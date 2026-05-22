"""Regression test for audit ``reset-view-style``.

The toolbar **Reset view** button used to take the
``Destructive.TButton`` red style — the same red that signals the
sandbox **PANIC: Flatten All** button which actually closes every
paper position. The reviewer's stock-trader persona was wary of
clicking the red toolbar button mid-trade thinking it might
destroy state. Reset view is a benign zoom restore. The fix
removes the destructive style so the button takes the default
``TButton`` style, matching its peer Settings / Watchlists
buttons.

This test ensures the red style does not return.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "tradinglab" / "app.py"
)


def test_reset_view_toolbar_button_uses_default_style() -> None:
    src = APP_PATH.read_text(encoding="utf-8")
    # Find the ttk.Button(... text="Reset view ...") block. It
    # spans at most a few lines (text + style? + command).
    match = re.search(
        r"ttk\.Button\(top, text=\"Reset view[^\"]*\"[^)]*\)",
        src, flags=re.DOTALL,
    )
    assert match is not None, (
        "Could not find the Reset view ttk.Button definition in "
        "app.py — has the toolbar been restructured?"
    )
    block = match.group(0)
    assert "Destructive.TButton" not in block, (
        "reset-view-style regression: the Reset view toolbar button "
        "is back to the Destructive.TButton (red) style. That style "
        "is reserved for genuinely-destructive actions (PANIC: "
        "Flatten All); Reset view is a benign zoom restore."
    )


def test_destructive_style_still_used_by_panic_flatten() -> None:
    """Sanity check: the Destructive.TButton style itself should
    still exist (it's used elsewhere). Removing it from Reset view
    must not have accidentally deleted the style class."""
    panic_path = (
        Path(__file__).resolve().parents[2]
        / "src" / "tradinglab" / "gui" / "exits_tab.py"
    )
    if panic_path.exists():
        text = panic_path.read_text(encoding="utf-8")
        assert "Destructive.TButton" in text, (
            "Destructive.TButton style appears to no longer be used "
            "by the PANIC: Flatten All button in exits_tab.py. Either "
            "the style was deleted (it should still exist in "
            "constants.py for future destructive actions) or the "
            "button was moved — investigate."
        )


def test_constants_still_define_destructive_button_style() -> None:
    """The style class itself remains registered for the PANIC
    button and any future destructive action that wants it."""
    constants_path = (
        Path(__file__).resolve().parents[2]
        / "src" / "tradinglab" / "constants.py"
    )
    text = constants_path.read_text(encoding="utf-8")
    assert "Destructive.TButton" in text, (
        "Destructive.TButton style spec must remain in constants.py "
        "(used by PANIC: Flatten All and reserved for future "
        "destructive-action buttons)."
    )
