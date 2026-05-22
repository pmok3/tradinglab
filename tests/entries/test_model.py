"""Tests for tradinglab.entries.model — JSON round-trip + validation."""

from __future__ import annotations

import pytest

from tradinglab.entries.model import (
    CURRENT_SCHEMA_VERSION,
    CreatedWith,
    Direction,
    EntryStrategy,
    EntryTrigger,
    OrderSide,
    PositionAlreadyOpenPolicy,
    ShareRounding,
    SizingKind,
    SizingRule,
    TimeInForce,
    TriggerKind,
    Universe,
    migrate,
    validate_strategy,
)
from tradinglab.scanner.model import (
    Condition,
    FieldRef,
    Group,
    OP_GT,
)


# ---------- helpers ----------

def _good_strategy(**overrides) -> EntryStrategy:
    base = EntryStrategy(
        name="VWAP reclaim long",
        direction=Direction.LONG,
        universe=Universe(symbols=("AAPL", "MSFT")),
        trigger=EntryTrigger(kind=TriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=100),
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


# ---------- SizingRule ----------

class TestSizingRule:
    def test_fixed_qty_round_trip(self):
        s = SizingRule(kind=SizingKind.FIXED_QTY, qty=50)
        out = SizingRule.from_dict(s.to_dict())
        assert out.kind == SizingKind.FIXED_QTY
        assert out.qty == 50.0
        assert out.notional is None

    def test_fixed_notional_round_trip(self):
        s = SizingRule(
            kind=SizingKind.FIXED_NOTIONAL,
            notional=10_000,
            share_rounding=ShareRounding.NEAREST,
        )
        out = SizingRule.from_dict(s.to_dict())
        assert out.kind == SizingKind.FIXED_NOTIONAL
        assert out.notional == 10_000.0
        assert out.share_rounding == ShareRounding.NEAREST

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="unknown SizingKind"):
            SizingRule.from_dict({"kind": "nonsense"})


# ---------- Universe ----------

class TestUniverse:
    def test_symbols_uppercased(self):
        u = Universe(symbols=("aapl", "msft"))
        assert u.symbols == ("AAPL", "MSFT")

    def test_round_trip_symbols(self):
        u = Universe(symbols=("SPY", "QQQ"))
        assert Universe.from_dict(u.to_dict()) == u

    def test_round_trip_scanner_id(self):
        u = Universe(scanner_id="abc-123")
        assert Universe.from_dict(u.to_dict()) == u

    def test_round_trip_attached_chart(self):
        u = Universe(from_attached_chart=True)
        assert Universe.from_dict(u.to_dict()) == u

    def test_empty_universe_constructible(self):
        # Permissive constructor — validation lives elsewhere.
        u = Universe()
        assert u.is_empty()

    def test_bad_symbols_type_raises(self):
        with pytest.raises(ValueError, match="must be a list/tuple"):
            Universe.from_dict({"symbols": "AAPL"})


# ---------- EntryTrigger ----------

class TestEntryTrigger:
    def test_market_round_trip(self):
        t = EntryTrigger(kind=TriggerKind.MARKET, label="open")
        out = EntryTrigger.from_dict(t.to_dict())
        assert out.kind == TriggerKind.MARKET
        assert out.label == "open"
        assert out.id == t.id

    def test_limit_round_trip(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT, price=99.5)
        out = EntryTrigger.from_dict(t.to_dict())
        assert out.kind == TriggerKind.LIMIT
        assert out.price == 99.5

    def test_stop_limit_round_trip(self):
        t = EntryTrigger(
            kind=TriggerKind.STOP_LIMIT, stop_price=100.0, price=100.5,
        )
        out = EntryTrigger.from_dict(t.to_dict())
        assert out.stop_price == 100.0
        assert out.price == 100.5

    def test_indicator_with_condition_round_trip(self):
        cond = Group(
            combinator="and",
            children=[
                Condition(
                    left=FieldRef(kind="builtin", id="close"),
                    op=OP_GT,
                    params={"right": FieldRef(kind="indicator", id="ema", params={"length": 9})},
                ),
            ],
        )
        t = EntryTrigger(
            kind=TriggerKind.INDICATOR,
            condition=cond,
            interval="1m",
            evaluate_intrabar=False,
        )
        out = EntryTrigger.from_dict(t.to_dict())
        assert out.kind == TriggerKind.INDICATOR
        assert out.interval == "1m"
        assert out.condition is not None
        assert out.condition.combinator == "and"
        assert len(out.condition.children) == 1

    def test_scanner_alert_round_trip(self):
        t = EntryTrigger(kind=TriggerKind.SCANNER_ALERT, scanner_id="rvol_2sig")
        out = EntryTrigger.from_dict(t.to_dict())
        assert out.scanner_id == "rvol_2sig"

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="unknown TriggerKind"):
            EntryTrigger.from_dict({"kind": "wormhole"})


# ---------- EntryStrategy round-trip ----------

