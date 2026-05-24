"""Tests for the RTH-only candle filter in strategy_tester.runner.

Verifies that ``TestConfig.include_extended_hours`` correctly toggles
the premarket / postmarket filter. Default ``False`` drops extended-
hours bars before they reach the evaluator (so indicators don't get
skewed by 04:00-09:30 ET / 16:00-20:00 ET prints).
"""

from __future__ import annotations

import datetime as _dt
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
from tradinglab.strategy_tester.runner import _filter_rth_only

_ET = ZoneInfo("America/New_York")


def _bars_monday_06_to_18(n: int = 12) -> list[Candle]:
    """12 5-min bars from 06:00 ET → ~17:00 ET on Monday 2026-01-05.

    Layout (intervals):
      06:00, 07:00, 08:00, 09:00   → 4 premarket bars
      09:30, 10:30, 11:30, 12:30, 13:30, 14:30, 15:30 → 7 RTH bars (09:30-16:00 inclusive)
      16:30 → 1 postmarket bar
    """
    out: list[Candle] = []
    # Spaced 1 hour apart for deterministic indexing.
    t = datetime(2026, 1, 5, 6, 0, tzinfo=_ET)
    for i in range(n):
        op = 100.0 + i
        cl = op + 0.5
        out.append(
            Candle(
                date=t, open=op, high=cl + 0.1, low=op - 0.1,
                close=cl, volume=1000, session="regular",
            )
        )
        # +30 min for the second bar so we land exactly at 09:30 ET RTH open.
        t = t + (timedelta(minutes=30) if i == 3 else timedelta(hours=1))
    return out


def _bars_saturday(n: int = 6) -> list[Candle]:
    """N bars all on Saturday 2026-01-03 inside 09:30-16:00 ET — no RTH session."""
    out: list[Candle] = []
    t = datetime(2026, 1, 3, 10, 0, tzinfo=_ET)
    for i in range(n):
        op = 100.0 + i
        cl = op + 0.5
        out.append(Candle(date=t, open=op, high=cl + 0.1, low=op - 0.1,
                          close=cl, volume=1000, session="regular"))
        t = t + timedelta(minutes=30)
    return out


def _entry() -> EntryStrategy:
    return EntryStrategy(
        id="e1", name="market",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TST",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=10.0,
                          share_rounding=ShareRounding.DOWN),
        max_fires_per_session_per_symbol=1,
        require_market_open=False,
    )


def _exit_no_eod() -> ExitStrategy:
    return ExitStrategy(
        id="x1", name="hold",
        legs=[ExitLeg(id="leg1", triggers=[
            ExitTrigger(kind=ExitTriggerKind.STOP, offset_pct=99.0, qty_pct=100.0),
        ])],
        eod_kill_switch=False,
    )


def _cfg(*, include_ext: bool) -> TestConfig:
    return TestConfig(
        entry_strategy_id="e1",
        exit_strategy_id="x1",
        universe=UniverseSpec(kind=UniverseKind.SYMBOLS, symbols=("TST",)),
        start_date="2026-01-01",
        end_date="2026-01-31",
        interval="5m",
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0),
        date_preset=DatePreset.CUSTOM,
        include_extended_hours=include_ext,
    )


# --- Unit-level filter --------------------------------------------------


def test_filter_rth_only_drops_premarket_and_postmarket() -> None:
    bars = _bars_monday_06_to_18(12)
    filtered = _filter_rth_only(bars)
    # 7 RTH bars: 09:30, 10:30, 11:30, 12:30, 13:30, 14:30, 15:30 ET
    assert len(filtered) == 7
    for c in filtered:
        et = c.date.astimezone(_ET)
        assert et.weekday() < 5
        assert _dt.time(9, 30) <= et.time() <= _dt.time(16, 0)


def test_filter_rth_only_saturday_returns_empty() -> None:
    assert _filter_rth_only(_bars_saturday(6)) == []


# --- Runner integration -------------------------------------------------


def test_runner_rth_only_default_drops_extended_hours_bars() -> None:
    """Default include_extended_hours=False → evaluator sees only RTH bars."""
    bars = _bars_monday_06_to_18(12)

    def fetcher(_sym: str, _interval: str) -> list[Candle]:
        return list(bars)

    cfg = _cfg(include_ext=False)
    result = run_test(
        cfg,
        candles_fetcher=fetcher,
        entry_loader=lambda _id: _entry(),
        exit_loader=lambda _id: _exit_no_eod(),
        max_workers=1,
    )

    # 7 RTH bars → SessionResult equity curve length == 7.
    import tradinglab.strategy_tester.storage as storage  # noqa: PLC0415

    per_sym = storage.load_session_result_for_symbol(result.run_dir, "TST")
    assert per_sym is not None
    assert len(per_sym.equity_curve) == 7


def test_runner_include_extended_hours_uses_all_bars() -> None:
    """include_extended_hours=True → evaluator sees every bar."""
    bars = _bars_monday_06_to_18(12)

    def fetcher(_sym: str, _interval: str) -> list[Candle]:
        return list(bars)

    cfg = _cfg(include_ext=True)
    result = run_test(
        cfg,
        candles_fetcher=fetcher,
        entry_loader=lambda _id: _entry(),
        exit_loader=lambda _id: _exit_no_eod(),
        max_workers=1,
    )

    import tradinglab.strategy_tester.storage as storage  # noqa: PLC0415

    per_sym = storage.load_session_result_for_symbol(result.run_dir, "TST")
    assert per_sym is not None
    assert len(per_sym.equity_curve) == 12


def test_runner_saturday_only_with_rth_filter_does_not_crash() -> None:
    """Saturday-only candles → 0 bars after RTH filter; no crash, empty result."""
    bars = _bars_saturday(6)

    def fetcher(_sym: str, _interval: str) -> list[Candle]:
        return list(bars)

    cfg = _cfg(include_ext=False)
    result = run_test(
        cfg,
        candles_fetcher=fetcher,
        entry_loader=lambda _id: _entry(),
        exit_loader=lambda _id: _exit_no_eod(),
        max_workers=1,
    )

    # Worker should have succeeded (no exception) with zero trades.
    assert len(result.outcomes) == 1
    assert result.outcomes[0].ok is True
    assert result.outcomes[0].trade_count == 0
