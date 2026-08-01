"""``SandboxController.skip_to_next_day`` — the day fast-forward.

The sandbox replay is deliberately untimed (no playback clock — the
trader gets unlimited deliberation per bar, chess-puzzle style). The
cost of that design is ergonomic: a boring day, or a trader who only
works the first hour, still has to press "next bar" ~78 times to reach
the next session. ``skip_to_next_day`` collapses that into one action.

What these tests pin:

* The clock lands on the **next session day**, and the bar count is
  "rest of this day + 1".
* The **end-of-day kill switch** runs at the skipped day's final bar —
  open positions are flattened at that bar's close, not carried.
* **Skipping never skips journaling**: every synthesised flatten still
  drives the mandatory post-trade review callback.
* The render is **batched** — one redraw for the whole skip, not one
  per suppressed bar — and the ``_defer_render`` flag is always
  restored, even though every other per-tick effect still fires.
* Auto-cycle (blind mode) delegates the day roll to ``cycle_to_next``
  so equity carries forward, and the kill switch does not double-fire.
* End-of-replay and inactive-controller guards.

Reuses the fake ``ChartApp`` from ``test_replay_state_machine`` — it is
the canonical stand-in for the controller-allowed primitive surface,
and duplicating it here would let the two drift.
"""
from __future__ import annotations

import datetime as _dt

from tradinglab.backtest.replay import SandboxController, SkipDayOutcome
from tradinglab.backtest.tags import TagStore
from tradinglab.models import Candle

from .test_replay_state_machine import _FakeChartApp, _make_session_spec

_UTC = _dt.timezone.utc

# Bars are minted at 14:30 UTC (= 09:30 ET) so the candle's wall-clock
# date and its UTC date agree. ``deck._candle_session_date`` reads the
# wall clock while ``SandboxController.current_session_date`` converts
# the epoch to UTC — mid-day UTC timestamps keep the two definitions
# identical on any developer machine's local timezone.
_OPEN_UTC_HOUR = 14
_OPEN_UTC_MINUTE = 30

# 2025-03-03 is a Monday, so a 3-day run stays inside one trading week.
_FIRST_DAY = _dt.date(2025, 3, 3)


def _multiday_candles(
    n_days: int,
    bars_per_day: int,
    *,
    first_day: _dt.date = _FIRST_DAY,
    interval_min: int = 5,
    base: float = 100.0,
) -> list[Candle]:
    """``n_days`` contiguous regular-session days of intraday candles."""
    out: list[Candle] = []
    for d in range(n_days):
        day = first_day + _dt.timedelta(days=d)
        start = _dt.datetime(
            day.year, day.month, day.day,
            _OPEN_UTC_HOUR, _OPEN_UTC_MINUTE, tzinfo=_UTC,
        )
        for i in range(bars_per_day):
            ts = start + _dt.timedelta(minutes=interval_min * i)
            px = base + d * 5.0 + i * 0.10
            out.append(Candle(
                date=ts,
                open=px,
                high=px + 0.20,
                low=px - 0.20,
                close=px + 0.05,
                volume=1_000_000,
                session="regular",
            ))
    return out


def _start(
    *,
    n_days: int = 3,
    bars_per_day: int = 12,
    session_day_offset: int = 1,
    lookback_days: int = 1,
    auto_cycle: bool = False,
) -> tuple[SandboxController, _FakeChartApp, list[Candle]]:
    """Start a free-mode (or auto-cycle) session on day ``offset``."""
    app = _FakeChartApp()
    candles = _multiday_candles(n_days, bars_per_day)
    all_days = [_FIRST_DAY + _dt.timedelta(days=d) for d in range(n_days)]
    session_date = all_days[session_day_offset]
    ctl = SandboxController(app=app, tag_store=TagStore())
    ctl.start_session(
        spec=_make_session_spec(),
        session_date=session_date,
        interval="5m",
        reference_symbol="SPY",
        reference_candles=candles,
        lookback_days=lookback_days,
        auto_cycle=auto_cycle,
        eligible_dates=all_days if auto_cycle else None,
    )
    return ctl, app, candles


def _open_a_long(ctl: SandboxController, qty: float = 10.0) -> None:
    """Queue a buy and tick once so it fills at the next bar's open."""
    ctl.submit_order(
        symbol="SPY",
        side="buy",
        quantity=qty,
        pre_trade_data={
            "setup_tag": "breakout",
            "thesis": "testing the eod kill switch",
            "conviction": 3,
            "size": qty,
            "target": None,
            "notes": "",
        },
    )
    ctl.next_bar()


