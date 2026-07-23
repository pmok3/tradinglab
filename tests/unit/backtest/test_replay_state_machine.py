"""Headless multi-layer tests for :class:`backtest.replay.SandboxController`.

The controller orchestrates the sandbox kernel against the
:class:`ChartApp` GUI. Smoke tests already cover the happy path end-to-
end against a real ``ChartApp``; this file targets the parts that smoke
doesn't reach:

* Error-path gating in ``start_session`` (already-active, empty bars,
  bad auto-cycle config, malformed ``display_intervals``).
* Subscriber registration / release / fan-out under exceptions.
* Memento capture + restore round-trip.
* ``submit_order`` order-id minting and queueing.
* ``next_bar`` happy-path side effects: visible-list growth, focused
  invalidation, draw-slice, card-subscriber fan-out.
* ``set_focus`` rejection of unknown symbols + active-only gate.
* Post-trade callback dispatch + ``user_review`` propagation.

Built around a minimal fake :class:`ChartApp` exposing only the
primitives the controller is contractually allowed to read / write
(see ``replay.py`` docstring) — that contract is exactly what these
tests pin down.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pytest

from tradinglab.backtest.replay import SandboxController, SandboxMemento
from tradinglab.backtest.session import ENGINE_VERSION, SessionResult, SessionSpec
from tradinglab.backtest.tags import TagStore
from tradinglab.models import Candle

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_intraday_candles(
    n: int,
    *,
    start: _dt.datetime = _dt.datetime(2025, 3, 4, 9, 30),
    interval_min: int = 5,
    base: float = 100.0,
) -> list[Candle]:
    """Generate ``n`` regular-session candles at ``interval_min`` cadence."""
    out: list[Candle] = []
    for i in range(n):
        ts = start + _dt.timedelta(minutes=interval_min * i)
        out.append(Candle(
            date=ts,
            open=base + i * 0.10,
            high=base + i * 0.10 + 0.20,
            low=base + i * 0.10 - 0.20,
            close=base + i * 0.10 + 0.05,
            volume=1_000_000,
            session="regular",
        ))
    return out


class _FakeVar:
    def __init__(self, initial: Any = "") -> None:
        self._v = initial

    def get(self) -> Any:
        return self._v

    def set(self, v: Any) -> None:
        self._v = v


@dataclass(eq=False)
class _FakeChartApp:
    """Minimal stand-in for :class:`ChartApp` that exposes only the
    public primitives the controller reads/writes. No Tk, no plotting."""

    _primary: list[Any] = field(default_factory=list)
    _compare: list[Any] = field(default_factory=list)
    candles: list[Any] = field(default_factory=list)
    _drilldown_day: Any = None
    _confirmed_primary_ticker: str = ""
    _confirmed_compare_ticker: str = ""
    _fetch_token: int = 0
    _prefetched_raw: Any = None
    _sandbox_panel: Any = None

    # Tk-var stand-ins.
    ticker_var: _FakeVar = field(default_factory=lambda: _FakeVar("SPY"))
    compare_ticker_var: _FakeVar = field(default_factory=lambda: _FakeVar(""))
    compare_var: _FakeVar = field(default_factory=lambda: _FakeVar(False))
    interval_var: _FakeVar = field(default_factory=lambda: _FakeVar("5m"))

    # Call recorders.
    render_calls: int = 0
    cancel_fetch_calls: int = 0
    install_primary_calls: list[dict] = field(default_factory=list)
    install_compare_calls: list[dict] = field(default_factory=list)
    invalidate_focused_calls: list[Any] = field(default_factory=list)
    notify_appended_calls: list[Any] = field(default_factory=list)
    draw_slice_calls: list[tuple] = field(default_factory=list)
    refresh_view_calls: list[str] = field(default_factory=list)
    capture_png_calls: int = 0
    hide_panel_calls: int = 0
    reset_compare_calls: int = 0

    # ------------------------------------------------------------------
    # The controller-allowed primitive surface
    # ------------------------------------------------------------------
    def _render(self) -> None:
        self.render_calls += 1

    def _cancel_background_fetch_jobs(self) -> None:
        self.cancel_fetch_calls += 1

    def _install_sandbox_primary_series(
        self, *, symbol: str, candles: list[Any], interval: str,
        full_session_length: int = 0, **_: Any,
    ) -> None:
        self._primary = candles  # the controller's identity-stable list
        self.candles = candles
        self.ticker_var.set(symbol)
        self.interval_var.set(interval)
        self._confirmed_primary_ticker = symbol
        self.install_primary_calls.append(
            dict(symbol=symbol, interval=interval,
                 full_session_length=full_session_length,
                 length=len(candles)))

    def _install_sandbox_compare_series(
        self, *, symbol: str, candles: list[Any], interval: str,
        full_session_length: int = 0, **_: Any,
    ) -> None:
        self._compare = candles
        self.compare_ticker_var.set(symbol)
        self.compare_var.set(True)
        self._confirmed_compare_ticker = symbol
        self.install_compare_calls.append(
            dict(symbol=symbol, interval=interval,
                 length=len(candles)))

    def _invalidate_focused_panels(self, candles: Any) -> None:
        self.invalidate_focused_calls.append(candles)

    def _notify_focused_panels_appended(self, candles: Any) -> None:
        self.notify_appended_calls.append(candles)

    def _draw_slice(self, slot_key: str, start: int, end: int) -> None:
        self.draw_slice_calls.append((slot_key, start, end))

    def _refresh_view_after_append(self, slot_key: str) -> None:
        self.refresh_view_calls.append(slot_key)

    def _capture_chart_png(self, *_a, **_kw) -> str | None:
        self.capture_png_calls += 1
        return None

    def _hide_sandbox_panel(self) -> None:
        self.hide_panel_calls += 1

    def _sandbox_reset_compare_for_session_start(self) -> None:
        self.reset_compare_calls += 1


def _make_session_spec(
    *,
    deck_seed: int = 42,
    tickers: tuple = ("SPY",),
    decision_logging_enabled: bool = False,
) -> SessionSpec:
    return SessionSpec(
        deck_seed=deck_seed,
        tickers=tickers,
        start_clock_iso="2025-03-04T09:30:00",
        slippage_bps=5.0,
        commission=0.0,
        engine_version=ENGINE_VERSION,
        setup_tags=(),
        starting_cash=100_000.0,
        include_extended=False,
        auto_cycle=False,
        decision_logging_enabled=decision_logging_enabled,
    )


def _ctl_and_start(
    *,
    spec: SessionSpec | None = None,
    n_bars: int = 30,
    session_date: _dt.date = _dt.date(2025, 3, 4),
    lookback_days: int = 1,
    auto_cycle: bool = False,
    eligible_dates: list[_dt.date] | None = None,
    display_intervals: tuple | None = None,
) -> tuple[SandboxController, _FakeChartApp, list[Candle]]:
    app = _FakeChartApp()
    spec = spec or _make_session_spec()
    candles = _make_intraday_candles(n_bars)
    ctl = SandboxController(app=app, tag_store=TagStore())
    ctl.start_session(
        spec=spec,
        session_date=session_date,
        interval="5m",
        reference_symbol="SPY",
        reference_candles=candles,
        lookback_days=lookback_days,
        auto_cycle=auto_cycle,
        eligible_dates=eligible_dates,
        display_intervals=display_intervals,
    )
    return ctl, app, candles


# ---------------------------------------------------------------------------
# 1. Construction / activation gates
# ---------------------------------------------------------------------------


class TestActivationGates:
    def test_default_is_inactive(self):
        ctl = SandboxController(app=_FakeChartApp(), tag_store=TagStore())
        assert ctl.is_active() is False
        assert ctl.engine is None
        assert ctl.cash() == 0.0  # safe access while inactive
        assert ctl.positions_snapshot() == []
        assert ctl.clock_ts() is None
        assert ctl.tickers() == []
        assert ctl.result() is None

    def test_start_with_empty_reference_raises(self):
        app = _FakeChartApp()
        ctl = SandboxController(app=app, tag_store=TagStore())
        with pytest.raises(ValueError, match="empty"):
            ctl.start_session(
                spec=_make_session_spec(),
                session_date=_dt.date(2025, 3, 4),
                interval="5m",
                reference_symbol="SPY",
                reference_candles=[],
            )
        assert ctl.is_active() is False

    def test_start_twice_raises_runtime_error(self):
        ctl, _, _ = _ctl_and_start()
        with pytest.raises(RuntimeError, match="already active"):
            ctl.start_session(
                spec=_make_session_spec(),
                session_date=_dt.date(2025, 3, 4),
                interval="5m",
                reference_symbol="SPY",
                reference_candles=_make_intraday_candles(10),
            )

    def test_auto_cycle_requires_eligible_dates(self):
        app = _FakeChartApp()
        ctl = SandboxController(app=app, tag_store=TagStore())
        with pytest.raises(ValueError, match="eligible_dates"):
            ctl.start_session(
                spec=_make_session_spec(),
                session_date=_dt.date(2025, 3, 4),
                interval="5m",
                reference_symbol="SPY",
                reference_candles=_make_intraday_candles(30),
                auto_cycle=True,
                eligible_dates=None,
            )

    def test_display_intervals_smallest_must_equal_primary(self):
        """The toolbar interval combobox is restricted to display_intervals;
        the smallest must equal the engine's tick interval."""
        app = _FakeChartApp()
        ctl = SandboxController(app=app, tag_store=TagStore())
        # primary=5m but smallest entry is 1m → invalid.
        with pytest.raises(ValueError, match="smallest"):
            ctl.start_session(
                spec=_make_session_spec(),
                session_date=_dt.date(2025, 3, 4),
                interval="5m",
                reference_symbol="SPY",
                reference_candles=_make_intraday_candles(30),
                display_intervals=("1m", "5m"),
            )

    def test_display_intervals_must_divide_evenly(self):
        app = _FakeChartApp()
        ctl = SandboxController(app=app, tag_store=TagStore())
        # 5m doesn't divide evenly into 2m primary (5 % 2 = 1).
        with pytest.raises(ValueError, match="integer"):
            ctl.start_session(
                spec=_make_session_spec(),
                session_date=_dt.date(2025, 3, 4),
                interval="2m",
                reference_symbol="SPY",
                reference_candles=_make_intraday_candles(30, interval_min=2),
                display_intervals=("2m", "5m"),
            )


