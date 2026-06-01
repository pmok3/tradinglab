"""Indicator perf-budget regression gates.

Opt-in suite (``pytest -m perf``). Default ``pytest`` runs skip these
via the ``-m 'not perf'`` filter in ``pyproject.toml`` so the
millisecond-scale timings don't bloat the smoke matrix's wall-time.

Run locally:

    pytest tests/perf -m perf -v

Run a single budget:

    pytest tests/perf -m perf -k macd -v

What this catches
-----------------
Each test exercises one of the post-vectorisation IIR hot paths
(MACD, Chandelier, Keltner, SMI, LRSI — see ``docs/PERFORMANCE.md``
and CLAUDE.md §7.27 for context) against a synthetic 25k-bar
regular-session OHLCV series and asserts the **min-of-N** runtime
sits below a generous post-vectorisation budget.

Budgets are sized at **~4× the v0.3.0 Snapdragon ARM64 dev-box
baseline**. The 4× headroom absorbs CI runner contention and
slower architectures while still catching any 2× algorithmic
regression on the fastest hardware. Per CLAUDE.md §7.26 we use
``min(samples_ms)`` not ``median`` so the assertion represents
best-case algorithmic timing and isn't tripped by GC pauses or
transient noise (which only slow SOME samples).

When a budget tightens (e.g. JIT/numba sprint)
-----------------------------------------------
1. Re-run ``python tools/benchmark_indicators.py --json after.json``
   on the same hardware.
2. Update the budget in ``_BUDGETS_MS_25K`` to ``~4× new min``.
3. Update the speedup row in ``docs/PERFORMANCE.md``.

When a budget loosens (regression you've decided to accept)
-----------------------------------------------------------
Same as above but document the reason in the commit body — the
gate's whole purpose is to make accepting regressions a deliberate
choice rather than silent drift.
"""

from __future__ import annotations

import statistics
import time

import numpy as np
import pytest

import tradinglab.indicators  # noqa: F401 — populate INDICATORS
from tradinglab.core.bars import Bars
from tradinglab.indicators.base import INDICATORS

# Standard test size — large enough to exercise the IIR steady-state,
# small enough that the suite finishes in well under a second.
# (25k bars ≈ a 5m chart over a full trading year.)
_TEST_BARS = 25_000

# Number of timed samples (min-of-N) per indicator. 11 is the §7.26
# convention — enough samples to reliably surface the algorithmic
# best-case, few enough to stay sub-second.
_TIMING_RUNS = 11

# Per-indicator min_ms budgets at 25_000 bars. Each is ~4× the v0.3.0
# Snapdragon ARM64 baseline captured in ``files/bench_after.json``;
# see module docstring for the methodology. Order: most-expensive first
# so a regression on the dominant cost lights up the test list first.
_BUDGETS_MS_25K: dict[str, float] = {
    # baseline 15.96 ms → budget 64 ms (~4x)
    "smi": 64.0,
    # baseline 10.90 ms → budget 46 ms (~4x)
    "lrsi": 46.0,
    # baseline 3.30 ms → budget 14 ms (~4x)
    "macd": 14.0,
    # baseline 1.88 ms → budget 8 ms (~4x)
    "chandelier": 8.0,
    # baseline 1.76 ms → budget 8 ms (~4x; matches chandelier)
    "keltner": 8.0,
}


