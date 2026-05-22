"""Relative Strength Index."""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from ..core.bars import Bars
from ..models import Candle
from .base import LineStyle, ParamDef


class RSI:
    """Wilder's RSI over closes.

    ``compute`` returns ``{"rsi": ndarray}`` in ``[0, 100]``. The first
    ``length`` entries are ``NaN`` (need at least ``length`` deltas to
    seed the average gain/loss).
    """

    kind_id: ClassVar[str] = "rsi"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("length", "int", default=14, min=2, max=2000, step=1,
                 description="Length"),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        "rsi": LineStyle(color="#d62728", width=1.4),
    }

    overlay = False

    def __init__(self, length: int = 14) -> None:
        if length < 2:
            raise ValueError("length must be >= 2")
        self.length = length
        self.name = f"RSI({length})"

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        closes = bars.close
        n = self.length
        out = np.full_like(closes, np.nan)
        if closes.size <= n:
            return {"rsi": out}

        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = gains[:n].mean()
        avg_loss = losses[:n].mean()

        def _rsi_from(ag: float, al: float) -> float:
            if al == 0.0:
                return 100.0
            rs = ag / al
            return 100.0 - (100.0 / (1.0 + rs))

        out[n] = _rsi_from(avg_gain, avg_loss)
        for i in range(n + 1, closes.size):
            g = gains[i - 1]
            l = losses[i - 1]
            avg_gain = (avg_gain * (n - 1) + g) / n
            avg_loss = (avg_loss * (n - 1) + l) / n
            out[i] = _rsi_from(avg_gain, avg_loss)
        return {"rsi": out}

    def compute(self, candles: list[Candle]) -> dict[str, np.ndarray]:
        return self.compute_arr(Bars.from_candles(candles))

