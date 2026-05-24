"""Tests for evaluator's per-bar cancel-token polling.

``evaluate_symbol`` now accepts an optional ``cancel_token`` and polls
``cancel_token.is_cancelled()`` every 256 bars on the hot per-bar loop. This
lets a user clicking Stop mid-Run see evaluation halt within tens of ms
even on multi-tens-of-thousands-of-bar symbols (e.g. 1 year of 5m bars
≈ 25k bars).

Pinned contracts:

1. A token that flips True early causes early termination — the returned
   ``SessionResult.equity_curve`` is shorter than ``len(candles)``.
2. With ``cancel_token=None`` the loop is unchanged (regression for the
   perf-critical path).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    ShareRounding,
    SizingKind,
    SizingRule,
)
from tradinglab.entries.model import TriggerKind as EntryTriggerKind
from tradinglab.entries.model import Universe as EntryUniverse
from tradinglab.exits.model import (
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
)
from tradinglab.exits.model import TriggerKind as ExitTriggerKind
from tradinglab.models import Candle
from tradinglab.strategy_tester import CostModel
from tradinglab.strategy_tester.evaluator import evaluate_symbol

_ET = ZoneInfo("America/New_York")


def _rth_candles(n: int) -> list[Candle]:
    """Build N tz-aware ET RTH-aligned 1m candles starting at Mon 09:35 ET."""
    out: list[Candle] = []
    t = datetime(2024, 6, 3, 9, 35, tzinfo=_ET)  # Monday
    p = 100.0
    for i in range(n):
        out.append(Candle(
            date=t + timedelta(minutes=i),
            open=p, high=p + 0.2, low=p - 0.1, close=p + 0.1,
            volume=1000, session="regular",
        ))
        p += 0.01
    return out


def _entry() -> EntryStrategy:
    return EntryStrategy(
        id="e1", name="market",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("X",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=1.0,
                          share_rounding=ShareRounding.DOWN),
        max_fires_per_session_per_symbol=1,
        require_market_open=False,
    )


def _exit() -> ExitStrategy:
    return ExitStrategy(
        id="x1", name="stop",
        legs=[ExitLeg(id="leg1", triggers=[
            ExitTrigger(kind=ExitTriggerKind.STOP, offset_pct=99.0,
                        qty_pct=100.0),
        ])],
    )


class _CountingCancelToken:
    """Reports cancelled=True after ``threshold`` is_cancelled() calls."""

    def __init__(self, threshold: int):
        self.threshold = threshold
        self.calls = 0

    def is_cancelled(self) -> bool:
        self.calls += 1
        return self.calls > self.threshold


def test_cancel_token_short_circuits_loop():
    n = 2000  # spans ~7 cancel-poll windows (every 256 bars)
    candles = _rth_candles(n)
    # Flip True after the first poll → loop exits at bar 256 or thereabouts.
    token = _CountingCancelToken(threshold=1)

    result = evaluate_symbol(
        symbol="X",
        candles=candles,
        interval="1m",
        entry_strategy=_entry(),
        exit_strategy=_exit(),
        starting_cash=10_000.0,
        cost_model=CostModel(),
        cancel_token=token,
    )

    assert len(result.equity_curve) < n, (
        f"cancel must terminate the loop early (got {len(result.equity_curve)} of {n})"
    )
    assert token.calls >= 1


def test_no_cancel_token_runs_to_completion():
    n = 600
    candles = _rth_candles(n)

    result = evaluate_symbol(
        symbol="X",
        candles=candles,
        interval="1m",
        entry_strategy=_entry(),
        exit_strategy=_exit(),
        starting_cash=10_000.0,
        cost_model=CostModel(),
        cancel_token=None,
    )

    # Engine emits one equity curve entry per bar.
    assert len(result.equity_curve) == n


def test_token_that_never_trips_runs_to_completion():
    n = 600
    candles = _rth_candles(n)

    class _AlwaysFalse:
        def is_cancelled(self) -> bool:
            return False

    result = evaluate_symbol(
        symbol="X",
        candles=candles,
        interval="1m",
        entry_strategy=_entry(),
        exit_strategy=_exit(),
        starting_cash=10_000.0,
        cost_model=CostModel(),
        cancel_token=_AlwaysFalse(),
    )

    assert len(result.equity_curve) == n


def test_broken_token_does_not_break_loop():
    """A token whose ``is_cancelled`` raises must not abort evaluation."""

    class _Broken:
        def is_cancelled(self) -> bool:
            raise RuntimeError("broken")

    n = 600
    candles = _rth_candles(n)
    result = evaluate_symbol(
        symbol="X",
        candles=candles,
        interval="1m",
        entry_strategy=_entry(),
        exit_strategy=_exit(),
        starting_cash=10_000.0,
        cost_model=CostModel(),
        cancel_token=_Broken(),
    )
    assert len(result.equity_curve) == n