# ---------------------------------------------------------------------------
# 1. Day advance
# ---------------------------------------------------------------------------


class TestDayAdvance:
    def test_lands_on_the_next_session_day(self):
        ctl, _, _ = _start(bars_per_day=12)
        before = ctl.current_session_date()
        outcome = ctl.skip_to_next_day()
        after = ctl.current_session_date()

        assert outcome.day_changed is True
        assert after is not None and before is not None
        assert after == before + _dt.timedelta(days=1)

    def test_bar_count_is_rest_of_day_plus_one(self):
        """Start sits on the session day's first bar, so a 12-bar day
        costs 11 ticks to reach the close + 1 to cross the boundary."""
        ctl, _, _ = _start(bars_per_day=12)
        outcome = ctl.skip_to_next_day()
        assert outcome.bars_advanced == 12

    def test_outcome_is_truthy_when_work_happened(self):
        ctl, _, _ = _start()
        assert bool(ctl.skip_to_next_day()) is True

    def test_visible_list_grew_by_the_skipped_bars(self):
        ctl, _, _ = _start(bars_per_day=12)
        before = len(ctl.visible_candles_by_symbol["SPY"])
        outcome = ctl.skip_to_next_day()
        after = len(ctl.visible_candles_by_symbol["SPY"])
        assert after - before == outcome.bars_advanced

    def test_visible_list_identity_is_stable_across_a_skip(self):
        """Skipping must still append in place — the app holds this exact
        list object as ``_primary``."""
        ctl, _, _ = _start()
        first_id = id(ctl.visible_candles_by_symbol["SPY"])
        ctl.skip_to_next_day()
        assert id(ctl.visible_candles_by_symbol["SPY"]) == first_id

    def test_day_ordinal_advances_exactly_once(self):
        ctl, _, _ = _start(bars_per_day=12)
        before = ctl.current_day_ordinal()
        ctl.skip_to_next_day()
        assert ctl.current_day_ordinal() == before + 1


# ---------------------------------------------------------------------------
# 2. End-of-day kill switch
# ---------------------------------------------------------------------------


class TestEodKillSwitch:
    def test_open_position_is_flattened(self):
        ctl, _, _ = _start()
        _open_a_long(ctl)
        assert ctl.engine.portfolio.positions["SPY"].quantity == 10.0

        outcome = ctl.skip_to_next_day()

        assert outcome.positions_flattened == 1
        assert ctl.engine.portfolio.positions["SPY"].quantity == 0.0

    def test_flatten_price_is_the_skipped_days_close(self):
        ctl, _, candles = _start(bars_per_day=12, session_day_offset=1)
        _open_a_long(ctl)
        ctl.skip_to_next_day()

        post = ctl.engine.post_trades
        assert len(post) == 1
        # Day index 1 of the generated series; its final bar's close.
        day_1 = [c for c in candles
                 if c.date.date() == _FIRST_DAY + _dt.timedelta(days=1)]
        assert post[0].exit_price == day_1[-1].close

    def test_no_position_means_no_flatten(self):
        ctl, _, _ = _start()
        outcome = ctl.skip_to_next_day()
        assert outcome.positions_flattened == 0
        assert ctl.engine.post_trades == []

    def test_flatten_still_drives_the_mandatory_review_callback(self):
        """Skipping the day must never skip journaling."""
        ctl, _, _ = _start()
        seen: list = []

        ctl.set_post_trade_callback(
            lambda ptr: seen.append(ptr) or "reviewed on skip")
        _open_a_long(ctl)
        ctl.skip_to_next_day()

        assert len(seen) == 1
        assert ctl.engine.post_trades[0].user_review == "reviewed on skip"

    def test_position_does_not_survive_into_the_next_day(self):
        ctl, _, _ = _start(n_days=3, bars_per_day=8)
        _open_a_long(ctl)
        ctl.skip_to_next_day()
        # Second skip finds a flat book — nothing carried across.
        outcome = ctl.skip_to_next_day()
        assert outcome.positions_flattened == 0


# ---------------------------------------------------------------------------
# 3. Render batching
# ---------------------------------------------------------------------------


