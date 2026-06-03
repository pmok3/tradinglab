"""Color picker — thin wrapper over the native OS color chooser.

Single entry point :func:`pick_color` blocks on
:func:`tkinter.colorchooser.askcolor` and returns the normalised
chosen hex (e.g. ``"#1f77b4"``) or ``None`` if the user cancels.

Audit tag: ``color-picker-native-only``.

Previous revisions shipped a rich custom :class:`HexColorPalette`
(saturation/value gradient + honeycomb swatches + System… escape
hatch). The user reported the custom palette was too sparse and
asked that the System (native OS) chooser become the canonical
*and only* color UI for indicator colors. The custom dialog and
its honeycomb / HSV helpers were deleted in the same commit.

Note: the native chooser follows the OS theme, NOT the app's
dark/light theme — that is the explicit trade-off the user
accepted by requesting the System popup as the only surface.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser

#: Default mid-gray used when callers pass an empty / falsy initial.
#: Matches :class:`tradinglab.indicators.base.LineStyle` default.
DEFAULT_COLOR = "#888888"


def _normalise(color: str) -> str:
    """Normalise a hex color string to lower-case ``"#rrggbb"``.

    * ``"#RRGGBB"`` → lower-cased.
    * ``"#RGB"`` short-form → expanded then lower-cased.
    * Empty / falsy input → :data:`DEFAULT_COLOR`.
    * Anything else (e.g. an X11 color name) is returned unchanged so
      Tk can still resolve it downstream — matches the original
      ``HexColorPalette._normalise`` contract pinned by
      ``tests/unit/test_hex_case_constants.py``.
    """
    s = (color or "").strip()
    if not s:
        return DEFAULT_COLOR
    if s.startswith("#") and len(s) == 7:
        return "#" + s[1:].lower()
    if s.startswith("#") and len(s) == 4:
        r, g, b = s[1], s[2], s[3]
        return ("#" + r + r + g + g + b + b).lower()
    return s


def pick_color(
    parent: tk.Misc,
    initial: str = DEFAULT_COLOR,
    title: str = "Pick a color",
) -> str | None:
    """Open the OS native color chooser modally; return the chosen hex.

    Blocks the calling thread until the user dismisses the chooser;
    must be invoked from the Tk main thread.

    Returns the normalised ``"#rrggbb"`` lower-case hex on OK, or
    ``None`` if the user cancels / closes the dialog or the
    underlying Tk call fails.
    """
    safe_initial = _normalise(initial)
    try:
        _rgb, hex_color = colorchooser.askcolor(
            color=safe_initial, parent=parent, title=title,
        )
    except tk.TclError:
        return None
    if not hex_color:
        return None
    return _normalise(hex_color)


__all__ = ["DEFAULT_COLOR", "pick_color"]
