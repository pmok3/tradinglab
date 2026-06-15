"""Ad-hoc profiler: cost of ``ChartApp._render`` with N indicator panes.

Boots a headless ``ChartApp`` (stubbed fetcher), adds 5 lower-pane
indicators, then times + cProfiles ``_render`` to find the hot spots that
make "5+ indicators" feel less snappy. Not a test — a throwaway tool.

    python tools/profile_render_indicators.py
"""
from __future__ import annotations

import cProfile
import io
import os
import pstats
import statistics
import sys
import tempfile
import time

os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("TRADINGLAB_NO_SPLASH", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("TRADINGLAB_CACHE_DIR", tempfile.mkdtemp(prefix="tl_prof_"))

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import tkinter as _tk  # noqa: E402

_tk.Variable.__del__ = lambda self: None  # type: ignore[assignment]
_tk.Image.__del__ = lambda self: None  # type: ignore[assignment]

from tests.smoke._helpers import _pump, _stub_yfinance  # noqa: E402

_stub_yfinance()

from tradinglab.app import ChartApp  # noqa: E402
from tradinglab.indicators.config import IndicatorConfig  # noqa: E402


def main() -> None:
    app = ChartApp()
    try:
        app.geometry("1400x900-3000-3000")
        app.withdraw()
    except Exception:  # noqa: BLE001
        pass
    _pump(app, 0.4)
    n_bars = len(getattr(app, "_primary", []) or [])
    print(f"primary bars: {n_bars}")

    mgr = app._indicator_manager
    kinds = ["rsi", "atr", "macd", "adx", "rvol"]  # 5 separate lower panes
    for kid in kinds:
        mgr.add(IndicatorConfig(kind_id=kid, params={}, scopes=frozenset({"main"})))
    _pump(app, 0.3)

    # Warm the indicator cache so we measure render, not first-compute.
    app._render()
    _pump(app, 0.1)

    samples = []
    for _ in range(15):
        t0 = time.perf_counter()
        app._render()
        samples.append((time.perf_counter() - t0) * 1000.0)
    print(
        f"_render() with {len(kinds)} panes: "
        f"min={min(samples):.1f}ms  median={statistics.median(samples):.1f}ms"
    )

    pr = cProfile.Profile()
    pr.enable()
    for _ in range(10):
        app._render()
    pr.disable()
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(20)
    print(s.getvalue())

    try:
        app._on_close()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
