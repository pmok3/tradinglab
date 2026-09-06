from __future__ import annotations

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators.ichimoku import IchimokuCloud


def _bars(high: list[float], low: list[float], close: list[float]) -> Bars:
    n = len(close)
    return Bars.from_arrays(
        open=np.asarray(close, dtype=np.float64),
        high=np.asarray(high, dtype=np.float64),
        low=np.asarray(low, dtype=np.float64),
        close=np.asarray(close, dtype=np.float64),
        volume=np.ones(n, dtype=np.float64),
        timestamps=(
            np.datetime64("2026-01-05T14:30")
            + np.arange(n) * np.timedelta64(1, "m")
        ),
        session=np.full(n, "regular", dtype=object),
    )


def test_hand_calculated_components_are_causal_and_unshifted() -> None:
    bars = _bars(
        [2, 4, 6, 8, 10, 12],
        [0, 1, 2, 3, 4, 5],
        [1, 3, 5, 7, 9, 11],
    )
    out = IchimokuCloud(
        conversion_period=2,
        base_period=3,
        span_b_period=4,
        displacement=2,
    ).compute_arr(bars)

    np.testing.assert_allclose(
        out["tenkan"],
        [np.nan, 2.0, 3.5, 5.0, 6.5, 8.0],
        equal_nan=True,
    )
    np.testing.assert_allclose(
        out["kijun"],
        [np.nan, np.nan, 3.0, 4.5, 6.0, 7.5],
        equal_nan=True,
    )
    np.testing.assert_allclose(
        out["senkou_span_a_raw"],
        [np.nan, np.nan, 3.25, 4.75, 6.25, 7.75],
        equal_nan=True,
    )
    np.testing.assert_allclose(
        out["senkou_span_b_raw"],
        [np.nan, np.nan, np.nan, 4.0, 5.5, 7.0],
        equal_nan=True,
    )
    np.testing.assert_allclose(out["chikou_source"], bars.close)


def test_short_input_is_nan_padded_and_output_length_is_stable() -> None:
    bars = _bars([2, 3, 4], [0, 1, 2], [1, 2, 3])
    out = IchimokuCloud(
        conversion_period=2,
        base_period=3,
        span_b_period=4,
        displacement=2,
    ).compute_arr(bars)
    assert set(out) == {
        "tenkan",
        "kijun",
        "senkou_span_a_raw",
        "senkou_span_b_raw",
        "chikou_source",
    }
    assert all(arr.shape == (3,) for arr in out.values())
    assert np.isnan(out["senkou_span_b_raw"]).all()


def test_window_with_nonfinite_high_or_low_is_strictly_nan() -> None:
    bars = _bars(
        [2, np.nan, 6, 8],
        [0, 1, 2, 3],
        [1, 3, 5, 7],
    )
    out = IchimokuCloud(
        conversion_period=2,
        base_period=2,
        span_b_period=2,
        displacement=1,
    ).compute_arr(bars)
    assert np.isnan(out["tenkan"][1])
    assert np.isnan(out["tenkan"][2])
    assert np.isfinite(out["tenkan"][3])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"conversion_period": 0},
        {"conversion_period": True},
        {"base_period": 9.0},
        {"span_b_period": 501},
        {"displacement": 0},
        {"conversion_period": 10, "base_period": 9},
        {"base_period": 53, "span_b_period": 52},
    ],
)
def test_parameter_validation(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        IchimokuCloud(**kwargs)


def test_plot_fill_readout_and_symbol_metadata() -> None:
    params = {
        "conversion_period": 9,
        "base_period": 26,
        "span_b_period": 52,
        "displacement": 26,
    }
    specs = IchimokuCloud.output_plot_specs(params)
    assert specs["senkou_span_a_raw"].x_offset == 26
    assert specs["senkou_span_b_raw"].x_offset == 26
    assert specs["chikou_source"].x_offset == -26
    assert IchimokuCloud.fill_specs[0].key == "cloud"
    assert IchimokuCloud.effective_output_keys(params) == ("tenkan", "kijun")
    state = IchimokuCloud.readout_state_spec(params)
    assert state.first_output == "senkou_span_a_raw"
    assert state.second_output == "senkou_span_b_raw"
    assert state.fill_key == "cloud"
    assert IchimokuCloud.is_available_for_symbol("AAPL").ok
    assert IchimokuCloud.is_available_for_symbol("^VIX/15.87").ok
    assert not IchimokuCloud.is_available_for_symbol("AMD/NVDA").ok


def test_prefix_causality_for_every_compute_output() -> None:
    rng = np.random.default_rng(42)
    close = 100 + np.cumsum(rng.normal(0, 1, 100))
    spread = np.abs(rng.normal(1, 0.2, 100))
    bars = _bars(list(close + spread), list(close - spread), list(close))
    indicator = IchimokuCloud()
    full = indicator.compute_arr(bars)
    for end in (1, 9, 26, 52, 73, 100):
        prefix_bars = _bars(
            list(bars.high[:end]),
            list(bars.low[:end]),
            list(bars.close[:end]),
        )
        prefix = indicator.compute_arr(prefix_bars)
        for key in full:
            np.testing.assert_allclose(
                prefix[key],
                full[key][:end],
                equal_nan=True,
                err_msg=f"{key} changed when future bars were appended",
            )
