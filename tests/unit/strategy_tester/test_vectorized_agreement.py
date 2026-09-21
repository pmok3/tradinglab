"""Old-vs-new exact-agreement tests for the vectorized strategy evaluation.

``evaluate_symbol(..., use_vectorized=True)`` (the default) must produce a
**bit-identical** :class:`SessionResult` to the legacy per-bar path
(``use_vectorized=False``): same fills, pre/post trades, equity curve,
final cash, cash/quantity adjustments, decisions, and dtypes.

Covers every entry trigger kind, every exit trigger kind, both
directions, arm-window (incl. midnight wrap + malformed values),
RTH gating, cooldown/caps, STACK policy (vectorized entry, legacy
exits), partial exits, EOD kill switch, NaNs, flat series, single-bar
input, timestamp gaps, warmup mode, and fixed-seed randomized
strategy/candle fuzzing.
"""

from __future__ import annotations

import copy
import math
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    PositionAlreadyOpenPolicy,
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
    TrailUnit,
)
from tradinglab.exits.model import (
    TriggerKind as ExitTriggerKind,
)
from tradinglab.models import Candle
from tradinglab.scanner.model import OP_GT, OP_LT, Condition, FieldRef, Group
from tradinglab.strategy_tester import CostModel, evaluate_symbol

_ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Candle builders
# ---------------------------------------------------------------------------


def _et_candles(
    closes: list[float],
    *,
    start: datetime | None = None,
    step_minutes: int = 5,
    session: str = "regular",
    volume: int = 1000,
) -> list[Candle]:
    """Candles with explicit closes; open=prior close, high/low ±0.5.

    NaN closes are passed through as NaN OHLC (tests NaN handling).
    """
    out: list[Candle] = []
    t = start or datetime(2026, 1, 5, 9, 35, tzinfo=_ET)  # Mon RTH
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i > 0 else c
        if math.isnan(c) or (not math.isnan(prev) and math.isnan(c)):
            op = hi = lo = cl = float("nan")
        else:
            op = float(prev)
            cl = float(c)
            hi = max(op, cl) + 0.5
            lo = min(op, cl) - 0.5
        out.append(
            Candle(date=t, open=op, high=hi, low=lo, close=cl,
                   volume=volume + i, session=session)
        )
        t = t + timedelta(minutes=step_minutes)
    return out


def _random_walk_candles(rng: random.Random, n: int) -> list[Candle]:
    """Seeded random walk with occasional NaNs and timestamp gaps."""
    closes: list[float] = []
    price = 100.0
    t = datetime(2026, 1, 5, 9, 35, tzinfo=_ET)
    out: list[Candle] = []
    for i in range(n):
        r = rng.random()
        if r < 0.03:
            c = float("nan")
        else:
            price += rng.uniform(-1.5, 1.6)
            c = price
        if not math.isnan(c):
            prev = closes[-1] if closes and not math.isnan(closes[-1]) else c
            op = float(prev)
            hi = max(op, c) + rng.uniform(0.0, 0.8)
            lo = min(op, c) - rng.uniform(0.0, 0.8)
        else:
            op = hi = lo = float("nan")
        out.append(
            Candle(date=t, open=op, high=hi, low=lo, close=c,
                   volume=1000 + i, session="regular")
        )
        closes.append(c)
        # Occasional timestamp gap (skipped bars).
        t = t + timedelta(minutes=5 * rng.choice([1, 1, 1, 2, 6]))
    return out


def _multi_day_candles(
    n_days: int = 3, bars_per_day: int = 20, start_price: float = 100.0,
    seed: int = 42,
) -> list[Candle]:
    """Several RTH days (09:35 → intraday), for EOD-kill agreement."""
    rng = random.Random(seed)
    out: list[Candle] = []
    price = start_price
    day = datetime(2026, 1, 5, tzinfo=_ET)  # Monday
    for _ in range(n_days):
        t = day.replace(hour=9, minute=35)
        for _ in range(bars_per_day):
            price += rng.uniform(-0.8, 1.0)
            op = price - rng.uniform(-0.3, 0.3)
            cl = price
            hi = max(op, cl) + 0.3
            lo = min(op, cl) - 0.3
            out.append(
                Candle(date=t, open=op, high=hi, low=lo, close=cl,
                       volume=1000, session="regular")
            )
            t = t + timedelta(minutes=5)
        day = day + timedelta(days=1)
        # Skip weekends.
        while day.weekday() >= 5:
            day = day + timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Strategy builders