# ---------------------------------------------------------------------------
# 2. Happy-path start_session: engine built, memento captured, primary installed
# ---------------------------------------------------------------------------


class TestStartSessionHappyPath:
    def test_engine_constructed_and_active(self):
        ctl, app, _ = _ctl_and_start()
        assert ctl.is_active() is True
        assert ctl.engine is not None
        assert ctl.spec is not None
        assert ctl.reference_symbol == "SPY"
        assert ctl.session_date == _dt.date(2025, 3, 4)
        # Primary series installed on the app.
        assert len(app.install_primary_calls) == 1
        assert app.install_primary_calls[0]["symbol"] == "SPY"
        # Background fetch was cancelled.
        assert app.cancel_fetch_calls == 1
        # Memento captured the pre-session state.
        assert ctl._memento is not None
        assert isinstance(ctl._memento, SandboxMemento)

    def test_memento_records_pre_session_state(self):
        app = _FakeChartApp()
        app.ticker_var.set("OLD")
        app.interval_var.set("1d")
        app._drilldown_day = _dt.date(2024, 12, 31)
        app._primary = [object()]
        ctl = SandboxController(app=app, tag_store=TagStore())
        ctl.start_session(
            spec=_make_session_spec(),
            session_date=_dt.date(2025, 3, 4),
            interval="5m",
            reference_symbol="SPY",
            reference_candles=_make_intraday_candles(30),
        )
        # Memento captured the original ticker/interval.
        assert ctl._memento.ticker == "OLD"
        assert ctl._memento.interval == "1d"
        assert ctl._memento.drilldown_day == _dt.date(2024, 12, 31)
        # Drilldown was cleared at session start.
        assert app._drilldown_day is None
        # _fetch_token bumped (cancellation of stale loads).
        assert app._fetch_token == 1

    def test_visible_list_seeded_and_identity_stable(self):
        ctl, app, _ = _ctl_and_start()
        visible = ctl.visible_candles_by_symbol["SPY"]
        first_id = id(visible)
        # Tick a few times — identity must NOT change (in-place append).
        for _ in range(3):
            ctl.next_bar()
        assert id(ctl.visible_candles_by_symbol["SPY"]) == first_id

    def test_focus_symbol_defaults_to_reference(self):
        ctl, _, _ = _ctl_and_start()
        assert ctl.focus_symbol == "SPY"
        assert "SPY" in ctl.tickers()


