"""Moving-average indicators: SMA, EMA."""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from ..core.bars import Bars
from ._iir import ema_sma_seeded as _ema_sma_seeded
from ._palette import PRIMARY_LINE, SECONDARY_LINE, TAB10_GRAY, TERTIARY_LINE
from .base import BaseIndicator, LineStyle, ParamDef


class SMA(BaseIndicator):
    """Simple moving average over closes.

    ``compute`` returns ``{"sma": ndarray}`` where the first ``length-1``
    entries are ``NaN``.
    """

    kind_id: ClassVar[str] = "sma"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("length", "int", default=20, min=1, max=2000, step=1,
                 description="Length"),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        "sma": LineStyle(color=PRIMARY_LINE, width=1.4),
    }
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = (
        ("sma", "numeric"),
    )

    overlay = True

    def __init__(self, length: int = 20) -> None:
        if length < 1:
            raise ValueError("length must be >= 1")
        self.length = length
        self.name = f"SMA({length})"

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        closes = bars.close
        out = np.full_like(closes, np.nan)
        n = self.length
        if closes.size >= n:
            csum = np.concatenate(([0.0], np.cumsum(closes)))
            out[n - 1:] = (csum[n:] - csum[:-n]) / n
        return {"sma": out}


    # --- incremental protocol -------------------------------------------
    # Closed-bar append fast path. Forming-bar updates fall back to full
    # recompute via :class:`IndicatorMemo` (intentional — forming is rare
    # relative to closed-bar appends).

    def inc_init(self, bars: Bars) -> dict[str, object]:
        """Build initial incremental state mirroring :meth:`compute_arr`."""
        return {"output": self.compute_arr(bars), "len": int(bars.close.size)}

    def inc_step(
        self,
        state: dict[str, object],
        bars: Bars,
        *,
        prev_len: int,
    ) -> dict[str, object]:
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
        # Rolling-window means via cumulative sum. We mirror
        # ``compute_arr``'s cumsum so byte-equality across the inc /
        # full paths is preserved (test_indicator_cache asserts this).
        # The work delta vs the prior per-bar slice-mean loop is the
        # critical bit: the loop was O((n - prev_len) * L) Python-level
        # multiplications, this is O(n) at C speed.
        if n >= L:
            csum = np.concatenate(([0.0], np.cumsum(closes)))
            tail_means = (csum[L:] - csum[:-L]) / L
            # tail_means[j] is the mean over closes[j:j+L], placed at
            # output index ``j + L - 1``.
            first_new = max(prev_len, L - 1)
            if first_new > prev_len:
                new_out[prev_len:first_new] = np.nan
            if first_new < n:
                new_out[first_new:n] = tail_means[first_new - (L - 1):]
        else:
            new_out[prev_len:n] = np.nan
        return {"output": {"sma": new_out}, "len": n}


class EMA(BaseIndicator):
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
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("length", "int", default=20, min=1, max=2000, step=1,
                 description="Length"),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        "ema": LineStyle(color=SECONDARY_LINE, width=1.4),
    }
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = (
        ("ema", "numeric"),
    )

    overlay = True

    def __init__(self, length: int = 20) -> None:
        if length < 1:
            raise ValueError("length must be >= 1")
        self.length = length
        self.alpha = 2.0 / (length + 1)
        self.name = f"EMA({length})"

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        # Route through the shared vectorised IIR kernel (the same one
        # ``MovingAverage(ma_type="EMA")`` and ``ma_kernels.ema`` use) —
        # no per-bar Python loop. The kernel seeds with the SMA of the
        # first ``length`` closes published at index ``length-1``, matching
        # this class's documented convention. The closed-form tail differs
        # from a scalar recurrence only by float64 round-off; the
        # incremental ``inc_step`` path (a true recurrence) stays within
        # the same ~1e-12 tolerance the parity tests assert — the exact
        # full=kernel / inc=loop split already shipped for ``MovingAverage``.
        return {"ema": _ema_sma_seeded(bars.close, self.length)}


    # --- incremental protocol -------------------------------------------

    def inc_init(self, bars: Bars) -> dict[str, object]:
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
        state: dict[str, object],
        bars: Bars,
        *,
        prev_len: int,
    ) -> dict[str, object]:
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


# ---------------------------------------------------------------------------
# Unified Moving Average indicator (registered as "Moving Average")
# ---------------------------------------------------------------------------

from .ma_kernels import MA_TYPES, apply_ma  # noqa: E402

