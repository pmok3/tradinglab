"""Keltner Channels — volatility envelopes built around a moving-average centerline.

Two well-known variants, both supported through the ``method`` choice
parameter:

* ``method="atr"`` *(modern, default)* — Linda Bradford Raschke /
  TradingView convention. ``middle = ma(close, length)``;
  ``upper = middle + multiplier * atr``;
  ``lower = middle - multiplier * atr`` where ``atr`` is smoothed by
  the ``atr_ma_type`` kernel over ``atr_length`` bars. ATR is the
  rolling smoothing of Wilder's True Range
  (:func:`indicators.wilder.true_range`).

* ``method="original"`` *(Chester Keltner, 1960)* —
  ``middle = ma(typical_price, length)``;
  ``upper = middle + multiplier * ma(high - low, length)``;
  ``lower = middle - multiplier * ma(high - low, length)`` where
  ``typical_price = (H + L + C) / 3``. The same ``ma_type`` and
  ``length`` drive both the centerline and the range envelope —
  ``atr_length`` and ``atr_ma_type`` are inert in this mode (stored
  verbatim on the instance for round-trip but not consulted).

Both methods emit the same three-key output schema
``{"middle", "upper", "lower"}`` and are ``overlay=True`` (drawn on
the price axis).

Centerline kernel selection mirrors :class:`indicators.bollinger.BollingerBands`
— any of ``SMA / EMA / WMA / RMA``. The default is ``EMA`` because
TradingView, ThinkOrSwim and TA-Lib all default Keltner to EMA, and
the modern formulation is by far the more commonly cited.

Warmup
------

* Modern (``"atr"``): NaN until index ``max(length, atr_length+1) - 1``.
  ATR requires ``atr_length+1`` valid bars because ``TR[0]`` is NaN
  (no prior close).
* Original (``"original"``): NaN until index ``length - 1``.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from ..core.bars import Bars
from ..models import Candle
from .base import LineStyle, ParamDef
from .ma_kernels import MA_TYPES, apply_ma
from .wilder import true_range as _true_range

KELTNER_METHODS: tuple[str, ...] = ("atr", "original")


# Constructor defaults — also used by the name-rendering logic to
# decide which suffix tags are "noise" vs "informative". A tag is
# only appended when the parameter differs from its default.
_DEFAULT_LENGTH = 20
_DEFAULT_MULTIPLIER = 2.0
_DEFAULT_ATR_LENGTH = 10
_DEFAULT_ATR_MA_TYPE = "RMA"
_DEFAULT_METHOD = "atr"

# Centerline kernel defaults are method-specific (TradingView uses EMA
# for the modern ATR-based method; the original Chester Keltner
# formulation predates EMA on charts and used SMA). When the caller
# leaves ``ma_type`` at the sentinel, the constructor picks the
# method-appropriate default at hydration time.
_MA_TYPE_DEFAULT_SENTINEL = "__keltner_default__"
_DEFAULT_MA_TYPE_BY_METHOD: dict[str, str] = {
    "atr": "EMA",
    "original": "SMA",
}


_DEFAULT_COLOR_BY_MA: dict[str, str] = {
    "SMA": "#1f77b4",  # blue
    "EMA": "#ff7f0e",  # orange (KC's signature TradingView hue)
    "WMA": "#9467bd",  # purple
    "RMA": "#17becf",  # teal
}


class KeltnerChannels:
    """Keltner Channels — three overlay lines around a MA centerline.

    Outputs ``{"middle": ndarray, "upper": ndarray, "lower": ndarray}``.
    """

    kind_id: ClassVar[str] = "keltner"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("length", "int", default=_DEFAULT_LENGTH, min=2, max=2000,
                 step=1, description="Length"),
        ParamDef("multiplier", "float", default=_DEFAULT_MULTIPLIER, min=0.1,
                 max=20.0, step=0.1, description="Multiplier"),
        ParamDef("atr_length", "int", default=_DEFAULT_ATR_LENGTH, min=2,
                 max=2000, step=1, description="ATR length"),
        ParamDef("ma_type", "choice", default=_DEFAULT_MA_TYPE_BY_METHOD["atr"],
                 choices=MA_TYPES, description="Moving Average"),
        ParamDef("atr_ma_type", "choice", default=_DEFAULT_ATR_MA_TYPE,
                 choices=MA_TYPES, description="ATR smoothing"),
        ParamDef("method", "choice", default=_DEFAULT_METHOD,
                 choices=KELTNER_METHODS,
                 description="atr | original"),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        "middle": LineStyle(color=_DEFAULT_COLOR_BY_MA["EMA"], width=1.2),
        "upper":  LineStyle(color=_DEFAULT_COLOR_BY_MA["EMA"], width=1.0),
        "lower":  LineStyle(color=_DEFAULT_COLOR_BY_MA["EMA"], width=1.0),
    }
    reference_levels: ClassVar[tuple[float, ...]] = ()
    overlay: ClassVar[bool] = True

    def __init__(
        self,
        length: int = _DEFAULT_LENGTH,
        multiplier: float = _DEFAULT_MULTIPLIER,
        atr_length: int = _DEFAULT_ATR_LENGTH,
        ma_type: str = _MA_TYPE_DEFAULT_SENTINEL,
        atr_ma_type: str = _DEFAULT_ATR_MA_TYPE,
        method: str = _DEFAULT_METHOD,
    ) -> None:
        if int(length) < 2:
            raise ValueError("length must be >= 2")
        if float(multiplier) <= 0:
            raise ValueError("multiplier must be > 0")
        if int(atr_length) < 2:
            raise ValueError("atr_length must be >= 2")
        method_norm = str(method).lower()
        if method_norm not in KELTNER_METHODS:
            raise ValueError(
                f"method must be one of {KELTNER_METHODS}; got {method!r}"
            )
        # Sentinel resolution: pick the method-appropriate default kernel
        # so ``KC()`` and ``KC(method='original')`` both produce the
        # conventionally-expected centerline (EMA / SMA respectively).
        # The dialog passes the params_schema default verbatim ("EMA"),
        # which is the modern-method default — that's what the user
        # sees in the Add Indicator dialog before they touch anything.
        if ma_type == _MA_TYPE_DEFAULT_SENTINEL:
            ma_type_norm = _DEFAULT_MA_TYPE_BY_METHOD[method_norm]
        else:
            ma_type_norm = str(ma_type).upper()
            if ma_type_norm not in MA_TYPES:
                raise ValueError(
                    f"ma_type must be one of {MA_TYPES}; got {ma_type!r}"
                )
        atr_ma_type_norm = str(atr_ma_type).upper()
        if atr_ma_type_norm not in MA_TYPES:
            raise ValueError(
                f"atr_ma_type must be one of {MA_TYPES}; got {atr_ma_type!r}"
            )
        self.length = int(length)
        self.multiplier = float(multiplier)
        self.atr_length = int(atr_length)
        self.ma_type = ma_type_norm
        self.atr_ma_type = atr_ma_type_norm
        self.method = method_norm
        self.name = self._render_name()

    def _render_name(self) -> str:
        """Compact display label — tags appear only when a param differs from default.

        Modern method (``method="atr"``):
          * ``KC(20,2)`` — all defaults.
          * ``KC(20,2,SMA)`` — centerline differs from default EMA.
          * ``KC(20,2,EMA/SMA)`` — ATR kernel differs from default RMA
            (centerline tag is always present when the ATR-kernel tag is).
          * Appends ``,σ={atr_length}`` when ``atr_length`` differs from
            the default 10 (e.g. ``KC(20,2,σ=14)``).

        Original method (``method="original"``):
          * ``KC-Orig(20,2)`` — SMA default centerline.
          * ``KC-Orig(20,2,EMA)`` — non-default centerline. ``atr_length``
            and ``atr_ma_type`` are inert in this mode and never appear.
        """
        L = self.length
        m = f"{self.multiplier:g}"
        method_ma_default = _DEFAULT_MA_TYPE_BY_METHOD[self.method]
        if self.method == "original":
            ma_tag = "" if self.ma_type == method_ma_default else f",{self.ma_type}"
            return f"KC-Orig({L},{m}{ma_tag})"
        # method == "atr"
        ma_is_default = self.ma_type == method_ma_default
        atr_ma_is_default = self.atr_ma_type == _DEFAULT_ATR_MA_TYPE
        if ma_is_default and atr_ma_is_default:
            kernel_tag = ""
        elif atr_ma_is_default:
            kernel_tag = f",{self.ma_type}"
        else:
            kernel_tag = f",{self.ma_type}/{self.atr_ma_type}"
        atr_tag = "" if self.atr_length == _DEFAULT_ATR_LENGTH else f",σ={self.atr_length}"
        return f"KC({L},{m}{kernel_tag}{atr_tag})"

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        closes = bars.close
        n = closes.size
        def empty():
            return np.full(n, np.nan, dtype=np.float64)
        middle, upper, lower = empty(), empty(), empty()
        if n == 0:
            return {"middle": middle, "upper": upper, "lower": lower}

        if self.method == "original":
            highs = bars.high
            lows = bars.low
            typical = (highs + lows + closes) / 3.0
            mid = apply_ma(self.ma_type, typical, self.length)
            rng = highs - lows
            band = apply_ma(self.ma_type, rng, self.length)
            middle[:] = mid
            upper[:] = mid + self.multiplier * band
            lower[:] = mid - self.multiplier * band
            return {"middle": middle, "upper": upper, "lower": lower}

        # method == "atr"
        mid = apply_ma(self.ma_type, closes, self.length)
        highs = bars.high
        lows = bars.low
        tr = _true_range(highs, lows, closes)
        atr = apply_ma(self.atr_ma_type, tr, self.atr_length)
        middle[:] = mid
        upper[:] = mid + self.multiplier * atr
        lower[:] = mid - self.multiplier * atr
        return {"middle": middle, "upper": upper, "lower": lower}

    def compute(self, candles: list[Candle]) -> dict[str, np.ndarray]:
        return self.compute_arr(Bars.from_candles(candles))
