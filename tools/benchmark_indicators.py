"""Micro-benchmark harness for indicator ``compute_arr`` hot paths.

Generates synthetic but realistic intraday OHLCV ``Bars`` (regular-session
tagged, contiguous datetime64 timestamps) at several sizes and times every
registered indicator's ``compute_arr`` over a median-of-N loop.

Usage::

    python tools/benchmark_indicators.py
    python tools/benchmark_indicators.py --sizes 5000 25000 100000 --runs 7
    python tools/benchmark_indicators.py --json out.json

The harness is deterministic (seeded RNG) so before/after runs are
comparable. It is a developer tool — never imported by the app.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any, Callable

import numpy as np

import tradinglab.indicators  # noqa: F401 - populate the registry
from tradinglab.core.bars import Bars
from tradinglab.indicators.base import INDICATORS, factory_by_kind_id

_DEFAULT_SIZES = (1_000, 5_000, 25_000, 100_000)
_DEFAULT_RUNS = 7

# Indicators that need a companion-symbol / registry context to do real
# work; we still time them (they degrade to NaN), but flag the caveat.
_NEEDS_CONTEXT = {"rrvol"}


def _make_bars(n: int, *, seed: int = 1234) -> Bars:
    """Build an ``n``-bar regular-session intraday ``Bars`` value.

    Geometric-brownian-ish close, OHLC bracketing it, positive volume,
    one-minute contiguous timestamps, all sessions tagged ``"regular"``
    so session-grouping indicators (RVOL / AVWAP) exercise real work.
    """
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 0.5, size=n).astype(np.float64)
    close = 100.0 + np.cumsum(steps)
    close = np.abs(close) + 1.0
    spread = np.abs(rng.normal(0.0, 0.3, size=n)) + 0.05
    high = close + spread
    low = np.maximum(close - spread, 0.01)
    open_ = (high + low) / 2.0
    volume = np.abs(rng.normal(1_000_000.0, 250_000.0, size=n)) + 1.0
    start = np.datetime64("2024-01-02T14:30", "ns")
    timestamps = start + np.arange(n, dtype="timedelta64[m]").astype("timedelta64[ns]")
    session = np.full(n, "regular", dtype=object)
    return Bars.from_arrays(
        open=open_, high=high, low=low, close=close, volume=volume,
        timestamps=timestamps, session=session,
    )


def _time_call(fn: Callable[[], Any], runs: int) -> tuple[float, float]:
    """Return (min_ms, median_ms) over ``runs`` invocations after 1 warmup."""
    fn()  # warmup (JIT/caches/branch predictor)
    samples: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return min(samples), statistics.median(samples)


def _registered_factories() -> list[tuple[str, Any]]:
    """(kind_id, factory) for every distinct registered indicator."""
    seen: dict[str, Any] = {}
    for factory in INDICATORS.values():
        kind_id = getattr(factory, "kind_id", None) or factory.__name__
        seen.setdefault(kind_id, factory)
    # Include legacy SMA/EMA via kind-id lookup so the raw MA loops show up.
    for kid in ("sma", "ema"):
        fac = factory_by_kind_id(kid)
        if fac is not None:
            seen.setdefault(kid, fac)
    return sorted(seen.items())


def run(sizes: tuple[int, ...], runs: int) -> dict[str, Any]:
    factories = _registered_factories()
    bars_by_size = {n: _make_bars(n) for n in sizes}
    results: dict[str, Any] = {"sizes": list(sizes), "runs": runs, "rows": []}

    for kind_id, factory in factories:
        try:
            ind = factory()
        except Exception as exc:  # noqa: BLE001
            results["rows"].append({"kind_id": kind_id, "error": f"init: {exc}"})
            continue
        if not hasattr(ind, "compute_arr"):
            results["rows"].append({"kind_id": kind_id, "error": "no compute_arr"})
            continue
        row: dict[str, Any] = {"kind_id": kind_id, "per_size": {}}
        if kind_id in _NEEDS_CONTEXT:
            row["caveat"] = "needs companion context; degrades to NaN"
        for n in sizes:
            bars = bars_by_size[n]
            try:
                mn, md = _time_call(lambda b=bars, i=ind: i.compute_arr(b), runs)
                row["per_size"][n] = {"min_ms": mn, "median_ms": md}
            except Exception as exc:  # noqa: BLE001
                row["per_size"][n] = {"error": str(exc)}
        results["rows"].append(row)
    return results


def _print_table(results: dict[str, Any]) -> None:
    sizes = results["sizes"]
    header = f"{'indicator':<26}" + "".join(f"{n:>14,}" for n in sizes)
    print(header)
    print("-" * len(header))
    rows = sorted(
        results["rows"],
        key=lambda r: r.get("per_size", {}).get(sizes[-1], {}).get("min_ms", -1.0),
        reverse=True,
    )
    for row in rows:
        name = row["kind_id"]
        if "error" in row:
            print(f"{name:<26}  ERROR: {row['error']}")
            continue
        cells = ""
        for n in sizes:
            cell = row["per_size"].get(n, {})
            if "error" in cell:
                cells += f"{'err':>14}"
            else:
                cells += f"{cell['min_ms']:>14.3f}"
        flag = "  *" + row["caveat"] if "caveat" in row else ""
        print(f"{name:<26}{cells}{flag}")
    print("\n(values are min-of-N milliseconds; lower is better)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", type=int, nargs="+", default=list(_DEFAULT_SIZES))
    ap.add_argument("--runs", type=int, default=_DEFAULT_RUNS)
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args()

    results = run(tuple(args.sizes), args.runs)
    _print_table(results)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