# ---------------------------------------------------------------------------
# 3. Card-subscriber registration + fan-out
# ---------------------------------------------------------------------------


class TestCardSubscribers:
    def test_register_returns_release_callable(self):
        ctl, _, _ = _ctl_and_start()
        calls = []
        release = ctl.register_card_subscriber(lambda: calls.append("a"))
        ctl._fire_card_subscribers()
        assert calls == ["a"]
        release()
        ctl._fire_card_subscribers()
        # Should not fire again after release.
        assert calls == ["a"]

    def test_release_is_idempotent(self):
        ctl, _, _ = _ctl_and_start()
        release = ctl.register_card_subscriber(lambda: None)
        release()
        # Second call should not raise.
        release()

    def test_register_rejects_non_callable(self):
        ctl, _, _ = _ctl_and_start()
        with pytest.raises(TypeError, match="callable"):
            ctl.register_card_subscriber(42)  # type: ignore[arg-type]

    def test_one_bad_subscriber_doesnt_block_others(self):
        ctl, _, _ = _ctl_and_start()
        calls: list[str] = []

        def bad() -> None:
            raise RuntimeError("boom")

        ctl.register_card_subscriber(lambda: calls.append("before"))
        ctl.register_card_subscriber(bad)
        ctl.register_card_subscriber(lambda: calls.append("after"))
        ctl._fire_card_subscribers()
        assert calls == ["before", "after"]

    def test_subscribers_fire_on_next_bar(self):
        ctl, _, _ = _ctl_and_start()
        ticks: list[int] = []
        ctl.register_card_subscriber(lambda: ticks.append(1))
        ctl.next_bar()
        ctl.next_bar()
        assert len(ticks) == 2

    def test_end_session_fires_subscribers_one_last_time(self):
        ctl, _, _ = _ctl_and_start()
        observed_active: list[bool] = []
        ctl.register_card_subscriber(
            lambda: observed_active.append(ctl.is_active()))
        ctl.end_session()
        # Final pass observed active=False so cards can clean up.
        assert observed_active and observed_active[-1] is False

    def test_subscribers_cleared_after_end(self):
        ctl, _, _ = _ctl_and_start()
        calls: list[int] = []
        ctl.register_card_subscriber(lambda: calls.append(1))
        ctl.end_session()
        # After end_session, the subscriber list is wiped.
        assert ctl._card_subscribers == []


