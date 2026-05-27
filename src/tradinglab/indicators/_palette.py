"""Single source of truth for indicator default colors.

Replaces hardcoded matplotlib-tab10 hex codes scattered across 15+
indicator ``default_style`` blocks plus the ``#888888`` fallback gray
in 6+ other sites. One source of truth -> a global dark-mode palette
can be added with one hook.

The split:

* **Tab10 names** (``TAB10_BLUE`` ... ``TAB10_CYAN``) -- the raw
  matplotlib ``tab10`` color-cycle hex codes. Stable, well-known,
  used by indicators whose "right" color is "the N-th tab10 slot"
  (e.g. SMA = blue, EMA = orange -- matches TradingView convention).
* **Semantic roles** (``PRIMARY_LINE``, ``SECONDARY_LINE``, ...) --
  what an indicator IS. A single-line indicator imports
  ``PRIMARY_LINE``; a Bollinger upper+lower pair both import
  ``QUATERNARY``. If the canonical palette ever changes (e.g. a
  dark-mode rework picks a brighter blue), flip the roles here and
  the whole UI ripples.

Off-palette literals (e.g. tab20 light variants used by RVOL,
Material red/teal pairs used by MACD histogram and Chandelier) are
intentionally NOT promoted to roles -- they're per-indicator visual
decisions and stay inline as literals.
"""

from __future__ import annotations

# matplotlib tab10 names (canonical color cycle)
TAB10_BLUE    = "#1f77b4"
TAB10_ORANGE  = "#ff7f0e"
TAB10_GREEN   = "#2ca02c"
TAB10_RED     = "#d62728"
TAB10_PURPLE  = "#9467bd"
TAB10_BROWN   = "#8c564b"
TAB10_PINK    = "#e377c2"
TAB10_GRAY    = "#7f7f7f"
TAB10_OLIVE   = "#bcbd22"
TAB10_CYAN    = "#17becf"

# Semantic line-color roles. Indicators consume these by ROLE so the
# canonical palette can change once and ripple through the whole UI.
PRIMARY_LINE   = TAB10_BLUE      # MA, BB middle, MACD line, etc.
SECONDARY_LINE = TAB10_ORANGE    # MA #2, MACD signal, etc.
TERTIARY_LINE  = TAB10_GREEN     # MA #3, etc.
QUATERNARY     = TAB10_RED       # bands upper / lower, hi/lo
QUINARY        = TAB10_PURPLE    # extras

# Bullish / bearish semantic colors (slightly off-tab10 for visibility).
BULLISH = "#1bb556"  # green dot at high-water mark, MFE marker
BEARISH = "#d62728"  # red dot, MAE marker

# Default fallback gray used when an indicator's style is unset.
FALLBACK_GRAY = "#888888"