# ---------------------------------------------------------------------------


def _sizing() -> SizingRule:
    return SizingRule(
        kind=SizingKind.FIXED_QTY, qty=5.0,
        share_rounding=ShareRounding.DOWN,
    )


def _entry_strategy(
    trigger: EntryTrigger,
    *,
    direction: Direction = Direction.LONG,
    policy: PositionAlreadyOpenPolicy = PositionAlreadyOpenPolicy.BLOCK,
    arm_window_start: str | None = "09:35",
    arm_window_end: str | None = "15:30",
    require_market_open: bool = True,
    cooldown_secs: int = 0,
    max_total: int | None = None,
    max_per_symbol: int = 100,
    enabled: bool = True,
) -> EntryStrategy:
    return EntryStrategy(
        id="entry-agree",
        name="agreement",
        direction=direction,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=trigger,
        sizing=_sizing(),
        position_already_open_policy=policy,
        arm_window_start=arm_window_start,
        arm_window_end=arm_window_end,
        require_market_open=require_market_open,
        cooldown_secs=cooldown_secs,
        max_fires_per_session_total=max_total,
        max_fires_per_session_per_symbol=max_per_symbol,
        enabled=enabled,
    )


def _exit_strategy(legs: list[ExitLeg], *, eod_kill_switch: bool = False) -> ExitStrategy:
    return ExitStrategy(
        id="exit-agree", name="agreement", legs=legs,
        eod_kill_switch=eod_kill_switch,
    )


def _leg(trigger: ExitTrigger, *, leg_id: str = "leg") -> ExitLeg:
    return ExitLeg(id=leg_id, triggers=[trigger])


def _close_gt(threshold: float) -> Group:
    return Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op=OP_GT,
                params={"right": FieldRef(kind="literal", value=float(threshold))},
                interval="5m",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Runner + comparator
# ---------------------------------------------------------------------------


def _run_both(
    *,
    candles: list[Candle],
    entry: EntryStrategy,
    exit: ExitStrategy,
    interval: str = "5m",
    **kwargs,
) -> tuple[object, object]:
    base = dict(
        symbol="TEST",
        candles=candles,
        interval=interval,
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0, commission_per_trade=0.0),
    )
    base.update(kwargs)
    r_new = evaluate_symbol(
        **base,
        entry_strategy=copy.deepcopy(entry),
        exit_strategy=copy.deepcopy(exit),
        use_vectorized=True,
    )
    r_old = evaluate_symbol(
        **base,
        entry_strategy=copy.deepcopy(entry),
        exit_strategy=copy.deepcopy(exit),
        use_vectorized=False,
    )
    return r_new, r_old