# ---------------------------------------------------------------------------
# 4. next_bar side effects
# ---------------------------------------------------------------------------


class TestNextBar:
    def test_next_bar_advances_clock_and_grows_visible(self):
        ctl, app, _ = _ctl_and_start()
        before = len(ctl.visible_candles_by_symbol["SPY"])
        assert ctl.next_bar() is True
        after = len(ctl.visible_candles_by_symbol["SPY"])
        assert after == before + 1

    def test_next_bar_returns_false_when_inactive(self):
        ctl = SandboxController(app=_FakeChartApp(), tag_store=TagStore())
        assert ctl.next_bar() is False

    def test_next_bar_invokes_focused_invalidation(self):
        ctl, app, _ = _ctl_and_start()
        before = len(app.invalidate_focused_calls)
        ctl.next_bar()
        # Either invalidate or notify_appended must have been called.
        called = (len(app.invalidate_focused_calls) > before
                  or len(app.notify_appended_calls) > 0)
        assert called

    def test_next_bar_triggers_view_refresh(self):
        ctl, app, _ = _ctl_and_start()
        before = len(app.refresh_view_calls) + len(app.draw_slice_calls)
        ctl.next_bar()
        after = len(app.refresh_view_calls) + len(app.draw_slice_calls)
        assert after > before

    def test_next_bar_eventually_returns_false_at_end_of_timeline(self):
        """Without auto-cycle, after enough ticks the engine exhausts
        the master timeline and ``next_bar`` returns False."""
        ctl, _, candles = _ctl_and_start(n_bars=10)
        # Tick more than the bar count; eventually we get False.
        results: list[bool] = []
        for _ in range(50):
            results.append(ctl.next_bar())
            if results[-1] is False:
                break
        assert False in results

    def test_clock_ts_increases_monotonically(self):
        ctl, _, _ = _ctl_and_start()
        prev = ctl.clock_ts()
        for _ in range(3):
            ctl.next_bar()
            now = ctl.clock_ts()
            assert now is not None
            assert prev is None or now >= prev
            prev = now


