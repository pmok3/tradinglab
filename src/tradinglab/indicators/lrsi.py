"""Laguerre RSI (LRSI) — John F. Ehlers (2002).

A 4-pole Laguerre filter applied to price, then turned into an RSI-
shaped oscillator. The filter trades phase lag for smoothness; LRSI
reaches overbought / oversold faster than a classical RSI of the
same effective length, with less noise in flat-market regimes.

Algorithm (Ehlers, "Time Warp - Without Space Travel"):

    p = close[i]                         # price input
    L0 = (1 - gamma) * p   + gamma * L0_prev
    L1 =     -gamma * L0   + L0_prev + gamma * L1_prev
    L2 =     -gamma * L1   + L1_prev + gamma * L2_prev
    L3 =     -gamma * L2   + L2_prev + gamma * L3_prev

    For each consecutive pair (L0,L1), (L1,L2), (L2,L3):
        if L_prev >= L_next: CU += L_prev - L_next  # cumulative up
        else:                CD += L_next - L_prev  # cumulative down

    LRSI_norm = CU / (CU + CD)           # in [0, 1]
    LRSI      = 100 * LRSI_norm           # we expose the [0, 100]
                                           # form to match RSI's scale.

Defaults: ``gamma = 0.5`` (Ehlers' classic). Lower gamma ⇒ less
smoothing / faster response; higher gamma ⇒ more smoothing / more lag.

User-tunable reference axhlines (drawn by the render layer via the
b46 mechanism, which now reads ``reference_levels`` from the
*instance* so per-config tuning works):

* ``oversold``  (default 15)
* ``overbought`` (default 85)
* ``show_reference_lines`` toggle (default ``True``); when False,
  the instance reports an empty ``reference_levels`` tuple and the
  render layer draws no axhlines for this config's pane.

Warmup: the recurrence is finite from index 0 onward (each L_k is
seeded with the first price), but the first few bars are dominated
by the seed and may not be meaningful. We follow common convention
and emit NaN for the first 3 bars (one per Laguerre stage); from
index 3 onward the LRSI is published.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from ..core.bars import Bars
from ._iir import iir_tail
from ._palette import TAB10_OLIVE
from .base import BaseIndicator, LineStyle, ParamDef


class LRSI(BaseIndicator):
    """Laguerre RSI (Ehlers).

    ``compute`` returns ``{"lrsi": ndarray}`` in the range ``[0, 100]``.
    """

    kind_id: ClassVar[str] = "lrsi"
    kind_version: ClassVar[int] = 1
    #: Whitelist of params that actually affect compute output.
    #: ``oversold`` / ``overbought`` / ``show_reference_lines`` are
    #: consumed only by ``__init__`` to build :attr:`reference_levels`
    #: (drawn as axhlines by the render layer); they never enter the
    #: indicator's compute path, so the entries/exits/scanner trigger
    #: form hides them. See :data:`tradinglab.scanner.fields._build_indicator_specs`.
    TRIGGER_RELEVANT_PARAMS: ClassVar[tuple[str, ...]] = ("gamma",)
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("gamma", "float", default=0.5, min=0.0, max=0.999, step=0.01,
                 description="γ (damping)"),
        ParamDef("oversold", "int", default=15, min=0, max=100, step=1,
                 description="Oversold"),
        ParamDef("overbought", "int", default=85, min=0, max=100, step=1,
                 description="Overbought"),
        ParamDef("show_reference_lines", "bool", default=True,
                 description="Reference lines"),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        # Olive: distinct from RSI (default blue) so a chart with both
        # indicators is readable; matches LRSI's convention of being
        # the "smarter cousin" of RSI.
        "lrsi": LineStyle(color=TAB10_OLIVE, width=1.4),
    }
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = (
        ("lrsi", "numeric"),
    )

    overlay = False  # pane indicator

    # Instance attribute ``reference_levels`` is populated in __init__
    # from ``oversold`` / ``overbought`` / ``show_reference_lines``;
    # the render layer reads from the instance in preference to the
    # class. Class-level default is empty so static introspection
    # of LRSI without instantiation correctly reports "no levels".
    reference_levels: ClassVar[tuple[float, ...]] = ()

    def __init__(
        self,
        gamma: float = 0.5,
        oversold: int = 15,
        overbought: int = 85,
        show_reference_lines: bool = True,
    ) -> None:
        if not (0.0 <= float(gamma) < 1.0):
            raise ValueError("gamma must be in [0.0, 1.0)")
        if not (0 <= int(oversold) <= 100):
            raise ValueError("oversold must be in [0, 100]")
        if not (0 <= int(overbought) <= 100):
            raise ValueError("overbought must be in [0, 100]")
        if int(oversold) >= int(overbought):
            raise ValueError("oversold must be strictly less than overbought")
        self.gamma = float(gamma)
        self.oversold = int(oversold)
        self.overbought = int(overbought)
        self.show_reference_lines = bool(show_reference_lines)
        self.reference_levels: tuple[float, ...] = (
            (float(self.oversold), float(self.overbought))
            if self.show_reference_lines
            else ()
        )
        self.name = f"LRSI({self.gamma:g})"

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        n = len(bars)
        out = np.full(n, np.nan, dtype=np.float64)
        if n == 0:
            return {"lrsi": out}

        prices = bars.close
        gamma = float(self.gamma)

        # The scalar reference seeds all four Laguerre stages with the raw
        # first price (prices[0]) and advances state only on finite bars
        # (non-finite bars are skipped, preserving prior state). A non-finite
        # prices[0] therefore poisons the cascade with NaN for the whole
        # series; replicate that exactly.
        seed = float(prices[0])
        if not np.isfinite(seed):
            return {"lrsi": out}

        idx = np.flatnonzero(np.isfinite(prices))
        if idx.size == 0:
            return {"lrsi": out}

        pf = prices[idx].astype(np.float64, copy=False)

        # Stage 0: L0[k] = (1-g)*p[k] + g*L0[k-1], with L0[-1] = seed.
        # Because pf[0] == seed, L0[0] == seed.
        L0 = np.empty(pf.size, dtype=np.float64)
        L0[0] = seed
        if pf.size > 1:
            L0[1:] = iir_tail((1.0 - gamma) * pf[1:], gamma, seed)

        def _next_stage(prev: np.ndarray) -> np.ndarray:
            # Lk[m] = g*Lk[m-1] + (-g*prev[m] + prev[m-1]); seeds at `seed`.
            st = np.empty(prev.size, dtype=np.float64)
            st[0] = seed
            if prev.size > 1:
                u = -gamma * prev[1:] + prev[:-1]
                st[1:] = iir_tail(u, gamma, seed)
            return st

        L1 = _next_stage(L0)
        L2 = _next_stage(L1)
        L3 = _next_stage(L2)

        d01 = L0 - L1
        d12 = L1 - L2
        d23 = L2 - L3
        cu = np.maximum(d01, 0.0) + np.maximum(d12, 0.0) + np.maximum(d23, 0.0)
        cd = np.maximum(-d01, 0.0) + np.maximum(-d12, 0.0) + np.maximum(-d23, 0.0)
        denom = cu + cd
        with np.errstate(divide="ignore", invalid="ignore"):
            vals = np.where(denom <= 0.0, 50.0, 100.0 * cu / denom)

        out[idx] = vals
        # Mask the first 3 bars (filter not yet warmed up — output is
        # dominated by seed bias). The scalar loop masks original index < 3.
        out[:3] = np.nan
        return {"lrsi": out}