def _canon(obj):
    """NaN-aware canonical form for exact result comparison.

    ``float("nan") != float("nan")``, so plain ``==`` on result trees
    reports a false divergence wherever the engine legitimately emits
    NaN (e.g. equity marks on NaN bars). Canonicalising NaN to a
    sentinel keeps the comparison exact for every other value.
    """
    if isinstance(obj, float) and math.isnan(obj):
        return ("__nan__",)
    if isinstance(obj, (list, tuple)):
        return [_canon(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _canon(v) for k, v in obj.items()}
    if hasattr(obj, "__dataclass_fields__"):
        return (
            type(obj).__name__,
            [_canon(getattr(obj, f)) for f in obj.__dataclass_fields__],
        )
    return obj


def _assert_identical(r_new, r_old) -> None:
    c_new, c_old = _canon(r_new), _canon(r_old)
    assert c_new == c_old, (
        "SessionResult differs between vectorized and legacy paths"
    )
    # Field-level messages for the common cases (faster diagnosis).
    assert _canon(r_new.fills) == _canon(r_old.fills), (
        f"fills differ:\n new={r_new.fills}\n old={r_old.fills}"
    )
    assert _canon(r_new.equity_curve) == _canon(r_old.equity_curve), (
        "equity_curve differs"
    )
    assert r_new.final_cash == r_old.final_cash or (
        math.isnan(r_new.final_cash) and math.isnan(r_old.final_cash)
    ), f"final_cash differs: {r_new.final_cash} != {r_old.final_cash}"


# ---------------------------------------------------------------------------
# Entry kinds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry_kind,kwargs",
    [
        (EntryTriggerKind.MARKET, {}),
        (EntryTriggerKind.LIMIT, {"price": 101.5}),
        (EntryTriggerKind.LIMIT, {"price": float("nan")}),
        (EntryTriggerKind.STOP, {"stop_price": 102.5}),
        (EntryTriggerKind.STOP_LIMIT, {"stop_price": 102.0, "price": 101.0}),
    ],
)
@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
def test_entry_kinds_agree(entry_kind, kwargs, direction) -> None:
    closes = [100 + 0.4 * i for i in range(60)]
    candles = _et_candles(closes)
    entry = _entry_strategy(
        EntryTrigger(kind=entry_kind, **kwargs), direction=direction,
    )
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_limit_entry_exact_touch_boundary_agrees() -> None:
    """Boundary inclusion: LONG LIMIT fires when low == price *exactly*.

    Guards the ``<=`` (not ``<``) touch semantics — a perturbed
    comparison must fail this test.
    """
    # Craft bars whose low prints exactly 100.0 on several bars.
    candles: list[Candle] = []
    t = datetime(2026, 1, 5, 9, 35, tzinfo=_ET)
    lows = [101.0, 100.0, 100.5, 100.0, 102.0, 100.0]
    for lo in lows:
        candles.append(
            Candle(date=t, open=lo + 1.0, high=lo + 2.0, low=lo,
                   close=lo + 1.0, volume=1000, session="regular")
        )
        t = t + timedelta(minutes=5)
    entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.LIMIT,
                                         price=100.0))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    r_new, r_old = _run_both(candles=candles, entry=entry, exit=exit)
    _assert_identical(r_new, r_old)
    assert len(r_new.fills) >= 1, "expected the exact-touch limit to fire"


def test_entry_limit_missing_price_never_fires() -> None:
    candles = _et_candles([100 + i for i in range(20)])
    entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.LIMIT))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    r_new, r_old = _run_both(candles=candles, entry=entry, exit=exit)
    _assert_identical(r_new, r_old)
    assert r_new.fills == []


