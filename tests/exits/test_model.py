"""Tests for ``exits.model``.

Round-trip, schema versioning, validation, OCO disjointness.
"""

from __future__ import annotations

import pytest

from tradinglab.exits.model import (
    CURRENT_SCHEMA_VERSION,
    ActivationUnit,
    CreatedWith,
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
    OCOGroup,
    TimeInForce,
    TrailBasis,
    TrailUnit,
    TriggerKind,
    migrate,
    validate_strategy,
)
from tradinglab.scanner.model import (
    Condition,
    FieldRef,
)
from tradinglab.scanner.model import (
    Group as ConditionGroup,
)

# ---------------------------------------------------------------------------
# Trigger field defaults / construction
# ---------------------------------------------------------------------------


def test_trigger_default_kind_is_market():
    t = ExitTrigger()
    assert t.kind == TriggerKind.MARKET
    assert t.qty_pct == 100.0
    assert t.enabled is True
    assert t.time_in_force == TimeInForce.DAY
    assert t.trail_basis == TrailBasis.INTRABAR


def test_trigger_id_defaults_unique():
    t1 = ExitTrigger()
    t2 = ExitTrigger()
    assert t1.id != t2.id
    assert len(t1.id) > 0


# ---------------------------------------------------------------------------
# JSON round-trip — single trigger by kind
# ---------------------------------------------------------------------------


def _round_trip_trigger(t: ExitTrigger) -> ExitTrigger:
    return ExitTrigger.from_dict(t.to_dict())


def test_trigger_market_round_trip():
    t = ExitTrigger(kind=TriggerKind.MARKET, qty_pct=50.0, label="bail")
    rt = _round_trip_trigger(t)
    assert rt.kind == TriggerKind.MARKET
    assert rt.qty_pct == 50.0
    assert rt.label == "bail"
    assert rt.id == t.id


def test_trigger_limit_round_trip():
    t = ExitTrigger(kind=TriggerKind.LIMIT, price=185.50)
    rt = _round_trip_trigger(t)
    assert rt.kind == TriggerKind.LIMIT
    assert rt.price == 185.50


def test_trigger_stop_round_trip():
    t = ExitTrigger(kind=TriggerKind.STOP, offset_pct=-2.0)
    rt = _round_trip_trigger(t)
    assert rt.kind == TriggerKind.STOP
    assert rt.offset_pct == -2.0


def test_trigger_stop_limit_round_trip():
    t = ExitTrigger(
        kind=TriggerKind.STOP_LIMIT,
        price=180.0,
        stop_limit_price=179.50,
    )
    rt = _round_trip_trigger(t)
    assert rt.kind == TriggerKind.STOP_LIMIT
    assert rt.price == 180.0
    assert rt.stop_limit_price == 179.50


def test_trigger_trailing_stop_round_trip():
    t = ExitTrigger(
        kind=TriggerKind.TRAILING_STOP,
        trail_unit=TrailUnit.PERCENT,
        trail_value=2.5,
        activation_unit=ActivationUnit.R_MULTIPLE,
        activation_value=1.0,
        trail_basis=TrailBasis.CLOSE,
    )
    rt = _round_trip_trigger(t)
    assert rt.trail_unit == TrailUnit.PERCENT
    assert rt.trail_value == 2.5
    assert rt.activation_unit == ActivationUnit.R_MULTIPLE
    assert rt.activation_value == 1.0
    assert rt.trail_basis == TrailBasis.CLOSE


def test_trigger_time_of_day_round_trip():
    t = ExitTrigger(kind=TriggerKind.TIME_OF_DAY, time_of_day="15:55")
    rt = _round_trip_trigger(t)
    assert rt.kind == TriggerKind.TIME_OF_DAY
    assert rt.time_of_day == "15:55"


def test_trigger_indicator_round_trip():
    cond = ConditionGroup(
        combinator="and",
        children=[
            Condition(
                left=FieldRef.indicator("ema", params={"length": 20}),
                op="crosses_below",
                params={"right": FieldRef.builtin("close"), "lookback": 1},
            ),
        ],
    )
    t = ExitTrigger(
        kind=TriggerKind.INDICATOR,
        condition=cond,
        interval="5m",
        evaluate_intrabar=True,
    )
    rt = _round_trip_trigger(t)
    assert rt.kind == TriggerKind.INDICATOR
    assert rt.interval == "5m"
    assert rt.evaluate_intrabar is True
    assert rt.condition is not None
    assert rt.condition.combinator == "and"
    assert len(rt.condition.children) == 1


