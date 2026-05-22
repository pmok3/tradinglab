"""Regression test for the ``indicator-color-uniqueness`` audit.

The reviewer flagged that several built-in indicators shared their
default color, making a multi-indicator chart legend ambiguous:

* RSI + VWAP both ``#9467bd`` (purple)
* LRSI + RVOL both ``#bcbd22`` (yellow-green)
* SMA + AVWAP + ADX all ``#1f77b4`` (blue)
* MACD ``#2196f3`` ≈ SMA ``#1f77b4`` (visually indistinguishable)

After the fix every built-in indicator's *primary output* (the
output key that drives the legend swatch / overlay-legend dot)
has a unique default color, drawn from matplotlib's `tab10` /
`tab20` palette.

These tests pin the per-indicator primary defaults AND assert
mutual distinctness so a future palette tweak can't silently
reintroduce a collision.
"""
from __future__ import annotations

from tradinglab.indicators.adx import ADX
from tradinglab.indicators.atr import ATR
from tradinglab.indicators.avwap import AnchoredVWAP
from tradinglab.indicators.lrsi import LRSI
from tradinglab.indicators.macd import MACD
from tradinglab.indicators.moving_averages import EMA, SMA
from tradinglab.indicators.rrvol import RRVOL
from tradinglab.indicators.rsi import RSI
from tradinglab.indicators.rvol import RVOL
from tradinglab.indicators.smi import SMI
from tradinglab.indicators.vwap import VWAP

# kind_id → (primary output key, expected default color)
_EXPECTED_DEFAULTS = {
    "sma":    ("sma",    "#1f77b4"),  # T10 blue
    "ema":    ("ema",    "#ff7f0e"),  # T10 orange
    "vwap":   ("vwap",   "#9467bd"),  # T10 purple
    "avwap":  ("avwap",  "#8c564b"),  # T10 brown (was #1f77b4)
    "rsi":    ("rsi",    "#d62728"),  # T10 red (was #9467bd)
    "lrsi":   ("lrsi",   "#bcbd22"),  # T10 yellow-green
    "macd":   ("macd",   "#2ca02c"),  # T10 green (was #2196f3)
    "smi":    ("smi",    "#17becf"),  # T10 cyan
    "adx":    ("adx",    "#7f7f7f"),  # T10 grey (was #1f77b4)
    "atr":    ("atr",    "#ffbb78"),  # T20 light orange (was #9467bd)
    "rvol":   ("rvol",   "#aec7e8"),  # T20 light blue (was #bcbd22)
    "rrvol":  ("rvol",   "#c5b0d5"),  # T20 light purple (was #7f7f7f)
}

_CLASS_BY_KIND_ID = {
    "sma": SMA, "ema": EMA, "vwap": VWAP, "avwap": AnchoredVWAP,
    "rsi": RSI, "lrsi": LRSI, "macd": MACD, "smi": SMI, "adx": ADX,
    "atr": ATR, "rvol": RVOL, "rrvol": RRVOL,
}


def test_each_indicator_primary_default_color_pinned():
    """Every indicator must keep its post-audit primary default."""
    for kind_id, (output_key, expected) in _EXPECTED_DEFAULTS.items():
        cls = _CLASS_BY_KIND_ID[kind_id]
        ls = cls.default_style[output_key]
        assert ls.color.lower() == expected.lower(), (
            f"{kind_id} primary output {output_key!r} default color "
            f"is {ls.color!r}; audit indicator-color-uniqueness expects "
            f"{expected!r}.")


def test_indicator_primary_defaults_are_mutually_unique():
    """Across all 12 indicators the primary default colors must
    form a set of 12 distinct values — no two share a hue."""
    colors = []
    for kind_id, (output_key, _expected) in _EXPECTED_DEFAULTS.items():
        cls = _CLASS_BY_KIND_ID[kind_id]
        colors.append((kind_id, cls.default_style[output_key].color.lower()))
    by_color: dict[str, list[str]] = {}
    for kid, color in colors:
        by_color.setdefault(color, []).append(kid)
    collisions = {c: kids for c, kids in by_color.items() if len(kids) > 1}
    assert not collisions, (
        f"Indicator primary default colors are colliding: {collisions}. "
        f"Each built-in indicator must have a unique default so the "
        f"legend reads unambiguously on a multi-indicator chart "
        f"(audit indicator-color-uniqueness).")


def test_specific_audit_collisions_resolved():
    """Spot-check the four collisions the reviewer explicitly named."""
    assert RSI.default_style["rsi"].color.lower() != \
           VWAP.default_style["vwap"].color.lower(), \
        "RSI + VWAP must no longer share #9467bd"
    assert LRSI.default_style["lrsi"].color.lower() != \
           RVOL.default_style["rvol"].color.lower(), \
        "LRSI + RVOL must no longer share #bcbd22"
    assert SMA.default_style["sma"].color.lower() != \
           AnchoredVWAP.default_style["avwap"].color.lower(), \
        "SMA + AVWAP must no longer share #1f77b4"
    assert SMA.default_style["sma"].color.lower() != \
           ADX.default_style["adx"].color.lower(), \
        "SMA + ADX must no longer share #1f77b4"
    assert MACD.default_style["macd"].color.lower() != \
           SMA.default_style["sma"].color.lower(), \
        "MACD must no longer be a near-blue (#2196f3) that visually " \
        "matches the SMA blue"


def test_atr_default_does_not_collide_with_vwap():
    """ATR default mode is RMA which previously inherited #9467bd
    — the same as VWAP. After the fix ATR's RMA color is light
    orange #ffbb78, distinct from every other indicator default."""
    assert ATR.default_style["atr"].color.lower() != \
           VWAP.default_style["vwap"].color.lower(), \
        "ATR + VWAP must no longer share #9467bd"


def test_rrvol_default_does_not_collide_with_adx():
    """RRVOL was #7f7f7f (grey), and the audit moved ADX to grey too.
    The fix puts RRVOL on light purple #c5b0d5."""
    assert RRVOL.default_style["rvol"].color.lower() != \
           ADX.default_style["adx"].color.lower(), \
        "RRVOL + ADX must not share #7f7f7f"
