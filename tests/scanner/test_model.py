"""Smoke tests for ``tradinglab.scanner.model``.

Round-trip + structural validation. No external deps, no registry
lookups — these tests cover the pure-data layer only.
"""

from __future__ import annotations

import json

import pytest

from tradinglab.scanner.model import (
    ALL_OPERATORS,
    OPERATOR_PARAM_SCHEMA,
    SCHEMA_VERSION,
    VIEW_ACTIVE,
    VIEW_NEW,
    WITHIN_LAST_MODE_ALL,
    WITHIN_LAST_MODE_ANY,
    WITHIN_LAST_MODE_EXACTLY,
    WITHIN_LAST_MODES,
    Condition,
    FieldRef,
    Group,
    MatchEvidence,
    OutputColumn,
    OUTPUT_COL_CONDITION_VALUE,
    OUTPUT_COL_FIELD,
    ScanDefinition,
    ScanOptions,
    UniverseFilter,
    migrate,
)


# ---------------------------------------------------------------------------
# FieldRef
# ---------------------------------------------------------------------------


def test_fieldref_literal_round_trip():
    f = FieldRef.literal(2.5)
    assert f.kind == "literal"
    assert f.value == 2.5
    d = f.to_dict()
    assert d == {"kind": "literal", "value": 2.5}
    assert FieldRef.from_dict(d) == f


def test_fieldref_builtin_round_trip():
    f = FieldRef.builtin("close")
    d = f.to_dict()
    assert d == {"kind": "builtin", "id": "close"}
    assert FieldRef.from_dict(d) == f


def test_fieldref_indicator_round_trip_with_params_and_output_key():
    f = FieldRef.indicator("bbands", params={"length": 20, "stddev": 2}, output_key="upper")
    d = f.to_dict()
    assert d["kind"] == "indicator"
    assert d["id"] == "bbands"
    assert d["params"] == {"length": 20, "stddev": 2}
    assert d["output_key"] == "upper"
    f2 = FieldRef.from_dict(d)
    assert f2.kind == "indicator"
    assert f2.id == "bbands"
    assert f2.params == {"length": 20, "stddev": 2}
    assert f2.output_key == "upper"


def test_fieldref_interval_override_persisted():
    f = FieldRef.indicator("sma", params={"length": 50}, interval="1d")
    d = f.to_dict()
    assert d["interval"] == "1d"
    f2 = FieldRef.from_dict(d)
    assert f2.interval == "1d"


def test_fieldref_literal_rejects_id_or_params():
    with pytest.raises(ValueError):
        FieldRef(kind="literal", id="x", value=1.0)
    with pytest.raises(ValueError):
        FieldRef(kind="literal", params={"a": 1}, value=1.0)


def test_fieldref_non_literal_requires_id():
    with pytest.raises(ValueError):
        FieldRef(kind="indicator")
    with pytest.raises(ValueError):
        FieldRef(kind="builtin")


def test_fieldref_unknown_kind_rejected():
    with pytest.raises(ValueError):
        FieldRef(kind="bogus", id="x")


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------


def test_condition_gt_round_trip():
    c = Condition(
        left=FieldRef.builtin("close"),
        op=">",
        params={"right": FieldRef.literal(100.0)},
        interval="5m",
    )
    d = c.to_dict()
    c2 = Condition.from_dict(d)
    assert c2.op == ">"
    assert c2.left == c.left
    assert c2.params == c.params
    assert c2.id == c.id


def test_condition_within_pct_with_float_param():
    c = Condition(
        left=FieldRef.builtin("close"),
        op="within_pct",
        params={"target": FieldRef.builtin("vwap"), "tolerance_pct": 1.5},
    )
    d = c.to_dict()
    assert d["params"]["tolerance_pct"] == 1.5
    c2 = Condition.from_dict(d)
    assert c2.params["tolerance_pct"] == 1.5
    assert isinstance(c2.params["target"], FieldRef)


def test_condition_crosses_above_with_lookback():
    c = Condition(
        left=FieldRef.builtin("close"),
        op="crosses_above",
        params={
            "right": FieldRef.indicator("sma", params={"length": 20}),
            "lookback": 3,
        },
    )
    d = c.to_dict()
    assert d["params"]["lookback"] == 3
    c2 = Condition.from_dict(d)
    assert c2.params["lookback"] == 3


