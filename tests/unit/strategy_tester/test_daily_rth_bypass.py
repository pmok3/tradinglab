"""Daily-interval RTH-filter regression — the MSFT 3/8 EMA zero-trades bug.

User report: zero trades on MSFT 1d with the canonical 3/8 EMA cross
entry over 5y, despite ~70 visible cross-aboves on the chart.

Root cause discovered by the investigation agent (see plan.md
checkpoint): yfinance daily candles are timestamped at 00:00 ET.
``runner._filter_rth_only`` drops every bar outside 09:30-16:00 ET,
which means 100 % of daily candles get filtered. Same for the
evaluator-side ``require_market_open`` + ``arm_window`` gates.

Fix: gate all three behind ``is_intraday(interval)``. Daily / weekly /
monthly bars bypass the RTH machinery entirely (the concept of
"regular trading hours" is meaningless for a bar that summarises
a whole session).

This file pins:

1. ``_prepare_fetched_candles`` keeps every bar for 1d/1wk/1mo even
   with ``include_extended_hours=False``.
2. ``_filter_rth_only`` still works on intraday bars (drops a
   00:00 ET bar, keeps a 10:00 ET bar).
3. ``evaluator._check_entry`` no longer rejects daily bars on
   ``require_market_open`` or ``arm_window`` when the caller threads
   ``interval="1d"``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest


@pytest.fixture
def synthetic_daily_bars():
    """Build a 250-day MSFT-like ramp + waves.

    Each bar is timestamped at 00:00 UTC of a successive Monday-Friday
    weekday (so the strategy-tester sees them as Mon-Fri daily candles
    with ``00:00 ET`` wall-clock).
    """
    from tradinglab.models import Candle

    bars: list[Candle] = []
    start = datetime(2022, 1, 3, 0, 0, tzinfo=timezone.utc)  # Monday
    day = 0
    while len(bars) < 250:
        dt = start + timedelta(days=day)
        day += 1
        if dt.weekday() >= 5:  # skip Sat/Sun
            continue
        base = 100.0 + (len(bars) * 0.5)
        close = base + 5.0 * np.sin(len(bars) * 0.35)
        open_ = close + 0.1
        high = close + 1.0
        low = close - 1.0
        volume = 1_000_000
        bars.append(Candle(
            date=dt, open=open_, high=high, low=low, close=close,
            volume=volume, session="regular",
        ))
    return bars


def test_filter_rth_only_keeps_all_daily_bars(synthetic_daily_bars):
    """Daily candles at 00:00 ET must NOT be dropped by the RTH filter.

    Before the fix, this returned 0 candles for any daily series —
    a 100 % silent drop that produced "zero trades" for every
    daily-timeframe strategy tester run.
    """
    from tradinglab.strategy_tester.runner import _prepare_fetched_candles

    start = synthetic_daily_bars[0].date.date()
    end = synthetic_daily_bars[-1].date.date()
    out = _prepare_fetched_candles(
        synthetic_daily_bars,
        fetch_start_date=start,
        end_date=end,
        include_extended_hours=False,
        interval="1d",
    )
    assert len(out) == len(synthetic_daily_bars), (
        f"daily RTH filter must keep all bars; got {len(out)} of "
        f"{len(synthetic_daily_bars)}"
    )


@pytest.mark.parametrize("interval", ["1d", "1wk", "1mo"])
def test_filter_rth_only_keeps_all_non_intraday(synthetic_daily_bars, interval):
    """1d / 1wk / 1mo all bypass the intraday RTH gate."""
    from tradinglab.strategy_tester.runner import _prepare_fetched_candles

    start = synthetic_daily_bars[0].date.date()
    end = synthetic_daily_bars[-1].date.date()
    out = _prepare_fetched_candles(
        synthetic_daily_bars,
        fetch_start_date=start,
        end_date=end,
        include_extended_hours=False,
        interval=interval,
    )
    assert len(out) == len(synthetic_daily_bars), (
        f"{interval}: filter must keep all {len(synthetic_daily_bars)} "
        f"bars; got {len(out)}"
    )


def test_prepare_fetched_keeps_filter_active_for_intraday():
    """Sanity: 5m bars still get filtered on the intraday code path."""
    from datetime import timezone as _tz

    from tradinglab.models import Candle
    from tradinglab.strategy_tester.runner import _prepare_fetched_candles

    # Mix one premarket bar (04:00 ET = 09:00 UTC summer-DST) + one
    # RTH bar (09:30 ET = 13:30 UTC).
    pre = Candle(
        date=datetime(2024, 6, 3, 8, 0, tzinfo=_tz.utc),  # 04:00 ET
        open=100.0, high=101.0, low=99.0, close=100.5,
        volume=1_000, session="pre",
    )
    rth = Candle(
        date=datetime(2024, 6, 3, 13, 30, tzinfo=_tz.utc),  # 09:30 ET
        open=100.5, high=101.5, low=99.5, close=101.0,
        volume=2_000, session="regular",
    )
    out = _prepare_fetched_candles(
        [pre, rth],
        fetch_start_date=pre.date.date(),
        end_date=rth.date.date(),
        include_extended_hours=False,
        interval="5m",
    )
    # Premarket dropped, RTH kept.
    assert len(out) == 1
    assert out[0] is rth


def test_check_entry_skips_market_open_gate_on_daily(synthetic_daily_bars):
    """On 1d, require_market_open must be auto-skipped.

    Before the fix, a daily candle whose ET timestamp falls outside
    09:30-16:00 (i.e. all of them — they're at 00:00 ET) was rejected
    by ``_check_entry``'s require_market_open gate, producing zero
    fires on every daily-timeframe template that left the flag at
    its default ``True``.
    """
    from tradinglab.entries.model import (
        EntryStrategy,
        EntryTrigger,
        SizingKind,
        SizingRule,
        TriggerKind,
    )
    from tradinglab.strategy_tester.evaluator import EvalContext, _check_entry

    strategy = EntryStrategy(
        name="test-daily-market",
        trigger=EntryTrigger(kind=TriggerKind.MARKET),
        require_market_open=True,
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=100.0),
    )
    # ExitStrategy is required by EvalContext but not exercised here.
    from tradinglab.exits.model import ExitStrategy

    ctx = EvalContext(
        symbol="MSFT",
        entry_strategy=strategy,
        exit_strategy=ExitStrategy(name="noop"),
        starting_cash=100_000.0,
    )

    bar = synthetic_daily_bars[100]
    bar_tuple = (
        float(bar.open), float(bar.high), float(bar.low), float(bar.close),
    )
    # Simulate the runner's call site: an explicitly is_rth=False
    # signal (because the bar is at 00:00 ET) plus interval="1d".
    fired, _side, qty = _check_entry(
        ctx, bar_tuple,
        bar_ts=int(bar.date.timestamp()),
        et_now=None,
        is_rth=False,    # would block before the fix
        interval="1d",   # NEW: caller threads interval so the
                         # evaluator can skip intraday-only gates.
    )
    assert fired is True, (
        "1d MARKET-kind entry must fire — require_market_open gate "
        "must auto-skip on non-intraday intervals"
    )
    assert qty > 0.0


def test_check_entry_still_enforces_market_open_on_intraday():
    """On 5m, require_market_open MUST still reject non-RTH bars."""
    from tradinglab.entries.model import (
        EntryStrategy,
        EntryTrigger,
        TriggerKind,
    )
    from tradinglab.exits.model import ExitStrategy
    from tradinglab.strategy_tester.evaluator import EvalContext, _check_entry

    strategy = EntryStrategy(
        name="test-intraday-market",
        trigger=EntryTrigger(kind=TriggerKind.MARKET),
        require_market_open=True,
    )
    ctx = EvalContext(
        symbol="MSFT",
        entry_strategy=strategy,
        exit_strategy=ExitStrategy(name="noop"),
        starting_cash=100_000.0,
    )
    bar_tuple = (100.0, 101.0, 99.0, 100.5)
    fired, _side, _qty = _check_entry(
        ctx, bar_tuple,
        bar_ts=1717419600,  # arbitrary
        et_now=None,
        is_rth=False,
        interval="5m",
    )
    assert fired is False, (
        "5m intraday entry with is_rth=False must still be blocked by "
        "require_market_open — the gate is intraday-only, not removed"
    )

