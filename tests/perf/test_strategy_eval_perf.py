"""Perf gate: vectorized strategy evaluation must beat the legacy per-bar path.

Runs ``evaluate_symbol`` over ~4,000 intraday bars with an INDICATOR
entry + STOP exit (+ arm-window / RTH gates + EOD kill switch — the
configuration that used to pay per-bar ``datetime`` construction,
``"HH:MM"`` re-parsing, and full trigger-dispatch on every bar).

Timing uses min-of-N with the two paths interleaved so machine drift
cannot systematically favour either side (per AGENTS.md §7.26 the min,
not the median, is the gate). The required margin is deliberately
generous but fixed at 1.5x. Actual speedup is machine-dependent and
printed on each run; shared scalar orchestration remains the floor.
"""

from __future__ import annotations

import copy
import math
import statistics
import sys
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tradinglab.backtest.session import SessionResult
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
from tradinglab.exits.model import ExitLeg, ExitStrategy, ExitTrigger
from tradinglab.exits.model import (
    TriggerKind as ExitTriggerKind,
)
from tradinglab.models import Candle
from tradinglab.scanner.model import OP_GT, Condition, FieldRef, Group
from tradinglab.strategy_tester import CostModel, evaluate_symbol

_ET = ZoneInfo("America/New_York")

_BARS = 4056  # 52 trading days x 78 five-minute bars
_TIMING_RUNS = 7
# The vectorized path must be at least this much faster than the legacy
# path (min-of-N vs min-of-N).
_REQUIRED_SPEEDUP = 1.5


def _bars_4k() -> list[Candle]:
    import random

    rng = random.Random(20260920)
    candles: list[Candle] = []
    price = 100.0
    day = datetime(2026, 1, 5, tzinfo=_ET)
    n_days = 0
    while n_days < 52:
        if day.weekday() < 5:
            t = day.replace(hour=9, minute=35)
            for _ in range(78):
                price += rng.uniform(-0.6, 0.65)
                op = price - rng.uniform(-0.2, 0.2)
                cl = price
                candles.append(
                    Candle(
                        date=t,
                        open=op,
                        high=max(op, cl) + 0.3,
                        low=min(op, cl) - 0.3,
                        close=cl,
                        volume=1000,
                        session="regular",
                    )
                )
                t += timedelta(minutes=5)
            n_days += 1
        day += timedelta(days=1)
    assert len(candles) == _BARS
    return candles


def _entry() -> EntryStrategy:
    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op=OP_GT,
                params={"right": FieldRef(kind="literal", value=100.0)},
                interval="5m",
            ),
        ],
    )
    return EntryStrategy(
        id="perf-entry",
        name="perf",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(
            kind=EntryTriggerKind.INDICATOR, condition=cond, interval="5m"  # type: ignore[arg-type]
        ),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY, qty=10.0,
            share_rounding=ShareRounding.DOWN,
        ),
        arm_window_start="09:35",
        arm_window_end="15:30",
        require_market_open=True,
        max_fires_per_session_per_symbol=100,
    )


def _exit() -> ExitStrategy:
    return ExitStrategy(
        id="perf-exit",
        name="perf",
        legs=[
            ExitLeg(
                id="leg",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.STOP, offset_pct=3.0,
                        qty_pct=100.0,
                    ),
                ],
            ),
        ],
        eod_kill_switch=True,
    )


@pytest.fixture(scope="module")
def candles_4k() -> list[Candle]:
    return _bars_4k()


@pytest.mark.perf
def test_vectorized_eval_beats_legacy(candles_4k: list[Candle]) -> None:
    """Min-of-N wall-clock: vectorized must clear the speedup bar.

    Prints both timings (and medians for context) so the measured
    speedup is visible in the test log.
    """
    vec_samples: list[float] = []
    legacy_samples: list[float] = []
    reference: SessionResult | None = None
    for _ in range(_TIMING_RUNS):
        # Interleave: a slow machine phase hits both paths equally.
        for use_vec, acc in ((True, vec_samples), (False, legacy_samples)):
            t0 = time.perf_counter()
            result = evaluate_symbol(
                symbol="TEST",
                candles=candles_4k,
                interval="5m",
                entry_strategy=_entry(),
                exit_strategy=_exit(),
                starting_cash=100_000.0,
                cost_model=CostModel(),
                use_vectorized=use_vec,
            )
            acc.append(time.perf_counter() - t0)
            assert isinstance(result, SessionResult), "benchmark sample returned no SessionResult"
            assert len(result.equity_curve) == _BARS, "benchmark sample omitted bars"
            assert result.post_trades and len(result.fills) == 2 * len(result.post_trades), (
                "benchmark sample must contain completed round-trip trades"
            )
            assert math.isfinite(result.final_cash)
            assert all(math.isfinite(equity) for _, equity in result.equity_curve)
            if reference is None:
                reference = result
            else:
                assert result == reference, "benchmark sample differs from complete reference result"

    min_vec = min(vec_samples)
    min_legacy = min(legacy_samples)
    med_vec = statistics.median(vec_samples)
    med_legacy = statistics.median(legacy_samples)
    speedup = min_legacy / min_vec
    print(
        f"\nstrategy-eval 4k bars: vectorized min={min_vec:.3f}s "
        f"(median={med_vec:.3f}s) vs legacy min={min_legacy:.3f}s "
        f"(median={med_legacy:.3f}s) → speedup={speedup:.2f}x "
        f"(required ≥{_REQUIRED_SPEEDUP}x)"
    )
    assert speedup >= _REQUIRED_SPEEDUP, (
        f"vectorized strategy-eval regression: min {min_vec:.3f}s vs "
        f"legacy min {min_legacy:.3f}s = {speedup:.2f}x speedup, "
        f"below the {_REQUIRED_SPEEDUP}x gate at {_BARS} bars. "
        f"(medians: vec={med_vec:.3f}s legacy={med_legacy:.3f}s)"
    )


@pytest.mark.parametrize("sample", [0, 1, 6, 13])
@pytest.mark.parametrize("damage", ["missing", "truncated", "empty", "divergent"])
def test_perf_gate_rejects_invalid_sample(monkeypatch, candles_4k, sample, damage) -> None:
    reference = evaluate_symbol(
        symbol="TEST", candles=candles_4k, interval="5m",
        entry_strategy=_entry(), exit_strategy=_exit(),
        starting_cash=100_000.0, cost_model=CostModel(), use_vectorized=False,
    )
    broken = copy.deepcopy(reference)
    if damage == "missing":
        broken = None
    elif damage == "truncated":
        broken.equity_curve.pop()
    elif damage == "empty":
        broken.fills.clear()
        broken.post_trades.clear()
    else:
        broken.final_cash += 1.0
    samples = iter(broken if i == sample else reference for i in range(2 * _TIMING_RUNS))
    ticks = iter([0.0, 1.0, 0.0, 2.0] * _TIMING_RUNS)
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "evaluate_symbol", lambda **kwargs: next(samples))
    monkeypatch.setattr(module, "time", SimpleNamespace(perf_counter=lambda: next(ticks)))
    with pytest.raises(AssertionError):
        test_vectorized_eval_beats_legacy(candles_4k)
