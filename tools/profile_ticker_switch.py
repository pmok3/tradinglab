"""Profile ticker-switch latency: cache-hit vs cache-miss vs revisit.

Run via:
    cd C:\\Users\\pacomok\\copilot_testing\\copilot_testing
    & 'C:\\Users\\pacomok\\AppData\\Local\\Programs\\Python\\Python312-arm64\\python.exe' tools\\profile_ticker_switch.py

Measures wall-clock of:
- pure cache-miss switch (fresh fetch + disk merge + render)
- pure cache-hit switch (memory hit, no fetch)
- revisit of a previously-loaded ticker

Uses the smoke-test fake-fetcher so HTTP latency is zero — isolates
the Tk-thread cost from network. Outputs a breakdown of WHICH phase
inside _load_data costs the most wall-clock.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Use the same fixture pattern as tests/smoke
os.environ.setdefault("TRADINGLAB_HEADLESS", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# --- Stub yfinance BEFORE app import ---------------------------------
from tradinglab import data as _data_pkg  # noqa: E402
from tradinglab.models import Candle  # noqa: E402


def _fake_candles(n: int, *, end: datetime | None = None) -> list[Candle]:
    if end is None:
        end = datetime.now().replace(second=0, microsecond=0)
    out = []
    price = 100.0
    for i in range(n):
        t = end - timedelta(minutes=5 * (n - 1 - i))
        out.append(Candle(date=t, open=price, high=price + 0.5,
                          low=price - 0.5, close=price + 0.1,
                          volume=1000 + i, session="regular"))
        price += 0.1
    return out


def _make_fake_fetcher(call_count: list[int]):
    def _f(ticker, interval):
        call_count[0] += 1
        return _fake_candles(150)
    return _f


# Replace yfinance with the fake
_call_count = [0]
_data_pkg.DATA_SOURCES["yfinance"] = _make_fake_fetcher(_call_count)

# Now we can build a ChartApp
from tradinglab.app import ChartApp  # noqa: E402


def _pump(app, secs: float) -> None:
    end = time.time() + secs
    while time.time() < end:
        try:
            app.update_idletasks()
            app.update()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.005)


def _measure_switch(app, ticker: str, *, label: str) -> dict:
    """Set ticker, schedule reload, pump until done; return timings."""
    # Monkey-patch _load_data on this app instance to capture timing
    original_load_data = app._load_data
    timings: dict = {"load_data_calls": 0, "total_load_data_ms": 0.0}

    def _wrapped_load_data():
        t0 = time.perf_counter()
        result = original_load_data()
        timings["load_data_calls"] += 1
        timings["total_load_data_ms"] += (time.perf_counter() - t0) * 1000
        timings["completion_time"] = time.perf_counter()
        return result

    app._load_data = _wrapped_load_data

    before_calls = _call_count[0]
    t0 = time.perf_counter()
    app.ticker_var.set(ticker)
    t_set = time.perf_counter() - t0

    t0 = time.perf_counter()
    app._schedule_reload(delay_ms=0)
    t_schedule = time.perf_counter() - t0

    # Pump until _load_data has been called + ticker is in cache
    t_pump_start = time.perf_counter()
    src = app.source_var.get()
    interval = app.interval_var.get()
    key = (src, ticker, interval)
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if (
            timings["load_data_calls"] >= 1
            and key in app._full_cache
            and len(app._primary) > 0
        ):
            break
        _pump(app, 0.005)

    # Wall time = from schedule_reload to load_data completion
    if "completion_time" in timings:
        t_total = (timings["completion_time"] - t_pump_start) * 1000
    else:
        t_total = (time.perf_counter() - t_pump_start) * 1000

    after_calls = _call_count[0]
    # Restore
    app._load_data = original_load_data

    return {
        "label": label,
        "ticker": ticker,
        "fetcher_calls": after_calls - before_calls,
        "t_set_var_ms": t_set * 1000,
        "t_schedule_ms": t_schedule * 1000,
        "t_total_wall_ms": t_total,
        "t_load_data_ms": timings["total_load_data_ms"],
        "load_data_calls": timings["load_data_calls"],
        "primary_bars": len(app._primary),
        "in_cache": key in app._full_cache,
    }


def main():
    print("=== ticker-switch latency probe ===\n")
    print("Building ChartApp...")
    t0 = time.perf_counter()
    app = ChartApp()
    try:
        app.withdraw()
    except Exception:  # noqa: BLE001
        pass
    _pump(app, 0.5)
    t_startup = time.perf_counter() - t0
    print(f"  startup: {t_startup*1000:.0f}ms")
    print()

    # Disable compare so we measure single-ticker only
    try:
        app.compare_var.set(False)
    except Exception:  # noqa: BLE001
        pass

    # Switch to a fresh ticker — cache MISS (worst case)
    print("Switching to TESTSYM1 (cache MISS)...")
    r1 = _measure_switch(app, "TESTSYM1", label="miss")
    print(f"  fetcher calls: {r1['fetcher_calls']}")
    print(f"  t_total_wall:  {r1['t_total_wall_ms']:.2f}ms (pump-to-completion)")
    print(f"  t_load_data:   {r1['t_load_data_ms']:.2f}ms (sync work on Tk thread)")
    print(f"  load_data calls: {r1['load_data_calls']}")
    print(f"  primary bars:  {r1['primary_bars']}")
    print()

    # Switch back to TESTSYM1 — cache HIT
    print("Switching back to TESTSYM1 (cache HIT)...")
    r2 = _measure_switch(app, "TESTSYM1", label="hit-revisit")
    print(f"  fetcher calls: {r2['fetcher_calls']}")
    print(f"  t_total_wall:  {r2['t_total_wall_ms']:.2f}ms (pump-to-completion)")
    print(f"  t_load_data:   {r2['t_load_data_ms']:.2f}ms (sync work on Tk thread)")
    print(f"  load_data calls: {r2['load_data_calls']}")
    print(f"  primary bars:  {r2['primary_bars']}")
    print()

    # Switch to another new ticker — cache MISS
    print("Switching to TESTSYM2 (cache MISS again)...")
    r3 = _measure_switch(app, "TESTSYM2", label="miss-2")
    print(f"  fetcher calls: {r3['fetcher_calls']}")
    print(f"  t_total_wall:  {r3['t_total_wall_ms']:.2f}ms (pump-to-completion)")
    print(f"  t_load_data:   {r3['t_load_data_ms']:.2f}ms (sync work on Tk thread)")
    print(f"  load_data calls: {r3['load_data_calls']}")
    print(f"  primary bars:  {r3['primary_bars']}")
    print()

    # Switch back to TESTSYM1 — cache HIT
    print("Switching back to TESTSYM1 (cache HIT after switch away)...")
    r4 = _measure_switch(app, "TESTSYM1", label="hit-roundtrip")
    print(f"  fetcher calls: {r4['fetcher_calls']}")
    print(f"  t_total_wall:  {r4['t_total_wall_ms']:.2f}ms (pump-to-completion)")
    print(f"  t_load_data:   {r4['t_load_data_ms']:.2f}ms (sync work on Tk thread)")
    print(f"  load_data calls: {r4['load_data_calls']}")
    print(f"  primary bars:  {r4['primary_bars']}")
    print()

    try:
        app.destroy()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