def test_condition_between_two_field_refs():
    c = Condition(
        left=FieldRef.builtin("close"),
        op="between",
        params={
            "low":  FieldRef.literal(95.0),
            "high": FieldRef.literal(105.0),
        },
    )
    c2 = Condition.from_dict(c.to_dict())
    assert c2.params["low"].value == 95.0
    assert c2.params["high"].value == 105.0


def test_condition_no_param_operators():
    for op in ("inside_bar", "outside_bar", "nr7"):
        c = Condition(left=FieldRef.builtin("close"), op=op, params={})
        c2 = Condition.from_dict(c.to_dict())
        assert c2.op == op
        assert c2.params == {}


def test_condition_unknown_op_rejected():
    with pytest.raises(ValueError):
        Condition(left=FieldRef.builtin("close"), op="weird", params={})


def test_condition_missing_required_param_rejected():
    with pytest.raises(ValueError):
        Condition(left=FieldRef.builtin("close"), op=">", params={})


def test_condition_extra_param_rejected():
    with pytest.raises(ValueError):
        Condition(
            left=FieldRef.builtin("close"),
            op=">",
            params={
                "right": FieldRef.literal(100.0),
                "bogus": 1,
            },
        )


def test_condition_bool_param_rejected_on_serialize():
    # Construct manually — bypass __post_init__ schema check (the bool
    # is in a numeric slot whose key is valid).
    c = Condition.__new__(Condition)
    c.left = FieldRef.builtin("close")
    c.op = "crosses_above"
    c.params = {"right": FieldRef.literal(100.0), "lookback": True}  # type: ignore[dict-item]
    c.interval = "5m"
    c.enabled = True
    c.id = "x"
    c.comment = ""
    with pytest.raises(TypeError):
        c.to_dict()


def test_condition_all_operators_have_examples():
    """Every operator in the registry can build a valid condition."""
    samples = {
        ">":  {"right": FieldRef.literal(1.0)},
        "<":  {"right": FieldRef.literal(1.0)},
        ">=": {"right": FieldRef.literal(1.0)},
        "<=": {"right": FieldRef.literal(1.0)},
        "==": {"right": FieldRef.literal(1.0)},
        "!=": {"right": FieldRef.literal(1.0)},
        "between":        {"low": FieldRef.literal(0.0), "high": FieldRef.literal(1.0)},
        "crosses_above":  {"right": FieldRef.literal(1.0), "lookback": 1},
        "crosses_below":  {"right": FieldRef.literal(1.0), "lookback": 1},
        "is_rising":      {"lookback": 3},
        "is_falling":     {"lookback": 3},
        "within_pct":     {"target": FieldRef.literal(100.0), "tolerance_pct": 0.5},
        "new_high_n_bars": {"n": 12},
        "new_low_n_bars":  {"n": 12},
        "holding_above":   {"reference": FieldRef.builtin("vwap"), "bars": 5},
        "holding_below":   {"reference": FieldRef.builtin("vwap"), "bars": 5},
        "inside_bar":  {},
        "outside_bar": {},
        "nr7":         {},
    }
    assert set(samples.keys()) == set(ALL_OPERATORS)
    for op, params in samples.items():
        c = Condition(left=FieldRef.builtin("close"), op=op, params=params)
        c2 = Condition.from_dict(c.to_dict())
        assert c2.op == op
        assert c.params.keys() == {n for n, _ in OPERATOR_PARAM_SCHEMA[op]}


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


def test_group_nested_round_trip():
    g = Group(
        combinator="and",
        children=[
            Condition(left=FieldRef.builtin("close"), op=">",
                      params={"right": FieldRef.literal(100.0)}),
            Group(combinator="or", children=[
                Condition(left=FieldRef.builtin("volume"), op=">",
                          params={"right": FieldRef.literal(1e6)}),
                Condition(left=FieldRef.builtin("close"), op="inside_bar", params={}),
            ]),
        ],
    )
    d = g.to_dict()
    g2 = Group.from_dict(d)
    assert g2.combinator == "and"
    assert len(g2.children) == 2
    assert isinstance(g2.children[0], Condition)
    assert isinstance(g2.children[1], Group)
    assert g2.children[1].combinator == "or"
    assert len(g2.children[1].children) == 2


def test_group_invalid_combinator_rejected():
    with pytest.raises(ValueError):
        Group(combinator="xor")