def test_trigger_to_dict_omits_none_fields():
    t = ExitTrigger(kind=TriggerKind.MARKET)
    d = t.to_dict()
    assert "price" not in d
    assert "offset_pct" not in d
    assert "trail_unit" not in d
    assert "condition" not in d


def test_trigger_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown TriggerKind"):
        ExitTrigger.from_dict({"kind": "rocket"})


def test_trigger_missing_kind_raises():
    with pytest.raises(ValueError, match="kind missing"):
        ExitTrigger.from_dict({})


# ---------------------------------------------------------------------------
# ExitLeg round-trip
# ---------------------------------------------------------------------------


def test_leg_round_trip_with_multiple_triggers():
    leg = ExitLeg(
        label="profit-target",
        triggers=[
            ExitTrigger(kind=TriggerKind.LIMIT, price=200.0, qty_pct=50.0),
            ExitTrigger(kind=TriggerKind.LIMIT, price=210.0, qty_pct=100.0),
        ],
    )
    rt = ExitLeg.from_dict(leg.to_dict())
    assert rt.label == "profit-target"
    assert len(rt.triggers) == 2
    assert rt.triggers[0].price == 200.0
    assert rt.triggers[1].price == 210.0


def test_leg_default_enabled_true():
    leg = ExitLeg.from_dict({"id": "L1", "triggers": []})
    assert leg.enabled is True


# ---------------------------------------------------------------------------
# OCOGroup
# ---------------------------------------------------------------------------


def test_oco_default_cancel_on_full_closeout():
    g = OCOGroup(leg_ids=("a", "b"))
    assert g.cancel_on == "full_closeout"


def test_oco_any_fire_round_trip():
    g = OCOGroup(leg_ids=("a", "b"), cancel_on="any_fire")
    rt = OCOGroup.from_dict(g.to_dict())
    assert rt.leg_ids == ("a", "b")
    assert rt.cancel_on == "any_fire"


def test_oco_invalid_cancel_on_raises():
    with pytest.raises(ValueError, match="cancel_on must be one of"):
        OCOGroup(leg_ids=("a", "b"), cancel_on="never")


def test_oco_leg_ids_coerced_to_tuple():
    g = OCOGroup(leg_ids=["a", "b"])  # type: ignore[arg-type]
    assert g.leg_ids == ("a", "b")


# ---------------------------------------------------------------------------
# ExitStrategy round-trip + schema
# ---------------------------------------------------------------------------


def _make_bracket_strategy() -> ExitStrategy:
    """Bracket: profit-target leg + hard-stop leg, OCO full_closeout."""
    pt = ExitLeg(
        id="pt",
        label="profit-target",
        triggers=[ExitTrigger(kind=TriggerKind.LIMIT, price=200.0)],
    )
    stop = ExitLeg(
        id="stop",
        label="hard-stop",
        triggers=[ExitTrigger(kind=TriggerKind.STOP, price=180.0)],
    )
    return ExitStrategy(
        name="bracket-AAPL",
        legs=[pt, stop],
        oco_groups=[OCOGroup(leg_ids=("pt", "stop"))],
    )


def test_strategy_round_trip():
    s = _make_bracket_strategy()
    rt = ExitStrategy.from_dict(s.to_dict())
    assert rt.name == "bracket-AAPL"
    assert len(rt.legs) == 2
    assert len(rt.oco_groups) == 1
    assert rt.oco_groups[0].cancel_on == "full_closeout"
    assert rt.eod_kill_switch is True
    assert rt.eod_offset_min == 5


def test_strategy_default_eod_kill_switch_on():
    s = ExitStrategy(name="x")
    assert s.eod_kill_switch is True
    assert s.eod_offset_min == 5


def test_strategy_schema_version_is_current():
    s = ExitStrategy(name="x")
    assert s.schema_version == CURRENT_SCHEMA_VERSION


