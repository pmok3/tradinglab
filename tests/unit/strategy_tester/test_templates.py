"""Regression tests for the shipped entry/exit strategy templates.

Each on-disk template under ``data/{entry,exit}_strategy_templates/`` is
pinned by:

1. **Shape assertions** — the condition tree (or exit legs/triggers)
   must match the template's *name* end-to-end. This catches the class
   of bug where e.g. ``tmpl-ema-3-8-cross-long.json`` silently becomes
   "EMA(3) between 0 and 0" instead of "EMA(3) crosses_above EMA(8)".

2. **Functional assertions** — synthetic candles are crafted so the
   correct condition fires at least once. The candles are also chosen
   such that obviously-broken variants (e.g. ``between(0, 0)``,
   ``crosses_above(literal 0)``) would NOT fire — distinguishing
   "fix is correct" from "test always passes".

Run with::

    pytest tests/unit/strategy_tester/test_templates.py -v
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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
from tradinglab.scanner.model import FieldRef
from tradinglab.strategy_tester import CostModel, evaluate_symbol

_ET = ZoneInfo("America/New_York")

# Project root → data/<bucket>/<file>.json
_REPO = Path(__file__).resolve().parents[3]
_ENTRY_DIR = _REPO / "data" / "entry_strategy_templates"
_EXIT_DIR = _REPO / "data" / "exit_strategy_templates"


# ---------------------------------------------------------------------------
# Candle helpers
# ---------------------------------------------------------------------------


def _bar(t: datetime, op: float, hi: float, lo: float, cl: float,
         vol: float = 1000.0) -> Candle:
    return Candle(date=t, open=op, high=hi, low=lo, close=cl,
                  volume=vol, session="regular")


def _start_ts() -> datetime:
    """Monday 2026-01-05 09:35 ET — inside default arm window + RTH."""
    return datetime(2026, 1, 5, 9, 35, tzinfo=_ET)


def _flat_then_ramp_up(n_flat: int = 14, n_ramp: int = 30,
                       base: float = 100.0, step: float = 0.5) -> list[Candle]:
    """Flat bars then a steady ramp upward.

    Designed to make a fast MA cross over a slow MA: during the flat
    section all EMAs collapse to ``base``; once the ramp starts the
    EMA(3) leads EMA(8) up and ``crosses_above`` fires.
    """
    out: list[Candle] = []
    t = _start_ts()
    for _ in range(n_flat):
        out.append(_bar(t, base, base + 0.1, base - 0.1, base))
        t += timedelta(minutes=5)
    price = base
    for _ in range(n_ramp):
        op = price
        cl = price + step
        out.append(_bar(t, op, cl + 0.1, op - 0.1, cl))
        t += timedelta(minutes=5)
        price = cl
    return out


def _uptrend_with_pullback_bar() -> list[Candle]:
    """30-bar ramp that ends with a green-body bar whose ``low`` wicks
    down to (or below) a trailing EMA(9) and whose close still beats
    open and VWAP. Triggers the 9 EMA pullback template at the final
    bar.
    """
    out: list[Candle] = []
    t = _start_ts()
    base = 100.0
    # 14 strong-up bars to lift VWAP and price well above EMA(9).
    for i in range(14):
        op = base + i
        cl = op + 1.0
        out.append(_bar(t, op, cl + 0.1, op - 0.1, cl))
        t += timedelta(minutes=5)
    # 16 more bars where each is a green body whose low wicks well
    # below the EMA(9) zone but the close keeps trending up. By bar 30
    # close >> EMA(9), close > VWAP, close > open, low <= EMA(9) on
    # multiple bars.
    price = base + 14
    for _ in range(16):
        op = price
        cl = op + 1.0
        lo = op - 8.0  # deep wick guaranteed to undercut EMA(9)
        out.append(_bar(t, op, cl + 0.1, lo, cl))
        t += timedelta(minutes=5)
        price = cl
    return out


def _downtrend_then_green_recovery() -> list[Candle]:
    """Long red leg drags RSI(14) under 30. The penultimate bar is
    given crushing volume so the session VWAP collapses to roughly
    that bar's close — a small subsequent green bar can then clear
    both ``open`` AND ``VWAP`` without spiking RSI back above 30
    (a large green bar would lift Wilder's avg_gain past the
    oversold threshold).
    """
    out: list[Candle] = []
    t = _start_ts()
    price = 200.0
    # 18 light-volume red bars: pure RSI fuel.
    for _ in range(18):
        op = price
        cl = op - 2.0
        out.append(_bar(t, op, op + 0.1, cl - 0.1, cl, vol=100.0))
        t += timedelta(minutes=5)
        price = cl
    # One ENORMOUS-volume red bar yanks VWAP down to ~this close.
    op = price
    cl = op - 2.0
    out.append(_bar(t, op, op + 0.1, cl - 0.1, cl, vol=10_000_000.0))
    t += timedelta(minutes=5)
    price = cl  # ~162
    # Small green bar: +2 gain is enough to clear VWAP (~162.5) but
    # too small to push RSI(14) above 30.
    op = price
    cl = op + 2.0
    out.append(_bar(t, op, cl + 0.1, op - 0.1, cl, vol=100.0))
    t += timedelta(minutes=5)
    # Follow-through for next-bar fill.
    price = cl
    for _ in range(5):
        out.append(_bar(t, price, price + 0.1, price - 0.1, price + 0.05,
                        vol=100.0))
        price += 0.05
        t += timedelta(minutes=5)
    return out


def _v_bottom_for_vwap_reclaim() -> list[Candle]:
    """Down leg pushes close under VWAP, then a big green bar reclaims
    VWAP. Trailing bars give the next-bar order somewhere to fill.
    """
    out: list[Candle] = []
    t = _start_ts()
    price = 200.0
    for _ in range(18):
        op = price
        cl = op - 1.0
        out.append(_bar(t, op, op + 0.1, cl - 0.1, cl))
        t += timedelta(minutes=5)
        price = cl
    op = price
    cl = 220.0
    out.append(_bar(t, op, cl + 0.1, op - 0.1, cl))
    t += timedelta(minutes=5)
    price = cl
    for _ in range(5):
        out.append(_bar(t, price, price + 0.1, price - 0.1, price + 0.05))
        price += 0.05
        t += timedelta(minutes=5)
    return out


def _inverted_v_for_vwap_reject() -> list[Candle]:
    """Up leg, then a big red bar that closes back under VWAP. Trailing
    bars give the next-bar order somewhere to fill.
    """
    out: list[Candle] = []
    t = _start_ts()
    price = 100.0
    for _ in range(18):
        op = price
        cl = op + 1.0
        out.append(_bar(t, op, cl + 0.1, op - 0.1, cl))
        t += timedelta(minutes=5)
        price = cl
    op = price
    cl = 80.0
    out.append(_bar(t, op, op + 0.1, cl - 0.1, cl))
    t += timedelta(minutes=5)
    price = cl
    for _ in range(5):
        out.append(_bar(t, price, price + 0.1, price - 0.1, price - 0.05))
        price -= 0.05
        t += timedelta(minutes=5)
    return out


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_entry(name: str) -> EntryStrategy:
    path = _ENTRY_DIR / name
    return EntryStrategy.from_dict(json.loads(path.read_text()))


def _load_exit(name: str) -> ExitStrategy:
    path = _EXIT_DIR / name
    return ExitStrategy.from_dict(json.loads(path.read_text()))


def _eval(entry: EntryStrategy, exit_strat: ExitStrategy,
          candles: list[Candle], interval: str = "5m"):
    return evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval=interval,
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=1_000_000.0,
        cost_model=CostModel(slippage_bps=0.0, commission_per_trade=0.0),
    )


def _stop_5pct_long_exit() -> ExitStrategy:
    """Minimal -5% stop exit used as a passive lid when we only care
    about entries firing."""
    return ExitStrategy(
        id="exit-noop",
        name="passive 5% stop",
        legs=[
            ExitLeg(id="leg", triggers=[ExitTrigger(
                kind=ExitTriggerKind.STOP, offset_pct=-5.0, qty_pct=100.0,
            )]),
        ],
        eod_kill_switch=False,
    )


def _market_long_for_exit_test() -> EntryStrategy:
    """Plain market-buy entry — used to seed a position so the loaded
    exit template can fire."""
    return EntryStrategy(
        id="entry-mkt",
        name="market long",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=10.0,
                          share_rounding=ShareRounding.DOWN),
        max_fires_per_session_per_symbol=1,
    )


# ---------------------------------------------------------------------------
# Generic shape helpers
# ---------------------------------------------------------------------------


def _leaves(group) -> list:
    out: list = []
    for c in group.children:
        if hasattr(c, "children"):
            out.extend(_leaves(c))
        else:
            out.append(c)
    return out


# ===========================================================================
# Entry templates
# ===========================================================================


def test_tmpl_ema_3_8_cross_long_shape() -> None:
    """Regression for the canonical bug: this template must evaluate to
    ``EMA(3) crosses_above EMA(8)`` with ``lookback=1`` — NOT
    ``EMA(3) between 0 and 0`` or any other shape.
    """
    s = _load_entry("tmpl-ema-3-8-cross-long.json")
    assert s.name == "3/8 EMA cross (long)"
    assert s.direction == Direction.LONG

    leaves = _leaves(s.trigger.condition)
    assert len(leaves) == 1
    cond = leaves[0]
    assert cond.op == "crosses_above"
    assert cond.left.kind == "indicator"
    assert cond.left.id == "ema"
    assert cond.left.params == {"length": 3}
    right = cond.params["right"]
    assert isinstance(right, FieldRef), (
        "right must be a FieldRef pointing at EMA(8), NOT a literal — the "
        "previously-reported bug was a literal value=0 on this slot."
    )
    assert right.kind == "indicator"
    assert right.id == "ema"
    assert right.params == {"length": 8}
    assert cond.params["lookback"] == 1


def test_tmpl_ema_3_8_cross_long_fires() -> None:
    s = _load_entry("tmpl-ema-3-8-cross-long.json")
    candles = _flat_then_ramp_up(n_flat=12, n_ramp=40, base=100.0, step=0.5)
    result = _eval(s, _stop_5pct_long_exit(), candles)
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert buys, (
        "EMA(3) crosses_above EMA(8) should fire during the post-flat ramp. "
        "If this fails, the template likely regressed to a broken op/right."
    )


def test_tmpl_vwap_reclaim_long_shape() -> None:
    s = _load_entry("tmpl-vwap-reclaim-long.json")
    assert s.name == "VWAP reclaim (long)"
    assert s.direction == Direction.LONG

    leaves = _leaves(s.trigger.condition)
    assert len(leaves) == 1
    cond = leaves[0]
    assert cond.op == "crosses_above"
    assert cond.left.kind == "builtin"
    assert cond.left.id == "close"
    right = cond.params["right"]
    assert isinstance(right, FieldRef)
    assert right.kind == "indicator" and right.id == "vwap"
    assert cond.params["lookback"] == 1


def test_tmpl_vwap_reclaim_long_fires() -> None:
    s = _load_entry("tmpl-vwap-reclaim-long.json")
    candles = _v_bottom_for_vwap_reclaim()
    result = _eval(s, _stop_5pct_long_exit(), candles)
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert buys, "close should cross_above VWAP on the reclaim bar"


def test_tmpl_vwap_reject_short_shape() -> None:
    s = _load_entry("tmpl-vwap-reject-short.json")
    assert s.name == "VWAP reject (short)"
    assert s.direction == Direction.SHORT

    leaves = _leaves(s.trigger.condition)
    assert len(leaves) == 1
    cond = leaves[0]
    assert cond.op == "crosses_below"
    assert cond.left.kind == "builtin" and cond.left.id == "close"
    right = cond.params["right"]
    assert isinstance(right, FieldRef)
    assert right.kind == "indicator" and right.id == "vwap"
    assert cond.params["lookback"] == 1


def test_tmpl_vwap_reject_short_fires() -> None:
    s = _load_entry("tmpl-vwap-reject-short.json")
    candles = _inverted_v_for_vwap_reject()
    # Short template will emit SELL fills on entry, not BUY.
    result = _eval(s, _stop_5pct_long_exit(), candles)
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert sells, "close should cross_below VWAP on the reject bar"


def test_tmpl_rsi_oversold_long_shape() -> None:
    s = _load_entry("tmpl-rsi-oversold-long-1m.json")
    assert s.name == "RSI(14) oversold reversion (long, 1m)"
    assert s.direction == Direction.LONG

    leaves = _leaves(s.trigger.condition)
    assert len(leaves) == 3
    rsi_cond, body_cond, vwap_cond = leaves

    assert rsi_cond.op == "<"
    assert rsi_cond.left.kind == "indicator" and rsi_cond.left.id == "rsi"
    assert rsi_cond.left.params == {"length": 14}
    rsi_right = rsi_cond.params["right"]
    assert isinstance(rsi_right, FieldRef)
    assert rsi_right.kind == "literal" and rsi_right.value == 30.0

    assert body_cond.op == ">"
    assert body_cond.left.kind == "builtin" and body_cond.left.id == "close"
    body_right = body_cond.params["right"]
    assert isinstance(body_right, FieldRef)
    assert body_right.kind == "builtin" and body_right.id == "open"

    assert vwap_cond.op == ">"
    assert vwap_cond.left.kind == "builtin" and vwap_cond.left.id == "close"
    vwap_right = vwap_cond.params["right"]
    assert isinstance(vwap_right, FieldRef)
    assert vwap_right.kind == "indicator" and vwap_right.id == "vwap"


def test_tmpl_rsi_oversold_long_fires() -> None:
    s = _load_entry("tmpl-rsi-oversold-long-1m.json")
    candles = _downtrend_then_green_recovery()
    result = _eval(s, _stop_5pct_long_exit(), candles)
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert buys, "RSI<30 + close>open + close>VWAP should hit on recovery bar"


def test_tmpl_ema9_pullback_long_shape() -> None:
    s = _load_entry("tmpl-ema9-pullback-long-1m.json")
    assert s.name == "9 EMA pullback (long, 1m)"
    assert s.direction == Direction.LONG

    leaves = _leaves(s.trigger.condition)
    assert len(leaves) == 4
    low_cond, close_ema_cond, body_cond, vwap_cond = leaves

    assert low_cond.op == "<="
    assert low_cond.left.kind == "builtin" and low_cond.left.id == "low"
    low_right = low_cond.params["right"]
    assert isinstance(low_right, FieldRef)
    assert low_right.kind == "indicator" and low_right.id == "ema"
    assert low_right.params == {"length": 9}

    assert close_ema_cond.op == ">"
    assert close_ema_cond.left.id == "close"
    ce_right = close_ema_cond.params["right"]
    assert isinstance(ce_right, FieldRef) and ce_right.id == "ema"
    assert ce_right.params == {"length": 9}

    assert body_cond.op == ">"
    assert body_cond.left.id == "close"
    body_right = body_cond.params["right"]
    assert isinstance(body_right, FieldRef)
    assert body_right.kind == "builtin" and body_right.id == "open"

    assert vwap_cond.op == ">"
    assert vwap_cond.left.id == "close"
    v_right = vwap_cond.params["right"]
    assert isinstance(v_right, FieldRef)
    assert v_right.kind == "indicator" and v_right.id == "vwap"


def test_tmpl_ema9_pullback_long_fires() -> None:
    s = _load_entry("tmpl-ema9-pullback-long-1m.json")
    candles = _uptrend_with_pullback_bar()
    result = _eval(s, _stop_5pct_long_exit(), candles)
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert buys, (
        "9 EMA pullback requires low<=EMA9 AND close>EMA9 AND close>open "
        "AND close>VWAP — the wick-down green-body bars should trigger it."
    )


# ===========================================================================
# Exit templates
# ===========================================================================


def test_tmpl_exit_bracket_2pct_shape() -> None:
    s = _load_exit("tmpl-exit-bracket-2pct.json")
    assert s.name == "Bracket ±2% (long)"
    leg_ids = sorted(leg.id for leg in s.legs)
    assert leg_ids == ["pt", "stop"]

    pt = next(leg for leg in s.legs if leg.id == "pt")
    stop = next(leg for leg in s.legs if leg.id == "stop")
    pt_trig = pt.triggers[0]
    stop_trig = stop.triggers[0]
    assert pt_trig.kind == ExitTriggerKind.LIMIT
    assert pt_trig.offset_pct == 2.0
    assert pt_trig.qty_pct == 100.0
    assert stop_trig.kind == ExitTriggerKind.STOP
    assert stop_trig.offset_pct == -2.0
    assert stop_trig.qty_pct == 100.0

    assert len(s.oco_groups) == 1
    assert set(s.oco_groups[0].leg_ids) == {"pt", "stop"}


def test_tmpl_exit_bracket_2pct_runs() -> None:
    s = _load_exit("tmpl-exit-bracket-2pct.json")
    candles = _flat_then_ramp_up(n_flat=0, n_ramp=30, base=100.0, step=0.5)
    result = _eval(_market_long_for_exit_test(), s, candles)
    buys = [f for f in result.fills if f.side.value == "buy"]
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert buys, "market entry should buy on bar 1"
    # Ramp goes 100 -> 115, so +2% PT (= ~102) hits early.
    assert sells, "+2% profit-target leg should fire on the ramp"


def test_tmpl_exit_chandelier_22_3_shape() -> None:
    s = _load_exit("tmpl-exit-chandelier-22-3.json")
    assert s.name == "Chandelier stop (22, 3x)"
    assert len(s.legs) == 1
    trig = s.legs[0].triggers[0]
    assert trig.kind == ExitTriggerKind.CHANDELIER
    assert trig.chandelier_lookback == 22
    assert trig.chandelier_atr_period == 22
    assert trig.chandelier_multiplier == 3.0
    assert trig.chandelier_ma_type == "RMA"
    assert trig.qty_pct == 100.0


def test_tmpl_exit_chandelier_22_3_runs() -> None:
    s = _load_exit("tmpl-exit-chandelier-22-3.json")
    s.eod_kill_switch = False
    candles = _flat_then_ramp_up(n_flat=0, n_ramp=40, base=100.0, step=0.5)
    result = _eval(_market_long_for_exit_test(), s, candles)
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert buys, "market entry should still buy with chandelier exit attached"


def test_tmpl_exit_scale_out_1_3_shape() -> None:
    s = _load_exit("tmpl-exit-scale-out-1-3.json")
    assert s.name == "Scale out 50/50 @ +1% / +3%"
    leg_ids = sorted(leg.id for leg in s.legs)
    assert leg_ids == ["pt1", "pt2", "stop"]
    by_id = {leg.id: leg.triggers[0] for leg in s.legs}
    assert by_id["pt1"].kind == ExitTriggerKind.LIMIT
    assert by_id["pt1"].offset_pct == 1.0
    assert by_id["pt1"].qty_pct == 50.0
    assert by_id["pt2"].kind == ExitTriggerKind.LIMIT
    assert by_id["pt2"].offset_pct == 3.0
    assert by_id["pt2"].qty_pct == 50.0
    assert by_id["stop"].kind == ExitTriggerKind.STOP
    assert by_id["stop"].offset_pct == -1.0
    assert by_id["stop"].qty_pct == 100.0
    assert len(s.oco_groups) == 1
    assert set(s.oco_groups[0].leg_ids) == {"pt1", "pt2", "stop"}


def test_tmpl_exit_scale_out_1_3_runs() -> None:
    s = _load_exit("tmpl-exit-scale-out-1-3.json")
    candles = _flat_then_ramp_up(n_flat=0, n_ramp=40, base=100.0, step=0.5)
    result = _eval(_market_long_for_exit_test(), s, candles)
    buys = [f for f in result.fills if f.side.value == "buy"]
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert buys, "market entry should buy"
    assert sells, "ramp should trigger at least the +1% scale-out leg"


def test_tmpl_exit_time_stop_1555_shape() -> None:
    s = _load_exit("tmpl-exit-time-stop-1555.json")
    assert s.name == "Time stop @ 15:55 + EOD"
    assert len(s.legs) == 1
    trig = s.legs[0].triggers[0]
    assert trig.kind == ExitTriggerKind.TIME_OF_DAY
    assert trig.time_of_day == "15:55"
    assert trig.qty_pct == 100.0
    assert s.eod_kill_switch is True


def test_tmpl_exit_time_stop_1555_runs() -> None:
    s = _load_exit("tmpl-exit-time-stop-1555.json")
    # Build candles spanning 09:35 through past 15:55 ET so the
    # time-of-day trigger has a chance to fire.
    out: list[Candle] = []
    t = _start_ts()
    price = 100.0
    # ~9 hours of 5m bars covers 09:35 → 18:35 (well past 15:55).
    for _ in range(110):
        out.append(_bar(t, price, price + 0.1, price - 0.1, price + 0.05))
        price += 0.05
        t += timedelta(minutes=5)
    result = _eval(_market_long_for_exit_test(), s, out)
    buys = [f for f in result.fills if f.side.value == "buy"]
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert buys, "market entry should buy"
    assert sells, "15:55 time-of-day exit should flatten before EOD"


def test_tmpl_exit_trailing_2pct_shape() -> None:
    s = _load_exit("tmpl-exit-trailing-2pct.json")
    assert s.name == "2% trailing stop"
    assert len(s.legs) == 1
    trig = s.legs[0].triggers[0]
    assert trig.kind == ExitTriggerKind.TRAILING_STOP
    # trail_unit / trail_basis are enum-valued; compare the string value.
    assert trig.trail_unit.value == "percent"
    assert trig.trail_value == 2.0
    assert trig.trail_basis.value == "intrabar"
    assert trig.qty_pct == 100.0


def test_tmpl_exit_trailing_2pct_runs() -> None:
    s = _load_exit("tmpl-exit-trailing-2pct.json")
    s.eod_kill_switch = False
    # Ramp up then sharp pullback so the 2% trail trips.
    out: list[Candle] = []
    t = _start_ts()
    price = 100.0
    for _ in range(20):
        op = price
        cl = op + 1.0
        out.append(_bar(t, op, cl + 0.1, op - 0.1, cl))
        t += timedelta(minutes=5)
        price = cl
    # Pullback: a -5% bar
    op = price
    cl = op * 0.95
    out.append(_bar(t, op, op + 0.1, cl - 0.1, cl))
    t += timedelta(minutes=5)
    # Follow-through bars so any next-bar order has a slot to fill in.
    price = cl
    for _ in range(5):
        out.append(_bar(t, price, price + 0.1, price - 0.1, price))
        t += timedelta(minutes=5)
    result = _eval(_market_long_for_exit_test(), s, out)
    buys = [f for f in result.fills if f.side.value == "buy"]
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert buys, "market entry should buy"
    assert sells, "2% trailing stop should trip on the -5% pullback bar"


# ---------------------------------------------------------------------------
# Catch-all: every shipped template loads & every name is unique.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    sorted(_ENTRY_DIR.glob("*.json")),
    ids=lambda p: p.name,
)
def test_every_entry_template_loads(path: Path) -> None:
    s = EntryStrategy.from_dict(json.loads(path.read_text()))
    assert s.name
    assert s.trigger is not None


@pytest.mark.parametrize(
    "path",
    sorted(_EXIT_DIR.glob("*.json")),
    ids=lambda p: p.name,
)
def test_every_exit_template_loads(path: Path) -> None:
    s = ExitStrategy.from_dict(json.loads(path.read_text()))
    assert s.name
    assert len(s.legs) >= 1
