"""Unit tests for the shared exit-trigger dispatch registry."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

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
from tradinglab.exits.model import ExitTrigger, TrailUnit, TriggerKind
from tradinglab.exits.spec import Bar
from tradinglab.models import Candle
from tradinglab.positions.model import Position
from tradinglab.scanner.engine import make_context
from tradinglab.scanner.model import OP_GT, Condition, FieldRef, Group
from tradinglab.strategy_tester.evaluator import EvalContext, _check_exits


def _position(*, side: str = "long") -> Position:
    return Position(
        id="p1",
        symbol="AAPL",
        side=side,  # type: ignore[arg-type]
        qty_initial=100.0,
        qty_open=100.0,
        avg_entry_price=100.0,
        entry_time=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
        source="sandbox",
    )


def _entry_strategy() -> EntryStrategy:
    return EntryStrategy(
        id="entry",
        name="entry",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("AAPL",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY,
            qty=100.0,
            share_rounding=ShareRounding.DOWN,
        ),
    )


def _mechanical_ctx(trigger: ExitTrigger) -> EvalContext:
    from tradinglab.exits.model import ExitLeg, ExitStrategy

    return EvalContext(
        symbol="AAPL",
        entry_strategy=_entry_strategy(),
        exit_strategy=ExitStrategy(
            id="exit",
            name="exit",
            legs=[ExitLeg(id="leg", triggers=[trigger])],
        ),
        starting_cash=100_000.0,
        position_open=True,
        position_side="buy",
        position_qty=100.0,
        position_avg_price=100.0,
        position_entry_ts=1_704_210_600,
    )


class TestRegistryContract:
    def test_every_trigger_kind_has_handler(self):
        from tradinglab.exits.dispatch import supported_trigger_kinds

        missing = set(TriggerKind) - supported_trigger_kinds()
        assert not missing, f"unregistered exit trigger kinds: {missing}"

    def test_strategy_tester_alias_is_same_object(self):
        from tradinglab.exits.dispatch import _EXIT_DISPATCH
        from tradinglab.strategy_tester import evaluator as st_eval

        assert st_eval._EXIT_HANDLERS is _EXIT_DISPATCH

    def test_unknown_kind_returns_no_fire(self):
        from tradinglab.exits.dispatch import (
            _EXIT_DISPATCH,
            ExitTriggerContext,
            check_trigger_decision,
        )

        saved = _EXIT_DISPATCH.pop(TriggerKind.MARKET)
        try:
            decision = check_trigger_decision(
                ExitTrigger(kind=TriggerKind.MARKET),
                ExitTriggerContext(
                    position=_position(),
                    bar=Bar(open=100.0, high=101.0, low=99.0, close=100.5),
                ),
            )
            assert decision.fire is False
        finally:
            _EXIT_DISPATCH[TriggerKind.MARKET] = saved

    def test_adding_kind_lights_up_supported_set(self):
        from tradinglab.exits.dispatch import _EXIT_DISPATCH, supported_trigger_kinds

        sentinel_kind = "test_sentinel_exit_kind"

        def _handler(_trigger, _ctx):
            return None

        _EXIT_DISPATCH[sentinel_kind] = _handler  # type: ignore[index]
        try:
            assert sentinel_kind in supported_trigger_kinds()
        finally:
            del _EXIT_DISPATCH[sentinel_kind]
        assert sentinel_kind not in supported_trigger_kinds()


class TestDispatchBehavior:
    def test_market_handler_returns_decision(self):
        from tradinglab.exits.dispatch import ExitTriggerContext, check_trigger_decision

        decision = check_trigger_decision(
            ExitTrigger(kind=TriggerKind.MARKET),
            ExitTriggerContext(
                position=_position(),
                bar=Bar(open=100.0, high=101.0, low=99.0, close=100.5),
            ),
        )
        assert decision.fire is True
        assert decision.fire_price == 100.5

    def test_strategy_tester_legacy_signed_stop_offset_is_explicit_context_policy(self):
        from tradinglab.exits.dispatch import ExitTriggerContext, check_trigger_decision

        trigger = ExitTrigger(kind=TriggerKind.STOP, offset_pct=5.0)
        bar = Bar(open=100.0, high=104.0, low=96.0, close=101.0)

        live_decision = check_trigger_decision(
            trigger,
            ExitTriggerContext(position=_position(), bar=bar),
        )
        mechanical_decision = check_trigger_decision(
            trigger,
            ExitTriggerContext(
                position=_position(),
                bar=bar,
                legacy_signed_offsets=True,
            ),
        )

        assert live_decision.fire is True
        assert mechanical_decision.fire is False


class TestLiveMechanicalParity:
    @pytest.mark.parametrize(
        ("trigger", "bar", "bar_ts"),
        [
            (
                ExitTrigger(kind=TriggerKind.MARKET, qty_pct=25.0),
                Bar(open=100.0, high=101.0, low=99.0, close=100.5),
                1_704_210_600,
            ),
            (
                ExitTrigger(kind=TriggerKind.LIMIT, price=101.0, qty_pct=25.0),
                Bar(open=100.0, high=101.5, low=99.5, close=101.0),
                1_704_210_600,
            ),
            (
                ExitTrigger(kind=TriggerKind.STOP, price=99.0, qty_pct=25.0),
                Bar(open=100.0, high=100.5, low=98.5, close=99.0),
                1_704_210_600,
            ),
            (
                ExitTrigger(
                    kind=TriggerKind.STOP_LIMIT,
                    price=99.0,
                    stop_limit_price=98.75,
                    qty_pct=25.0,
                ),
                Bar(open=100.0, high=100.5, low=98.5, close=99.0),
                1_704_210_600,
            ),
            (
                ExitTrigger(kind=TriggerKind.TIME_OF_DAY, time_of_day="15:55", qty_pct=25.0),
                Bar(open=100.0, high=100.5, low=99.5, close=100.0),
                1_704_233_700,  # 2024-01-02 15:55 ET
            ),
            (
                ExitTrigger(
                    kind=TriggerKind.TRAILING_STOP,
                    trail_unit=TrailUnit.PERCENT,
                    trail_value=1.0,
                    qty_pct=25.0,
                ),
                Bar(open=104.0, high=105.0, low=103.0, close=104.0),
                1_704_210_600,
            ),
        ],
    )
    def test_live_dispatch_fire_matches_mechanical_check_exits(self, trigger, bar, bar_ts):
        from tradinglab.exits.dispatch import ExitTriggerContext, check_trigger_decision
        from tradinglab.exits.spec import TriggerState
        from tradinglab.strategy_tester.evaluator import _bar_ts_to_et

        live_state = TriggerState()
        live_decision = check_trigger_decision(
            trigger,
            ExitTriggerContext(
                position=_position(),
                bar=bar,
                trigger_state=live_state,
                now=_bar_ts_to_et(bar_ts) if trigger.kind is TriggerKind.TIME_OF_DAY else None,
                legacy_signed_offsets=True,
            ),
        )

        fired, qty = _check_exits(
            _mechanical_ctx(trigger),
            (bar.open, bar.high, bar.low, bar.close),
            bar_ts=bar_ts,
        )

        assert fired is live_decision.fire
        assert qty == 25.0

    def test_indicator_dispatch_fire_matches_mechanical_check_exits(self):
        from tradinglab.exits.dispatch import ExitTriggerContext, check_trigger_decision

        condition = Group(
            combinator="and",
            children=[
                Condition(
                    left=FieldRef.builtin("close"),
                    op=OP_GT,
                    params={"right": FieldRef.literal(100.0)},
                ),
            ],
        )
        trigger = ExitTrigger(
            kind=TriggerKind.INDICATOR,
            condition=condition,
            qty_pct=25.0,
        )
        bar = Bar(open=100.0, high=102.0, low=99.0, close=101.0)
        candles = [
            Candle(
                date=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=1000.0,
            )
        ]

        live_decision = check_trigger_decision(
            trigger,
            ExitTriggerContext(
                position=_position(),
                bar=bar,
                scanner_eval_ctx=make_context("AAPL", "5m", candles),
            ),
        )
        fired, qty = _check_exits(
            _mechanical_ctx(trigger),
            (bar.open, bar.high, bar.low, bar.close),
            eval_ctx=make_context("AAPL", "5m", candles),
            bar_ts=1_704_210_600,
        )

        assert fired is live_decision.fire
        assert qty == 25.0

    def test_chandelier_dispatch_fire_matches_mechanical_check_exits(self):
        from tradinglab.exits.dispatch import ExitTriggerContext, check_trigger_decision
        from tradinglab.exits.spec import TriggerState, update_chandelier_state

        trigger = ExitTrigger(
            kind=TriggerKind.CHANDELIER,
            chandelier_lookback=2,
            chandelier_atr_period=2,
            chandelier_multiplier=1.0,
            qty_pct=25.0,
        )
        history = [
            Bar(open=100.0, high=102.0, low=99.0, close=100.0),
            Bar(open=100.0, high=102.0, low=99.0, close=100.0),
            Bar(open=100.0, high=102.0, low=99.0, close=100.0),
        ]
        live_state = TriggerState()
        mechanical_state = TriggerState()
        for state in (live_state, mechanical_state):
            update_chandelier_state(state, trigger, _position(), history[0], is_activation=True)
            update_chandelier_state(state, trigger, _position(), history[1], is_activation=False)
            update_chandelier_state(state, trigger, _position(), history[2], is_activation=False)
        assert live_state.chandelier_stop is not None

        touch_bar = Bar(
            open=live_state.chandelier_stop + 0.5,
            high=live_state.chandelier_stop + 0.5,
            low=live_state.chandelier_stop - 0.5,
            close=live_state.chandelier_stop,
        )
        live_decision = check_trigger_decision(
            trigger,
            ExitTriggerContext(
                position=_position(),
                bar=touch_bar,
                trigger_state=live_state,
            ),
        )
        mechanical_ctx = _mechanical_ctx(trigger)
        mechanical_ctx.trigger_states[trigger.id] = mechanical_state
        fired, qty = _check_exits(
            mechanical_ctx,
            (touch_bar.open, touch_bar.high, touch_bar.low, touch_bar.close),
            bar_ts=1_704_210_600,
        )

        assert fired is live_decision.fire
        assert qty == 25.0
