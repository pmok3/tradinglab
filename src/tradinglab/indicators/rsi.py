"""Relative Strength Index."""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from ..core.bars import Bars
from .base import BaseIndicator, LineStyle, ParamDef
from .wilder import wilder_smooth_avg


class RSI(BaseIndicator):
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
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = (
        ("rsi", "numeric"),
    )

    overlay = False

    def __init__(self, length: int = 14) -> None:
        if length < 2:
            raise ValueError("length must be >= 2")
        self.length = length
        self.name = f"RSI({length})"

    @property
    def warmup_bars(self) -> int:
        """4×length — Wilder smoothing is IIR; values converge asymptotically.

        The first-finite RSI index is just ``length`` (one delta + the seed
        average), but the recurrence ``S_i = S_{i-1}·(n-1)/n + v_i/n`` keeps
        the average drifting toward truth for many bars after that.
        ``4×length`` is the textbook "fully hydrated" cutoff used across
        every charting platform's docs.
        """
        return 4 * int(self.length)

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        closes = bars.close
        n = self.length
        out = np.full_like(closes, np.nan)
        if closes.size <= n:
            return {"rsi": out}

        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        # Shared Wilder kernel: same recurrence the inline loop was
        # running, but evaluated via cumsum substitution. The kernel
        # seeds at index ``n-1`` with ``gains[:n].mean()`` (mirrors
        # the original ``avg_gain`` seed at the loop entry) and steps
        # forward with ``S_i = S_{i-1} * (n-1)/n + v_i / n``.
        avg_gain = wilder_smooth_avg(gains, n)
        avg_loss = wilder_smooth_avg(losses, n)

        # ``out[i]`` in closes coords uses the avg_gain / avg_loss
        # values aligned at gains index ``i - 1`` (because gains is
        # one shorter than closes). Slicing from ``n - 1`` produces
        # exactly ``closes.size - n`` valid values that line up with
        # ``out[n:]``.
        ag = avg_gain[n - 1:]
        al = avg_loss[n - 1:]

        with np.errstate(divide="ignore", invalid="ignore"):
            rs = np.where(al > 0.0, ag / al, np.inf)
            rsi = np.where(
                np.isinf(rs), 100.0, 100.0 - 100.0 / (1.0 + rs),
            )
        out[n:] = rsi
        return {"rsi": out}