def test_group_unknown_child_type_rejected():
    with pytest.raises(ValueError):
        Group.from_dict({"type": "group", "combinator": "and",
                         "children": [{"type": "weird"}]})


# ---------------------------------------------------------------------------
# Within-last-N-bars (Condition + Group)
# ---------------------------------------------------------------------------


def _make_simple_condition(**kw):
    return Condition(
        left=FieldRef.builtin("close"),
        op=">",
        params={"right": FieldRef.literal(100.0)},
        **kw,
    )


def test_condition_within_last_defaults_to_zero_any():
    c = _make_simple_condition()
    assert c.within_last_bars == 0
    assert c.within_last_mode == WITHIN_LAST_MODE_ANY


def test_group_within_last_defaults_to_zero_any():
    g = Group(combinator="and", children=[_make_simple_condition()])
    assert g.within_last_bars == 0
    assert g.within_last_mode == WITHIN_LAST_MODE_ANY


def test_condition_to_dict_omits_within_last_when_default():
    c = _make_simple_condition()
    d = c.to_dict()
    assert "within_last_bars" not in d
    assert "within_last_mode" not in d


def test_group_to_dict_omits_within_last_when_default():
    g = Group(combinator="and", children=[_make_simple_condition()])
    d = g.to_dict()
    assert "within_last_bars" not in d
    assert "within_last_mode" not in d


def test_condition_round_trip_persists_nondefault_within_last():
    c = _make_simple_condition(
        within_last_bars=3,
        within_last_mode=WITHIN_LAST_MODE_ALL,
    )
    d = c.to_dict()
    assert d["within_last_bars"] == 3
    assert d["within_last_mode"] == WITHIN_LAST_MODE_ALL
    c2 = Condition.from_dict(d)
    assert c2.within_last_bars == 3
    assert c2.within_last_mode == WITHIN_LAST_MODE_ALL


def test_group_round_trip_persists_nondefault_within_last():
    g = Group(
        combinator="or",
        children=[_make_simple_condition()],
        within_last_bars=2,
        within_last_mode=WITHIN_LAST_MODE_EXACTLY,
    )
    d = g.to_dict()
    assert d["within_last_bars"] == 2
    assert d["within_last_mode"] == WITHIN_LAST_MODE_EXACTLY
    g2 = Group.from_dict(d)
    assert g2.within_last_bars == 2
    assert g2.within_last_mode == WITHIN_LAST_MODE_EXACTLY


def test_condition_within_last_omits_only_default_value():
    c = _make_simple_condition(within_last_bars=2)
    d = c.to_dict()
    assert d["within_last_bars"] == 2
    # Mode is still default → omitted
    assert "within_last_mode" not in d


def test_condition_within_last_negative_bars_rejected():
    with pytest.raises(ValueError, match="within_last_bars"):
        _make_simple_condition(within_last_bars=-1)


def test_group_within_last_negative_bars_rejected():
    with pytest.raises(ValueError, match="within_last_bars"):
        Group(combinator="and", children=[], within_last_bars=-3)


def test_condition_within_last_invalid_mode_rejected():
    with pytest.raises(ValueError, match="within_last_mode"):
        _make_simple_condition(within_last_mode="bogus")


def test_group_within_last_invalid_mode_rejected():
    with pytest.raises(ValueError, match="within_last_mode"):
        Group(combinator="and", children=[], within_last_mode="any-of")


def test_condition_within_last_bool_bars_rejected():
    # Guard against accidental bool-as-int (Python treats True as int).
    with pytest.raises(ValueError, match="within_last_bars"):
        _make_simple_condition(within_last_bars=True)  # type: ignore[arg-type]


def test_condition_legacy_dict_loads_with_within_last_defaults():
    # Simulate a pre-feature JSON: no within_last_* keys at all.
    legacy = {
        "type": "condition",
        "id": "abc",
        "enabled": True,
        "interval": "5m",
        "left": {"kind": "builtin", "id": "close"},
        "op": ">",
        "params": {"right": {"kind": "literal", "value": 100.0}},
    }
    c = Condition.from_dict(legacy)
    assert c.within_last_bars == 0
    assert c.within_last_mode == WITHIN_LAST_MODE_ANY


def test_group_legacy_dict_loads_with_within_last_defaults():
    legacy = {
        "type": "group",
        "id": "g1",
        "enabled": True,
        "combinator": "and",
        "children": [],
    }
    g = Group.from_dict(legacy)
    assert g.within_last_bars == 0
    assert g.within_last_mode == WITHIN_LAST_MODE_ANY


