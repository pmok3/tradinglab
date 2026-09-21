"""Strategy-eval per-bar timing gate.

Opt-in suite (``pytest -m perf``). Default ``pytest`` runs skip these
via the ``-m 'not perf and not longhaul'`` filter in ``pyproject.toml``
so the timing loop doesn't bloat the smoke matrix's wall-time. The CI
``perf-gate`` job runs ``pytest tests/perf -m perf -v --tb=short`` on
every push/PR, which is how this gate is enforced.

Run locally:

    pytest tests/perf -m perf -v

Run a single budget:

    pytest tests/perf -m perf -k market -v

Entry point under test
----------------------
``tradinglab.strategy_tester.evaluator.evaluate_symbol`` — the
single entry point for mechanical per-symbol strategy evaluation
(the per-bar loop ``for i in range(n):`` calls ``engine.tick()``
plus entry/exit trigger checks per bar). This gate measures
end-to-end evaluation time divided by bar count, i.e. the
amortised per-bar cost of the evaluator.

What this catches
-----------------
Each test runs a representative built-in strategy (market entry +
stop exit; ``close > EMA(21)`` indicator entry + stop exit) over a
synthetic 8k-bar regular-session 5m OHLCV series and asserts the
**min-of-N** per-bar time sits below a generous budget. This is a
backstop against order-of-magnitude regressions in the evaluator's
per-bar cost — e.g. re-introducing per-bar allocations or Python-level
work inside the loop (see CLAUDE.md §7.14 for the perf contracts that
motivated this), not a precision instrument for 2× regressions.

Budgets and rationale
---------------------
Budgets are sized at roughly **20×+ the dev-box min-of-N baseline** so
they stay green across CI runner contention and slower architectures
while still failing hard on a real blowup (a 2ms-per-bar ``sleep``
injected into the loop fails both budgets — verified manually when
this gate was written):

- ``market-stop``: baseline ~22.5µs/bar → budget 0.5ms/bar (~22×)
- ``close-gt-ema21-stop``: baseline ~35.9µs/bar → budget 2.0ms/bar (~55×)

Per CLAUDE.md §7.26 we gate on ``min(samples)``, not the median, so the
assertion represents best-case algorithmic timing and isn't tripped by
GC pauses or transient noise (which only slow SOME samples); the median
is reported in the failure message for context.

When a budget tightens (e.g. the evaluator vectorisation lands and
per-bar cost drops materially)
---------------------------------------------------------------
1. Re-run the timing on representative hardware.
2. Update the budget in ``_BUDGETS_MS_PER_BAR`` to ~20× the new min.
3. Document the re-baseline in the commit body.

When a budget loosens (regression you've decided to accept)
-----------------------------------------------------------
Same as above but document the reason in the commit body — the gate's
whole purpose is to make accepting regressions a deliberate choice
rather than silent drift.
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    ShareRounding,
    SizingKind,
    SizingRule,
)
from tradinglab.entries.model import (
    TriggerKind as EntryTriggerKind,
)
from tradinglab.entries.model import (
    Universe as EntryUniverse,
)
from tradinglab.exits.model import (
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
)
from tradinglab.exits.model import (
    TriggerKind as ExitTriggerKind,
)
from tradinglab.models import Candle
from tradinglab.scanner.model import OP_GT, Condition, FieldRef, Group
from tradinglab.strategy_tester import CostModel, evaluate_symbol

_ET = ZoneInfo("America/New_York")

# Standard test size — large enough to exercise the evaluator's
# bar-loop steady state, small enough to stay cheap.
# (8k 5m bars ≈ 103 regular sessions.)
_TEST_BARS = 8_000

# Fixed seed so every run (local, CI) evaluates identical input.
_SEED = 20260920

# Number of timed samples (min-of-N) per strategy. One extra untimed
# warmup run precedes the loop so import/class construction and any
# first-touch allocation is outside the measurement.
_TIMING_RUNS = 7

# Per-strategy per-bar budgets in MILLISECONDS. Each is ~20×+ the
# dev-box min-of-N baseline (recorded above); see the module docstring
# for the methodology. Order: cheapest first.
_BUDGETS_MS_PER_BAR: dict[str, float] = {
    # baseline 22.5µs/bar → budget 0.5ms (~22x)
    "market-stop": 0.5,
    # baseline 35.9µs/bar → budget 2.0ms (~55x)
    "close-gt-ema21-stop": 2.0,
}


def _synthetic_rth_5m_candles(n: int = _TEST_BARS, *, seed: int = _SEED) -> list[Candle]:
    """Build ``n`` synthetic 5m bars confined to ET regular sessions.

    Geometric-brownian-ish close, OHLC bracketing it, positive volume,
    tz-aware ET timestamps stepping 5 minutes inside 09:30–16:00
    Monday–Friday, every bar tagged ``"regular"``. Confinement to RTH
    is deliberate: the evaluator's RTH gates (``require_market_open`` /
    ``include_extended_hours=False``) need real session membership to
    exercise the same entry/exit paths production runs take. Seeded
    RNG so different runs see identical input.
    """
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 0.4, size=n)
    closes = np.abs(100.0 + np.cumsum(steps)) + 1.0
    spread = np.abs(rng.normal(0.0, 0.25, size=n)) + 0.05
    opens = closes - rng.normal(0.0, 0.2, size=n)
    volumes = (np.abs(rng.normal(1_000_000.0, 200_000.0, size=n)) + 1.0).astype(int)

    out: list[Candle] = []
    t = datetime(2024, 1, 2, 9, 30, tzinfo=_ET)  # a Tuesday
    i = 0
    while i < n:
        if t.weekday() >= 5 or t.hour * 60 + t.minute >= 16 * 60:
            t = (t + timedelta(days=1)).replace(hour=9, minute=30)
            continue
        c = float(closes[i])
        sp = float(spread[i])
        op = float(opens[i])
        out.append(
            Candle(
                date=t,
                open=op,
                high=max(op, c) + sp,
                low=min(op, c) - sp,
                close=c,
                volume=int(volumes[i]),
                session="regular",
            )
        )
        i += 1
        t = t + timedelta(minutes=5)
    return out


def _market_entry() -> EntryStrategy:
    return EntryStrategy(
        id="e-perf",
        name="perf-market",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY, qty=10.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )


def _close_gt_ema21_entry() -> EntryStrategy:
    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef.builtin("close"),
                op=OP_GT,
                params={"right": FieldRef.indicator("ema", params={"length": 21})},
            ),
        ],
    )
    return EntryStrategy(
        id="e-perf",
        name="perf-close-gt-ema21",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.INDICATOR, condition=cond),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY, qty=10.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )


def _stop_5pct_exit() -> ExitStrategy:
    return ExitStrategy(
        id="x-perf",
        name="perf-stop",
        eod_kill_switch=True,
        legs=[
            ExitLeg(
                id="leg-stop",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.STOP,
                        offset_pct=5.0,
                        qty_pct=100.0,
                    ),
                ],
            ),
        ],
    )


def _evaluate(entry: EntryStrategy, candles: list[Candle]):
    return evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=_stop_5pct_exit(),
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0, commission_per_trade=0.0),
    )


def _entry_for(strategy_id: str) -> EntryStrategy:
    if strategy_id == "market-stop":
        return _market_entry()
    if strategy_id == "close-gt-ema21-stop":
        return _close_gt_ema21_entry()
    pytest.fail(f"perf gate: unknown strategy {strategy_id!r}; "
                f"budgeted strategies: {sorted(_BUDGETS_MS_PER_BAR)}")


@pytest.fixture(scope="module")
def synthetic_candles() -> list[Candle]:
    return _synthetic_rth_5m_candles()


@pytest.mark.perf
def test_eval_workloads_produce_trades(synthetic_candles: list[Candle]) -> None:
    """Anti-vacuity guard: every budgeted strategy must actually trade.

    A timing gate that only ever ticks bars with no entries/exits would
    still catch loop-level regressions, but asserting fills exist keeps
    the workload representative of the entry+exit paths it claims to
    cover. Fails loudly if fixture/strategy drift ever neuters it.
    """
    for strategy_id in _BUDGETS_MS_PER_BAR:
        result = _evaluate(_entry_for(strategy_id), synthetic_candles)
        assert len(result.post_trades) > 0, (
            f"{strategy_id}: evaluator produced zero closed trades on the "
            f"synthetic workload — the timing gate would no longer be "
            f"measuring the entry/exit paths. Fix the fixture or the strategy."
        )


@pytest.mark.perf
@pytest.mark.parametrize(
    "strategy_id,budget_ms_per_bar",
    sorted(_BUDGETS_MS_PER_BAR.items()),
    ids=lambda v: str(v) if isinstance(v, str) else f"{v}ms",
)
def test_strategy_eval_per_bar_budget(
    strategy_id: str, budget_ms_per_bar: float, synthetic_candles: list[Candle]
) -> None:
    """``evaluate_symbol`` must average under ``budget_ms_per_bar`` per bar.

    One untimed warmup run, then a min-of-N timed loop; the gate is
    ``min(per_bar_us) < budget`` per CLAUDE.md §7.26. Median is reported
    in the assertion message for context but is NOT the gate.
    """
    entry = _entry_for(strategy_id)

    # Warmup: import/class construction and first-touch allocation stay
    # out of the measurement.
    _evaluate(entry, synthetic_candles)

    n = len(synthetic_candles)
    samples_us_per_bar: list[float] = []
    for _ in range(_TIMING_RUNS):
        t0 = time.perf_counter()
        _evaluate(entry, synthetic_candles)
        samples_us_per_bar.append((time.perf_counter() - t0) * 1e6 / n)

    min_us = min(samples_us_per_bar)
    median_us = statistics.median(samples_us_per_bar)
    budget_us = budget_ms_per_bar * 1000.0

    assert min_us < budget_us, (
        f"{strategy_id} strategy-eval perf regression: "
        f"min={min_us:.1f}µs/bar exceeds budget {budget_ms_per_bar:.2f}ms/bar "
        f"at {n} bars (median={median_us:.1f}µs/bar). "
        f"If this is an intentional regression (e.g. a correctness fix "
        f"needs more work per bar), update _BUDGETS_MS_PER_BAR and document "
        f"the reason in the commit body. If unintentional, look for new "
        f"per-bar allocations in the evaluator loop — see CLAUDE.md §7.14."
    )
