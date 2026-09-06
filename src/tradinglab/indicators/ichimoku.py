"""Causal Ichimoku Cloud computation and render metadata."""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Integral
from typing import ClassVar

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from ..core.bars import Bars
from ._palette import (
    BEARISH,
    BULLISH,
    PRIMARY_LINE,
    QUINARY,
    SECONDARY_LINE,
)
from .base import (
    Availability,
    BaseIndicator,
    FillSpec,
    LineStyle,
    OutputPlotSpec,
    ParamDef,
    ReadoutStateSpec,
)

_MAX_PERIOD = 500


def _rolling_midpoint(
    highs: np.ndarray,
    lows: np.ndarray,
    period: int,
) -> np.ndarray:
    """Return strict-window Donchian midpoints, NaN-padded to input length."""
    n = int(highs.size)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return out
    high_windows = sliding_window_view(highs, period)
    low_windows = sliding_window_view(lows, period)
    valid = (
        np.isfinite(high_windows).all(axis=1)
        & np.isfinite(low_windows).all(axis=1)
    )
    values = (np.max(high_windows, axis=1) + np.min(low_windows, axis=1)) / 2.0
    values[~valid] = np.nan
    out[period - 1:] = values
    return out


def _positive_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an integer")
    number = int(value)
    if not 1 <= number <= _MAX_PERIOD:
        raise ValueError(f"{name} must be between 1 and {_MAX_PERIOD}")
    return number


class IchimokuCloud(BaseIndicator):
    """Traditional Ichimoku study with causal, calculation-time outputs."""

    kind_id: ClassVar[str] = "ichimoku"
    kind_version: ClassVar[int] = 1
    overlay: ClassVar[bool] = True
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef(
            "conversion_period", "int", default=9, min=1, max=_MAX_PERIOD, step=1,
            description="Conversion period",
        ),
        ParamDef(
            "base_period", "int", default=26, min=1, max=_MAX_PERIOD, step=1,
            description="Base period",
        ),
        ParamDef(
            "span_b_period", "int", default=52, min=1, max=_MAX_PERIOD, step=1,
            description="Leading Span B period",
        ),
        ParamDef(
            "displacement", "int", default=26, min=1, max=_MAX_PERIOD, step=1,
            description="Lead / lag displacement",
        ),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        "tenkan": LineStyle(color=PRIMARY_LINE, width=1.1),
        "kijun": LineStyle(color=SECONDARY_LINE, width=1.1),
        "senkou_span_a_raw": LineStyle(color=BULLISH, width=0.9),
        "senkou_span_b_raw": LineStyle(color=BEARISH, width=0.9),
        "chikou_source": LineStyle(color=QUINARY, width=0.9, visible=False),
    }
    semantic_output_colors: ClassVar[Mapping[str, str]] = {
        "senkou_span_a_raw": "bull",
        "senkou_span_b_raw": "bear",
    }
    fill_specs: ClassVar[tuple[FillSpec, ...]] = (
        FillSpec(
            key="cloud",
            first_output="senkou_span_a_raw",
            second_output="senkou_span_b_raw",
            alpha=0.16,
            default_visible=True,
        ),
    )
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = ()

    def __init__(
        self,
        conversion_period: int = 9,
        base_period: int = 26,
        span_b_period: int = 52,
        displacement: int = 26,
    ) -> None:
        conversion = _positive_int("conversion_period", conversion_period)
        base = _positive_int("base_period", base_period)
        span_b = _positive_int("span_b_period", span_b_period)
        shift = _positive_int("displacement", displacement)
        if not conversion <= base <= span_b:
            raise ValueError(
                "periods must satisfy conversion_period <= base_period <= "
                "span_b_period",
            )
        self.conversion_period = conversion
        self.base_period = base
        self.span_b_period = span_b
        self.displacement = shift
        self.name = (
            f"Ichimoku({conversion},{base},{span_b},{shift})"
        )

    @property
    def warmup_bars(self) -> int:
        return self.span_b_period

    @classmethod
    def output_plot_specs(
        cls, params: Mapping[str, object],
    ) -> Mapping[str, OutputPlotSpec]:
        displacement = _positive_int(
            "displacement", params.get("displacement", 26),
        )
        return {
            "senkou_span_a_raw": OutputPlotSpec(x_offset=displacement),
            "senkou_span_b_raw": OutputPlotSpec(x_offset=displacement),
            "chikou_source": OutputPlotSpec(x_offset=-displacement),
        }

    @classmethod
    def effective_output_keys(cls, params: dict) -> tuple[str, ...]:
        return ("tenkan", "kijun")

    @classmethod
    def readout_state_spec(
        cls, params: Mapping[str, object],
    ) -> ReadoutStateSpec:
        return ReadoutStateSpec(
            first_output="senkou_span_a_raw",
            second_output="senkou_span_b_raw",
            label="Cloud",
            fill_key="cloud",
        )

    @classmethod
    def legend_label(cls, display_name: str, params: dict) -> str:
        custom = (display_name or "").strip()
        if (
            custom
            and custom not in {"Ichimoku", "Ichimoku Cloud"}
            and not custom.startswith("Ichimoku(")
        ):
            return custom
        values = (
            params.get("conversion_period", 9),
            params.get("base_period", 26),
            params.get("span_b_period", 52),
            params.get("displacement", 26),
        )
        return "Ichi(" + ",".join(str(v) for v in values) + ")"

    @classmethod
    def output_key_label(cls, key: str) -> str:
        return {
            "tenkan": "T",
            "kijun": "K",
            "senkou_span_a_raw": "Span A",
            "senkou_span_b_raw": "Span B",
            "chikou_source": "Chikou",
        }.get(key, key)

    @classmethod
    def is_available_for_symbol(
        cls, symbol: str, params: Mapping[str, object] | None = None,
    ) -> Availability:
        from ..data.ratio_source import is_quotient_ratio

        if is_quotient_ratio(symbol):
            return Availability(
                False,
                "Ichimoku is unavailable for quotient ratios "
                "(their high/low values are approximate)",
            )
        return Availability(True, "")

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        tenkan = _rolling_midpoint(
            bars.high, bars.low, self.conversion_period,
        )
        kijun = _rolling_midpoint(
            bars.high, bars.low, self.base_period,
        )
        span_a = (tenkan + kijun) / 2.0
        span_b = _rolling_midpoint(
            bars.high, bars.low, self.span_b_period,
        )
        chikou = np.asarray(bars.close, dtype=np.float64).copy()
        chikou[~np.isfinite(chikou)] = np.nan
        return {
            "tenkan": tenkan,
            "kijun": kijun,
            "senkou_span_a_raw": span_a,
            "senkou_span_b_raw": span_b,
            "chikou_source": chikou,
        }