# ---------------------------------------------------------------------------
# 5. submit_order + accessor methods
# ---------------------------------------------------------------------------


class TestSubmitOrderAndAccessors:
    def test_submit_order_mints_sequential_ids(self):
        ctl, _, _ = _ctl_and_start()
        pre = {"thesis": "test thesis", "size": 10, "setup_tag": "long",
               "conviction": 3, "notes": "test"}
        o1 = ctl.submit_order(symbol="SPY", side="buy", quantity=10,
                              pre_trade_data=pre)
        o2 = ctl.submit_order(symbol="SPY", side="sell", quantity=5,
                              pre_trade_data=pre)
        # IDs are sequential ord-NNNN strings.
        assert o1.startswith("ord-")
        assert o2.startswith("ord-")
        assert o1 != o2
        # Strict sequential numbering.
        assert int(o2.split("-")[1]) == int(o1.split("-")[1]) + 1

    def test_submit_order_rejects_empty_thesis(self):
        ctl, _, _ = _ctl_and_start()
        with pytest.raises(ValueError, match="thesis"):
            ctl.submit_order(symbol="SPY", side="buy", quantity=10,
                             pre_trade_data={"thesis": "", "size": 10})

    def test_submit_order_rejects_nonpositive_quantity(self):
        ctl, _, _ = _ctl_and_start()
        with pytest.raises(ValueError, match="quantity"):
            ctl.submit_order(symbol="SPY", side="buy", quantity=0,
                             pre_trade_data={"thesis": "t", "size": 1})

    def test_submit_order_rejects_unknown_symbol(self):
        ctl, _, _ = _ctl_and_start()
        with pytest.raises(ValueError, match="not in this session"):
            ctl.submit_order(symbol="NOPE", side="buy", quantity=10,
                             pre_trade_data={"thesis": "t", "size": 1})

    def test_submit_order_when_inactive_raises(self):
        ctl = SandboxController(app=_FakeChartApp(), tag_store=TagStore())
        with pytest.raises(RuntimeError, match="no active"):
            ctl.submit_order(symbol="SPY", side="buy", quantity=10,
                             pre_trade_data={"thesis": "t", "size": 1})

    def test_cash_reflects_starting_capital(self):
        ctl, _, _ = _ctl_and_start()
        # Starting cash from the spec is 100,000.
        assert ctl.cash() == pytest.approx(100_000.0)

    def test_positions_snapshot_empty_at_start(self):
        ctl, _, _ = _ctl_and_start()
        assert ctl.positions_snapshot() == []

    def test_tickers_includes_reference(self):
        ctl, _, _ = _ctl_and_start()
        assert "SPY" in ctl.tickers()

    def test_clock_ts_returns_int_when_active(self):
        ctl, _, _ = _ctl_and_start()
        ctl.next_bar()
        ts = ctl.clock_ts()
        assert ts is not None
        assert isinstance(ts, (int, np.integer))


# ---------------------------------------------------------------------------
# 6. set_focus gating
# ---------------------------------------------------------------------------


class TestSetFocus:
    def test_set_focus_to_unknown_symbol_is_noop(self):
        ctl, _, _ = _ctl_and_start()
        ctl.set_focus("NOPE")
        assert ctl.focus_symbol == "SPY"

    def test_set_focus_when_inactive_is_noop(self):
        ctl = SandboxController(app=_FakeChartApp(), tag_store=TagStore())
        # Manually populate the dict so the unknown-symbol gate doesn't
        # short-circuit first.
        ctl.visible_candles_by_symbol = {"XYZ": []}
        ctl.set_focus("XYZ")
        assert ctl.focus_symbol is None

    def test_set_focus_to_current_is_noop(self):
        ctl, app, _ = _ctl_and_start()
        before = len(app.install_primary_calls)
        ctl.set_focus("SPY")  # already focused
        assert len(app.install_primary_calls) == before


# ---------------------------------------------------------------------------
# 7. end_session restores memento
# ---------------------------------------------------------------------------


