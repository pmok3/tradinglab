"""Tests for tradinglab.entries.spec — pure trigger-fire helpers."""

from __future__ import annotations

import pytest

from tradinglab.entries.model import (
    Direction,
    EntryTrigger,
    TimeInForce,
    TriggerKind,
)
from tradinglab.entries.spec import (
    BarLike,
    should_fire_limit,
    should_fire_market,
    should_fire_stop,
    should_fire_stop_limit,
    trigger_fill_price,
)


def _bar(o, h, l, c) -> BarLike:
    return BarLike(open=o, high=h, low=l, close=c)


# ---------- MARKET ----------

class TestMarket:
    def test_fires_on_close(self):
        t = EntryTrigger(kind=TriggerKind.MARKET)
        assert should_fire_market(t, _bar(100, 101, 99, 100.5), is_close=True) is True

    def test_does_not_fire_on_forming(self):
        t = EntryTrigger(kind=TriggerKind.MARKET)
        assert should_fire_market(t, _bar(100, 101, 99, 100.5), is_close=False) is False

    def test_wrong_kind_returns_false(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT, price=100)
        assert should_fire_market(t, _bar(100, 101, 99, 100), is_close=True) is False


# ---------- LIMIT ----------

class TestLimit:
    def test_long_fires_when_low_reaches_price(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT, price=99.0)
        assert should_fire_limit(t, _bar(100, 101, 98.5, 99.5), direction=Direction.LONG) is True

    def test_long_does_not_fire_when_low_above_price(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT, price=99.0)
        assert should_fire_limit(t, _bar(100, 101, 99.5, 100.5), direction=Direction.LONG) is False

    def test_long_exact_touch_fires(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT, price=99.0)
        assert should_fire_limit(t, _bar(100, 101, 99.0, 100), direction=Direction.LONG) is True

    def test_short_fires_when_high_reaches_price(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT, price=101.0)
        assert should_fire_limit(t, _bar(100, 101.5, 99, 100), direction=Direction.SHORT) is True

    def test_short_does_not_fire_when_high_below_price(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT, price=101.0)
        assert should_fire_limit(t, _bar(100, 100.5, 99, 100), direction=Direction.SHORT) is False

    def test_no_price_returns_false(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT)
        assert should_fire_limit(t, _bar(100, 101, 99, 100), direction=Direction.LONG) is False


# ---------- STOP ----------

class TestStop:
    def test_long_breakout_fires(self):
        # Buy stop at 105; bar high reaches it.
        t = EntryTrigger(kind=TriggerKind.STOP, stop_price=105)
        assert should_fire_stop(t, _bar(100, 105.5, 99, 104), direction=Direction.LONG) is True

    def test_long_breakout_does_not_fire(self):
        t = EntryTrigger(kind=TriggerKind.STOP, stop_price=105)
        assert should_fire_stop(t, _bar(100, 104.5, 99, 102), direction=Direction.LONG) is False

    def test_short_breakdown_fires(self):
        t = EntryTrigger(kind=TriggerKind.STOP, stop_price=95)
        assert should_fire_stop(t, _bar(100, 102, 94.5, 96), direction=Direction.SHORT) is True

    def test_short_breakdown_does_not_fire(self):
        t = EntryTrigger(kind=TriggerKind.STOP, stop_price=95)
        assert should_fire_stop(t, _bar(100, 102, 95.5, 98), direction=Direction.SHORT) is False


# ---------- STOP_LIMIT ----------

class TestStopLimit:
    def test_long_both_hit_in_one_bar(self):
        # Stop=105, Limit=106. High 106.5 satisfies stop & limit.
        t = EntryTrigger(kind=TriggerKind.STOP_LIMIT, stop_price=105, price=106)
        # bar.high >= 105 (stop) AND bar.low <= 106 (limit) — yes, low=104
        assert should_fire_stop_limit(
            t, _bar(104, 106.5, 104, 105.5), direction=Direction.LONG,
        ) is True

    def test_long_stop_hit_but_limit_blew_through(self):
        # Stop=105, Limit=105.5. Bar gaps and never trades back below.
        t = EntryTrigger(kind=TriggerKind.STOP_LIMIT, stop_price=105, price=105.5)
        assert should_fire_stop_limit(
            t, _bar(106, 110, 106, 109), direction=Direction.LONG,
        ) is False

    def test_long_stop_already_armed_only_checks_limit(self):
        t = EntryTrigger(kind=TriggerKind.STOP_LIMIT, stop_price=105, price=106)
        # stop was hit on a prior bar; this bar only needs limit.low <= 106
        assert should_fire_stop_limit(
            t, _bar(107, 108, 105.5, 107),
            direction=Direction.LONG, stop_already_armed=True,
        ) is True

    def test_short_both_hit(self):
        t = EntryTrigger(kind=TriggerKind.STOP_LIMIT, stop_price=95, price=94)
        # bar.low<=95 (stop) AND bar.high>=94 (limit)
        assert should_fire_stop_limit(
            t, _bar(96, 96, 94.5, 95.5), direction=Direction.SHORT,
        ) is True


# ---------- trigger_fill_price ----------

class TestFillPrice:
    def test_market_uses_close(self):
        t = EntryTrigger(kind=TriggerKind.MARKET)
        assert trigger_fill_price(t, _bar(100, 101, 99, 100.5), direction=Direction.LONG) == 100.5

    def test_limit_uses_trigger_price(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT, price=99.0)
        assert trigger_fill_price(t, _bar(100, 101, 98, 99.5), direction=Direction.LONG) == 99.0

    def test_stop_uses_stop_price(self):
        t = EntryTrigger(kind=TriggerKind.STOP, stop_price=105.0)
        assert trigger_fill_price(t, _bar(100, 105.5, 99, 104), direction=Direction.LONG) == 105.0

    def test_stop_limit_uses_limit_price(self):
        t = EntryTrigger(kind=TriggerKind.STOP_LIMIT, stop_price=105, price=106)
        assert trigger_fill_price(t, _bar(104, 106.5, 104, 105.5), direction=Direction.LONG) == 106.0

    def test_indicator_uses_close(self):
        t = EntryTrigger(kind=TriggerKind.INDICATOR)
        assert trigger_fill_price(t, _bar(100, 101, 99, 100.25), direction=Direction.LONG) == 100.25

    def test_scanner_alert_uses_close(self):
        t = EntryTrigger(kind=TriggerKind.SCANNER_ALERT, scanner_id="x")
        assert trigger_fill_price(t, _bar(100, 101, 99, 100.75), direction=Direction.LONG) == 100.75