# ---------------------------------------------------------------------------
# Exit kinds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exit_trigger",
    [
        ExitTrigger(kind=ExitTriggerKind.MARKET, qty_pct=100.0),
        ExitTrigger(kind=ExitTriggerKind.STOP, price=95.0, qty_pct=100.0),
        ExitTrigger(kind=ExitTriggerKind.STOP, offset_pct=3.0, qty_pct=100.0),
        ExitTrigger(kind=ExitTriggerKind.STOP, offset_dollar=2.0, qty_pct=100.0),
        ExitTrigger(kind=ExitTriggerKind.LIMIT, price=108.0, qty_pct=100.0),
        ExitTrigger(kind=ExitTriggerKind.LIMIT, offset_pct=4.0, qty_pct=100.0),
        ExitTrigger(kind=ExitTriggerKind.STOP_LIMIT, price=97.0,
                    stop_limit_price=96.5, qty_pct=100.0),
        ExitTrigger(kind=ExitTriggerKind.TIME_OF_DAY, time_of_day="10:30",
                    qty_pct=100.0),
        ExitTrigger(kind=ExitTriggerKind.TIME_OF_DAY, time_of_day="bogus",
                    qty_pct=100.0),
        ExitTrigger(kind=ExitTriggerKind.TRAILING_STOP,
                    trail_unit=TrailUnit.PERCENT, trail_value=5.0,
                    qty_pct=100.0),
        ExitTrigger(kind=ExitTriggerKind.TRAILING_STOP,
                    trail_unit=TrailUnit.DOLLAR, trail_value=2.0,
                    qty_pct=100.0),
    ],
)
@pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
def test_exit_kinds_agree(exit_trigger, direction) -> None:
    # Up then down — exercises both profit-taking and stop legs.
    closes = [100 + i for i in range(15)] + [115 - 2 * i for i in range(15)]
    candles = _et_candles(closes)
    entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.MARKET),
                            direction=direction)
    exit = _exit_strategy([_leg(exit_trigger)])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_exit_indicator_leg_agrees() -> None:
    closes = [100 + 0.5 * i for i in range(40)]
    candles = _et_candles(closes)
    entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.MARKET))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.INDICATOR,
        condition=_close_gt(112.0),  # type: ignore[arg-type]
        qty_pct=100.0,
    ))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_chandelier_exit_agrees() -> None:
    closes = [100, 102, 104, 106, 108, 110, 108, 104, 100, 95, 92, 90]
    candles = _et_candles(closes)
    entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.MARKET))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.CHANDELIER,
        chandelier_lookback=10, chandelier_atr_period=3,
        chandelier_multiplier=2.0, chandelier_ma_type="SMA",
        qty_pct=100.0,
    ))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_multi_leg_exit_order_agrees() -> None:
    """First firing leg wins — leg order must be preserved."""
    closes = [100 + i for i in range(12)] + [112 - 3 * i for i in range(12)]
    candles = _et_candles(closes)
    entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.MARKET))
    exit = _exit_strategy([
        _leg(ExitTrigger(kind=ExitTriggerKind.LIMIT, offset_pct=5.0,
                         qty_pct=100.0), leg_id="leg-limit"),
        _leg(ExitTrigger(kind=ExitTriggerKind.TRAILING_STOP,
                         trail_unit=TrailUnit.PERCENT, trail_value=3.0,
                         qty_pct=100.0), leg_id="leg-trail"),
        _leg(ExitTrigger(kind=ExitTriggerKind.STOP, offset_pct=4.0,
                         qty_pct=100.0), leg_id="leg-stop"),
    ])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_partial_exit_agrees() -> None:
    closes = [100 + i for i in range(20)]
    candles = _et_candles(closes)
    entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.MARKET))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.LIMIT, offset_pct=3.0, qty_pct=50.0))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


# ---------------------------------------------------------------------------
# Indicator / scanner-alert entries
# ---------------------------------------------------------------------------


def test_indicator_entry_agrees() -> None:
    closes = [100 + 0.5 * i for i in range(40)]
    candles = _et_candles(closes)
    entry = _entry_strategy(EntryTrigger(
        kind=EntryTriggerKind.INDICATOR,
        condition=_close_gt(105.0),  # type: ignore[arg-type]
        interval="5m",
    ))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_indicator_cross_entry_agrees() -> None:
    """Crosses exercise the first-bar/shift edge of the vec path."""
    closes = [100 - 0.5 * i for i in range(10)] + [95 + i for i in range(30)]
    candles = _et_candles(closes)
    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op="crosses_above",
                params={"right": FieldRef(kind="literal", value=100.0),
                        "lookback": 1},
                interval="5m",
            ),
        ],
    )
    entry = _entry_strategy(EntryTrigger(
        kind=EntryTriggerKind.INDICATOR,
        condition=cond,  # type: ignore[arg-type]
        interval="5m",
    ))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def _save_scan(scan_id: str, threshold: float, monkeypatch, tmp_path) -> str:
    from tradinglab.scanner import storage as scanner_storage
    from tradinglab.scanner.model import ScanDefinition

    monkeypatch.setattr(scanner_storage, "scans_dir", lambda: tmp_path)
    scan = ScanDefinition(
        id=scan_id,
        name="agree-scan",
        root=_close_gt(threshold),
        primary_interval="5m",
    )
    scanner_storage.save(scan)
    return scan.id