#: Allowed values for the ``MovingAverage.source`` parameter. ``Close``
#: is the traditional default that most traders reach for; the
#: typical-price sources (HL2 / HLC3 / OHLC4) are useful for midpoint
#: smoothing and pivot-style analysis.
SOURCE_TYPES: tuple[str, ...] = (
    "Close", "Open", "High", "Low", "HL2", "HLC3", "OHLC4",
)


#: Trader-mental-model default colors. SMA = blue, EMA = orange —
#: matches the pre-consolidation classes AND TradingView's convention.
#: WMA / RMA pick complementary palette slots so a chart carrying
#: multiple MA types doesn't collapse to a single hue.
_DEFAULT_COLOR_BY_MA: dict[str, str] = {
    "SMA": PRIMARY_LINE,
    "EMA": SECONDARY_LINE,
    "WMA": TERTIARY_LINE,
    "RMA": TAB10_GRAY,
}


def _source_array(bars: Bars, source: str) -> np.ndarray:
    """Return the 1-D source array selected by ``source``.

    Unknown ``source`` strings fall through to closes — the dialog's
    Combobox is read-only so this only matters for direct programmatic
    misuse, and a silent fallback is friendlier than a crash mid-render.
    """
    src = str(source).upper()
    if src == "OPEN":
        return bars.open
    if src == "HIGH":
        return bars.high
    if src == "LOW":
        return bars.low
    if src == "HL2":
        return (bars.high + bars.low) / 2.0
    if src == "HLC3":
        return (bars.high + bars.low + bars.close) / 3.0
    if src == "OHLC4":
        return (bars.open + bars.high + bars.low + bars.close) / 4.0
    return bars.close