def test_within_last_modes_enum_completeness():
    assert set(WITHIN_LAST_MODES) == {
        WITHIN_LAST_MODE_ANY,
        WITHIN_LAST_MODE_ALL,
        WITHIN_LAST_MODE_EXACTLY,
    }


# ---------------------------------------------------------------------------
# MatchEvidence
# ---------------------------------------------------------------------------


def test_match_evidence_minimal_round_trip():
    e = MatchEvidence(node_id="cond-1", bars_ago=2)
    d = e.to_dict()
    assert d == {"node_id": "cond-1", "bars_ago": 2}
    assert MatchEvidence.from_dict(d) == e


def test_match_evidence_full_round_trip():
    e = MatchEvidence(
        node_id="cond-2",
        bars_ago=1,
        timestamp="2026-05-06T10:35:00-04:00",
        value=42.5,
    )
    d = e.to_dict()
    assert d["node_id"] == "cond-2"
    assert d["bars_ago"] == 1
    assert d["timestamp"] == "2026-05-06T10:35:00-04:00"
    assert d["value"] == 42.5
    assert MatchEvidence.from_dict(d) == e


def test_match_evidence_value_zero_is_persisted():
    # Float 0.0 is falsy in Python; make sure to_dict doesn't drop it.
    e = MatchEvidence(node_id="x", bars_ago=0, value=0.0)
    d = e.to_dict()
    assert d["value"] == 0.0
    assert MatchEvidence.from_dict(d) == e


# ---------------------------------------------------------------------------
# UniverseFilter
# ---------------------------------------------------------------------------


def test_universe_filter_all():
    u = UniverseFilter.all()
    assert u.to_dict() == {"kind": "all"}
    assert UniverseFilter.from_dict({"kind": "all"}) == u


def test_universe_filter_watchlist():
    u = UniverseFilter(kind="watchlist", name="My WL")
    assert u.to_dict() == {"kind": "watchlist", "name": "My WL"}


def test_universe_filter_symbols_normalized_uppercase():
    u = UniverseFilter(kind="symbols", symbols=("aapl", "msft"))
    assert u.symbols == ("AAPL", "MSFT")


def test_universe_filter_invalid_kind():
    with pytest.raises(ValueError):
        UniverseFilter(kind="bogus")


def test_universe_filter_watchlist_requires_name():
    with pytest.raises(ValueError):
        UniverseFilter(kind="watchlist")


def test_universe_filter_symbols_requires_list():
    with pytest.raises(ValueError):
        UniverseFilter(kind="symbols", symbols=())


# ---------------------------------------------------------------------------
# OutputColumn
# ---------------------------------------------------------------------------


def test_output_column_condition_value_round_trip():
    c = OutputColumn(kind=OUTPUT_COL_CONDITION_VALUE,
                     condition_id="abc-123",
                     label="5m RVOL")
    d = c.to_dict()
    c2 = OutputColumn.from_dict(d)
    assert c2.kind == OUTPUT_COL_CONDITION_VALUE
    assert c2.condition_id == "abc-123"


def test_output_column_field_round_trip():
    c = OutputColumn(kind=OUTPUT_COL_FIELD,
                     field=FieldRef.indicator("atr", params={"length": 14}),
                     interval="1d",
                     label="1d ATR")
    d = c.to_dict()
    c2 = OutputColumn.from_dict(d)
    assert c2.kind == OUTPUT_COL_FIELD
    assert c2.interval == "1d"
    assert c2.field is not None and c2.field.id == "atr"


def test_output_column_condition_value_requires_id():
    with pytest.raises(ValueError):
        OutputColumn(kind=OUTPUT_COL_CONDITION_VALUE)


def test_output_column_field_requires_field():
    with pytest.raises(ValueError):
        OutputColumn(kind=OUTPUT_COL_FIELD, interval="5m")


# ---------------------------------------------------------------------------
# ScanOptions
# ---------------------------------------------------------------------------


def test_scan_options_defaults():
    o = ScanOptions()
    assert o.default_view == VIEW_NEW
    assert o.show_insufficient_data_rows is False
    assert o.new_view_capacity == 500


