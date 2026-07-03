"""Prior Day High / Low / Close.

Horizontal reference lines drawn at the previous regular-session
trading day's high, low, and close on intraday charts. These are
the most fundamental support/resistance levels for discretionary
intraday trading:

- **PDH** — prior day high. Breakout above PDH signals new demand;
  rejection at PDH is a short setup.
- **PDL** — prior day low. Breakdown below PDL signals trend-day
  weakness; reclaim from below is a strength signal.
- **PDC** — prior day close. Defines the gap. "Are we above or below
  yesterday's close?" contextualises gap-up/gap-down behaviour
  within the first 30 minutes of the open.

Each level is independently visible (controlled via the per-output
visibility toggle in the Manage Indicators dialog) and has its own
colour swatch. All three default to ON.

Definition:
    "Prior day" = the most recent *completed* regular-session
    (9:30–16:00 ET) trading day present in the loaded bar data.
    Extended hours are excluded. On Monday mornings, "prior day" =
    Friday. Half-days (early close) use the shortened session's H/L/C.

Availability:
    Intraday intervals only (1m through 4h). Auto-hidden on daily,
    weekly, and monthly charts (the previous candle IS the prior day
    on those timeframes — drawing it as a separate line would be
    redundant).

Warmup:
    Requires at least two completed trading days in the loaded data
    to produce any finite output. If only today's bars are present,
    all outputs are NaN and no lines are drawn.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from ..constants import BEAR_COLOR, BULL_COLOR
from ..core.bars import Bars
from .base import Availability, BaseIndicator, LineStyle, ParamDef, intraday_only
from .sessions import is_intraday_np, session_groups_np


class PriorDayHLC(BaseIndicator):
    """Prior Day High / Low / Close reference lines.

    ``compute_arr`` returns ``{"pdh": ndarray, "pdl": ndarray,
    "pdc": ndarray}``. Each array is the same length as the input
    bars. For each intraday session that has a completed prior
    session in the data, the corresponding bars are set to the prior
    session's high, low, and close respectively. Bars belonging to
    the earliest session (no prior data) and all bars on non-intraday
    intervals are NaN.
    """

    kind_id: ClassVar[str] = "prior_day_hlc"
    kind_version: ClassVar[int] = 2
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("show_high", "bool", default=True,
                 description="Prior Day High"),
        ParamDef("show_low", "bool", default=True,
                 description="Prior Day Low"),
        ParamDef("show_close", "bool", default=True,
                 description="Prior Day Close"),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        # PDH = bull (support-from-above breakout), PDL = bear, sourced from
        # the live bull/bear palette so they follow the Okabe-Ito toggle.
        # Audit ``color-blind-palette-audit``.
        "prior_day_high":  LineStyle(color=BULL_COLOR, width=1.2),
        "prior_day_low":   LineStyle(color=BEAR_COLOR, width=1.2),
        "prior_day_close": LineStyle(color="#9e9e9e", width=1.0),   # gray
    }

    overlay = True  # draw on the price axes

    def __init__(self, show_high: bool = True, show_low: bool = True,
                 show_close: bool = True) -> None:
        self.show_high = bool(show_high)
        self.show_low = bool(show_low)
        self.show_close = bool(show_close)
        parts = []
        if self.show_high:
            parts.append("H")
        if self.show_low:
            parts.append("L")
        if self.show_close:
            parts.append("C")
        self.name = f"Prior Day {'/'.join(parts)}" if parts else "Prior Day (none)"

    # Compact per-output labels for the in-chart readout legend. The
    # canonical output keys (``prior_day_high`` / ``prior_day_low`` /
    # ``prior_day_close``) stay stable on disk (style + per-output
    # visibility persistence); only the displayed band label is shortened.
    _OUTPUT_KEY_LABELS: ClassVar[dict[str, str]] = {
        "prior_day_high": "pd_high",
        "prior_day_low": "pd_low",
        "prior_day_close": "pd_close",
    }

    @staticmethod
    def is_available_for(interval: str) -> Availability:
        """Only available on intraday intervals."""
        return intraday_only(interval)

    @classmethod
    def effective_output_keys(cls, params: dict) -> tuple[str, ...]:
        """Only the *enabled* levels are visible outputs.

        When a level's ``show_*`` param is off, its output key (e.g.
        ``prior_day_close`` when ``show_close=False``) is dropped from the
        rendered / legend output set so a deselected level does NOT appear
        on the chart — not as a line and not as a readout-legend entry.

        ``compute_arr`` still returns the full three-key dict (disabled
        levels all-NaN) for back-compat with the persisted ``style`` /
        per-output-visibility keys and the existing output-key tests; this
        method is the single source of truth for *which* of those outputs
        are actually shown.
        """
        p = params or {}
        keys: list[str] = []
        if p.get("show_high", True):
            keys.append("prior_day_high")
        if p.get("show_low", True):
            keys.append("prior_day_low")
        if p.get("show_close", True):
            keys.append("prior_day_close")
        return tuple(keys)

    @classmethod
    def legend_label(cls, display_name: str, params: dict) -> str | None:
        """Show the clean name only — suppress the noisy params suffix.

        The generic ``format_indicator_label`` walker would render the
        boolean ``show_high`` / ``show_low`` / ``show_close`` toggles as a
        ``(True, show_low=True, show_close=True)`` suffix on the chart
        legend. Which levels are active is already conveyed by the display
        name ("Prior Day H/L/C" → "Prior Day H/L") and the per-output band
        labels (``pd_high`` / ``pd_low`` / ``pd_close``), so the params add
        only clutter. Return the display name verbatim. Mirrors AVWAP's
        override (audit ``avwap-anchor-only-label``).
        """
        return (display_name or "Prior Day H/L/C").strip() or "Prior Day H/L/C"

    @classmethod
    def output_key_label(cls, key: str) -> str:
        """Abbreviate the per-output band label for the readout legend.

        ``prior_day_high`` → ``pd_high`` etc. Unknown keys pass through.
        """
        return cls._OUTPUT_KEY_LABELS.get(key, key)

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        n = len(bars)
        pdh = np.full(n, np.nan, dtype=np.float64)
        pdl = np.full(n, np.nan, dtype=np.float64)
        pdc = np.full(n, np.nan, dtype=np.float64)
        result = {"prior_day_high": pdh, "prior_day_low": pdl,
                  "prior_day_close": pdc}

        if n == 0 or not is_intraday_np(bars):
            return result

        # Nothing to compute if all three are disabled.
        if not (self.show_high or self.show_low or self.show_close):
            return result

        groups = session_groups_np(bars, regular_only=True)
        if len(groups) < 2:
            return result

        highs, lows, closes = bars.high, bars.low, bars.close

        for i in range(1, len(groups)):
            prev_grp = groups[i - 1]
            cur_grp = groups[i]
            if prev_grp.size == 0 or cur_grp.size == 0:
                continue
            if self.show_high:
                pdh[cur_grp] = float(np.nanmax(highs[prev_grp]))
            if self.show_low:
                pdl[cur_grp] = float(np.nanmin(lows[prev_grp]))
            if self.show_close:
                pdc[cur_grp] = float(closes[prev_grp][-1])

        # Break the line between sessions so matplotlib does not draw
        # a vertical connector when the prior-day level changes from
        # one day to the next. Insert NaN at the last bar of the
        # *previous* session's group so the line ends cleanly there
        # and the new session's first bar still shows its level.
        for i in range(2, len(groups)):
            prev_grp = groups[i - 1]
            if prev_grp.size == 0:
                continue
            # NaN at the last bar of the previous session breaks the
            # line before the new session starts.
            tail = prev_grp[-1]
            pdh[tail] = np.nan
            pdl[tail] = np.nan
            pdc[tail] = np.nan

        return result

