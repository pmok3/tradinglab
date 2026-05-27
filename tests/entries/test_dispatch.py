"""Tests for :mod:`tradinglab.entries.dispatch` — the shared
trigger-dispatch registry (audit item #4).

Pins three guarantees:

1. Each registered handler returns the expected ``(fires, evidence)``
   tuple for its kind in isolation.
2. The registry is the single source of truth — popping a kind from
   ``_ENTRY_DISPATCH`` removes it from both ``check_trigger_fires``
   (the entries-side facade) AND the mechanical strategy_tester's
   ``_ENTRY_HANDLERS`` (back-compat alias). Drift is structurally
   impossible.
3. The same handler invoked with the live-flavoured context and the
   mechanical-flavoured context returns equivalent fire/no-fire
   decisions for shared-shape inputs (the unification contract).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tradinglab.entries import dispatch as ed
from tradinglab.entries.dispatch import (
    _ENTRY_DISPATCH,
    BarView,
    TriggerContext,
    check_trigger_fires,
    reference_price,
    signal_price_for_kind,
    supported_trigger_kinds,
)
from tradinglab.entries.model import Direction, EntryTrigger, TriggerKind
from tradinglab.entries.signals import EntryOrderKind

# ---------------------------------------------------------------------------
# BarView
# ---------------------------------------------------------------------------


class TestBarView:
    def test_from_tuple(self):
        bv = BarView.from_any((10.0, 12.0, 9.0, 11.0))
        assert (bv.open, bv.high, bv.low, bv.close) == (10.0, 12.0, 9.0, 11.0)

    def test_from_object_with_attributes(self):
        bar = SimpleNamespace(open=1.0, high=2.0, low=0.5, close=1.5)
        bv = BarView.from_any(bar)
        assert (bv.open, bv.high, bv.low, bv.close) == (1.0, 2.0, 0.5, 1.5)

    def test_from_object_missing_attrs_defaults_zero(self):
        bv = BarView.from_any(SimpleNamespace())
        assert (bv.open, bv.high, bv.low, bv.close) == (0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Per-kind handlers
# ---------------------------------------------------------------------------


def _bar(o=100.0, h=101.0, lo=99.0, c=100.5) -> BarView:
    return BarView(open=o, high=h, low=lo, close=c)


def _ctx(direction=Direction.LONG, **overrides: Any) -> TriggerContext:
    return TriggerContext(
        direction=direction, bar=overrides.pop("bar", _bar()), **overrides,
    )


class TestMarketHandler:
    def test_fires_on_close(self):
        t = EntryTrigger(kind=TriggerKind.MARKET)
        fired, ev = check_trigger_fires(t, _ctx(is_close=True))
        assert fired is True
        assert ev == []

    def test_no_fire_on_forming(self):
        t = EntryTrigger(kind=TriggerKind.MARKET)
        fired, ev = check_trigger_fires(t, _ctx(is_close=False))
        assert fired is False
        assert ev == []


class TestLimitHandler:
    def test_long_fires_when_low_reaches_price(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT, price=99.0)
        fired, _ = check_trigger_fires(
            t, _ctx(bar=_bar(lo=98.5)),
        )
        assert fired is True

    def test_long_no_fire_when_low_above_price(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT, price=99.0)
        fired, _ = check_trigger_fires(
            t, _ctx(bar=_bar(lo=99.5)),
        )
        assert fired is False

    def test_short_fires_when_high_reaches_price(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT, price=101.0)
        fired, _ = check_trigger_fires(
            t, _ctx(direction=Direction.SHORT, bar=_bar(h=101.5)),
        )
        assert fired is True


class TestStopHandler:
    def test_long_fires_when_high_reaches_stop(self):
        t = EntryTrigger(kind=TriggerKind.STOP, stop_price=101.0)
        fired, _ = check_trigger_fires(
            t, _ctx(bar=_bar(h=101.2)),
        )
        assert fired is True

    def test_short_fires_when_low_reaches_stop(self):
        t = EntryTrigger(kind=TriggerKind.STOP, stop_price=99.0)
        fired, _ = check_trigger_fires(
            t, _ctx(direction=Direction.SHORT, bar=_bar(lo=98.5)),
        )
        assert fired is True


class TestStopLimitHandler:
    def test_long_fires_both_legs(self):
        t = EntryTrigger(
            kind=TriggerKind.STOP_LIMIT, stop_price=101.0, price=102.0,
        )
        # stop_hit = high >= stop_price; limit_hit = low <= price (limit)
        fired, _ = check_trigger_fires(
            t, _ctx(bar=_bar(h=101.5, lo=100.0)),
        )
        assert fired is True

    def test_long_no_fire_stop_not_touched(self):
        t = EntryTrigger(
            kind=TriggerKind.STOP_LIMIT, stop_price=101.0, price=102.0,
        )
        fired, _ = check_trigger_fires(
            t, _ctx(bar=_bar(h=100.5, lo=100.0)),
        )
        assert fired is False


# ---------------------------------------------------------------------------
# INDICATOR handler — both call sites tested
# ---------------------------------------------------------------------------


class _FakeScannerCtx:
    """Minimal stand-in for :class:`scanner.engine.EvaluationContext`."""

    def __init__(self, *, evidence=()):
        self.evidence = list(evidence)
        self.symbol = "TEST"
        self.current_index = 0


class TestIndicatorHandler:
    def test_no_fire_on_forming_bar(self, monkeypatch):
        monkeypatch.setattr(ed, "_evaluate_group", lambda *_a, **_k: True)
        cond = object()
        t = EntryTrigger(kind=TriggerKind.INDICATOR, condition=cond)
        fired, _ = check_trigger_fires(
            t,
            _ctx(
                is_close=False,
                scanner_eval_ctx=_FakeScannerCtx(),
            ),
        )
        assert fired is False

    def test_no_fire_when_eval_ctx_missing(self):
        cond = object()
        t = EntryTrigger(kind=TriggerKind.INDICATOR, condition=cond)
        fired, _ = check_trigger_fires(t, _ctx())
        assert fired is False

    def test_no_fire_when_condition_missing(self):
        t = EntryTrigger(kind=TriggerKind.INDICATOR, condition=None)
        fired, _ = check_trigger_fires(
            t, _ctx(scanner_eval_ctx=_FakeScannerCtx()),
        )
        assert fired is False

    def test_fires_when_evaluate_group_returns_true(self, monkeypatch):
        monkeypatch.setattr(ed, "_evaluate_group", lambda *_a, **_k: True)
        cond = object()
        t = EntryTrigger(kind=TriggerKind.INDICATOR, condition=cond)
        ev_obj = SimpleNamespace(node_id="x", bars_ago=1)
        fake = _FakeScannerCtx(evidence=[ev_obj])
        fired, evidence = check_trigger_fires(
            t, _ctx(scanner_eval_ctx=fake),
        )
        assert fired is True
        assert evidence == [ev_obj]

    def test_uses_normalized_conditions_when_provided(self, monkeypatch):
        captured = {}

        def _stub(cond, _ctx):
            captured["cond"] = cond
            return True

        monkeypatch.setattr(ed, "_evaluate_group", _stub)
        original = object()
        normalized = object()
        t = EntryTrigger(
            kind=TriggerKind.INDICATOR, condition=original, id="tid",
        )
        fired, _ = check_trigger_fires(
            t,
            _ctx(
                scanner_eval_ctx=_FakeScannerCtx(),
                normalized_conditions={"tid": normalized},
            ),
        )
        assert fired is True
        assert captured["cond"] is normalized

    def test_notimplemented_returns_no_fire(self, monkeypatch):
        def _raises(*_a, **_k):
            raise NotImplementedError

        monkeypatch.setattr(ed, "_evaluate_group", _raises)
        t = EntryTrigger(kind=TriggerKind.INDICATOR, condition=object())
        fired, _ = check_trigger_fires(
            t, _ctx(scanner_eval_ctx=_FakeScannerCtx()),
        )
        assert fired is False

    def test_generic_exception_returns_no_fire(self, monkeypatch):
        def _raises(*_a, **_k):
            raise RuntimeError("boom")

        monkeypatch.setattr(ed, "_evaluate_group", _raises)
        t = EntryTrigger(kind=TriggerKind.INDICATOR, condition=object())
        fired, _ = check_trigger_fires(
            t, _ctx(scanner_eval_ctx=_FakeScannerCtx()),
        )
        assert fired is False


# ---------------------------------------------------------------------------
# SCANNER_ALERT handler — live path AND mechanical path
# ---------------------------------------------------------------------------


class TestScannerAlertLivePath:
    def test_fires_when_row_present(self):
        ev_obj = SimpleNamespace(node_id="z")
        row = SimpleNamespace(symbol="AAPL", evidence=[ev_obj])
        t = EntryTrigger(kind=TriggerKind.SCANNER_ALERT, scanner_id="s1")
        fired, evidence = check_trigger_fires(
            t, _ctx(scanner_row=row),
        )
        assert fired is True
        assert evidence == [ev_obj]

    def test_no_fire_with_no_row_and_no_mechanical_ctx(self):
        t = EntryTrigger(kind=TriggerKind.SCANNER_ALERT, scanner_id="s1")
        fired, _ = check_trigger_fires(t, _ctx())
        assert fired is False


class TestScannerAlertMechanicalPath:
    def test_bar0_observes_no_fire(self, monkeypatch):
        # First evaluation should record current match but not fire.
        monkeypatch.setattr(ed, "_evaluate_group", lambda *_a, **_k: True)
        prev_match: dict[str, bool] = {}
        t = EntryTrigger(
            kind=TriggerKind.SCANNER_ALERT, scanner_id="s1", id="tid",
        )
        fired, _ = check_trigger_fires(
            t,
            _ctx(
                scanner_eval_ctx=_FakeScannerCtx(),
                normalized_conditions={"tid": object()},
                scanner_alert_prev_match=prev_match,
            ),
        )
        assert fired is False
        assert prev_match["tid"] is True

    def test_false_to_true_transition_fires(self, monkeypatch):
        monkeypatch.setattr(ed, "_evaluate_group", lambda *_a, **_k: True)
        prev_match: dict[str, bool] = {"tid": False}
        t = EntryTrigger(
            kind=TriggerKind.SCANNER_ALERT, scanner_id="s1", id="tid",
        )
        fired, _ = check_trigger_fires(
            t,
            _ctx(
                scanner_eval_ctx=_FakeScannerCtx(),
                normalized_conditions={"tid": object()},
                scanner_alert_prev_match=prev_match,
            ),
        )
        assert fired is True
        assert prev_match["tid"] is True

    def test_true_to_true_no_fire(self, monkeypatch):
        monkeypatch.setattr(ed, "_evaluate_group", lambda *_a, **_k: True)
        prev_match: dict[str, bool] = {"tid": True}
        t = EntryTrigger(
            kind=TriggerKind.SCANNER_ALERT, scanner_id="s1", id="tid",
        )
        fired, _ = check_trigger_fires(
            t,
            _ctx(
                scanner_eval_ctx=_FakeScannerCtx(),
                normalized_conditions={"tid": object()},
                scanner_alert_prev_match=prev_match,
            ),
        )
        assert fired is False

    def test_no_scanner_id_no_fire(self, monkeypatch):
        monkeypatch.setattr(ed, "_evaluate_group", lambda *_a, **_k: True)
        t = EntryTrigger(kind=TriggerKind.SCANNER_ALERT, scanner_id="")
        fired, _ = check_trigger_fires(
            t,
            _ctx(
                scanner_eval_ctx=_FakeScannerCtx(),
                normalized_conditions={},
                scanner_alert_prev_match={},
            ),
        )
        assert fired is False

    def test_condition_missing_from_cache_no_fire(self, monkeypatch):
        monkeypatch.setattr(ed, "_evaluate_group", lambda *_a, **_k: True)
        t = EntryTrigger(
            kind=TriggerKind.SCANNER_ALERT, scanner_id="s1", id="tid",
        )
        fired, _ = check_trigger_fires(
            t,
            _ctx(
                scanner_eval_ctx=_FakeScannerCtx(),
                normalized_conditions={},
                scanner_alert_prev_match={},
            ),
        )
        assert fired is False


# ---------------------------------------------------------------------------
# Registry contract — drift is structurally impossible
# ---------------------------------------------------------------------------


class TestRegistryContract:
    def test_every_trigger_kind_has_handler(self):
        """Every value in :class:`TriggerKind` must be in the dispatch."""
        kinds = set(TriggerKind)
        registered = supported_trigger_kinds()
        missing = kinds - registered
        assert not missing, f"unregistered trigger kinds: {missing}"

    def test_strategy_tester_alias_is_same_object(self):
        """``strategy_tester._ENTRY_HANDLERS`` must be the SAME dict
        object as ``entries.dispatch._ENTRY_DISPATCH`` — that's the
        structural drift guarantee.
        """
        from tradinglab.strategy_tester import evaluator as st_eval

        assert st_eval._ENTRY_HANDLERS is _ENTRY_DISPATCH

    def test_unknown_kind_returns_false(self):
        """``check_trigger_fires`` returns ``(False, [])`` when no
        handler is registered. Callers that need a typed error raise
        themselves (the strategy_tester does this via
        :class:`UnsupportedTriggerKind`).
        """
        t = EntryTrigger(kind=TriggerKind.MARKET)
        saved = _ENTRY_DISPATCH.pop(TriggerKind.MARKET)
        try:
            fired, evidence = check_trigger_fires(t, _ctx(is_close=True))
            assert fired is False
            assert evidence == []
        finally:
            _ENTRY_DISPATCH[TriggerKind.MARKET] = saved

    def test_adding_kind_lights_up_both_evaluators(self):
        """Adding an entry to ``_ENTRY_DISPATCH`` is immediately
        visible via :func:`supported_trigger_kinds` (consumed by both
        evaluators).
        """
        sentinel_kind = "test_sentinel_kind"  # NOT a real TriggerKind enum

        def _h(_trig, _ctx):
            return True, []

        _ENTRY_DISPATCH[sentinel_kind] = _h  # type: ignore[index]
        try:
            assert sentinel_kind in supported_trigger_kinds()
        finally:
            del _ENTRY_DISPATCH[sentinel_kind]
        assert sentinel_kind not in supported_trigger_kinds()


# ---------------------------------------------------------------------------
# reference_price / signal_price_for_kind
# ---------------------------------------------------------------------------


class TestReferencePrice:
    def test_market_uses_close(self):
        t = EntryTrigger(kind=TriggerKind.MARKET)
        bar = SimpleNamespace(close=42.5)
        assert reference_price(t, bar) == 42.5

    def test_limit_uses_trigger_price(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT, price=10.0)
        assert reference_price(t, SimpleNamespace(close=99.0)) == 10.0

    def test_stop_uses_trigger_stop_price(self):
        t = EntryTrigger(kind=TriggerKind.STOP, stop_price=20.0)
        assert reference_price(t, SimpleNamespace(close=99.0)) == 20.0

    def test_stop_limit_uses_trigger_price(self):
        t = EntryTrigger(
            kind=TriggerKind.STOP_LIMIT, stop_price=20.0, price=22.0,
        )
        assert reference_price(t, SimpleNamespace(close=99.0)) == 22.0

    def test_indicator_uses_close(self):
        t = EntryTrigger(kind=TriggerKind.INDICATOR, condition=object())
        assert reference_price(t, SimpleNamespace(close=11.0)) == 11.0

    def test_scanner_alert_uses_close(self):
        t = EntryTrigger(kind=TriggerKind.SCANNER_ALERT, scanner_id="s1")
        assert reference_price(t, SimpleNamespace(close=33.0)) == 33.0

    def test_missing_limit_price_returns_none(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT)
        assert reference_price(t, SimpleNamespace(close=99.0)) is None


class TestSignalPriceForKind:
    def test_market_returns_market_no_prices(self):
        t = EntryTrigger(kind=TriggerKind.MARKET)
        kind, price, limit = signal_price_for_kind(
            TriggerKind.MARKET, t, SimpleNamespace(close=10.0),
        )
        assert (kind, price, limit) == (EntryOrderKind.MARKET, None, None)

    def test_limit_returns_limit_with_price(self):
        t = EntryTrigger(kind=TriggerKind.LIMIT, price=10.0)
        kind, price, limit = signal_price_for_kind(
            TriggerKind.LIMIT, t, SimpleNamespace(close=99.0),
        )
        assert (kind, price, limit) == (EntryOrderKind.LIMIT, 10.0, None)

    def test_stop_returns_stop_with_stop_price(self):
        t = EntryTrigger(kind=TriggerKind.STOP, stop_price=20.0)
        kind, price, limit = signal_price_for_kind(
            TriggerKind.STOP, t, SimpleNamespace(close=99.0),
        )
        assert (kind, price, limit) == (EntryOrderKind.STOP, 20.0, None)

    def test_stop_limit_returns_both_prices(self):
        t = EntryTrigger(
            kind=TriggerKind.STOP_LIMIT, stop_price=20.0, price=22.0,
        )
        kind, price, limit = signal_price_for_kind(
            TriggerKind.STOP_LIMIT, t, SimpleNamespace(close=99.0),
        )
        assert (kind, price, limit) == (EntryOrderKind.STOP_LIMIT, 20.0, 22.0)

    def test_indicator_collapses_to_market(self):
        t = EntryTrigger(kind=TriggerKind.INDICATOR, condition=object())
        kind, price, limit = signal_price_for_kind(
            TriggerKind.INDICATOR, t, SimpleNamespace(close=99.0),
        )
        assert (kind, price, limit) == (EntryOrderKind.MARKET, None, None)

    def test_scanner_alert_collapses_to_market(self):
        t = EntryTrigger(kind=TriggerKind.SCANNER_ALERT, scanner_id="s1")
        kind, price, limit = signal_price_for_kind(
            TriggerKind.SCANNER_ALERT, t, SimpleNamespace(close=99.0),
        )
        assert (kind, price, limit) == (EntryOrderKind.MARKET, None, None)


# ---------------------------------------------------------------------------
# Live-vs-mechanical context parity — same handler, same outcome for
# price-only kinds when both call sites feed the same bar shape.
# ---------------------------------------------------------------------------


class TestLiveMechanicalParity:
    """For price-only kinds, the dispatch returns the same fire
    decision regardless of whether the call site is the live
    evaluator (passes a Bar-like object) or the mechanical evaluator
    (passes a ``(o, h, l, c)`` tuple). This is the headline benefit
    of unification.
    """

    @pytest.mark.parametrize(
        ("kind", "direction", "trigger_kwargs", "bar_kwargs", "expected"),
        [
            (TriggerKind.MARKET, Direction.LONG, {}, {}, True),
            (
                TriggerKind.LIMIT, Direction.LONG, {"price": 99.0},
                {"lo": 98.0}, True,
            ),
            (
                TriggerKind.LIMIT, Direction.LONG, {"price": 99.0},
                {"lo": 99.5}, False,
            ),
            (
                TriggerKind.STOP, Direction.LONG, {"stop_price": 101.0},
                {"h": 101.5}, True,
            ),
            (
                TriggerKind.STOP, Direction.SHORT, {"stop_price": 99.0},
                {"lo": 98.5}, True,
            ),
            (
                TriggerKind.STOP_LIMIT, Direction.LONG,
                {"stop_price": 101.0, "price": 102.0},
                {"h": 101.5, "lo": 100.0}, True,
            ),
        ],
    )
    def test_same_outcome_for_tuple_and_object_bar(
        self, kind, direction, trigger_kwargs, bar_kwargs, expected,
    ):
        t = EntryTrigger(kind=kind, **trigger_kwargs)

        # Live-style: object with attributes.
        defaults = {"o": 100.0, "h": 101.0, "lo": 99.0, "c": 100.5}
        defaults.update(bar_kwargs)
        live_bar = SimpleNamespace(
            open=defaults["o"], high=defaults["h"],
            low=defaults["lo"], close=defaults["c"],
        )
        live_ctx = TriggerContext(
            direction=direction, bar=BarView.from_any(live_bar),
            is_close=True,
        )

        # Mechanical-style: (o, h, l, c) tuple.
        mech_bar = (defaults["o"], defaults["h"], defaults["lo"], defaults["c"])
        mech_ctx = TriggerContext(
            direction=direction, bar=BarView.from_any(mech_bar),
            is_close=True,
        )

        live_fired, _ = check_trigger_fires(t, live_ctx)
        mech_fired, _ = check_trigger_fires(t, mech_ctx)
        assert live_fired == mech_fired == expected
