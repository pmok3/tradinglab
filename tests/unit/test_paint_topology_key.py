"""Topology-key instrumentation tests — Stage 0 of the topology-preserving
paint pipeline (``docs/PAINT_PIPELINE_REFACTOR.md``).

``ChartApp._compute_topology_key`` must yield a key that is EQUAL iff the
figure topology is unchanged, and DIFFERENT for every transition that would
need a full ``figure.clear()`` rebuild: compare on/off, interval change,
indicator pane add/remove, indicator reorder, drill-down enter/exit. Data-only
changes (``axis_mode`` / style / params) must be the SAME topology. It must
never raise (it runs on the render hot path purely as instrumentation).

Driven via the unbound method with a lightweight ``SimpleNamespace`` ``self``
(no Tk root) — it only reads a handful of attributes plus a real
``IndicatorManager``.
"""
from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

from tradinglab.app import ChartApp
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager


def _var(value):
    return SimpleNamespace(get=lambda: value)


def _stub(*, compare_on=False, interval="1d", drill=None, configs=()):
    mgr = IndicatorManager()
    for c in configs:
        mgr.add(c)
    return SimpleNamespace(
        compare_var=_var(compare_on),
        _compare=["CMP"] if compare_on else [],
        interval_var=_var(interval),
        _indicator_manager=mgr,
        _drilldown_day=drill,
    )


def _key(stub):
    return ChartApp._compute_topology_key(stub)


def _rsi(length=14):
    return IndicatorConfig(kind_id="rsi", params={"length": length},
                           scopes=frozenset({"main"}))


def test_key_is_hashable_and_deterministic():
    s = _stub(configs=[_rsi()])
    k = _key(s)
    assert isinstance(k, tuple)
    assert hash(k) == hash(_key(s))
    assert _key(s) == _key(s)


def test_interval_change_changes_key():
    assert _key(_stub(interval="1d")) != _key(_stub(interval="5m"))


def test_compare_toggle_changes_key():
    assert _key(_stub(compare_on=False)) != _key(_stub(compare_on=True))


def test_indicator_add_changes_key():
    assert _key(_stub(configs=[])) != _key(_stub(configs=[_rsi()]))


def test_indicator_remove_changes_key():
    assert _key(_stub(configs=[_rsi(14), _rsi(21)])) != _key(_stub(configs=[_rsi(14)]))


def test_indicator_reorder_changes_key():
    a, b = _rsi(14), _rsi(21)
    # Same two panes, opposite order → different ORDERED signature (a reorder
    # swaps which axes hosts which indicator, so the fast path must rebuild).
    assert _key(_stub(configs=[a, b])) != _key(_stub(configs=[b, a]))


def test_drilldown_enter_exit_changes_key():
    day = _dt.date(2024, 6, 3)
    assert _key(_stub(drill=None)) != _key(_stub(drill=day))


def test_axis_mode_change_is_same_topology():
    # axis_mode is a per-pane DATA update (re-applied by render_for_slot), NOT
    # a structural change → SAME topology key.
    base = IndicatorConfig(kind_id="rvol", params={"axis_mode": "centered"},
                           scopes=frozenset({"main"}))
    other = IndicatorConfig(kind_id="rvol", params={"axis_mode": "log"},
                            scopes=frozenset({"main"}))
    other.id = base.id  # only params differ; ordered-id signature is identical
    assert _key(_stub(configs=[base])) == _key(_stub(configs=[other]))


def test_stable_across_repeated_calls():
    s = _stub(compare_on=True, interval="5m", configs=[_rsi()])
    assert _key(s) == _key(s) == _key(s)


def test_never_raises_on_broken_state():
    broken = SimpleNamespace()  # missing every attribute
    k = ChartApp._compute_topology_key(broken)
    assert isinstance(k, tuple)
    assert k[0] is False  # compare_on degraded to False