class MovingAverage(BaseIndicator):
    """Single unified moving-average overlay.

    ``compute`` returns ``{"ma": ndarray}``. The legend label encodes
    the ``ma_type`` and ``length`` (and ``source`` only when non-default)
    so a glance reads ``EMA(20)`` rather than ``MovingAverage(20, EMA)``.

    Replaces the legacy :class:`SMA` and :class:`EMA` menu entries. Old
    saved configs (``kind_id="sma"`` / ``"ema"``) migrate to
    ``kind_id="ma"`` via :func:`indicators.base.migrate_kind_id` at load
    time; preset JSONs on disk are not rewritten.

    The incremental fast path (``inc_init`` / ``inc_step``) is honored
    for ``ma_type in {"SMA", "EMA"}`` on Close only — those are the
    cases the chart's per-tick redraw cares about. Other combinations
    fall back to a full O(N) recompute via the standard cache miss
    path; that's still microseconds for the visible candle window.
    """

    kind_id: ClassVar[str] = "ma"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("ma_type", "choice", default="SMA",
                 choices=MA_TYPES, description="Type"),
        ParamDef("length", "int", default=20, min=1, max=2000, step=1,
                 description="Length"),
        ParamDef("source", "choice", default="Close",
                 choices=SOURCE_TYPES, description="Source"),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        "ma": LineStyle(color=_DEFAULT_COLOR_BY_MA["SMA"], width=1.4),
    }

    overlay = True

    def __init__(
        self,
        length: int = 20,
        ma_type: str = "SMA",
        source: str = "Close",
    ) -> None:
        if length < 1:
            raise ValueError("length must be >= 1")
        ma_type_norm = str(ma_type).upper()
        if ma_type_norm not in MA_TYPES:
            raise ValueError(
                f"ma_type must be one of {MA_TYPES}; got {ma_type!r}",
            )
        source_norm = self._normalize_source(source)
        self.length = int(length)
        self.ma_type = ma_type_norm
        self.source = source_norm
        src_tag = "" if source_norm == "Close" else f",{source_norm}"
        self.name = f"{self.ma_type}({self.length}{src_tag})"
        self.style_overrides: dict[str, LineStyle] = {
            "ma": LineStyle(
                color=_DEFAULT_COLOR_BY_MA.get(
                    self.ma_type, _DEFAULT_COLOR_BY_MA["SMA"],
                ),
                width=1.4,
            ),
        }

    @staticmethod
    def _normalize_source(source: str) -> str:
        """Canonicalize ``source`` to the casing of :data:`SOURCE_TYPES`.

        Accepts case-insensitive input from saved configs; raises
        :class:`ValueError` for values outside the allowed set so a
        corrupt persisted source doesn't silently fall back to Close.
        """
        if source is None:
            return "Close"
        upper = str(source).strip().upper()
        for canonical in SOURCE_TYPES:
            if canonical.upper() == upper:
                return canonical
        raise ValueError(
            f"source must be one of {SOURCE_TYPES}; got {source!r}",
        )

    @classmethod
    def legend_label(cls, display_name: str, params: dict) -> str | None:
        """Condensed price-pane legend prefix: ``MA(EMA, 9, close)``.

        The generic schema walker renders
        ``MA(EMA, length=9, source=Close)`` — param *names* plus a
        capitalised source. This override shows the moving-average
        **type, length and source as bare VALUES** (source lowercased)
        so a glance reads ``MA(EMA, 9, close)``.

        A genuine user rename is preserved: only an empty display name
        OR the factory's auto-generated instance name (``EMA(9)`` /
        ``SMA(20,HLC3)`` — see :meth:`__init__`) is replaced with the
        condensed form; any other custom ``display_name`` passes through
        unchanged. Audit ``ma-legend-values``.
        """
        p = params or {}
        ma_type = str(p.get("ma_type") or "SMA").upper()
        raw_len = p.get("length")
        try:
            length: int | None = int(raw_len)
        except (TypeError, ValueError):
            length = None
        try:
            source_norm = cls._normalize_source(p.get("source"))
        except ValueError:
            source_norm = "Close"
        # Reconstruct the auto instance name (``self.name``) so we can
        # tell it apart from a genuine user rename and override only it.
        src_tag = "" if source_norm == "Close" else f",{source_norm}"
        auto_name = (
            f"{ma_type}({length}{src_tag})" if length is not None else ""
        )
        name = (display_name or "").strip()
        kind_label = str(getattr(cls, "kind_id", "") or "").upper()  # "MA"
        if name and name != auto_name and name.upper() != kind_label:
            return name
        parts = [ma_type]
        if length is not None:
            parts.append(str(length))
        parts.append(source_norm.lower())
        return f"MA({', '.join(parts)})"

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        arr = _source_array(bars, self.source)
        out = apply_ma(self.ma_type, arr, self.length)
        return {"ma": out}


    def _can_inc(self) -> bool:
        return self.source == "Close" and self.ma_type in ("SMA", "EMA")

    def inc_init(self, bars: Bars) -> dict[str, object]:
        if not self._can_inc():
            raise ValueError(
                f"incremental not supported for {self.ma_type}({self.source})",
            )
        out_full = self.compute_arr(bars)["ma"]
        n = int(bars.close.size)
        L = self.length
        if self.ma_type == "SMA":
            return {"output": {"ma": out_full}, "len": n}
        if n >= L:
            committed_idx = n - 1
            committed_value = float(out_full[n - 1])
        else:
            committed_idx = -1
            committed_value = float("nan")
        return {
            "output": {"ma": out_full},
            "len": n,
            "committed_idx": committed_idx,
            "committed_value": committed_value,
        }

    def inc_step(
        self,
        state: dict[str, object],
        bars: Bars,
        *,
        prev_len: int,
    ) -> dict[str, object]:
        if not self._can_inc():
            raise ValueError(
                f"incremental not supported for {self.ma_type}({self.source})",
            )
        closes = bars.close
        n = int(closes.size)
        if n <= prev_len:
            raise ValueError(
                f"inc_step requires growth: prev_len={prev_len}, new_len={n}",
            )
        L = self.length
        old_out = state["output"]["ma"]  # type: ignore[index]
        new_out = np.empty(n, dtype=np.float64)
        new_out[:prev_len] = old_out

        if self.ma_type == "SMA":
            if n >= L:
                csum = np.concatenate(([0.0], np.cumsum(closes)))
                tail_means = (csum[L:] - csum[:-L]) / L
                first_new = max(prev_len, L - 1)
                if first_new > prev_len:
                    new_out[prev_len:first_new] = np.nan
                if first_new < n:
                    new_out[first_new:n] = tail_means[first_new - (L - 1):]
            else:
                new_out[prev_len:n] = np.nan
            return {"output": {"ma": new_out}, "len": n}

        a = 2.0 / (L + 1.0)
        committed_idx = int(state["committed_idx"])  # type: ignore[arg-type]
        committed_value = float(state["committed_value"])  # type: ignore[arg-type]
        for i in range(prev_len, n):
            if i < L - 1:
                new_out[i] = np.nan
                continue
            if committed_idx < L - 1:
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
            "output": {"ma": new_out},
            "len": n,
            "committed_idx": committed_idx,
            "committed_value": committed_value,
        }
