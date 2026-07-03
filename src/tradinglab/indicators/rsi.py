"""Relative Strength Index."""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from ..core.bars import Bars
from ._palette import QUATERNARY
from .base import BaseIndicator, LineStyle, ParamDef
from .wilder import wilder_smooth_avg


class RSI(BaseIndicator):
    """Wilder's RSI over closes.

    ``compute`` returns ``{"rsi": ndarray}`` in ``[0, 100]``. The first
    ``length`` entries are ``NaN`` (need at least ``length`` deltas to
    seed the average gain/loss).

    Two horizontal reference bands (default oversold 30 / overbought 70)
    are drawn as **dotted** axhlines in the RSI pane, user-configurable
    via the ``oversold`` / ``overbought`` params and toggleable via
    ``show_reference_lines``. These are render-only — they do not affect
    the RSI output value the scanner / entries / exits evaluate against.
    """

    kind_id: ClassVar[str] = "rsi"
    kind_version: ClassVar[int] = 1
    #: Only ``length`` affects the RSI output value. The band params
    #: (``oversold`` / ``overbought`` / ``show_reference_lines``) are
    #: consumed only by ``__init__`` to build :attr:`reference_levels`
    #: (drawn as axhlines by the render layer); they never enter the
    #: compute path, so the entries / exits / scanner trigger form hides
    #: them. Mirrors :class:`tradinglab.indicators.lrsi.LRSI`.
    TRIGGER_RELEVANT_PARAMS: ClassVar[tuple[str, ...]] = ("length",)
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("length", "int", default=14, min=2, max=2000, step=1,
                 description="Length"),
        ParamDef("oversold", "int", default=30, min=0, max=100, step=1,
                 description="Oversold (lower band)"),
        ParamDef("overbought", "int", default=70, min=0, max=100, step=1,
                 description="Overbought (upper band)"),
        ParamDef("show_reference_lines", "bool", default=True,
                 description="Reference bands"),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        "rsi": LineStyle(color=QUATERNARY, width=1.4),
    }
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = (
        ("rsi", "numeric"),
    )

    overlay = False

    # Instance attribute ``reference_levels`` is populated in __init__
    # from ``oversold`` / ``overbought`` / ``show_reference_lines``; the
    # render layer reads from the instance in preference to the class.
    # Class-level default is empty so static introspection of RSI without
    # instantiation correctly reports "no levels".
    reference_levels: ClassVar[tuple[float, ...]] = ()
    #: RSI reference bands render as *dotted* axhlines. The render layer
    #: reads this optional attribute (``render._resolve_reference_line_style``);
    #: every other oscillator falls back to the default dashed ``"--"``.
    reference_line_style: ClassVar[str] = ":"

    def __init__(
        self,
        length: int = 14,
        oversold: int = 30,
        overbought: int = 70,
        show_reference_lines: bool = True,
    ) -> None:
        if length < 2:
            raise ValueError("length must be >= 2")
        if not (0 <= int(oversold) <= 100):
            raise ValueError("oversold must be in [0, 100]")
        if not (0 <= int(overbought) <= 100):
            raise ValueError("overbought must be in [0, 100]")
        if int(oversold) >= int(overbought):
            raise ValueError("oversold must be strictly less than overbought")
        self.length = length
        self.oversold = int(oversold)
        self.overbought = int(overbought)
        self.show_reference_lines = bool(show_reference_lines)
        self.reference_levels: tuple[float, ...] = (
            (float(self.oversold), float(self.overbought))
            if self.show_reference_lines
            else ()
        )
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

    # --- incremental protocol (closed-bar appends) ----------------------
    # RSI is a Wilder recurrence on the average gain / loss. A closed-bar
    # append extends it in O(k) from the committed averages instead of a
    # full O(N) recompute. The kernel is causal so the cached prefix is
    # bit-identical to a full recompute; only the k appended bars differ
    # from the vectorized kernel by float64 round-off (pinned within a
    # tight tolerance by tests/unit/test_incremental_indicators_wilder.py).

    def inc_init(self, bars: Bars) -> dict[str, object]:
        out = self.compute_arr(bars)["rsi"]
        closes = bars.close
        n_bars = int(closes.size)
        L = self.length
        state: dict[str, object] = {"output": {"rsi": out}, "len": n_bars}
        if n_bars > L:
            deltas = np.diff(closes)
            gains = np.where(deltas > 0, deltas, 0.0)
            losses = np.where(deltas < 0, -deltas, 0.0)
            ag = wilder_smooth_avg(gains, L)
            al = wilder_smooth_avg(losses, L)
            state["avg_gain"] = float(ag[-1])
            state["avg_loss"] = float(al[-1])
            state["last_close"] = float(closes[-1])
            state["seeded"] = True
        else:
            state["seeded"] = False
        return state

    def inc_step(
        self, state: dict[str, object], bars: Bars, *, prev_len: int,
    ) -> dict[str, object]:
        closes = bars.close
        n_bars = int(closes.size)
        if n_bars <= prev_len:
            raise ValueError(
                f"RSI.inc_step requires growth: prev_len={prev_len}, new_len={n_bars}"
            )
        if not state.get("seeded"):
            # Pre-seed appends (still in the warmup window) re-seed the
            # Wilder average non-trivially — defer to a full recompute.
            raise ValueError("RSI.inc_step: state not seeded yet")
        L = self.length
        q = (L - 1.0) / L
        a = 1.0 / L
        avg_gain = float(state["avg_gain"])  # type: ignore[arg-type]
        avg_loss = float(state["avg_loss"])  # type: ignore[arg-type]
        prev_c = float(state["last_close"])  # type: ignore[arg-type]
        old_out = state["output"]["rsi"]  # type: ignore[index]
        new_out = np.empty(n_bars, dtype=np.float64)
        new_out[:prev_len] = old_out
        for j in range(prev_len, n_bars):
            c = float(closes[j])
            delta = c - prev_c
            gain = delta if delta > 0.0 else 0.0
            loss = -delta if delta < 0.0 else 0.0
            avg_gain = avg_gain * q + gain * a
            avg_loss = avg_loss * q + loss * a
            if avg_loss > 0.0:
                new_out[j] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
            else:
                new_out[j] = 100.0
            prev_c = c
        return {
            "output": {"rsi": new_out},
            "len": n_bars,
            "avg_gain": avg_gain,
            "avg_loss": avg_loss,
            "last_close": prev_c,
            "seeded": True,
        }


