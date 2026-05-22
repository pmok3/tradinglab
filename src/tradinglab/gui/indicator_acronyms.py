"""Indicator acronym explanations for tooltip surfaces.

The Manage Indicators dialog and the per-indicator popup display
indicators by their short factory name — "RSI", "SMI", "LRSI",
"AVWAP", "RVOL", "RRVOL", "MACD", "ADX", "ATR", "VWAP", "SMA",
"EMA". A user new to technical analysis (or to a particular niche
indicator like LRSI / RRVOL) sees a fog of acronyms with no
explanation. The fix is a hover tooltip on the indicator kind
combobox that surfaces the full name plus a one-line description.

This module is the single source of truth for those blurbs so the
manager dialog and the per-indicator popup stay in sync.

Each entry is keyed by ``IndicatorFactory.kind_id`` because that's
the stable identifier — display names can be renamed via
``IndicatorConfig.display_name`` but the ``kind_id`` is the
persistence key.

Format
------
``ACRONYMS[kind_id] = (full_name, one_line_blurb)``

The blurb is intentionally short (≤ ~80 chars) so it fits a
single-line ToolTip without wrapping awkwardly. The full name
goes on the first line; the blurb on the second.
"""
from __future__ import annotations

# kind_id → (full name, brief blurb).
ACRONYMS: dict[str, tuple[str, str]] = {
    "sma": (
        "Simple Moving Average",
        "Equal-weighted mean of the last N closes — smooth trend reference.",
    ),
    "ema": (
        "Exponential Moving Average",
        "Smoothed mean that weights recent bars more heavily than older ones.",
    ),
    "vwap": (
        "Volume-Weighted Average Price",
        "Session-anchored mean weighted by volume; intraday fair-price proxy.",
    ),
    "avwap": (
        "Anchored VWAP",
        "VWAP whose start point you pick (event-anchored fair-price reference).",
    ),
    "rsi": (
        "Relative Strength Index",
        "Bounded momentum oscillator (0–100); >70 overbought, <30 oversold.",
    ),
    "lrsi": (
        "Laguerre RSI",
        "RSI variant using a Laguerre filter — fewer whipsaws than classic RSI.",
    ),
    "macd": (
        "Moving Average Convergence Divergence",
        "Trend/momentum from the spread between two EMAs and a signal line.",
    ),
    "smi": (
        "Stochastic Momentum Index",
        "Centered, smoothed stochastic that swings around zero.",
    ),
    "adx": (
        "Average Directional Index",
        "Trend-strength gauge (0–100); >25 typically signals a real trend.",
    ),
    "atr": (
        "Average True Range",
        "Volatility measure — average bar range; used for stops & sizing.",
    ),
    "rvol": (
        "Relative Volume",
        "Today's volume vs. the recent average — surfaces unusual activity.",
    ),
    "rrvol": (
        "Rolling Relative Volume",
        "Rolling-window relative volume; smoother RVOL alternative.",
    ),
    "bbands": (
        "Bollinger Bands",
        "Price channel around an SMA, widened by N standard deviations.",
    ),
    "bbands_ema": (
        "Bollinger Bands (EMA basis)",
        "Bollinger Bands centered on an EMA instead of an SMA.",
    ),
    "keltner": (
        "Keltner Channels",
        "Price channel around an EMA, widened by multiples of ATR.",
    ),
    "chandelier": (
        "Chandelier Stops",
        "Trailing stop level pinned N×ATR below the highest high since entry.",
    ),
    "prior_day_hlc": (
        "Prior Day High / Low / Close",
        "Yesterday's regular-session H/L/C as horizontal reference lines.",
    ),
    "overlap_score_inv": (
        "Overlap Score Inverted",
        "Weighted range overlap — high = new territory, low = consolidation.",
    ),
}


def explain_kind_id(kind_id: str) -> str:
    """Return a multi-line tooltip blurb for ``kind_id``.

    Falls back to ``"<kind_id>"`` when the kind is unknown so the
    tooltip surface degrades gracefully for third-party indicators
    that haven't been documented here yet.
    """
    entry = ACRONYMS.get(kind_id)
    if entry is None:
        return kind_id
    full_name, blurb = entry
    return f"{full_name}\n{blurb}"


__all__ = ["ACRONYMS", "explain_kind_id"]