def _synthetic_bars(n: int = _TEST_BARS, *, seed: int = 1234) -> Bars:
    """Build an ``n``-bar regular-session intraday ``Bars`` value.

    Mirrors the harness's ``_make_bars`` in ``tools/benchmark_indicators.py``
    so the perf-gate exercises the same synthetic shape the bench
    captures: geometric-brownian-ish close, OHLC bracketing it, positive
    volume, contiguous 1-minute timestamps, all sessions tagged
    ``"regular"`` so session-grouping indicators do real work.
    Seeded RNG so different runs see identical input.
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
    timestamps = (
        start + np.arange(n, dtype="timedelta64[m]").astype("timedelta64[ns]")
    )
    session = np.full(n, "regular", dtype=object)
    return Bars.from_arrays(
        open=open_, high=high, low=low, close=close, volume=volume,
        timestamps=timestamps, session=session,
    )


def _factory_for_kind_id(kind_id: str):
    """Return the indicator factory registered under ``kind_id``.

    Walks ``INDICATORS`` (display-name keyed) and matches by the
    factory's ``kind_id`` attribute. Fails the test (not the
    collection phase) if missing — the gate's job is to surface
    "the indicator I'm trying to gate no longer exists" as a
    clear test failure with the relevant kind_id, not a
    collection-time crash.
    """
    for factory in INDICATORS.values():
        if getattr(factory, "kind_id", None) == kind_id:
            return factory
    pytest.fail(
        f"perf gate: no indicator factory registered with kind_id={kind_id!r}; "
        f"available kind_ids: "
        f"{sorted(getattr(f, 'kind_id', None) or f.__name__ for f in INDICATORS.values())}"
    )


@pytest.fixture(scope="module")
def synthetic_bars() -> Bars:
    return _synthetic_bars()


@pytest.mark.perf
@pytest.mark.parametrize(
    "kind_id,budget_ms",
    sorted(_BUDGETS_MS_25K.items()),
    ids=lambda v: str(v) if isinstance(v, str) else f"{v}ms",
)
def test_indicator_perf_budget_25k_bars(
    kind_id: str, budget_ms: float, synthetic_bars: Bars,
) -> None:
    """``compute_arr(bars)`` for ``kind_id`` must finish under ``budget_ms``.

    Constructs a fresh indicator instance with default params, runs
    a min-of-N timed loop, and asserts the minimum is below the
    documented budget. Median is reported in the assertion message
    for context but is NOT the gate.
    """
    factory = _factory_for_kind_id(kind_id)
    instance = factory()
    assert hasattr(instance, "compute_arr"), (
        f"{kind_id}: indicator must expose ``compute_arr`` for the perf gate"
    )

    # Pre-warm the bars-derived intermediates the indicator may cache.
    instance.compute_arr(synthetic_bars)

    samples: list[float] = []
    for _ in range(_TIMING_RUNS):
        t0 = time.perf_counter()
        instance.compute_arr(synthetic_bars)
        samples.append((time.perf_counter() - t0) * 1000.0)
    min_ms = min(samples)
    median_ms = statistics.median(samples)

    assert min_ms < budget_ms, (
        f"{kind_id} perf regression: min_ms={min_ms:.3f} exceeds "
        f"budget {budget_ms:.1f}ms at {_TEST_BARS} bars "
        f"(median={median_ms:.3f}). The v0.3.0 IIR vectorisation "
        f"baseline was ~{budget_ms / 4.0:.2f}ms. If this is an "
        f"intentional regression (e.g. correctness fix needs more "
        f"work per bar), update _BUDGETS_MS_25K and document the "
        f"reason in the commit body. If unintentional, re-vectorise "
        f"the hot path — see CLAUDE.md §7.27 / docs/PERFORMANCE.md."
    )


@pytest.mark.perf
def test_every_budgeted_indicator_is_actually_registered() -> None:
    """Every key in ``_BUDGETS_MS_25K`` must resolve to a real indicator.

    Catches the "we vectorised X, set its budget, then renamed X" drift
    at the moment the rename lands rather than later via a stale
    benchmark.
    """
    registered_kind_ids = {
        getattr(f, "kind_id", None) for f in INDICATORS.values()
    }
    missing = set(_BUDGETS_MS_25K) - registered_kind_ids
    assert not missing, (
        f"perf gate references unregistered indicator kind_ids: {missing}. "
        f"Either restore the indicator or remove the budget entry."
    )
