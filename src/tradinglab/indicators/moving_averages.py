"""Moving-average indicators: SMA, EMA."""

from __future__ import annotations

from typing import ClassVar, Dict, List, Tuple

import numpy as np

from ..core.bars import Bars
from ..models import Candle
from .base import LineStyle, ParamDef


class SMA:
    """Simple moving average over closes.

    ``compute`` returns ``{"sma": ndarray}`` where the first ``length-1``
    entries are ``NaN``.
    """

    kind_id: ClassVar[str] = "sma"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[Tuple[ParamDef, ...]] = (
        ParamDef("length", "int", default=20, min=1, max=2000, step=1,
                 description="Length"),
    )
    default_style: ClassVar[Dict[str, LineStyle]] = {
        "sma": LineStyle(color="#1f77b4", width=1.4),
    }

    overlay = True

    def __init__(self, length: int = 20) -> None:
        if length < 1:
            raise ValueError("length must be >= 1")
        self.length = length
        self.name = f"SMA({length})"

    def compute_arr(self, bars: Bars) -> Dict[str, np.ndarray]:
        closes = bars.close
        out = np.full_like(closes, np.nan)
        n = self.length
        if closes.size >= n:
            csum = np.concatenate(([0.0], np.cumsum(closes)))
            out[n - 1:] = (csum[n:] - csum[:-n]) / n
        return {"sma": out}

    def compute(self, candles: List[Candle]) -> Dict[str, np.ndarray]:
        return self.compute_arr(Bars.from_candles(candles))

    # --- incremental protocol -------------------------------------------
    # Closed-bar append fast path. Forming-bar updates fall back to full
    # recompute via :class:`IndicatorMemo` (intentional — forming is rare
    # relative to closed-bar appends).

    def inc_init(self, bars: Bars) -> Dict[str, object]:
        """Build initial incremental state mirroring :meth:`compute_arr`."""
        return {"output": self.compute_arr(bars), "len": int(bars.close.size)}

    def inc_step(
        self,
        state: Dict[str, object],
        bars: Bars,
        *,
        prev_len: int,
    ) -> Dict[str, object]:
        """Extend state by one or more closed bars.

        Raises ``ValueError`` if ``len(bars) <= prev_len`` — the caller
        is responsible for routing same-length / shrink cases elsewhere
        (typically a full rebuild).
        """
        closes = bars.close
        n = int(closes.size)
        if n <= prev_len:
            raise ValueError(
                f"SMA.inc_step requires growth: prev_len={prev_len}, new_len={n}"
            )
        L = self.length
        old_out = state["output"]["sma"]  # type: ignore[index]
        new_out = np.empty(n, dtype=np.float64)
        new_out[:prev_len] = old_out
        # For each newly-committed bar, recompute the rolling mean over
        # the trailing window. O(L) per bar — fine for k=1 (the common
        # tick case). Larger k still beats a full O(n) recompute.
        for i in range(prev_len, n):
            if i < L - 1:
                new_out[i] = np.nan
            else:
                new_out[i] = closes[i - L + 1 : i + 1].mean()
        return {"output": {"sma": new_out}, "len": n}


class EMA:
    """Exponential moving average over closes, recursive form.

    Seeded with the SMA of the first ``length`` closes, published at
    index ``length - 1``. Indices ``0..length-2`` are NaN. Recurrence
    from index ``length`` onward: ``ema[i] = alpha*close[i] +
    (1-alpha)*ema[i-1]`` with ``alpha = 2/(length+1)``.

    This matches TradingView and TA-Lib. It differs from
    ``pandas.ewm(adjust=False)``, which seeds at the first close and
    publishes from index 0.
    """

    kind_id: ClassVar[str] = "ema"
    kind_version: ClassVar[int] = 2
    params_schema: ClassVar[Tuple[ParamDef, ...]] = (
        ParamDef("length", "int", default=20, min=1, max=2000, step=1,
                 description="Length"),
    )
    default_style: ClassVar[Dict[str, LineStyle]] = {
        "ema": LineStyle(color="#ff7f0e", width=1.4),
    }

    overlay = True

    def __init__(self, length: int = 20) -> None:
        if length < 1:
            raise ValueError("length must be >= 1")
        self.length = length
        self.alpha = 2.0 / (length + 1)
        self.name = f"EMA({length})"

    def compute_arr(self, bars: Bars) -> Dict[str, np.ndarray]:
        closes = bars.close
        n = closes.size
        out = np.full(n, np.nan, dtype=np.float64)
        L = self.length
        if n < L:
            return {"ema": out}
        a = self.alpha
        seed = float(closes[:L].mean())
        out[L - 1] = seed
        prev = seed
        for i in range(L, n):
            prev = a * float(closes[i]) + (1.0 - a) * prev
            out[i] = prev
        return {"ema": out}

    def compute(self, candles: List[Candle]) -> Dict[str, np.ndarray]:
        return self.compute_arr(Bars.from_candles(candles))

    # --- incremental protocol -------------------------------------------

    def inc_init(self, bars: Bars) -> Dict[str, object]:
        """Build initial state.

        ``committed_idx`` is the highest index whose EMA has been
        committed (``-1`` while still in warmup). ``committed_value``
        is ``ema[committed_idx]``. The recurrence relies on these to
        compute future closed-bar values without re-walking the whole
        series.
        """
        out = self.compute_arr(bars)["ema"]
        n = int(bars.close.size)
        L = self.length
        if n >= L:
            committed_idx = n - 1
            committed_value = float(out[n - 1])
        else:
            committed_idx = -1
            committed_value = float("nan")
        return {
            "output": {"ema": out},
            "len": n,
            "committed_idx": committed_idx,
            "committed_value": committed_value,
        }

    def inc_step(
        self,
        state: Dict[str, object],
        bars: Bars,
        *,
        prev_len: int,
    ) -> Dict[str, object]:
        """Extend state by one or more closed bars via the recurrence.

        Crosses the seed threshold gracefully if ``prev_len < L``
        (state has ``committed_idx == -1``); seeds at index ``L-1`` to
        match :meth:`compute_arr` exactly, then continues with the
        standard recurrence.
        """
        closes = bars.close
        n = int(closes.size)
        if n <= prev_len:
            raise ValueError(
                f"EMA.inc_step requires growth: prev_len={prev_len}, new_len={n}"
            )
        L = self.length
        a = self.alpha
        old_out = state["output"]["ema"]  # type: ignore[index]
        new_out = np.empty(n, dtype=np.float64)
        new_out[:prev_len] = old_out
        committed_idx = int(state["committed_idx"])  # type: ignore[arg-type]
        committed_value = float(state["committed_value"])  # type: ignore[arg-type]

        for i in range(prev_len, n):
            if i < L - 1:
                new_out[i] = np.nan
                continue
            if committed_idx < L - 1:
                # Seed crossing: mean of the first ``L`` closes — same
                # convention as :meth:`compute_arr`. Only fires once
                # per indicator instance.
                seed = float(closes[:L].mean())
                new_out[i] = seed
                committed_idx = i
                committed_value = seed
                continue
            v = a * float(closes[i]) + (1.0 - a) * committed_value
            new_out[i] = v
            committed_idx = i
            committed_value = v

        return {
            "output": {"ema": new_out},
            "len": n,
            "committed_idx": committed_idx,
            "committed_value": committed_value,
        }

