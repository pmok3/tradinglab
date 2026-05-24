"""Regression tests for the EOD kill switch RTH-only constraint.

The ``eod_kill_switch`` must synthesise flatten fills on **regular-session
bars only** (Mon-Fri, 09:30-16:00 ET). If the input candle stream contains
postmarket bars (e.g. 1m yfinance data extending to 20:00 ET), the kill
previously landed on the last bar of the day at the postmarket close —
producing incorrect P&L and screenshots dated at extended-hours prices.

See landmine CLAUDE.md §7.12 and ``evaluator._find_last_rth_bar_at_or_before``.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

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
from tradinglab.strategy_tester import CostModel, evaluate_symbol

_ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Candle factories
# ---------------------------------------------------------------------------


def _build_candles(
    *,
    day: datetime,
    start_time: time,
    end_time: time,
    step_minutes: int = 5,
    start_price: float = 100.0,
    price_step: float = 0.1,
) -> list[Candle]:
    """Build linearly-rising candles for ``day`` between ``start_time`` and
    ``end_time`` (both ET, inclusive)."""
    out: list[Candle] = []
    t = day.replace(
        hour=start_time.hour, minute=start_time.minute, second=0, microsecond=0,
        tzinfo=_ET,
    )
    end_dt = day.replace(
        hour=end_time.hour, minute=end_time.minute, second=0, microsecond=0,
        tzinfo=_ET,
    )
    price = start_price
    while t <= end_dt:
        op = price
        cl = price + price_step
        hi = max(op, cl) + 0.05
        lo = min(op, cl) - 0.05
        out.append(Candle(date=t, open=op, high=hi, low=lo, close=cl,
                          volume=1000, session="regular"))
        price = cl
        t = t + timedelta(minutes=step_minutes)
    return out


def _market_long_strategy(*, require_market_open: bool = True,
                           arm_start: str = "09:35",
                           arm_end: str = "15:30") -> EntryStrategy:
    s = EntryStrategy(
        id="entry-eod-pm",
        name="Market Long",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY, qty=5.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )
    s.require_market_open = require_market_open
    s.arm_window_start = arm_start
    s.arm_window_end = arm_end
    return s


def _eod_only_exit() -> ExitStrategy:
    return ExitStrategy(
        id="exit-eod-only",
        name="EOD only",
        legs=[],
        eod_kill_switch=True,
    )


def _time_of_day_exit(cutoff: str) -> ExitStrategy:
    return ExitStrategy(
        id="exit-tod",
        name="time-of-day",
        legs=[
            ExitLeg(
                id="leg-tod",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.TIME_OF_DAY,
                        time_of_day=cutoff,
                        qty_pct=100.0,
                    ),
                ],
            ),
        ],
        eod_kill_switch=False,
    )


def _et_hhmm(fill_ts: float) -> tuple[int, int]:
    dt = datetime.fromtimestamp(fill_ts, tz=_ET)
    return (dt.hour, dt.minute)


# ---------------------------------------------------------------------------
# Test 1: per-day kill prefers RTH close over postmarket
# ---------------------------------------------------------------------------


def test_per_day_eod_kill_lands_on_rth_close_not_postmarket() -> None:
    """Monday 09:30→19:55 ET + Tuesday 09:30→11:00 ET, eod_kill_switch=True.

    The Tuesday-rollover per-day kill MUST flatten at Monday's 16:00 ET bar,
    NOT at Monday's 19:55 ET postmarket bar.
    """
    monday = datetime(2026, 1, 5)
    tuesday = datetime(2026, 1, 6)
    candles = (
        _build_candles(day=monday, start_time=time(9, 30), end_time=time(19, 55))
        + _build_candles(day=tuesday, start_time=time(9, 30), end_time=time(11, 0))
    )

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=_market_long_strategy(),
        exit_strategy=_eod_only_exit(),
        starting_cash=1_000_000.0,
        cost_model=CostModel(),
    )
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert sells, "expected at least one EOD-kill SELL fill"

    # The first SELL is the per-day kill at Monday's rollover.
    per_day_sell = sells[0]
    hh, mm = _et_hhmm(float(per_day_sell.fill_ts))
    # The bar selected MUST be a Monday RTH bar (≤ 16:00 ET).
    assert (hh, mm) <= (16, 0), (
        f"per-day EOD kill landed at {hh:02d}:{mm:02d} ET (postmarket); "
        "expected a regular-session bar at ≤ 16:00 ET"
    )
    # Sanity: confirm it's actually on Monday, not Tuesday.
    et_dt = datetime.fromtimestamp(float(per_day_sell.fill_ts), tz=_ET)
    assert et_dt.date() == monday.date(), (
        f"per-day kill timestamp {et_dt} is not on Monday {monday.date()}"
    )


# ---------------------------------------------------------------------------
# Test 2: end-of-run kill prefers RTH close over postmarket
# ---------------------------------------------------------------------------


def test_end_of_run_eod_kill_lands_on_rth_close_not_postmarket() -> None:
    """Monday 09:30 → 19:55 ET only, position open at end-of-run.

    The end-of-run kill MUST flatten at a Monday RTH bar (≤ 16:00 ET),
    NOT at the very last bar at 19:55 ET.
    """
    monday = datetime(2026, 1, 5)
    candles = _build_candles(day=monday, start_time=time(9, 30), end_time=time(19, 55))

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=_market_long_strategy(),
        exit_strategy=_eod_only_exit(),
        starting_cash=1_000_000.0,
        cost_model=CostModel(),
    )
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert len(sells) == 1, f"expected exactly 1 end-of-run SELL, got {len(sells)}"

    hh, mm = _et_hhmm(float(sells[0].fill_ts))
    assert (hh, mm) <= (16, 0), (
        f"end-of-run EOD kill landed at {hh:02d}:{mm:02d} ET (postmarket); "
        "expected a regular-session bar at ≤ 16:00 ET"
    )


# ---------------------------------------------------------------------------
# Test 3: no RTH bars → kill silently skipped, no crash
# ---------------------------------------------------------------------------


def test_eod_kill_skipped_when_no_rth_bars_exist() -> None:
    """Only premarket bars (04:00-09:25 ET). With an entry that fires
    pre-market (require_market_open=False, blank arm window), a position
    is opened but the timeline contains zero RTH bars. The end-of-run kill
    must skip cleanly (no exit, no exception)."""
    monday = datetime(2026, 1, 5)
    candles = _build_candles(day=monday, start_time=time(4, 0), end_time=time(9, 25))

    entry = _market_long_strategy(
        require_market_open=False, arm_start="", arm_end="",
    )

    # Should not raise.
    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=_eod_only_exit(),
        starting_cash=1_000_000.0,
        cost_model=CostModel(),
    )
    buys = [f for f in result.fills if f.side.value == "buy"]
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert buys, "sanity: expected at least one BUY in premarket window"
    assert sells == [], (
        f"expected NO SELL fills when no RTH bars exist (kill must skip); "
        f"got {len(sells)} SELL fills at {[_et_hhmm(float(f.fill_ts)) for f in sells]}"
    )


# ---------------------------------------------------------------------------
# Test 4: RTH-only data — backwards compatibility
# ---------------------------------------------------------------------------


def test_eod_kill_unchanged_for_rth_only_data() -> None:
    """RTH-only data (09:35-15:30 ET): the fix must NOT change behaviour.
    The end-of-run kill should land on the very last bar (which is also
    the last RTH bar)."""
    monday = datetime(2026, 1, 5)
    candles = _build_candles(day=monday, start_time=time(9, 35), end_time=time(15, 30))

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=_market_long_strategy(),
        exit_strategy=_eod_only_exit(),
        starting_cash=1_000_000.0,
        cost_model=CostModel(),
    )
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert len(sells) == 1, f"expected 1 end-of-run SELL, got {len(sells)}"

    # The fill_ts should equal the last bar's ts (which is 15:30 ET).
    last_bar_ts = int(candles[-1].date.timestamp())
    assert int(sells[0].fill_ts) == last_bar_ts, (
        f"end-of-run kill fill_ts {sells[0].fill_ts} != last RTH bar ts {last_bar_ts}"
    )
    hh, mm = _et_hhmm(float(sells[0].fill_ts))
    assert (hh, mm) == (15, 30)


# ---------------------------------------------------------------------------
# Test 5: TIME_OF_DAY trigger is NOT affected by the RTH-only fix
# ---------------------------------------------------------------------------


def test_time_of_day_exit_fires_in_postmarket_data_unchanged() -> None:
    """The TIME_OF_DAY trigger lives in ``_exit_time_of_day`` (a separate
    code path) and must still fire at its authored time even on postmarket
    bars. This guards against accidental scope-creep of the RTH-only kill
    constraint into the TIME_OF_DAY handler.
    """
    monday = datetime(2026, 1, 5)
    candles = _build_candles(day=monday, start_time=time(9, 30), end_time=time(19, 55))

    # arm window allows postmarket entries so we can verify the TOD exit
    # fires even when the position is opened/held into extended hours.
    entry = _market_long_strategy(
        require_market_open=False, arm_start="", arm_end="",
    )
    # Cutoff at 18:00 ET — well inside postmarket hours.
    exit_strat = _time_of_day_exit("18:00")

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=1_000_000.0,
        cost_model=CostModel(),
    )
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert sells, "expected TIME_OF_DAY exit to fire even on postmarket bars"
    hh, mm = _et_hhmm(float(sells[0].fill_ts))
    # First fill at/after 18:00 ET — postmarket.
    assert (hh, mm) >= (18, 0), (
        f"TIME_OF_DAY exit fired at {hh:02d}:{mm:02d} ET; "
        "expected at/after 18:00 ET cutoff (postmarket)"
    )
