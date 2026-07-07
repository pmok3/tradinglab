"""Profile intraday session-tagging in ``data.normalize.candles_from_dataframe``.

Perf-review item 3: the intraday branch tags each bar's session with a per-bar
Python call to ``constants.classify_session``. Multi-year 1m loads and intraday
universe preloads (the yfinance path) all flow through here, so the per-bar call
is paid ``n_bars x n_symbols`` times.

This tool measures, min-of-N (per CLAUDE.md §7.26, to reject GC/scheduling noise):

  1. End-to-end ``candles_from_dataframe()`` on a large ET-localized intraday
     DataFrame.
  2. The isolated session-classification cost: the old per-bar Python loop vs
     the vectorized ``constants.classify_session_arr`` (once it exists).

Run it BEFORE and AFTER the vectorization to read the delta.

    python tools/profile_normalize_sessions.py
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from tradinglab.constants import classify_session
from tradinglab.data.normalize import candles_from_dataframe

try:  # present only after the vectorization lands
    from tradinglab.constants import classify_session_arr
except ImportError:  # pragma: no cover - "before" run
    classify_session_arr = None


def _make_df(n: int) -> pd.DataFrame:
    """A realistic ET-localized 1-minute OHLCV frame spanning all sessions."""
    idx = pd.date_range("2020-01-01 04:00", periods=n, freq="1min",
                        tz="America/New_York")
    rng = np.random.default_rng(0)
    base = 100.0 + np.cumsum(rng.normal(0, 0.05, n))
    return pd.DataFrame(
        {
            "Open": base, "High": base + 0.1, "Low": base - 0.1,
            "Close": base, "Volume": rng.integers(1, 1_000_000, n).astype(float),
        },
        index=idx,
    )


def _min_ms(fn, repeats: int = 5) -> float:
    best = float("inf")
    for _ in range(repeats):
        t = time.perf_counter()
        fn()
        best = min(best, (time.perf_counter() - t) * 1000.0)
    return best


def main() -> None:
    print(f"classify_session_arr present: {classify_session_arr is not None}\n")

    print("End-to-end candles_from_dataframe(interval='1m'):")
    print(f"  {'bars':>9} | {'ms (min)':>10}")
    for n in (50_000, 200_000, 500_000):
        df = _make_df(n)
        ms = _min_ms(lambda: candles_from_dataframe(df, interval="1m"))
        print(f"  {n:>9,} | {ms:>10.1f}")

    n = 500_000
    idx = pd.date_range("2020-01-01 04:00", periods=n, freq="1min",
                        tz="America/New_York")
    hours = np.asarray(idx.hour)
    minutes = np.asarray(idx.minute)
    dts = idx.to_pydatetime()

    print(f"\nIsolated session classification ({n:,} bars):")
    scalar_ms = _min_ms(lambda: [classify_session(d.hour, d.minute) for d in dts])
    print(f"  per-bar Python loop : {scalar_ms:9.1f} ms")
    if classify_session_arr is not None:
        vec_ms = _min_ms(lambda: classify_session_arr(hours, minutes))
        print(f"  vectorized (arr)    : {vec_ms:9.1f} ms")
        print(f"  speedup             : {scalar_ms / vec_ms:8.1f}x")
        ref = [classify_session(d.hour, d.minute) for d in dts]
        print(f"  bit-for-bit equal   : {ref == list(classify_session_arr(hours, minutes))}")
    else:
        print("  vectorized (arr)    : (not implemented yet)")


if __name__ == "__main__":
    main()
