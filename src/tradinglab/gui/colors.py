"""Centralized semantic color tokens for the GUI.

Single source of truth for UI-affordance colors — positive/negative
sentiment, neutral warnings, error/help text — so that ad-hoc hex codes
sprinkled across dialogs, overlays, and panels don't drift.

These are **semantic** tokens, not theme tokens. They render the same
shade in light and dark theme because the underlying messages (P/L
sign, warning notice, error status, muted hint) read the same way
regardless of the chart's background. Theme-aware colors (axis text,
spine, axes background) still live in ``constants.LIGHT_THEME`` /
``DARK_THEME``.

For trader-direction colors on candles, ``constants.BULL_COLOR`` and
``constants.BEAR_COLOR`` are the canonical source — ``UP_GREEN`` and
``DOWN_RED`` are aliases that match those values so positive P/L and
bull candles render the same shade (and vice versa for negative P/L).
"""

from __future__ import annotations

from .. import constants as _constants
from ..constants import BEAR_COLOR, BULL_COLOR

# ---------------------------------------------------------------------------
# Sentiment colors — positive / negative semantic axis.
# ---------------------------------------------------------------------------

UP_GREEN: str = BULL_COLOR
"""Positive sentiment (gains, wins, in-the-money). Import-time snapshot
aliased to ``constants.BULL_COLOR``. **Prefer :func:`up_green` for any
color read at paint time** — this constant freezes the palette at import
and will not follow a runtime Okabe-Ito toggle. Audit
``color-blind-palette-audit``."""

DOWN_RED: str = BEAR_COLOR
"""Negative sentiment (losses, out-of-the-money). Import-time snapshot
aliased to ``constants.BEAR_COLOR``. **Prefer :func:`down_red`** for
live reads (see :data:`UP_GREEN`)."""


def up_green() -> str:
    """Live positive-sentiment color = current ``constants.BULL_COLOR``.

    Follows the Okabe-Ito color-blind palette toggle (orange when active)
    because it reads the constant at call time rather than import time.
    """
    return _constants.BULL_COLOR


def down_red() -> str:
    """Live negative-sentiment color = current ``constants.BEAR_COLOR``."""
    return _constants.BEAR_COLOR


# ---------------------------------------------------------------------------
# Status colors — neutral warnings, error states, and muted help text.
# ---------------------------------------------------------------------------

WARN_AMBER: str = "#a36b00"
"""Amber for neutral warnings ("approaching earnings", "near-stop
proximity", "budget exceeded"). Desaturated enough to read on both
light and dark backgrounds without becoming alarming."""

INFO_BLUE: str = "#1f6feb"
"""Informational blue for new-edge alerts and informational badges
("PMH break", "new scanner edge"). Distinct from sentiment colors
so a "new finding" doesn't read as a P/L sign."""

CAUTION_YELLOW: str = "#d4a017"
"""Caution yellow for context-warning badges ("earnings T-1",
"ex-div today"). Brighter than ``WARN_AMBER`` so it surfaces above
the per-card stroke colors without being alarming."""

ERROR_RED: str = "#a33333"
"""Error-state red — validation failures, error status text in modal
footers. Distinct from ``DOWN_RED`` (which is the candle-bear hue) so
"loss" vs "error" stay visually separated."""

MUTED_GREY: str = "#666666"
"""Help / hint / secondary-label text. Lower contrast than the
foreground theme color so it reads as deprioritized but stays legible
on both light and dark backgrounds."""


__all__ = [
    "UP_GREEN",
    "DOWN_RED",
    "up_green",
    "down_red",
    "WARN_AMBER",
    "INFO_BLUE",
    "CAUTION_YELLOW",
    "ERROR_RED",
    "MUTED_GREY",
]
