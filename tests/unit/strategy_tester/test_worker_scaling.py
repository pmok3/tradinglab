"""Tests for strategy_tester.runner worker-count scaling.

Covers:
- ``_default_max_workers`` respects the persisted settings tunable.
- ``_default_max_workers`` scales above 4 on high-core machines.
- ``_default_max_workers`` clamps to 64 (upper bound).
- ``_default_max_workers`` returns at least 1 on a single-core host.
- ``runner.run`` forwards the ``max_workers`` kwarg to ``ThreadPoolExecutor``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor as _RealTPE
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

import tradinglab.defaults as _defaults_mod
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
from tradinglab.exits.model import ExitLeg, ExitStrategy, ExitTrigger
from tradinglab.exits.model import TriggerKind as ExitTriggerKind
from tradinglab.models import Candle
from tradinglab.strategy_tester import (
    CostModel,
    DatePreset,
    TestConfig,
    UniverseKind,
    UniverseSpec,
)
from tradinglab.strategy_tester import run as run_test
from tradinglab.strategy_tester.runner import _default_max_workers

# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------


@contextmanager
def _patch_defaults_get(worker_count: int):
    """Replace defaults.get with a stub returning ``worker_count`` for every key."""
    orig = _defaults_mod.get
    _defaults_mod.get = lambda key: worker_count  # type: ignore[assignment]
    try:
        yield
    finally:
        _defaults_mod.get = orig  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ramp(n: int = 10) -> list[Candle]:
    out: list[Candle] = []
    t = datetime(2024, 6, 3, 9, 35)  # Monday — avoids require_market_open gate
    p = 100.0
    for _ in range(n):
        out.append(
            Candle(
                date=t,
                open=p,
                high=p + 0.2,
                low=p - 0.1,
                close=p + 0.1,
                volume=500,
                session="regular",
            )
        )
        p += 0.1
        t += timedelta(minutes=5)
    return out


def _entry() -> EntryStrategy:
    return EntryStrategy(
        id="e1",
        name="market",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("A",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY,
            qty=1.0,
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


# ---------------------------------------------------------------------------
# _default_max_workers tests
# ---------------------------------------------------------------------------


def test_default_max_workers_scales_above_4_on_high_core_machine():
    """16-core machine with no persisted setting should yield 15 (cpu-1), not 4."""
    with _patch_defaults_get(0), patch("os.cpu_count", return_value=16):
        result = _default_max_workers()

    assert result >= 12, f"Expected ≥12 workers on 16-core machine, got {result}"
    assert result != 4, "Hard cap of 4 must no longer apply"


def test_default_max_workers_respects_persisted_setting():
    """Persisted worker_count=12 should yield exactly 12."""
    with _patch_defaults_get(12):
        result = _default_max_workers()

    assert result == 12


def test_default_max_workers_clamps_to_64():
    """cpu_count=200 with no persisted override should clamp to 64."""
    with _patch_defaults_get(0), patch("os.cpu_count", return_value=200):
        result = _default_max_workers()

    assert result <= 64


def test_default_max_workers_clamps_persisted_to_64():
    """Persisted value > 64 should clamp to 64."""
    with _patch_defaults_get(200):
        result = _default_max_workers()

    assert result == 64


def test_default_max_workers_minimum_1():
    """cpu_count=1 with no persisted setting should yield at least 1."""
    with _patch_defaults_get(0), patch("os.cpu_count", return_value=1):
        result = _default_max_workers()

    assert result >= 1


# ---------------------------------------------------------------------------
# runner.run max_workers forwarding test
# ---------------------------------------------------------------------------


def test_runner_run_uses_max_workers_param():
    """runner.run(cfg, max_workers=8) must size the ThreadPoolExecutor to 8."""
    captured: list[int | None] = []

    class _CapturingTPE(_RealTPE):
        def __init__(self, *args, max_workers=None, **kwargs):  # type: ignore[override]
            captured.append(max_workers)
            super().__init__(*args, max_workers=max_workers, **kwargs)

    candles_store: dict[str, list[Candle]] = {"SYM1": _ramp()}
    entry = _entry()
    exit_ = _exit()

    with patch("tradinglab.strategy_tester.runner.ThreadPoolExecutor", _CapturingTPE):
        run_test(
            _cfg(("SYM1",)),
            max_workers=8,
            candles_fetcher=lambda sym, interval: candles_store.get(sym, []),
            entry_loader=lambda sid: entry,
            exit_loader=lambda sid: exit_,
        )

    assert captured, "ThreadPoolExecutor was never instantiated"
    assert captured[0] == 8, f"Expected max_workers=8, got {captured[0]}"
