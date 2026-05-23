"""Technical indicators.

Pure compute layer — no matplotlib / Tk / main-thread coupling. Safe to
invoke from worker threads and trivially unit-testable.

Public API::

    Indicator          — Protocol (kind_id, params_schema, default_style,
                                   name, overlay, compute)
    IndicatorFactory   — Callable[..., Indicator]
    INDICATORS         — registry {"SMA": SMA, "EMA": EMA, ...} keyed by
                         display name
    register_indicator — imperative registration helper
    factory_by_kind_id — stable-id lookup for persistence
    ParamDef           — typed parameter description (drives Add dialog)
    LineStyle          — per-output-key visual default
    SMA, EMA, RSI,     — built-in indicator classes
    BollingerBands

Higher-level facilities live in:

    indicators.config  — IndicatorConfig + IndicatorManager (presets,
                         observers, persistence round-trip)
    indicators.cache   — IndicatorCache (identity-keyed compute memo)
    indicators.loader  — discover_user_indicators (opt-in drop-in folder)

Usage::

    from tradinglab.indicators import INDICATORS
    sma20 = INDICATORS["SMA"](length=20)
    lines = sma20.compute(candles)   # {"sma": np.ndarray}

Adding a custom indicator: implement a class with ``kind_id``,
``params_schema``, ``default_style``, ``name``, ``overlay``, and
``compute(candles) -> Dict[str, np.ndarray]``; then call
``register_indicator("MyInd", MyInd)``.
"""

from .adx import ADX
from .atr import ATR
from .avwap import AnchoredVWAP
from .base import (
    INDICATORS,
    PARAM_KINDS,
    Indicator,
    IndicatorFactory,
    LineStyle,
    ParamDef,
    factory_by_kind_id,
    kind_id_for,
    register_indicator,
    register_legacy_indicator,
)
from .bollinger import BollingerBands
from .chandelier import ChandelierStops
from .keltner import KeltnerChannels
from .lrsi import LRSI
from .macd import MACD
from .moving_averages import EMA, SMA, MovingAverage
from .overlap_score import OverlapScoreInverted
from .prior_day import PriorDayHLC
from .rrvol import RRVOL
from .rsi import RSI
from .rvol import RVOL
from .smi import SMI
from .vwap import VWAP

register_indicator("Moving Average", MovingAverage)
# Legacy SMA / EMA: hidden from the Add menu (no INDICATORS entry) but
# kept in the kind_id lookup so direct in-memory uses (e.g. test
# fixtures, third-party scripts that bypass IndicatorConfig.from_dict)
# keep instantiating the originals. Persisted configs migrate through
# _KIND_ID_MIGRATIONS to the unified ``ma`` kind_id before lookup.
register_legacy_indicator("SMA", SMA)
register_legacy_indicator("EMA", EMA)
register_indicator("RSI", RSI)
register_indicator("Bollinger Bands", BollingerBands)
register_indicator("Keltner Channels", KeltnerChannels)
register_indicator("MACD", MACD)
register_indicator("VWAP", VWAP)
register_indicator("Anchored VWAP", AnchoredVWAP)
register_indicator("Stochastic Momentum Index", SMI)
register_indicator("Average Directional Index", ADX)
register_indicator("Average True Range", ATR)
register_indicator("Laguerre RSI", LRSI)
register_indicator("RVOL", RVOL)
register_indicator("RRVOL", RRVOL)
register_indicator("Chandelier Stops", ChandelierStops)
register_indicator("Prior Day H/L/C", PriorDayHLC)
register_indicator("Overlap Score Inverted", OverlapScoreInverted)

__all__ = [
    "INDICATORS",
    "Indicator",
    "IndicatorFactory",
    "LineStyle",
    "PARAM_KINDS",
    "ParamDef",
    "factory_by_kind_id",
    "kind_id_for",
    "register_indicator",
    "SMA",
    "EMA",
    "MovingAverage",
    "RSI",
    "BollingerBands",
    "KeltnerChannels",
    "MACD",
    "VWAP",
    "SMI",
    "ADX",
    "ATR",
    "LRSI",
    "RVOL",
    "RRVOL",
    "ChandelierStops",
    "PriorDayHLC",
    "OverlapScoreInverted",
]