def test_scanner_alert_entry_agrees(monkeypatch, tmp_path) -> None:
    scan_id = _save_scan("agree-scan-fires", 105.0, monkeypatch, tmp_path)
    closes = [100 + 0.5 * i for i in range(40)]
    candles = _et_candles(closes)
    entry = _entry_strategy(EntryTrigger(
        kind=EntryTriggerKind.SCANNER_ALERT, scanner_id=scan_id))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_scanner_alert_no_transition_agrees(monkeypatch, tmp_path) -> None:
    """True from bar 0 → no edge → no fire; prev_match init must agree."""
    scan_id = _save_scan("agree-scan-always", 50.0, monkeypatch, tmp_path)
    closes = [100 + 0.5 * i for i in range(20)]
    candles = _et_candles(closes)
    entry = _entry_strategy(EntryTrigger(
        kind=EntryTriggerKind.SCANNER_ALERT, scanner_id=scan_id))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    r_new, r_old = _run_both(candles=candles, entry=entry, exit=exit)
    _assert_identical(r_new, r_old)
    assert r_new.fills == []


# ---------------------------------------------------------------------------
# Time gates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arm_start,arm_end",
    [
        ("09:35", "15:30"),   # normal window
        ("15:30", "09:35"),   # midnight wrap
        ("09:35", "09:35"),   # single instant
        (None, None),         # disabled
        ("bogus", "15:30"),   # malformed start → disabled
        ("09:35", ""),        # blank end → disabled
    ],
)
def test_arm_window_agrees(arm_start, arm_end) -> None:
    closes = [100 + 0.3 * i for i in range(60)]
    candles = _et_candles(closes)
    entry = _entry_strategy(
        EntryTrigger(kind=EntryTriggerKind.MARKET),
        arm_window_start=arm_start, arm_window_end=arm_end,
        max_per_symbol=3,
    )
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_require_market_open_agrees() -> None:
    # Mix of RTH and extended-hours bars.
    closes = [100 + 0.2 * i for i in range(40)]
    candles = _et_candles(closes, session="extended")
    entry = _entry_strategy(
        EntryTrigger(kind=EntryTriggerKind.MARKET),
        require_market_open=True, max_per_symbol=5,
    )
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_daily_interval_bypasses_time_gates() -> None:
    """Non-intraday intervals skip arm-window/RTH gates exactly like legacy."""
    rng = random.Random(11)
    closes = [100.0]
    for _ in range(30):
        closes.append(closes[-1] + rng.uniform(-2, 2.2))
    start = datetime(2026, 1, 5, tzinfo=_ET)
    candles = [
        Candle(date=start + timedelta(days=i), open=c - 1, high=c + 1,
               low=c - 1.5, close=c, volume=1000, session="regular")
        for i, c in enumerate(closes)
    ]
    entry = _entry_strategy(
        EntryTrigger(kind=EntryTriggerKind.MARKET),
        arm_window_start="09:35", arm_window_end="09:36",
        require_market_open=True, max_per_symbol=2,
    )
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    _assert_identical(
        *_run_both(candles=candles, entry=entry, exit=exit, interval="1d")
    )


# ---------------------------------------------------------------------------
# Caps / cooldown / policy / EOD
# ---------------------------------------------------------------------------


def test_cooldown_and_caps_agree() -> None:
    closes = [100 + 0.3 * i for i in range(120)]
    candles = _et_candles(closes)
    entry = _entry_strategy(
        EntryTrigger(kind=EntryTriggerKind.MARKET),
        cooldown_secs=600, max_total=2, max_per_symbol=100,
    )
    exit = _exit_strategy(
        [_leg(ExitTrigger(kind=ExitTriggerKind.MARKET, qty_pct=100.0))],
    )
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_stack_policy_agrees() -> None:
    """STACK keeps the legacy per-bar exit path; the session must still agree."""
    closes = [100 + 0.5 * i for i in range(40)]
    candles = _et_candles(closes)
    entry = _entry_strategy(
        EntryTrigger(kind=EntryTriggerKind.MARKET),
        policy=PositionAlreadyOpenPolicy.STACK, max_per_symbol=5,
    )
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=10.0, qty_pct=100.0))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_eod_kill_switch_agrees() -> None:
    candles = _multi_day_candles(n_days=3, bars_per_day=20)
    entry = _entry_strategy(
        EntryTrigger(kind=EntryTriggerKind.MARKET), max_per_symbol=100,
    )
    exit = _exit_strategy(
        [_leg(ExitTrigger(kind=ExitTriggerKind.STOP, offset_pct=50.0,
                          qty_pct=100.0))],
        eod_kill_switch=True,
    )
    r_new, r_old = _run_both(candles=candles, entry=entry, exit=exit)
    _assert_identical(r_new, r_old)
    # Net flat after the end-of-run sweep.
    pos = sum(f.quantity if f.side.value == "buy" else -f.quantity
              for f in r_new.fills)
    assert pos == 0.0


