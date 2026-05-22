"""Multi-layer tests for :mod:`tradinglab.backtest.replay_events`.

This mixin is the explicit boundary between the headless replay
engine and the :mod:`tradinglab.events` subsystem. Production
coverage is decent (~53%) but the corporate-action translation +
gating accessors haven't been driven directly. We exercise the mixin
via a stateless harness that satisfies the attribute contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, List

import numpy as np
import pytest

from tradinglab.backtest.replay_events import EventsControllerMixin
from tradinglab.events.base import DividendRecord, EarningsRecord, EventBundle

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _make_div(ex_ts: int, *, amount: float = 0.0, kind: str = "cash",
              ratio_num: int = 1, ratio_den: int = 1,
              source: str = "test", symbol: str = "SPY") -> DividendRecord:
    return DividendRecord(
        ex_ts=ex_ts, symbol=symbol, amount=amount, kind=kind,
        ratio_num=ratio_num, ratio_den=ratio_den, source=source,
    )


def _make_earn(ts: int, *, symbol: str = "SPY", when: str = "AMC") -> EarningsRecord:
    return EarningsRecord(ts=ts, symbol=symbol, when=when)


def _make_bundle(symbol: str = "SPY", *,
                 dividends=(), earnings=()) -> EventBundle:
    return EventBundle(
        symbol=symbol, earnings=list(earnings), dividends=list(dividends),
    )


class _FakeClock:
    def __init__(self, timeline: list[int]) -> None:
        self.timeline = np.array(timeline, dtype=np.int64)


class _FakeEngine:
    def __init__(self, timeline: list[int]) -> None:
        self.clock = _FakeClock(timeline)
        self.registered: list[tuple] = []

    def register_corporate_actions(self, symbol, actions) -> int:
        self.registered.append((symbol, list(actions)))
        return len(actions)


class _EventsHarness(EventsControllerMixin):
    """Minimal stateful harness for the mixin."""

    def __init__(
        self,
        *,
        engine: _FakeEngine | None = None,
        active: bool = True,
        blind: bool = False,
        clock_ts_value: int | None = 0,
    ) -> None:
        self._raw_full_events: dict[str, Any] = {}
        self._events_fetch_token = 0
        self.engine = engine
        self.app = SimpleNamespace(_fetch_executor=None, _await_future_on_tk=None)
        self.active = active
        self.blind = blind
        self._clock_ts = clock_ts_value

    def clock_ts(self):
        return self._clock_ts


# ---------------------------------------------------------------------------
# 1. set_event_bundle
# ---------------------------------------------------------------------------


class TestSetEventBundle:
    def test_stores_bundle_by_symbol(self):
        h = _EventsHarness()
        bundle = _make_bundle()
        h.set_event_bundle("SPY", bundle)
        assert h._raw_full_events["SPY"] is bundle

    def test_overwrites_on_reinstall(self):
        h = _EventsHarness()
        b1, b2 = _make_bundle(), _make_bundle()
        h.set_event_bundle("SPY", b1)
        h.set_event_bundle("SPY", b2)
        assert h._raw_full_events["SPY"] is b2

    def test_engine_action_registration_when_engine_present(self):
        engine = _FakeEngine(timeline=[1_700_000_000, 1_700_000_300])
        h = _EventsHarness(engine=engine)
        div = _make_div(
            ex_ts=1_700_000_100 * 1000, amount=0.50, kind="cash",
        )
        b = _make_bundle(dividends=[div])
        h.set_event_bundle("SPY", b)
        # Registered exactly once.
        assert len(engine.registered) == 1
        sym, acts = engine.registered[0]
        assert sym == "SPY"
        assert len(acts) == 1
        assert acts[0].kind == "cash_dividend"

    def test_engine_registration_exception_swallowed(self):
        class _BoomEngine(_FakeEngine):
            def register_corporate_actions(self, symbol, actions):
                raise RuntimeError("boom")

        engine = _BoomEngine(timeline=[1_700_000_000, 1_700_000_300])
        h = _EventsHarness(engine=engine)
        div = _make_div(ex_ts=1_700_000_100 * 1000, amount=0.50)
        # Must not propagate.
        h.set_event_bundle("SPY", _make_bundle(dividends=[div]))
        assert h._raw_full_events["SPY"] is not None


# ---------------------------------------------------------------------------
# 2. _register_corporate_actions_from_bundle
# ---------------------------------------------------------------------------


class TestRegisterCorporateActions:
    def test_no_engine_returns_zero(self):
        h = _EventsHarness(engine=None)
        n = h._register_corporate_actions_from_bundle(
            "SPY", _make_bundle(dividends=[_make_div(ex_ts=1)]),
        )
        assert n == 0

    def test_empty_bundle_returns_zero(self):
        engine = _FakeEngine(timeline=[1, 2, 3])
        h = _EventsHarness(engine=engine)
        assert h._register_corporate_actions_from_bundle(
            "SPY", _make_bundle(),
        ) == 0
        assert engine.registered == []

    def test_empty_timeline_returns_zero(self):
        engine = _FakeEngine(timeline=[])
        h = _EventsHarness(engine=engine)
        div = _make_div(ex_ts=1_700_000_000 * 1000)
        assert h._register_corporate_actions_from_bundle(
            "SPY", _make_bundle(dividends=[div]),
        ) == 0

    def test_divs_outside_timeline_are_dropped(self):
        engine = _FakeEngine(timeline=[1_700_000_000, 1_700_001_000])
        h = _EventsHarness(engine=engine)
        # Below lo.
        early = _make_div(ex_ts=(1_700_000_000 - 60) * 1000)
        # Above hi.
        late = _make_div(ex_ts=(1_700_001_000 + 60) * 1000)
        n = h._register_corporate_actions_from_bundle(
            "SPY", _make_bundle(dividends=[early, late]),
        )
        assert n == 0
        assert engine.registered == []

    def test_div_in_range_registered_with_kind_map(self):
        engine = _FakeEngine(timeline=[1_700_000_000, 1_700_000_500, 1_700_001_000])
        h = _EventsHarness(engine=engine)
        cash = _make_div(ex_ts=1_700_000_500 * 1000, amount=0.7, kind="cash")
        special = _make_div(ex_ts=1_700_000_500 * 1000, amount=1.0, kind="special")
        split = _make_div(
            ex_ts=1_700_000_500 * 1000, amount=0.0, kind="stock_split",
            ratio_num=2, ratio_den=1,
        )
        spinoff = _make_div(ex_ts=1_700_000_500 * 1000, amount=0.5, kind="spinoff")
        n = h._register_corporate_actions_from_bundle(
            "SPY",
            _make_bundle(dividends=[cash, special, split, spinoff]),
        )
        assert n == 4
        sym, acts = engine.registered[0]
        kinds = {a.kind for a in acts}
        assert kinds == {
            "cash_dividend", "special_dividend",
            "stock_split", "spinoff_cash",
        }

    def test_div_snaps_to_first_timeline_entry_at_or_after_ex_ts(self):
        """`np.searchsorted(..., side='left')` finds the first timeline
        index ≥ ex_ts — the action ts is that exact timeline second."""
        engine = _FakeEngine(timeline=[1_700_000_000, 1_700_000_500, 1_700_001_000])
        h = _EventsHarness(engine=engine)
        # ex_ts between bars 0 and 1 → snaps to bar 1.
        div = _make_div(ex_ts=1_700_000_250 * 1000)
        h._register_corporate_actions_from_bundle("SPY", _make_bundle(dividends=[div]))
        action = engine.registered[0][1][0]
        assert action.ts == 1_700_000_500


# ---------------------------------------------------------------------------
# 3. events_visible_for — gating accessor
# ---------------------------------------------------------------------------


class TestEventsVisibleFor:
    def test_no_bundle_returns_none(self):
        h = _EventsHarness()
        assert h.events_visible_for("SPY") is None

    def test_no_clock_ts_returns_none(self):
        h = _EventsHarness(clock_ts_value=None)
        h._raw_full_events["SPY"] = _make_bundle()
        assert h.events_visible_for("SPY") is None

    def test_with_bundle_and_ts_delegates_to_gating(self):
        h = _EventsHarness(clock_ts_value=1_700_000_500)
        # Empty bundle — gating returns an empty view, not None.
        h._raw_full_events["SPY"] = _make_bundle()
        view = h.events_visible_for("SPY")
        # Gating returns an EventsView, not None.
        assert view is not None


# ---------------------------------------------------------------------------
# 4. _compute_event_proximity
# ---------------------------------------------------------------------------


class TestComputeEventProximity:
    def test_no_bundle_returns_zero_fields(self):
        h = _EventsHarness()
        out = h._compute_event_proximity("SPY", 1_700_000_000)
        # All-zero default contract.
        assert out["next_earnings_ts"] == 0
        assert out["last_earnings_ts"] == 0
        assert out["earnings_proximity_tag"] == ""
        assert out["dividend_proximity_tag"] == ""

    def test_post_print_tag_when_recent_earnings(self):
        h = _EventsHarness()
        ts_seconds = 1_700_000_000
        # past earnings 2 days before in ms.
        past = _make_earn(ts=(ts_seconds - 2 * 86_400) * 1000)
        h._raw_full_events["SPY"] = _make_bundle(earnings=[past])
        out = h._compute_event_proximity("SPY", ts_seconds)
        assert out["earnings_proximity_tag"] == "earnings_post_print"
        assert out["last_earnings_ts"] == past.ts

    def test_pre_print_tag_when_upcoming_earnings(self):
        h = _EventsHarness()
        ts_seconds = 1_700_000_000
        # forward earnings 3 days ahead in ms.
        fwd = _make_earn(ts=(ts_seconds + 3 * 86_400) * 1000)
        h._raw_full_events["SPY"] = _make_bundle(earnings=[fwd])
        out = h._compute_event_proximity("SPY", ts_seconds)
        assert out["earnings_proximity_tag"] == "earnings_pre_print"
        assert out["next_earnings_ts"] == fwd.ts

    def test_ex_div_day_tag_when_today_is_ex_date(self):
        h = _EventsHarness()
        ts_seconds = 1_700_000_000
        # ex_div same day as ts (within 24h before).
        div = _make_div(
            ex_ts=(ts_seconds - 3_600) * 1000, kind="cash", amount=0.5,
        )
        h._raw_full_events["SPY"] = _make_bundle(dividends=[div])
        out = h._compute_event_proximity("SPY", ts_seconds)
        assert out["dividend_proximity_tag"] == "ex_div_day"
        assert out["last_dividend_ts"] == div.ex_ts

    def test_last_split_ts_recorded(self):
        h = _EventsHarness()
        ts_seconds = 1_700_000_000
        split = _make_div(
            ex_ts=(ts_seconds - 10 * 86_400) * 1000,
            kind="stock_split",
            ratio_num=2, ratio_den=1,
        )
        h._raw_full_events["SPY"] = _make_bundle(dividends=[split])
        out = h._compute_event_proximity("SPY", ts_seconds)
        assert out["last_split_ts"] == split.ex_ts


# ---------------------------------------------------------------------------
# 5. prefetch_events_for — sync fallback path
# ---------------------------------------------------------------------------


class TestPrefetchEventsFor:
    def test_no_executor_uses_sync_fallback(self, monkeypatch):
        h = _EventsHarness()
        # No executor / no await helper ⇒ sync inline.
        installed_bundle = _make_bundle()

        def _fake_fetcher(symbol):
            return installed_bundle if symbol == "SPY" else None

        from tradinglab import events as _events
        monkeypatch.setitem(_events.EVENT_SOURCES, "yfinance", _fake_fetcher)
        h.prefetch_events_for("SPY")
        assert h._raw_full_events.get("SPY") is installed_bundle

    def test_fetcher_exception_does_not_propagate(self, monkeypatch):
        h = _EventsHarness()

        def _boom(symbol):
            raise RuntimeError("network down")

        from tradinglab import events as _events
        monkeypatch.setitem(_events.EVENT_SOURCES, "yfinance", _boom)
        # Must not raise.
        h.prefetch_events_for("SPY")
        # Nothing installed.
        assert "SPY" not in h._raw_full_events
