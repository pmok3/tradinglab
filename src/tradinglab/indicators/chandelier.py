"""Chandelier Stops indicator — always-on volatility-trailing stop overlay.

Chuck LeBeau, 1995. A chart-study indicator that draws two lines:

* ``long_stop``  = highest_high(lookback) − multiplier × ATR
* ``short_stop`` = lowest_low(lookback)   + multiplier × ATR

Both lines render as stair-step overlays on the price pane (because a
stop level must live in price units to be visually meaningful). The
stair-step `drawstyle="steps-post"` is intentional: it makes the
discrete ratchet events visually loud, which is exactly what a
learning trader needs to see.

This class is the **always-on** surface. It is independent of any
position — it answers "where would my stop be if I entered a long /
short on this bar?". The in-trade overlay (anchored at entry, ratcheted
forward) lives in :mod:`exits.spec` and shares the same math via
:mod:`core.chandelier_math`.

Parameters (per the locked design):

* ``lookback`` (int, default 22) — highest-high / lowest-low window.
* ``atr_period`` (int, default 22) — ATR smoothing period. LeBeau's
  original spec exposed these as **two separate knobs**, which the
  user has chosen to preserve in this app (vs. TradingView's collapsed
  single-length variant).
* ``multiplier`` (float, default 3.0, range 0.5–8.0) — ATR multiple.
  LeBeau's typical range was 2.5–4.0; the sandbox bounds are widened
  to 0.5–8.0 to support stress-testing extreme parameterisations.
* ``ma_type`` (choice {RMA, SMA, EMA, WMA}, default RMA) — ATR kernel.
  RMA matches LeBeau's 1995 spec (and the existing ATR indicator).

Warm-up: NaN until both the rolling window and the ATR kernel are
warm. No SMA-of-TR placeholder.

Ratcheting: always ON. The long line never descends; the short line
never rises. This is the defining trait of a chandelier and is not
exposed as a toggle.

This indicator is registered in :mod:`indicators.__init__` under the
display name ``"Chandelier Stops"`` and the stable ``kind_id =
"chandelier"``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

import numpy as np

from ..core.bars import Bars
from ..core.chandelier_math import (
    compute_atr as _compute_atr,
)
from ..core.chandelier_math import (
    compute_chandelier_long as _compute_long,
)
from ..core.chandelier_math import (
    compute_chandelier_short as _compute_short,
)
from .base import BaseIndicator, LineStyle, ParamDef
from .ma_kernels import MA_TYPES

# Locked-in defaults (per the user's design decisions).
_DEFAULT_LOOKBACK = 22
_DEFAULT_ATR_PERIOD = 22
_DEFAULT_MULTIPLIER = 3.0
_DEFAULT_MA_TYPE = "RMA"

#: Bounded multiplier range. Below 0.5 the stop becomes meaningless
#: noise; above 8.0 it is effectively a fixed stop and no longer a
#: chandelier.
_MIN_MULTIPLIER = 0.5
_MAX_MULTIPLIER = 8.0

#: Default line colors — green-shade for long, red-shade for short.
#: Theme-aware shading is the responsibility of the render layer
#: (chandelier piggybacks on the existing per-output style override
#: surface). These hexes are chosen to be distinct from common
#: candle palettes so the lines don't camouflage.
_DEFAULT_LONG_COLOR = "#2e7d32"   # darker green
_DEFAULT_SHORT_COLOR = "#c62828"  # darker red


class ChandelierStops(BaseIndicator):
    """Chandelier Stops — long + short volatility-trailing overlay.

    ``compute`` returns ``{"long_stop": ndarray, "short_stop": ndarray}``.
    Both arrays are the same length as ``bars``; NaN at warm-up indices.
    """

    kind_id: ClassVar[str] = "chandelier"
    kind_version: ClassVar[int] = 1
    overlay: ClassVar[bool] = True

    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("lookback", "int",
                 default=_DEFAULT_LOOKBACK, min=1, max=500, step=1,
                 description="Lookback (highest-high / lowest-low window)"),
        ParamDef("atr_period", "int",
                 default=_DEFAULT_ATR_PERIOD, min=2, max=500, step=1,
                 description="ATR period"),
        ParamDef("multiplier", "float",
                 default=_DEFAULT_MULTIPLIER,
                 min=_MIN_MULTIPLIER, max=_MAX_MULTIPLIER, step=0.1,
                 description="ATR multiplier"),
        ParamDef("ma_type", "choice",
                 default=_DEFAULT_MA_TYPE, choices=MA_TYPES,
                 description="ATR kernel"),
    )

    default_style: ClassVar[dict[str, LineStyle]] = {
        "long_stop":  LineStyle(color=_DEFAULT_LONG_COLOR,  width=1.4),
        "short_stop": LineStyle(color=_DEFAULT_SHORT_COLOR, width=1.4),
    }

    #: Both outputs render as stair-step lines so the discrete ratchet
    #: events are visually unmistakable. Consumed by
    #: :func:`indicators.render.render_for_slot` via the b72 stair_line
    #: dispatch path (which sets ``drawstyle="steps-post"``).
    output_kinds: ClassVar[Mapping[str, str]] = {
        "long_stop": "stair_line",
        "short_stop": "stair_line",
    }

    reference_levels: ClassVar[tuple[float, ...]] = ()

    def __init__(
        self,
        lookback: int = _DEFAULT_LOOKBACK,
        atr_period: int = _DEFAULT_ATR_PERIOD,
        multiplier: float = _DEFAULT_MULTIPLIER,
        ma_type: str = _DEFAULT_MA_TYPE,
    ) -> None:
        if int(lookback) < 1:
            raise ValueError(f"lookback must be >= 1; got {lookback}")
        if int(atr_period) < 2:
            raise ValueError(f"atr_period must be >= 2; got {atr_period}")
        if not (
            _MIN_MULTIPLIER <= float(multiplier) <= _MAX_MULTIPLIER
        ):
            raise ValueError(
                f"multiplier must be in [{_MIN_MULTIPLIER}, {_MAX_MULTIPLIER}]; "
                f"got {multiplier}"
            )
        ma_type_norm = str(ma_type).upper()
        if ma_type_norm not in MA_TYPES:
            raise ValueError(
                f"ma_type must be one of {MA_TYPES}; got {ma_type!r}"
            )
        self.lookback = int(lookback)
        self.atr_period = int(atr_period)
        self.multiplier = float(multiplier)
        self.ma_type = ma_type_norm
        self.name = self._render_name()

    @property
    def warmup_bars(self) -> int:
        """``max(lookback, 4×atr_period)`` for RMA — both legs must seed.

        The highest-high window needs ``lookback`` bars; the ATR leg
        needs Wilder's ``4×atr_period`` to converge. For non-RMA kernels
        the ATR leg settles in ``atr_period``, so just ``max(lookback,
        atr_period)`` would suffice — we use the RMA-conservative form
        because LeBeau's original chandelier spec uses RMA (the default).
        """
        atr_warmup = 4 * int(self.atr_period) if self.ma_type == "RMA" else int(self.atr_period)
        return max(int(self.lookback), atr_warmup)

    def _render_name(self) -> str:
        """Compact display label.

        * ``CHAND(22,22,3)`` — all defaults (RMA, lookback==atr_period).
        * ``CHAND(20,14,3)`` — lookback and atr_period decoupled.
        * ``CHAND(22,22,3,SMA)`` — non-default kernel; tag appended.
        """
        L, A, m = self.lookback, self.atr_period, f"{self.multiplier:g}"
        ma_tag = "" if self.ma_type == _DEFAULT_MA_TYPE else f",{self.ma_type}"
        return f"CHAND({L},{A},{m}{ma_tag})"

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        n = len(bars)
        if n == 0:
            empty = np.full(0, np.nan, dtype=np.float64)
            return {"long_stop": empty, "short_stop": empty.copy()}
        highs = bars.high.astype(np.float64, copy=False)
        lows = bars.low.astype(np.float64, copy=False)
        closes = bars.close.astype(np.float64, copy=False)
        atr = _compute_atr(
            highs, lows, closes,
            atr_period=self.atr_period,
            ma_type=self.ma_type,
        )
        long_stop, _ = _compute_long(
            highs, atr,
            lookback=self.lookback,
            multiplier=self.multiplier,
            anchor_idx=None,
        )
        short_stop, _ = _compute_short(
            lows, atr,
            lookback=self.lookback,
            multiplier=self.multiplier,
            anchor_idx=None,
        )
        return {"long_stop": long_stop, "short_stop": short_stop}