class TestEntryStrategyRoundTrip:
    def test_basic(self):
        s = _good_strategy()
        out = EntryStrategy.from_dict(s.to_dict())
        assert out.id == s.id
        assert out.name == s.name
        assert out.direction == Direction.LONG
        assert out.universe.symbols == ("AAPL", "MSFT")
        assert out.trigger.kind == TriggerKind.MARKET
        assert out.sizing.kind == SizingKind.FIXED_QTY
        assert out.sizing.qty == 100

    def test_on_fill_exit_ids_preserved(self):
        s = _good_strategy(on_fill_exit_ids=("exit-A", "exit-B"))
        out = EntryStrategy.from_dict(s.to_dict())
        assert out.on_fill_exit_ids == ("exit-A", "exit-B")

    def test_lifecycle_defaults(self):
        s = _good_strategy()
        assert s.cooldown_secs == 0
        assert s.max_fires_per_session_per_symbol == 1
        assert s.position_already_open_policy == PositionAlreadyOpenPolicy.BLOCK
        assert s.arm_window_start == "09:35"
        assert s.arm_window_end == "15:30"

    def test_future_schema_refused(self):
        s = _good_strategy()
        d = s.to_dict()
        d["schema_version"] = CURRENT_SCHEMA_VERSION + 99
        with pytest.raises(ValueError, match="schema_version"):
            EntryStrategy.from_dict(d)

    def test_unknown_direction_raises(self):
        s = _good_strategy()
        d = s.to_dict()
        d["direction"] = "sideways"
        with pytest.raises(ValueError, match="unknown Direction"):
            EntryStrategy.from_dict(d)

    def test_template_flag_round_trip(self):
        s = _good_strategy(created_with=CreatedWith(template=True))
        out = EntryStrategy.from_dict(s.to_dict())
        assert out.created_with.template is True

    def test_on_fill_exit_ids_must_be_list(self):
        s = _good_strategy()
        d = s.to_dict()
        d["on_fill_exit_ids"] = "exit-A"  # not a list
        with pytest.raises(ValueError, match="on_fill_exit_ids must be"):
            EntryStrategy.from_dict(d)


# ---------- validate_strategy ----------

class TestValidate:
    def test_good_strategy_passes(self):
        assert validate_strategy(_good_strategy()) == []

    def test_empty_name(self):
        s = _good_strategy(name="")
        assert any("name" in e for e in validate_strategy(s))

    def test_universe_empty_fails(self):
        s = _good_strategy(universe=Universe())
        errs = validate_strategy(s)
        assert any("universe is empty" in e for e in errs)

    def test_universe_xor_violation(self):
        s = _good_strategy(
            universe=Universe(
                symbols=("AAPL",),
                scanner_id="abc",
            ),
        )
        errs = validate_strategy(s)
        assert any("exactly ONE" in e for e in errs)

    def test_universe_all_three_set(self):
        s = _good_strategy(
            universe=Universe(
                symbols=("A",),
                scanner_id="abc",
                from_attached_chart=True,
            ),
        )
        errs = validate_strategy(s)
        assert any("exactly ONE" in e for e in errs)

    def test_limit_trigger_requires_price(self):
        s = _good_strategy(trigger=EntryTrigger(kind=TriggerKind.LIMIT))
        errs = validate_strategy(s)
        assert any("LIMIT" in e and "price" in e for e in errs)

    def test_stop_trigger_requires_stop_price(self):
        s = _good_strategy(trigger=EntryTrigger(kind=TriggerKind.STOP))
        errs = validate_strategy(s)
        assert any("STOP" in e for e in errs)

    def test_stop_limit_trigger_requires_both(self):
        s = _good_strategy(
            trigger=EntryTrigger(kind=TriggerKind.STOP_LIMIT, stop_price=100.0),
        )
        errs = validate_strategy(s)
        assert any("limit price" in e for e in errs)

    def test_indicator_trigger_requires_condition(self):
        s = _good_strategy(trigger=EntryTrigger(kind=TriggerKind.INDICATOR))
        errs = validate_strategy(s)
        assert any("condition" in e for e in errs)

    def test_scanner_alert_requires_scanner_id(self):
        s = _good_strategy(trigger=EntryTrigger(kind=TriggerKind.SCANNER_ALERT))
        errs = validate_strategy(s)
        assert any("scanner_id" in e for e in errs)

    def test_scanner_alert_incompatible_with_chart_universe(self):
        s = _good_strategy(
            universe=Universe(from_attached_chart=True),
            trigger=EntryTrigger(
                kind=TriggerKind.SCANNER_ALERT, scanner_id="abc",
            ),
        )
        errs = validate_strategy(s)
        assert any("from_attached_chart" in e for e in errs)

    def test_fixed_qty_sizing_requires_positive(self):
        s = _good_strategy(sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=0))
        errs = validate_strategy(s)
        assert any("FIXED_QTY" in e for e in errs)

    def test_fixed_notional_sizing_requires_positive(self):
        s = _good_strategy(
            sizing=SizingRule(kind=SizingKind.FIXED_NOTIONAL, notional=0),
        )
        errs = validate_strategy(s)
        assert any("FIXED_NOTIONAL" in e for e in errs)

    def test_negative_cooldown_fails(self):
        s = _good_strategy(cooldown_secs=-5)
        errs = validate_strategy(s)
        assert any("cooldown_secs" in e for e in errs)

    def test_zero_max_fires_fails(self):
        s = _good_strategy(max_fires_per_session_per_symbol=0)
        errs = validate_strategy(s)
        assert any("max_fires_per_session_per_symbol" in e for e in errs)

    def test_bad_arm_window_fails(self):
        s = _good_strategy(arm_window_start="9:35", arm_window_end="15:30")
        errs = validate_strategy(s)
        assert any("arm_window_start" in e for e in errs)

    def test_inverted_arm_window_fails(self):
        s = _good_strategy(arm_window_start="16:00", arm_window_end="09:35")
        errs = validate_strategy(s)
        assert any("must be <=" in e for e in errs)

    def test_bad_symbol_in_universe(self):
        s = _good_strategy(universe=Universe(symbols=("AAPL", "BAD$YM")))
        errs = validate_strategy(s)
        assert any("invalid symbol" in e for e in errs)


# ---------- migrate ----------

class TestMigrate:
    def test_no_op_for_v0(self):
        d = {"name": "x", "schema_version": 0}
        out = migrate(d, from_version=0)
        assert out["schema_version"] == 1
        assert out["name"] == "x"