class TestRenderBatching:
    def test_redraw_is_batched_not_per_bar(self):
        ctl, app, _ = _start(bars_per_day=12)
        before = len(app.refresh_view_calls)
        outcome = ctl.skip_to_next_day()
        drawn = len(app.refresh_view_calls) - before

        assert outcome.bars_advanced == 12
        # Only the final boundary-crossing tick renders through the
        # append path; the 11 suppressed ticks do not.
        assert drawn == 1

    def test_terminal_render_installs_the_focused_series_once(self):
        ctl, app, _ = _start(bars_per_day=12)
        before = len(app.install_primary_calls)
        ctl.skip_to_next_day()
        assert len(app.install_primary_calls) - before == 1

    def test_indicator_cache_still_invalidated_every_bar(self):
        """Only presentation is deferred — cache invalidation is not.

        ``_invalidate_focused`` prefers the append-aware
        ``_notify_focused_panels_appended`` hook (so the indicator
        cache can take its incremental path) and falls back to the
        full-invalidate primitive; count both.
        """
        ctl, app, _ = _start(bars_per_day=12)
        before = (len(app.notify_appended_calls)
                  + len(app.invalidate_focused_calls))
        outcome = ctl.skip_to_next_day()
        after = (len(app.notify_appended_calls)
                 + len(app.invalidate_focused_calls))
        assert after - before == outcome.bars_advanced

    def test_defer_flag_restored_after_skip(self):
        ctl, _, _ = _start()
        ctl.skip_to_next_day()
        assert ctl._defer_render is False
        assert ctl._pending_day_changed is False

    def test_defer_flag_restored_even_if_a_tick_raises(self):
        ctl, app, _ = _start()

        boom = RuntimeError("render exploded")

        def _explode(_candles):
            raise boom

        app._invalidate_focused_panels = _explode  # type: ignore[assignment]
        try:
            ctl.skip_to_next_day()
        except RuntimeError as exc:  # pragma: no cover - defensive
            assert exc is boom
        assert ctl._defer_render is False


# ---------------------------------------------------------------------------
# 4. Auto-cycle (blind mode)
# ---------------------------------------------------------------------------


class TestAutoCycle:
    def test_skip_rolls_to_the_next_eligible_date(self):
        ctl, _, _ = _start(auto_cycle=True, bars_per_day=8)
        before = ctl.current_session_date()
        outcome = ctl.skip_to_next_day()
        assert outcome.day_changed is True
        assert ctl.current_session_date() != before

    def test_kill_switch_does_not_double_close(self):
        """``cycle_to_next`` runs its own flatten; by then the book is
        already flat, so exactly one round-trip is recorded."""
        ctl, _, _ = _start(auto_cycle=True, bars_per_day=8)
        _open_a_long(ctl)
        outcome = ctl.skip_to_next_day()

        assert outcome.positions_flattened == 1
        # One entry fill + one flatten fill; a double-close would add a
        # third (and a second PostTradeReview).
        assert len(ctl.result().post_trades) == 1

    def test_equity_carries_across_the_roll(self):
        ctl, _, _ = _start(auto_cycle=True, bars_per_day=8)
        _open_a_long(ctl)
        ctl.skip_to_next_day()
        # New engine seeded from the carried cash, not the spec default.
        assert ctl.spec.starting_cash != 100_000.0


# ---------------------------------------------------------------------------
# 5. Guards / edges
# ---------------------------------------------------------------------------


class TestGuards:
    def test_inactive_controller_is_a_no_op(self):
        ctl = SandboxController(app=_FakeChartApp(), tag_store=TagStore())
        outcome = ctl.skip_to_next_day()
        assert isinstance(outcome, SkipDayOutcome)
        assert not outcome
        assert outcome.exhausted is True

    def test_last_day_reports_exhausted(self):
        ctl, _, _ = _start(n_days=2, bars_per_day=8, session_day_offset=1)
        outcome = ctl.skip_to_next_day()
        assert outcome.exhausted is True
        assert outcome.day_changed is False

    def test_last_day_still_runs_the_kill_switch(self):
        """Running out of bars must not strand an open position."""
        ctl, _, _ = _start(n_days=2, bars_per_day=8, session_day_offset=1)
        _open_a_long(ctl)
        outcome = ctl.skip_to_next_day()
        assert outcome.exhausted is True
        assert outcome.positions_flattened == 1
        assert ctl.engine.portfolio.positions["SPY"].quantity == 0.0