class TestEndSession:
    def test_end_session_when_inactive_returns_none(self):
        ctl = SandboxController(app=_FakeChartApp(), tag_store=TagStore())
        assert ctl.end_session() is None

    def test_end_session_returns_session_result(self):
        ctl, _, _ = _ctl_and_start()
        result = ctl.end_session()
        assert result is not None
        assert isinstance(result, SessionResult)
        # Spec round-trips.
        assert result.spec.deck_seed == 42

    def test_end_session_deactivates_controller(self):
        ctl, _, _ = _ctl_and_start()
        ctl.end_session()
        assert ctl.is_active() is False

    def test_end_session_restores_memento_state(self):
        app = _FakeChartApp()
        app.ticker_var.set("ORIG")
        app.interval_var.set("1d")
        app._drilldown_day = _dt.date(2024, 12, 1)
        ctl = SandboxController(app=app, tag_store=TagStore())
        ctl.start_session(
            spec=_make_session_spec(),
            session_date=_dt.date(2025, 3, 4),
            interval="5m",
            reference_symbol="SPY",
            reference_candles=_make_intraday_candles(30),
        )
        # During the session, the toolbar has the sandbox ticker.
        assert app.ticker_var.get() == "SPY"
        ctl.end_session()
        # End restored the pre-session values.
        assert app.ticker_var.get() == "ORIG"
        assert app.interval_var.get() == "1d"
        assert app._drilldown_day == _dt.date(2024, 12, 1)
        assert app.hide_panel_calls == 1

    def test_end_session_hides_panel(self):
        ctl, app, _ = _ctl_and_start()
        ctl.end_session()
        assert app.hide_panel_calls == 1


# ---------------------------------------------------------------------------
# 8. result() merging
# ---------------------------------------------------------------------------


class TestResultMerging:
    def test_result_when_inactive_engine_returns_none(self):
        ctl = SandboxController(app=_FakeChartApp(), tag_store=TagStore())
        assert ctl.result() is None

    def test_result_single_cycle_passes_through_engine_result(self):
        ctl, _, _ = _ctl_and_start()
        r = ctl.result()
        assert r is not None
        # No archived state ⇒ engine result returned unchanged.
        assert r.fills == []
        assert r.pre_trades == []
        assert r.post_trades == []

    def test_result_merges_archived_cycles(self):
        """Multi-cycle: archived lists are prepended to the current
        engine's lists. Construct synthetic archives directly to exercise
        the merge path without driving a full auto-cycle session."""
        ctl, _, _ = _ctl_and_start()
        # Inject archived state.
        ctl._archived_pre_trades = [object()]  # opaque marker
        ctl._archived_equity = [(0, 100_000.0)]
        r = ctl.result()
        assert r is not None
        # Archived entries appear before current.
        assert len(r.pre_trades) >= 1
        assert len(r.equity_curve) >= 1


# ---------------------------------------------------------------------------
# 9. Post-trade callback dispatch (light: just registration)
# ---------------------------------------------------------------------------


class TestPostTradeCallback:
    def test_set_post_trade_callback_stores_it(self):
        ctl, _, _ = _ctl_and_start()
        cb_received: list[Any] = []

        def cb(review):
            cb_received.append(review)
            return "thoughtful review"

        ctl.set_post_trade_callback(cb)
        assert ctl._post_trade_callback is cb

    def test_clear_post_trade_callback(self):
        ctl, _, _ = _ctl_and_start()
        ctl.set_post_trade_callback(lambda r: "")
        ctl.set_post_trade_callback(None)
        assert ctl._post_trade_callback is None


# ---------------------------------------------------------------------------
# 10. Per-day watch notes (blind-safe pre-trade journaling)
# ---------------------------------------------------------------------------


