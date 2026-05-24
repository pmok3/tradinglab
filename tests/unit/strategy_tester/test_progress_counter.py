"""Tests for TestRun progress-counter semantics (symbol_count_done / _total).

``TestRun.symbol_count_done`` is the counter that feeds the progress bar:
the runner increments it after each symbol completes and fires the
``progress`` callback with the updated ``TestRun``.  These tests verify
both the default field values and the runner's per-symbol increment
behaviour.
"""
from __future__ import annotations

from datetime import datetime, timedelta

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
from tradinglab.strategy_tester import (
    AcceptanceToken,
    CostModel,
    DatePreset,
    RunStatus,
    TestConfig,
    TestRun,
    UniverseKind,
    UniverseSpec,
)
from tradinglab.strategy_tester import run as run_test

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(symbols: tuple[str, ...] = ("AAPL",)) -> TestConfig:
    return TestConfig(
        entry_strategy_id="e1",
        exit_strategy_id="x1",
        universe=UniverseSpec(kind=UniverseKind.SYMBOLS, symbols=symbols),
        start_date="2020-01-01",
        end_date="2030-01-01",
        interval="5m",
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0),
        date_preset=DatePreset.CUSTOM,
    )


def _entry() -> EntryStrategy:
    return EntryStrategy(
        id="e1",
        name="market",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("A", "B", "C")),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY,
            qty=10.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )


def _exit() -> ExitStrategy:
    return ExitStrategy(
        id="x1",
        name="stop",
        legs=[
            ExitLeg(
                id="leg1",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.STOP,
                        offset_pct=5.0,
                        qty_pct=100.0,
                    )
                ],
            )
        ],
        eod_kill_switch=True,
    )


def _ramp(n: int = 30) -> list[Candle]:
    out: list[Candle] = []
    t = datetime(2024, 6, 1, 9, 30)
    p = 100.0
    for _ in range(n):
        out.append(
            Candle(
                date=t, open=p, high=p + 0.1, low=p - 0.1,
                close=p + 0.05, volume=1000, session="regular",
            )
        )
        p += 0.5
        t += timedelta(minutes=5)
    return out


# ---------------------------------------------------------------------------
# Model defaults
# ---------------------------------------------------------------------------


def test_symbol_count_done_default_zero() -> None:
    """TestRun.symbol_count_done must default to 0."""
    run = TestRun(run_id="abc", config=_cfg())
    assert run.symbol_count_done == 0


def test_symbol_count_total_default_zero() -> None:
    """TestRun.symbol_count_total must default to 0."""
    run = TestRun(run_id="abc", config=_cfg())
    assert run.symbol_count_total == 0


def test_round_trip_preserves_counts() -> None:
    """symbol_count_done / _total survive to_dict / from_dict round-trips."""
    cfg = _cfg(("A", "B", "C"))
    run = TestRun(
        run_id="abc",
        config=cfg,
        symbol_count_total=3,
        symbol_count_done=2,
    )
    revived = TestRun.from_dict(run.to_dict())
    assert revived.symbol_count_done == 2
    assert revived.symbol_count_total == 3


# ---------------------------------------------------------------------------
# Runner increment behaviour
# ---------------------------------------------------------------------------


def test_runner_increments_symbol_count_done(monkeypatch, tmp_path) -> None:
    """Runner increments symbol_count_done by 1 after each symbol finishes."""
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))

    snapshots: list[int] = []

    def _progress(test_run: TestRun) -> None:
        snapshots.append(test_run.symbol_count_done)

    result = run_test(
        _cfg(("A", "B", "C")),
        candles_fetcher=lambda sym, interval: _ramp(),
        entry_loader=lambda sid: _entry(),
        exit_loader=lambda sid: _exit(),
        max_workers=1,
        progress=_progress,
    )

    assert result.test_run.status is RunStatus.DONE
    assert result.test_run.symbol_count_total == 3
    assert result.test_run.symbol_count_done == 3

    # Intermediate values 1, 2, 3 must each appear in the snapshots.
    assert 1 in snapshots
    assert 2 in snapshots
    assert 3 in snapshots


def test_progress_callback_always_sees_correct_total(monkeypatch, tmp_path) -> None:
    """Every progress callback receives symbol_count_total == len(symbols)."""
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))

    totals: list[int] = []

    def _progress(test_run: TestRun) -> None:
        totals.append(test_run.symbol_count_total)

    run_test(
        _cfg(("A", "B")),
        candles_fetcher=lambda sym, interval: _ramp(),
        entry_loader=lambda sid: _entry(),
        exit_loader=lambda sid: _exit(),
        max_workers=1,
        progress=_progress,
    )

    assert totals, "progress must be called at least once"
    assert all(t == 2 for t in totals), f"unexpected totals: {totals}"


def test_done_count_matches_total_on_successful_run(monkeypatch, tmp_path) -> None:
    """After a DONE run, symbol_count_done == symbol_count_total."""
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))

    result = run_test(
        _cfg(("X", "Y", "Z")),
        candles_fetcher=lambda sym, interval: _ramp(),
        entry_loader=lambda sid: _entry(),
        exit_loader=lambda sid: _exit(),
        max_workers=1,
    )

    assert result.test_run.status is RunStatus.DONE
    assert result.test_run.symbol_count_done == result.test_run.symbol_count_total


def test_cancelled_run_reports_partial_done(monkeypatch, tmp_path) -> None:
    """A cancelled run records however many symbols completed before cancel."""
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))

    token = AcceptanceToken()

    call_count = 0

    def _counting_fetcher(sym: str, interval: str) -> list[Candle]:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            token.cancel()
        return _ramp()

    result = run_test(
        _cfg(("A", "B", "C", "D")),
        candles_fetcher=_counting_fetcher,
        entry_loader=lambda sid: _entry(),
        exit_loader=lambda sid: _exit(),
        cancel_token=token,
        max_workers=1,
    )

    assert result.test_run.status is RunStatus.CANCELLED
    # At least one symbol finished (the one that triggered cancel).
    assert result.test_run.symbol_count_done >= 1
    # Can't have done more than total.
    assert result.test_run.symbol_count_done <= result.test_run.symbol_count_total