def test_strategy_load_future_schema_raises():
    d = ExitStrategy(name="x").to_dict()
    d["schema_version"] = CURRENT_SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="schema_version"):
        ExitStrategy.from_dict(d)


def test_strategy_created_with_round_trip():
    s = ExitStrategy(name="x", created_with=CreatedWith(app="myapp", version="1.2.3"))
    rt = ExitStrategy.from_dict(s.to_dict())
    assert rt.created_with.app == "myapp"
    assert rt.created_with.version == "1.2.3"


def test_strategy_extra_round_trip():
    s = ExitStrategy(name="x", extra={"author": "ada"})
    rt = ExitStrategy.from_dict(s.to_dict())
    assert rt.extra == {"author": "ada"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_empty_name_fails():
    s = ExitStrategy(name="", legs=[])
    errs = validate_strategy(s)
    assert any("name is empty" in e for e in errs)


def test_validate_negative_eod_offset_fails():
    s = ExitStrategy(name="x", eod_offset_min=-1)
    errs = validate_strategy(s)
    assert any("eod_offset_min" in e for e in errs)


def test_validate_duplicate_leg_ids_fails():
    s = ExitStrategy(
        name="x",
        legs=[
            ExitLeg(id="L1", triggers=[ExitTrigger(kind=TriggerKind.MARKET)]),
            ExitLeg(id="L1", triggers=[ExitTrigger(kind=TriggerKind.MARKET)]),
        ],
    )
    errs = validate_strategy(s)
    assert any("duplicate leg ids" in e for e in errs)


def test_validate_enabled_leg_no_triggers_fails():
    s = ExitStrategy(name="x", legs=[ExitLeg(id="L1", triggers=[])])
    errs = validate_strategy(s)
    assert any("no triggers" in e for e in errs)


def test_validate_disabled_leg_no_triggers_ok():
    s = ExitStrategy(name="x", legs=[ExitLeg(id="L1", enabled=False, triggers=[])])
    errs = validate_strategy(s)
    assert not any("no triggers" in e for e in errs)


def test_validate_qty_pct_in_range():
    s = ExitStrategy(
        name="x",
        legs=[ExitLeg(id="L1", triggers=[ExitTrigger(kind=TriggerKind.MARKET, qty_pct=0.0)])],
    )
    assert any("qty_pct" in e for e in validate_strategy(s))

    s2 = ExitStrategy(
        name="x",
        legs=[ExitLeg(id="L1", triggers=[ExitTrigger(kind=TriggerKind.MARKET, qty_pct=150.0)])],
    )
    assert any("qty_pct" in e for e in validate_strategy(s2))


def test_validate_limit_requires_exactly_one_price_field():
    # No price set:
    s = ExitStrategy(
        name="x",
        legs=[ExitLeg(id="L1", triggers=[ExitTrigger(kind=TriggerKind.LIMIT)])],
    )
    assert any("limit" in e and "exactly one" in e for e in validate_strategy(s))

    # Two set:
    s2 = ExitStrategy(
        name="x",
        legs=[
            ExitLeg(
                id="L1",
                triggers=[ExitTrigger(kind=TriggerKind.LIMIT, price=200.0, offset_pct=2.0)],
            )
        ],
    )
    assert any("limit" in e and "exactly one" in e for e in validate_strategy(s2))


def test_validate_stop_limit_requires_both_pairs():
    s = ExitStrategy(
        name="x",
        legs=[
            ExitLeg(
                id="L1",
                triggers=[ExitTrigger(kind=TriggerKind.STOP_LIMIT, price=180.0)],
            )
        ],
    )
    errs = validate_strategy(s)
    assert any("stop_limit" in e for e in errs)


def test_validate_trailing_stop_requires_unit_and_value():
    s = ExitStrategy(
        name="x",
        legs=[
            ExitLeg(
                id="L1",
                triggers=[ExitTrigger(kind=TriggerKind.TRAILING_STOP)],
            )
        ],
    )
    errs = validate_strategy(s)
    assert any("trailing_stop" in e for e in errs)


def test_validate_trailing_stop_value_positive():
    s = ExitStrategy(
        name="x",
        legs=[
            ExitLeg(
                id="L1",
                triggers=[
                    ExitTrigger(
                        kind=TriggerKind.TRAILING_STOP,
                        trail_unit=TrailUnit.PERCENT,
                        trail_value=-1.0,
                    )
                ],
            )
        ],
    )
    assert any("trail_value" in e for e in validate_strategy(s))


def test_validate_activation_pair_consistent():
    s = ExitStrategy(
        name="x",
        legs=[
            ExitLeg(
                id="L1",
                triggers=[
                    ExitTrigger(
                        kind=TriggerKind.TRAILING_STOP,
                        trail_unit=TrailUnit.PERCENT,
                        trail_value=2.0,
                        activation_unit=ActivationUnit.PERCENT,
                        # activation_value missing
                    )
                ],
            )
        ],
    )
    assert any("activation" in e for e in validate_strategy(s))


def test_validate_time_of_day_format():
    s = ExitStrategy(
        name="x",
        legs=[
            ExitLeg(
                id="L1",
                triggers=[ExitTrigger(kind=TriggerKind.TIME_OF_DAY, time_of_day="9:30")],
            )
        ],
    )
    assert any("time_of_day" in e for e in validate_strategy(s))

    s2 = ExitStrategy(
        name="x",
        legs=[
            ExitLeg(
                id="L1",
                triggers=[ExitTrigger(kind=TriggerKind.TIME_OF_DAY, time_of_day="25:00")],
            )
        ],
    )
    assert any("time_of_day" in e for e in validate_strategy(s2))


def test_validate_indicator_requires_condition():
    s = ExitStrategy(
        name="x",
        legs=[
            ExitLeg(
                id="L1",
                triggers=[ExitTrigger(kind=TriggerKind.INDICATOR)],
            )
        ],
    )
    assert any("indicator" in e for e in validate_strategy(s))


def test_validate_oco_min_two_legs():
    s = ExitStrategy(
        name="x",
        legs=[ExitLeg(id="A", triggers=[ExitTrigger()])],
        oco_groups=[OCOGroup(leg_ids=("A",))],
    )
    assert any("at least 2" in e for e in validate_strategy(s))


def test_validate_oco_unknown_leg():
    s = ExitStrategy(
        name="x",
        legs=[ExitLeg(id="A", triggers=[ExitTrigger()])],
        oco_groups=[OCOGroup(leg_ids=("A", "X"))],
    )
    assert any("unknown leg_id" in e for e in validate_strategy(s))


def test_validate_oco_disjointness():
    s = ExitStrategy(
        name="x",
        legs=[
            ExitLeg(id="A", triggers=[ExitTrigger()]),
            ExitLeg(id="B", triggers=[ExitTrigger()]),
            ExitLeg(id="C", triggers=[ExitTrigger()]),
        ],
        oco_groups=[
            OCOGroup(leg_ids=("A", "B")),
            OCOGroup(leg_ids=("B", "C")),  # B repeats
        ],
    )
    assert any("disjoint" in e for e in validate_strategy(s))


def test_validate_valid_bracket_no_errors():
    s = _make_bracket_strategy()
    assert validate_strategy(s) == []


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migrate_current_version_passthrough():
    d = {"name": "x", "schema_version": CURRENT_SCHEMA_VERSION}
    out = migrate(d, from_version=CURRENT_SCHEMA_VERSION)
    assert out == d
    assert out is not d  # copy


def test_migrate_future_version_raises():
    with pytest.raises(ValueError):
        migrate({}, from_version=CURRENT_SCHEMA_VERSION + 1)


def test_migrate_unknown_old_version_raises():
    # No prior versions registered yet.
    with pytest.raises(ValueError):
        migrate({}, from_version=0)


# ---------------------------------------------------------------------------
# Bracket round-trip via JSON (full integration)
# ---------------------------------------------------------------------------


def test_bracket_full_json_round_trip():
    import json

    s = _make_bracket_strategy()
    blob = json.dumps(s.to_dict())
    rt = ExitStrategy.from_dict(json.loads(blob))
    assert rt.legs[0].triggers[0].price == 200.0
    assert rt.legs[1].triggers[0].price == 180.0
    assert rt.oco_groups[0].leg_ids == ("pt", "stop")
    assert rt.oco_groups[0].cancel_on == "full_closeout"
