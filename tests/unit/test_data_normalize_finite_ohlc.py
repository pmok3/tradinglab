"""NaN/Inf-OHLC rows are dropped by the data normalizers.

Regression for "today's data is missing — I can see the volume but not the
OHLC": providers (Yahoo especially) emit a placeholder row for the
current/next session before any trades print — NaN OHLC, sometimes with a
stray volume. Left in, such a row became a non-gap candle with NaN
open/high/low/close that renders as an invisible candle (NaN body/wick
verts) sitting behind a visible volume bar. ``candles_from_dataframe`` and
``candles_from_json_rows`` now drop any row whose OHLC is not all-finite,
keeping the prebuilt-arrays stash length-aligned with the returned list.

See ``src/tradinglab/data/normalize.spec.md``.
"""
from __future__ import annotations

import numpy as np
import pytest

from tradinglab.data import normalize as norm_mod
from tradinglab.data.normalize import (
    candles_from_dataframe,
    candles_from_json_rows,
    pop_prebuilt_arrays,
)

pd = pytest.importorskip("pandas")

_KEYMAP = {
    "ts": "ts", "open": "open", "high": "high",
    "low": "low", "close": "close", "volume": "volume",
}


@pytest.fixture(autouse=True)
def _clear_stash():
    norm_mod._PREBUILT_ARRAYS.clear()
    yield
    norm_mod._PREBUILT_ARRAYS.clear()


def _df(rows: list[tuple]) -> pd.DataFrame:
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame(
        {
            "Open": [r[1] for r in rows],
            "High": [r[2] for r in rows],
            "Low": [r[3] for r in rows],
            "Close": [r[4] for r in rows],
            "Volume": [r[5] for r in rows],
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# DataFrame path (yfinance / Polygon-pandas)
# ---------------------------------------------------------------------------


def test_dataframe_drops_trailing_nan_ohlc_row():
    nan = float("nan")
    df = _df([
        ("2026-06-08", 100.0, 102.0, 99.0, 101.0, 1000),
        ("2026-06-09", 101.0, 103.0, 100.0, 102.0, 1200),
        ("2026-06-10", nan, nan, nan, nan, 500),  # placeholder, real volume
    ])
    cs = candles_from_dataframe(df, interval="1d")
    assert len(cs) == 2
    assert [c.close for c in cs] == [101.0, 102.0]
    # The dropped row's stray volume must NOT leak in as a phantom bar.
    assert all(
        np.isfinite([c.open, c.high, c.low, c.close]).all() for c in cs
    )


def test_dataframe_stash_is_length_aligned_after_drop():
    nan = float("nan")
    df = _df([
        ("2026-06-08", 100.0, 102.0, 99.0, 101.0, 1000),
        ("2026-06-10", nan, nan, nan, nan, 500),
    ])
    cs = candles_from_dataframe(df, interval="1d")
    arr = pop_prebuilt_arrays(cs)
    assert arr is not None
    assert len(arr.closes) == len(cs) == 1


def test_dataframe_drops_partial_nan_and_inf():
    nan, inf = float("nan"), float("inf")
    df = _df([
        ("2026-06-08", 100.0, 102.0, 99.0, 101.0, 1000),
        ("2026-06-09", 101.0, 103.0, 100.0, nan, 1200),   # only close NaN
        ("2026-06-10", 102.0, inf, 101.0, 103.0, 1300),   # high Inf
    ])
    cs = candles_from_dataframe(df, interval="1d")
    assert len(cs) == 1
    assert cs[0].close == 101.0


def test_dataframe_all_finite_unchanged():
    df = _df([
        ("2026-06-08", 100.0, 102.0, 99.0, 101.0, 1000),
        ("2026-06-09", 101.0, 103.0, 100.0, 102.0, 1200),
    ])
    cs = candles_from_dataframe(df, interval="1d")
    assert len(cs) == 2


def test_dataframe_finite_ohlc_with_nan_volume_is_kept():
    """A finite-OHLC bar with NaN volume is legitimate (extended-hours):
    keep it, coerce volume to 0 — only OHLC gates row validity."""
    nan = float("nan")
    df = _df([
        ("2026-06-08", 100.0, 102.0, 99.0, 101.0, nan),
        ("2026-06-09", 101.0, 103.0, 100.0, 102.0, 1200),
    ])
    cs = candles_from_dataframe(df, interval="1d")
    assert len(cs) == 2
    assert cs[0].volume == 0


# ---------------------------------------------------------------------------
# JSON-rows path (Schwab / Alpaca / Polygon)
# ---------------------------------------------------------------------------


def test_json_rows_drops_nan_ohlc_row():
    nan = float("nan")
    rows = [
        {"ts": 1749340800000, "open": 100.0, "high": 102.0,
         "low": 99.0, "close": 101.0, "volume": 1000},
        {"ts": 1749427200000, "open": nan, "high": nan,
         "low": nan, "close": nan, "volume": 500},
    ]
    cs = candles_from_json_rows(
        rows, interval="1d", keymap=_KEYMAP, ts_unit="ms")
    assert len(cs) == 1
    assert cs[0].close == 101.0
    arr = pop_prebuilt_arrays(cs)
    assert arr is not None
    assert len(arr.closes) == 1


def test_json_rows_all_finite_unchanged():
    rows = [
        {"ts": 1749340800000, "open": 100.0, "high": 102.0,
         "low": 99.0, "close": 101.0, "volume": 1000},
        {"ts": 1749427200000, "open": 101.0, "high": 103.0,
         "low": 100.0, "close": 102.0, "volume": 1200},
    ]
    cs = candles_from_json_rows(
        rows, interval="1d", keymap=_KEYMAP, ts_unit="ms")
    assert len(cs) == 2