class TestDayNotes:
    def test_capture_and_result_injection(self):
        ctl, _, _ = _ctl_and_start()
        d = ctl.current_session_date()
        assert d is not None
        key = d.isoformat()
        assert ctl.current_day_note() == ""
        ctl.set_day_note("SPY pulling back to 9ema, NVDA holding RS")
        assert ctl.current_day_note() == "SPY pulling back to 9ema, NVDA holding RS"
        # Folded into the SessionResult (single-cycle fast path).
        r = ctl.result()
        assert r.day_notes[key] == "SPY pulling back to 9ema, NVDA holding RS"

    def test_whitespace_only_note_clears_entry(self):
        ctl, _, _ = _ctl_and_start()
        key = ctl.current_session_date().isoformat()
        ctl.set_day_note("temp thesis")
        assert ctl.current_day_note() == "temp thesis"
        ctl.set_day_note("   \n  ")
        assert ctl.current_day_note() == ""
        assert key not in ctl.result().day_notes

    def test_ordinal_starts_at_one(self):
        ctl, _, _ = _ctl_and_start()
        assert ctl.current_day_ordinal() == 1

    def test_notes_survive_result_merge(self):
        ctl, _, _ = _ctl_and_start()
        key = ctl.current_session_date().isoformat()
        ctl._archived_pre_trades = [object()]  # force the merge path
        ctl.set_day_note("archived-cycle day note")
        assert ctl.result().day_notes.get(key) == "archived-cycle day note"

    def test_set_day_note_noop_without_active_clock(self):
        ctl = SandboxController(app=_FakeChartApp(), tag_store=TagStore())
        # No session started ⇒ no clock ⇒ safe no-op, no crash.
        ctl.set_day_note("ignored")
        assert ctl.current_day_note() == ""
        assert ctl.current_day_ordinal() == 1


# ---------------------------------------------------------------------------
# 11. Optional discretionary decision logging
# ---------------------------------------------------------------------------


class TestDecisionLogging:
    def test_disabled_by_default_and_never_infers_passes(self):
        ctl, _, _ = _ctl_and_start()
        assert ctl.decision_logging_enabled() is False
        assert ctl.decisions_snapshot() == []
        ctl.next_bar()
        assert ctl.result().decisions == []
        with pytest.raises(RuntimeError, match="not enabled"):
            ctl.log_decision(
                action="pass",
                setup_tag="breakout",
                confidence=3,
            )

    def test_enabled_capture_is_controller_owned_and_persisted(self):
        spec = _make_session_spec(decision_logging_enabled=True)
        ctl, _, _ = _ctl_and_start(spec=spec)
        ts = ctl.clock_ts()
        record = ctl.log_decision(
            action=" Long ",
            setup_tag="  pullback ",
            confidence=4,
            note="  waited for confirmation  ",
        )
        assert record.ts == ts
        assert record.symbol == "SPY"
        assert record.action == "long"
        assert record.setup_tag == "pullback"
        assert record.confidence == 4
        assert record.note == "waited for confirmation"
        assert ctl.result().decisions == [record]
        assert ctl.decisions_snapshot() == [record]

    @pytest.mark.parametrize("action", ["", "buy", "flat"])
    def test_rejects_unknown_action(self, action):
        spec = _make_session_spec(decision_logging_enabled=True)
        ctl, _, _ = _ctl_and_start(spec=spec)
        with pytest.raises(ValueError, match="action"):
            ctl.log_decision(
                action=action,
                setup_tag="breakout",
                confidence=3,
            )

    def test_rejects_empty_setup_tag(self):
        spec = _make_session_spec(decision_logging_enabled=True)
        ctl, _, _ = _ctl_and_start(spec=spec)
        with pytest.raises(ValueError, match="setup_tag"):
            ctl.log_decision(
                action="watch",
                setup_tag="  ",
                confidence=3,
            )

    @pytest.mark.parametrize("confidence", [0, 6])
    def test_rejects_confidence_outside_one_to_five(self, confidence):
        spec = _make_session_spec(decision_logging_enabled=True)
        ctl, _, _ = _ctl_and_start(spec=spec)
        with pytest.raises(ValueError, match="1 to 5"):
            ctl.log_decision(
                action="watch",
                setup_tag="breakout",
                confidence=confidence,
            )

    def test_decisions_survive_multi_cycle_result_merge(self):
        spec = _make_session_spec(decision_logging_enabled=True)
        ctl, _, _ = _ctl_and_start(spec=spec)
        record = ctl.log_decision(
            action="pass",
            setup_tag="failed breakout",
            confidence=5,
        )
        ctl._archived_pre_trades = [object()]
        assert ctl.result().decisions == [record]

    def test_inactive_controller_rejects_capture(self):
        ctl = SandboxController(app=_FakeChartApp(), tag_store=TagStore())
        with pytest.raises(RuntimeError, match="no active"):
            ctl.log_decision(
                action="watch",
                setup_tag="breakout",
                confidence=3,
            )