# ---------------------------------------------------------------------------
# Edge-case inputs
# ---------------------------------------------------------------------------


def test_single_bar_agrees() -> None:
    candles = _et_candles([100.0])
    entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.MARKET))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_flat_series_agrees() -> None:
    candles = _et_candles([100.0] * 50)
    entry = _entry_strategy(EntryTrigger(
        kind=EntryTriggerKind.INDICATOR,
        condition=_close_gt(100.0),  # type: ignore[arg-type]
        interval="5m",
    ))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.TRAILING_STOP, trail_unit=TrailUnit.PERCENT,
        trail_value=5.0, qty_pct=100.0))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_nan_series_agrees() -> None:
    closes = [100.0, 101.0, float("nan"), 103.0, float("nan"),
              105.0, 106.0, float("nan"), 108.0, 109.0] * 3
    candles = _et_candles(closes)
    entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.LIMIT,
                                         price=104.0))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_nan_indicator_series_agrees() -> None:
    closes = [100.0, float("nan"), 102.0, float("nan"), 104.0,
              105.0, 106.0, 107.0]
    candles = _et_candles(closes)
    entry = _entry_strategy(EntryTrigger(
        kind=EntryTriggerKind.INDICATOR,
        condition=_close_gt(103.0),  # type: ignore[arg-type]
        interval="5m",
    ))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_gaps_agree() -> None:
    rng = random.Random(5)
    candles = _random_walk_candles(rng, 120)  # has gaps + NaNs
    entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.MARKET),
                            max_per_symbol=10)
    exit = _exit_strategy(
        [_leg(ExitTrigger(kind=ExitTriggerKind.MARKET, qty_pct=100.0))],
    )
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_warmup_mode_agrees() -> None:
    candles = _et_candles([100 + 0.4 * i for i in range(60)])
    entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.MARKET))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    warmup_until = int(candles[20].date.timestamp())
    _assert_identical(*_run_both(
        candles=candles, entry=entry, exit=exit,
        warmup_until_ts=warmup_until,
    ))


def test_disabled_entry_agrees() -> None:
    candles = _et_candles([100 + i for i in range(20)])
    entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.MARKET),
                            enabled=False)
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    r_new, r_old = _run_both(candles=candles, entry=entry, exit=exit)
    _assert_identical(r_new, r_old)
    assert r_new.fills == []


# ---------------------------------------------------------------------------
# Fallback surface
# ---------------------------------------------------------------------------


def test_within_last_indicator_falls_back_and_agrees() -> None:
    """Vec-unsupported trees (within-last) take the legacy loop — still identical."""
    closes = [100 + 0.5 * i for i in range(40)]
    candles = _et_candles(closes)
    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op=OP_GT,
                params={"right": FieldRef(kind="literal", value=105.0)},
                interval="5m",
                within_last_bars=3,
            ),
        ],
    )
    entry = _entry_strategy(EntryTrigger(
        kind=EntryTriggerKind.INDICATOR,
        condition=cond,  # type: ignore[arg-type]
        interval="5m",
    ))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    _assert_identical(*_run_both(candles=candles, entry=entry, exit=exit))


