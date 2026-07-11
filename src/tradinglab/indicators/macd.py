"""MACD — Moving Average Convergence Divergence (Gerald Appel, 1979).

The classic momentum oscillator. Compute three series:

* ``macd``      = ``MA(source, fast_length) - MA(source, slow_length)``
* ``signal``    = ``MA(macd, signal_length)``
* ``histogram`` = ``macd - signal``

Defaults match every charting platform's defaults (12/26/9 EMA). The
moving-average kernel is selectable (SMA / EMA / WMA / RMA) for users
who want a smoother or more responsive variant; the kernel applies
uniformly to all three MAs.

The price source is selectable (``close`` / ``hl2`` / ``hlc3`` /
``ohlc4``). ``close`` is the default and matches Appel's original
formulation.

The histogram is rendered as **vertical bars with a 4-color momentum
palette** (TradingView convention):

* bright green — value > 0 AND rising vs prev bar
* pale green   — value > 0 AND falling vs prev bar
* pale red     — value ≤ 0 AND rising vs prev bar
* bright red   — value ≤ 0 AND falling vs prev bar

The first defined bar's "rising" classification is undefined; it
inherits its sign-based color (bright on the side it falls on).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

import numpy as np

from ..constants import macd_histogram_palette
from ..core.bars import Bars
from ._palette import SECONDARY_LINE, TERTIARY_LINE
from .base import BaseIndicator, LineStyle, ParamDef
from .ma_kernels import MA_TYPES, apply_ma

#: Source selector — which price series feeds the fast/slow MAs.
_SOURCES: tuple[str, ...] = ("close", "hl2", "hlc3", "ohlc4")

#: Default values, also published via ``params_schema``.
_DEFAULT_FAST_LENGTH = 12
_DEFAULT_SLOW_LENGTH = 26
_DEFAULT_SIGNAL_LENGTH = 9
_DEFAULT_MA_TYPE = "EMA"
_DEFAULT_SOURCE = "close"

#: Four-class color palette for the histogram. Order:
#: ``(up_above, down_above, up_below, down_below)`` — i.e.
#: (rising-above-zero, falling-above-zero, rising-below-zero,
#: falling-below-zero). Sourced from ``constants.macd_histogram_palette``
#: (a single source of truth that is Okabe-Ito-aware) so the histogram
#: follows the color-blind palette. The histogram renderer re-resolves
#: this LIVE on every paint via the same function, so a runtime palette
#: toggle reaches the bars; this module-level snapshot drives only the
#: default-style legend swatch + ``histogram_palette`` introspection.
#: Audit ``color-blind-palette-audit``.
_HISTOGRAM_PALETTE: tuple[str, str, str, str] = macd_histogram_palette()


def _select_source(bars: Bars, source: str) -> np.ndarray:
    """Return the price series for ``source`` as a float64 ndarray."""
    if source == "close":
        return bars.close.astype(np.float64, copy=False)
    if source == "hl2":
        return ((bars.high + bars.low) / 2.0).astype(np.float64, copy=False)
    if source == "hlc3":
        return ((bars.high + bars.low + bars.close) / 3.0).astype(
            np.float64, copy=False,
        )
    if source == "ohlc4":
        return ((bars.open + bars.high + bars.low + bars.close) / 4.0).astype(
            np.float64, copy=False,
        )
    raise ValueError(f"unknown source {source!r}; expected one of {_SOURCES}")


def classify_histogram(hist: np.ndarray) -> np.ndarray:
    """Classify each histogram bar into one of four color classes.

    Returns an int array of the same shape as ``hist`` with values:

    * ``0`` — rising above zero
    * ``1`` — falling above zero
    * ``2`` — rising below zero
    * ``3`` — falling below zero
    * ``-1`` — undefined (input is NaN at this index)

    The first defined bar has no predecessor; its slope is treated as
    "rising" (so it gets the bright color on the side it falls on).
    """
    n = hist.shape[0]
    out = np.full(n, -1, dtype=np.int8)
    if n == 0:
        return out
    finite = np.isfinite(hist)
    if not finite.any():
        return out
    # Vectorised: "rising" iff this bar >= the immediately-preceding bar,
    # but only when that predecessor is itself finite (a NaN gap resets to
    # "rising", matching the per-bar loop which set prev = NaN on a gap).
    prev = np.empty(n, dtype=np.float64)
    prev[0] = np.nan
    prev[1:] = hist[:-1]
    prev_finite = np.isfinite(prev)
    with np.errstate(invalid="ignore"):
        rising = np.where(prev_finite, hist >= prev, True)
        above = hist > 0.0
    out = np.where(above, np.where(rising, 0, 1), np.where(rising, 2, 3)).astype(np.int8)
    out[~finite] = -1
    return out


class MACD(BaseIndicator):
    """Moving Average Convergence Divergence (Appel).

    ``compute`` returns ``{"macd": ndarray, "signal": ndarray,
    "histogram": ndarray}``. Output arrays have the same length as
    ``bars``; the first ``slow_length - 1`` entries of ``macd`` are
    NaN (the slow MA isn't defined yet), and the first
    ``slow_length + signal_length - 2`` entries of ``signal`` /
    ``histogram`` are NaN. SMA / WMA / RMA kernels follow the same
    warmup convention; EMA produces output from the first bar but
    early values are not yet stable.
    """

    kind_id: ClassVar[str] = "macd"
    kind_version: ClassVar[int] = 1
    overlay: ClassVar[bool] = False
    pane_group: ClassVar[str] = "macd"
    reference_levels: ClassVar[tuple[float, ...]] = (0.0,)

    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("fast_length", "int",
                 default=_DEFAULT_FAST_LENGTH, min=2, max=2000, step=1,
                 description="Fast MA length"),
        ParamDef("slow_length", "int",
                 default=_DEFAULT_SLOW_LENGTH, min=2, max=2000, step=1,
                 description="Slow MA length"),
        ParamDef("signal_length", "int",
                 default=_DEFAULT_SIGNAL_LENGTH, min=2, max=2000, step=1,
                 description="Signal MA length"),
        ParamDef("ma_type", "choice",
                 default=_DEFAULT_MA_TYPE, choices=MA_TYPES,
                 description="Moving-average kernel"),
        ParamDef("source", "choice",
                 default=_DEFAULT_SOURCE, choices=_SOURCES,
                 description="Price source"),
    )

    default_style: ClassVar[dict[str, LineStyle]] = {
        "macd":      LineStyle(color=TERTIARY_LINE,  width=1.4),  # green
        "signal":    LineStyle(color=SECONDARY_LINE, width=1.2),  # orange
        "histogram": LineStyle(color=_HISTOGRAM_PALETTE[0], width=1.0),
    }

    #: Output kinds keyed by output name. ``"histogram"`` triggers the
    #: vertical-bar render path with 4-color momentum classification.
    #: Outputs not listed default to ``"line"``.
    output_kinds: ClassVar[Mapping[str, str]] = {
        "macd": "line",
        "signal": "line",
        "histogram": "histogram",
    }

    #: Four hex colors keyed by the same order as
    #: :func:`classify_histogram` returns (0..3).
    histogram_palette: ClassVar[tuple[str, str, str, str]] = _HISTOGRAM_PALETTE

    def __init__(
        self,
        fast_length: int = _DEFAULT_FAST_LENGTH,
        slow_length: int = _DEFAULT_SLOW_LENGTH,
        signal_length: int = _DEFAULT_SIGNAL_LENGTH,
        ma_type: str = _DEFAULT_MA_TYPE,
        source: str = _DEFAULT_SOURCE,
    ) -> None:
        if int(fast_length) < 2:
            raise ValueError("fast_length must be >= 2")
        if int(slow_length) < 2:
            raise ValueError("slow_length must be >= 2")
        if int(signal_length) < 2:
            raise ValueError("signal_length must be >= 2")
        if int(slow_length) <= int(fast_length):
            raise ValueError(
                "slow_length must be > fast_length "
                f"(got fast={fast_length}, slow={slow_length})"
            )
        if ma_type not in MA_TYPES:
            raise ValueError(f"ma_type must be one of {MA_TYPES!r}")
        # Accept any casing (e.g. "Close" or "close") and canonicalize to the
        # lowercase form the indicator registry uses — matches MovingAverage's
        # case-insensitive source handling so a source value is portable across
        # every indicator (audit ``indicator-source-case-insensitive``).
        source = str(source).strip().lower()
        if source not in _SOURCES:
            raise ValueError(f"source must be one of {_SOURCES!r}")
        self.fast_length = int(fast_length)
        self.slow_length = int(slow_length)
        self.signal_length = int(signal_length)
        self.ma_type = str(ma_type)
        self.source = source
        self.name = self._render_name()

    @property
    def warmup_bars(self) -> int:
        """``max(fast, slow) + signal`` — signal MA chains on top of macd line.

        First-finite ``macd`` is at ``slow - 1`` (the slow MA seed), then
        the signal MA needs another ``signal - 1`` of its own seed → first
        finite ``signal`` / ``histogram`` is at ``slow + signal - 2``. We
        publish a slightly looser ``max(fast, slow) + signal`` so the
        signal line is one full window past its seed for SMA/RMA/WMA
        kernels. Matches every textbook formula.
        """
        return max(int(self.fast_length), int(self.slow_length)) + int(self.signal_length)

    def _render_name(self) -> str:
        """Compact display label.

        Examples:
          * ``MACD(12,26,9)`` — all defaults (EMA close).
          * ``MACD(12,26,9,SMA)`` — non-default kernel.
          * ``MACD(12,26,9,hl2)`` — non-default source.
          * ``MACD(12,26,9,SMA,hl2)`` — both non-default.
        """
        base = f"MACD({self.fast_length},{self.slow_length},{self.signal_length}"
        ma_tag = "" if self.ma_type == _DEFAULT_MA_TYPE else f",{self.ma_type}"
        src_tag = "" if self.source == _DEFAULT_SOURCE else f",{self.source}"
        return f"{base}{ma_tag}{src_tag})"


    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        n = len(bars)
        empty = np.full(n, np.nan, dtype=np.float64)
        if n == 0:
            return {
                "macd": empty,
                "signal": empty.copy(),
                "histogram": empty.copy(),
            }
        src = _select_source(bars, self.source)
        fast = apply_ma(self.ma_type, src, self.fast_length)
        slow = apply_ma(self.ma_type, src, self.slow_length)
        macd_line = fast - slow
        signal_line = apply_ma(self.ma_type, macd_line, self.signal_length)
        histogram = macd_line - signal_line
        return {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram,
        }

    # --- incremental protocol (closed-bar appends) ----------------------
    # MACD is three chained EMAs (fast, slow, and the signal EMA of the
    # macd line). A closed-bar append extends all three O(k) from the
    # committed EMA values. Gated to the default ma_type="EMA"; SMA/WMA/RMA
    # leave seeded=False → the cache full-recomputes. EMAs are causal so
    # the cached prefix is bit-identical; appended bars differ from the
    # vectorized kernel by float64 round-off only.

    def _inc_supported(self) -> bool:
        return self.ma_type == "EMA"

    def inc_init(self, bars: Bars) -> dict[str, object]:
        out = self.compute_arr(bars)
        n_bars = len(bars)
        state: dict[str, object] = {"output": out, "len": n_bars}
        if self._inc_supported() and n_bars > self.warmup_bars:
            src = _select_source(bars, self.source)
            fast = apply_ma("EMA", src, self.fast_length)
            slow = apply_ma("EMA", src, self.slow_length)
            f_last = float(fast[-1])
            s_last = float(slow[-1])
            sig_last = float(out["signal"][-1])
            if np.isfinite(f_last) and np.isfinite(s_last) and np.isfinite(sig_last):
                state["fast"] = f_last
                state["slow"] = s_last
                state["signal"] = sig_last
                state["seeded"] = True
                return state
        state["seeded"] = False
        return state

    def inc_step(
        self, state: dict[str, object], bars: Bars, *, prev_len: int,
    ) -> dict[str, object]:
        n_bars = len(bars)
        if n_bars <= prev_len:
            raise ValueError(
                f"MACD.inc_step requires growth: prev_len={prev_len}, new_len={n_bars}"
            )
        if not (self._inc_supported() and state.get("seeded")):
            raise ValueError("MACD.inc_step: unsupported config or unseeded state")
        af = 2.0 / (self.fast_length + 1.0)
        as_ = 2.0 / (self.slow_length + 1.0)
        asig = 2.0 / (self.signal_length + 1.0)
        fast = float(state["fast"])  # type: ignore[arg-type]
        slow = float(state["slow"])  # type: ignore[arg-type]
        signal = float(state["signal"])  # type: ignore[arg-type]
        src = _select_source(bars, self.source)
        old = state["output"]  # type: ignore[index]
        macd_out = np.empty(n_bars, dtype=np.float64)
        sig_out = np.empty(n_bars, dtype=np.float64)
        hist_out = np.empty(n_bars, dtype=np.float64)
        macd_out[:prev_len] = old["macd"]
        sig_out[:prev_len] = old["signal"]
        hist_out[:prev_len] = old["histogram"]
        for j in range(prev_len, n_bars):
            x = float(src[j])
            fast = af * x + (1.0 - af) * fast
            slow = as_ * x + (1.0 - as_) * slow
            macd_j = fast - slow
            signal = asig * macd_j + (1.0 - asig) * signal
            macd_out[j] = macd_j
            sig_out[j] = signal
            hist_out[j] = macd_j - signal
        return {
            "output": {"macd": macd_out, "signal": sig_out, "histogram": hist_out},
            "len": n_bars,
            "fast": fast,
            "slow": slow,
            "signal": signal,
            "seeded": True,
        }