def test_scan_options_round_trip_with_extra():
    o = ScanOptions(
        show_insufficient_data_rows=True,
        default_view=VIEW_ACTIVE,
        new_view_capacity=200,
        extra={"future_knob": 42},
    )
    o2 = ScanOptions.from_dict(o.to_dict())
    assert o2.show_insufficient_data_rows is True
    assert o2.default_view == VIEW_ACTIVE
    assert o2.new_view_capacity == 200
    assert o2.extra == {"future_knob": 42}


def test_scan_options_invalid_default_view():
    with pytest.raises(ValueError):
        ScanOptions(default_view="bogus")


def test_scan_options_unknown_top_level_keys_preserved_in_extra():
    raw = {
        "default_view": "new",
        "show_insufficient_data_rows": False,
        "new_view_capacity": 500,
        "future_knob_inline": 7,
    }
    o = ScanOptions.from_dict(raw)
    assert o.extra.get("future_knob_inline") == 7


# ---------------------------------------------------------------------------
# ScanDefinition
# ---------------------------------------------------------------------------


def _make_full_scan() -> ScanDefinition:
    return ScanDefinition(
        name="Strong RVOL momentum",
        primary_interval="5m",
        universe_filter=UniverseFilter.all(),
        rank_by=FieldRef.indicator("rvol", params={"mode": "cumulative"}),
        rank_dir="desc",
        root=Group(
            combinator="and",
            children=[
                Condition(
                    left=FieldRef.indicator("rvol", params={"mode": "cumulative"}),
                    op=">",
                    params={"right": FieldRef.literal(2.0)},
                    interval="5m",
                ),
                Condition(
                    left=FieldRef.builtin("close"),
                    op="crosses_above",
                    params={
                        "right": FieldRef.indicator("sma", params={"length": 20}),
                        "lookback": 1,
                    },
                    interval="5m",
                ),
                Group(
                    combinator="or",
                    children=[
                        Condition(
                            left=FieldRef.builtin("close"),
                            op=">",
                            params={
                                "right": FieldRef.indicator("sma", params={"length": 50}),
                            },
                            interval="1d",
                        ),
                        Condition(
                            left=FieldRef.builtin("close"),
                            op="new_high_n_bars",
                            params={"n": 12},
                            interval="5m",
                        ),
                    ],
                ),
                Condition(
                    left=FieldRef.builtin("close"),
                    op="within_pct",
                    params={
                        "target": FieldRef.builtin("vwap"),
                        "tolerance_pct": 0.5,
                    },
                    interval="5m",
                ),
            ],
        ),
    )


def test_scan_definition_round_trip_via_json():
    s = _make_full_scan()
    raw = json.dumps(s.to_dict())
    d = json.loads(raw)
    s2 = ScanDefinition.from_dict(d)
    assert s2.to_dict() == s.to_dict()


def test_scan_definition_id_and_timestamps_set():
    s = _make_full_scan()
    assert s.id
    assert s.created_at and s.updated_at
    s2 = s.touch()
    assert s2.updated_at >= s.updated_at


def test_scan_definition_output_columns_optional():
    s = _make_full_scan()
    d = s.to_dict()
    assert d["output_columns"] is None
    s2 = ScanDefinition(
        name="x",
        root=Group(),
        output_columns=[
            OutputColumn(kind=OUTPUT_COL_CONDITION_VALUE, condition_id="abc"),
        ],
    )
    d2 = s2.to_dict()
    assert isinstance(d2["output_columns"], list)
    assert len(d2["output_columns"]) == 1


def test_scan_definition_requires_name():
    with pytest.raises(ValueError):
        ScanDefinition(name="", root=Group())


def test_scan_definition_invalid_rank_dir():
    with pytest.raises(ValueError):
        ScanDefinition(name="x", root=Group(), rank_dir="middle")


def test_scan_definition_all_conditions_walks_tree():
    s = _make_full_scan()
    # Structure: [cond, cond, group(2 leaves), cond] → 3 top-level + 2 nested = 5
    assert len(s.all_conditions()) == 5


# ---------------------------------------------------------------------------
# migrate()
# ---------------------------------------------------------------------------


def test_migrate_v1_is_noop():
    d = _make_full_scan().to_dict()
    out = migrate(d, from_version=1)
    assert out["schema_version"] == SCHEMA_VERSION


def test_migrate_rejects_future_version():
    with pytest.raises(ValueError):
        migrate({"schema_version": 99}, from_version=99)
