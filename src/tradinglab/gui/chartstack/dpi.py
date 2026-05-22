"""DPI helpers for the ChartStack panel (M7).

Encapsulates the "is this a 4K-class display?" question so the
panel can:

* Auto-cap the card count at 6 when the trader is on a high-DPI
  display (per §5.2 of the spec — the cards are 220 px wide; on a
  4K monitor the strip can host 6 without crowding, but more would
  push the main chart's usable width below ~70 %).
* Surface a status-bar message the first time the cap is hit so
  the trader knows why their configured `chartstack.cards.count`
  was clamped.

The helper is intentionally tolerant: Tk's ``winfo_fpixels('1i')``
returns pixels-per-inch on the widget's current root; if it raises
(headless test stub, broken Tk display) we return ``False`` so the
panel keeps its existing 5-card ceiling.
"""

from __future__ import annotations

from typing import Any, Optional

# A 4K monitor at 24" is ~184 PPI; at 32" it's ~140 PPI. Use the
# common "Hi-DPI" threshold of 144 PPI which excludes regular 1080p
# monitors and includes the typical 4K+ class.
HI_DPI_THRESHOLD: float = 144.0

# Card-count caps. The 6-card cap is documented in §5.2 of the
# ChartStack spec. The 5-card cap is the historical default.
CARD_CAP_STANDARD: int = 5
CARD_CAP_HI_DPI: int = 6


def is_hi_dpi(widget: Any) -> bool:
    """Best-effort: is the widget's display ≥ ``HI_DPI_THRESHOLD`` PPI?

    Reads ``winfo_fpixels('1i')`` (pixels per logical inch). Returns
    ``False`` on any error so the test-stub path collapses to the
    standard 5-card cap.
    """
    if widget is None:
        return False
    try:
        ppi = float(widget.winfo_fpixels("1i"))
    except Exception:  # noqa: BLE001 - Tk may be torn down or stubbed
        return False
    return ppi >= HI_DPI_THRESHOLD


def card_count_cap(widget: Any) -> int:
    """Return the maximum card count permitted on this display.

    ``CARD_CAP_HI_DPI`` (6) on 4K-class displays;
    ``CARD_CAP_STANDARD`` (5) otherwise.
    """
    return CARD_CAP_HI_DPI if is_hi_dpi(widget) else CARD_CAP_STANDARD


__all__ = [
    "CARD_CAP_HI_DPI",
    "CARD_CAP_STANDARD",
    "HI_DPI_THRESHOLD",
    "card_count_cap",
    "is_hi_dpi",
]