def test_unsupported_entry_kind_raises_in_both_paths() -> None:
    from tradinglab.strategy_tester import (
        UnsupportedTriggerKind,
    )
    from tradinglab.strategy_tester import evaluator as st_eval

    candles = _et_candles([100 + i for i in range(5)])
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])

    saved = st_eval._ENTRY_HANDLERS.pop(EntryTriggerKind.MARKET)
    try:
        for use_vec in (True, False):
            entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.MARKET))
            try:
                evaluate_symbol(
                    symbol="TEST", candles=candles, interval="5m",
                    entry_strategy=entry, exit_strategy=exit,
                    starting_cash=100_000.0, cost_model=CostModel(),
                    use_vectorized=use_vec,
                )
                raise AssertionError("expected UnsupportedTriggerKind")
            except UnsupportedTriggerKind as exc:
                assert exc.side == "entry"
    finally:
        st_eval._ENTRY_HANDLERS[EntryTriggerKind.MARKET] = saved


# ---------------------------------------------------------------------------
# Fixed-seed randomized fuzzing
# ---------------------------------------------------------------------------


def _random_condition(rng: random.Random) -> Group:
    """Small vec-supported condition trees (plus occasional unsupported ones)."""
    field = rng.choice(["close", "open", "high", "low"])
    threshold = rng.uniform(95.0, 110.0)
    op = rng.choice([OP_GT, OP_LT, "crosses_above", "crosses_below"])
    params = {"right": FieldRef(kind="literal", value=threshold)}
    if op in ("crosses_above", "crosses_below"):
        params["lookback"] = 1
    leaf = Condition(
        left=FieldRef(kind="builtin", id=field),
        op=op,
        params=params,
        interval="5m",
        within_last_bars=rng.choice([0, 0, 0, 3]),  # sometimes unsupported
    )
    if rng.random() < 0.4:
        field2 = rng.choice(["close", "high"])
        leaf2 = Condition(
            left=FieldRef(kind="builtin", id=field2),
            op=rng.choice([OP_GT, OP_LT]),
            params={"right": FieldRef(kind="literal",
                                      value=rng.uniform(95.0, 110.0))},
            interval="5m",
        )
        return Group(
            combinator=rng.choice(["and", "or"]),
            children=[leaf, leaf2],
        )
    return Group(combinator="and", children=[leaf])


def _random_entry(rng: random.Random) -> EntryStrategy:
    kind = rng.choice(
        [EntryTriggerKind.MARKET, EntryTriggerKind.LIMIT,
         EntryTriggerKind.STOP, EntryTriggerKind.STOP_LIMIT,
         EntryTriggerKind.INDICATOR, EntryTriggerKind.INDICATOR]
    )
    kw: dict = {}
    if kind is EntryTriggerKind.LIMIT:
        kw["price"] = rng.choice([98.0, 102.0, float("nan")])
    elif kind is EntryTriggerKind.STOP:
        kw["stop_price"] = rng.choice([98.0, 103.0])
    elif kind is EntryTriggerKind.STOP_LIMIT:
        kw["stop_price"] = 103.0
        kw["price"] = 101.5
    elif kind is EntryTriggerKind.INDICATOR:
        kw["condition"] = _random_condition(rng)
        kw["interval"] = "5m"
    arm = rng.choice([
        ("09:35", "15:30"), ("15:30", "09:35"), (None, None),
        ("bogus", "15:30"),
    ])
    return _entry_strategy(
        EntryTrigger(kind=kind, **kw),
        direction=rng.choice([Direction.LONG, Direction.SHORT]),
        policy=rng.choice([PositionAlreadyOpenPolicy.BLOCK,
                           PositionAlreadyOpenPolicy.BLOCK,
                           PositionAlreadyOpenPolicy.STACK]),
        arm_window_start=arm[0], arm_window_end=arm[1],
        require_market_open=rng.random() < 0.7,
        cooldown_secs=rng.choice([0, 0, 300]),
        max_total=rng.choice([None, 2]),
        max_per_symbol=rng.choice([1, 3, 100]),
    )


def _random_exit(rng: random.Random) -> ExitStrategy:
    kinds: list[ExitTrigger] = []
    for leg_i in range(rng.choice([1, 1, 2])):
        kind = rng.choice([
            ExitTriggerKind.STOP, ExitTriggerKind.LIMIT,
            ExitTriggerKind.TRAILING_STOP, ExitTriggerKind.TIME_OF_DAY,
            ExitTriggerKind.MARKET, ExitTriggerKind.INDICATOR,
        ])
        if kind is ExitTriggerKind.STOP:
            mode = rng.random()
            if mode < 0.4:
                trig = ExitTrigger(kind=kind, price=rng.uniform(90, 115),
                                   qty_pct=100.0)
            elif mode < 0.7:
                trig = ExitTrigger(kind=kind, offset_pct=rng.uniform(1, 6),
                                   qty_pct=100.0)
            else:
                trig = ExitTrigger(kind=kind, offset_dollar=rng.uniform(1, 4),
                                   qty_pct=100.0)
        elif kind is ExitTriggerKind.LIMIT:
            trig = ExitTrigger(kind=kind, offset_pct=rng.uniform(1, 6),
                               qty_pct=rng.choice([100.0, 50.0]))
        elif kind is ExitTriggerKind.TRAILING_STOP:
            trig = ExitTrigger(
                kind=kind,
                trail_unit=rng.choice([TrailUnit.PERCENT, TrailUnit.DOLLAR]),
                trail_value=rng.uniform(1.0, 6.0), qty_pct=100.0)
        elif kind is ExitTriggerKind.TIME_OF_DAY:
            trig = ExitTrigger(kind=kind, time_of_day=rng.choice(
                ["10:30", "15:00", "bogus"]), qty_pct=100.0)
        elif kind is ExitTriggerKind.MARKET:
            trig = ExitTrigger(kind=kind, qty_pct=100.0)
        else:
            trig = ExitTrigger(kind=kind, condition=_random_condition(rng),
                               qty_pct=100.0)
        kinds.append(ExitLeg(id=f"leg-{leg_i}", triggers=[trig]))
    return _exit_strategy(kinds, eod_kill_switch=rng.random() < 0.3)


@pytest.mark.parametrize("seed", [7, 1234, 99991])
def test_randomized_strategies_agree(seed: int) -> None:
    rng = random.Random(seed)
    for trial in range(10):
        n = rng.choice([1, 7, 60, 250])
        candles = _random_walk_candles(rng, n)
        entry = _random_entry(rng)
        exit = _random_exit(rng)
        r_new, r_old = _run_both(candles=candles, entry=entry, exit=exit)
        try:
            _assert_identical(r_new, r_old)
        except AssertionError as exc:
            raise AssertionError(
                f"seed={seed} trial={trial} diverged: {exc}\n"
                f"entry={entry.trigger.kind} dir={entry.direction} "
                f"policy={entry.position_already_open_policy}\n"
                f"exits={[t.kind for leg in exit.legs for t in leg.triggers]}"
            ) from exc


def test_result_dtypes_agree() -> None:
    """Spot-check that numeric result dtypes match between the two paths."""
    closes = [100 + 0.4 * i for i in range(60)]
    candles = _et_candles(closes)
    entry = _entry_strategy(EntryTrigger(kind=EntryTriggerKind.MARKET))
    exit = _exit_strategy([_leg(ExitTrigger(
        kind=ExitTriggerKind.STOP, offset_pct=5.0, qty_pct=100.0))])
    r_new, r_old = _run_both(candles=candles, entry=entry, exit=exit)
    _assert_identical(r_new, r_old)
    assert r_new.fills, "expected fills for dtype comparison"
    for f_new, f_old in zip(r_new.fills, r_old.fills, strict=True):
        assert type(f_new.fill_price) is type(f_old.fill_price)
        assert type(f_new.quantity) is type(f_old.quantity)
        assert type(f_new.fill_ts) is type(f_old.fill_ts)
    for (ts_n, eq_n), (ts_o, eq_o) in zip(r_new.equity_curve, r_old.equity_curve, strict=True):
        assert type(ts_n) is type(ts_o)
        assert type(eq_n) is type(eq_o)
    assert type(r_new.final_cash) is type(r_old.final_cash)
