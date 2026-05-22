"""Multi-layer tests for :mod:`tradinglab.gui.polling`.

This module's coverage is dominated by Tk ``after()`` plumbing that
boots in the real app but is awkward to drive headlessly. The pure
scheduler helpers (``_market_window_et``,
``_postpone_past_closed_market``, ``_next_daily_close_epoch``,
``_compute_fetch_delay_ms``) are pure functions with no Tk
dependency — perfect targets. The :class:`PollingMixin` methods we
do exercise here (``_track_after``, ``_schedule_reload``) drive a
minimal harness with fake ``after`` / ``after_cancel`` recorders so
the timer-tracking + auto-eviction semantics get coverage without
booting a real Tk interpreter.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from tradinglab.gui import polling as _polling

ET = ZoneInfo("America/New_York")


def _epoch_at(year, month, day, hour, minute=0, second=0, *, tz=ET) -> float:
    return _dt.datetime(year, month, day, hour, minute, second, tzinfo=tz).timestamp()


# ---------------------------------------------------------------------------
# 1. _market_window_et
# ---------------------------------------------------------------------------


class TestMarketWindowET:
    def test_regular_hours(self):
        open_t, close_t = _polling._market_window_et(include_extended=False)
        assert open_t == _dt.time(9, 30)
        assert close_t == _dt.time(16, 0)

    def test_extended_hours(self):
        open_t, close_t = _polling._market_window_et(include_extended=True)
        assert open_t == _dt.time(4, 0)
        assert close_t == _dt.time(20, 0)


# ---------------------------------------------------------------------------
# 2. _postpone_past_closed_market
# ---------------------------------------------------------------------------


class TestPostponePastClosedMarket:
    def test_midday_weekday_passes_through(self):
        """13:00 ET Tue ⇒ inside RTH, no postpone."""
        t = _epoch_at(2025, 5, 13, 13, 0)  # Tue
        out = _polling._postpone_past_closed_market(t, include_extended=False)
        assert out == t

    def test_premarket_postpones_to_open(self):
        """06:00 ET Tue with RTH-only ⇒ jump to 09:30."""
        t = _epoch_at(2025, 5, 13, 6, 0)
        out = _polling._postpone_past_closed_market(t, include_extended=False)
        out_dt = _dt.datetime.fromtimestamp(out, tz=ET)
        assert out_dt.date() == _dt.date(2025, 5, 13)
        assert out_dt.time() == _dt.time(9, 30)

    def test_aftermarket_postpones_to_next_weekday_open(self):
        """22:00 ET Tue ⇒ next day's 09:30."""
        t = _epoch_at(2025, 5, 13, 22, 0)
        out = _polling._postpone_past_closed_market(t, include_extended=False)
        out_dt = _dt.datetime.fromtimestamp(out, tz=ET)
        assert out_dt.date() == _dt.date(2025, 5, 14)
        assert out_dt.time() == _dt.time(9, 30)

    def test_saturday_postpones_to_monday_open(self):
        """Sat target ⇒ Mon open."""
        # 2025-05-17 is a Saturday.
        t = _epoch_at(2025, 5, 17, 10, 0)
        out = _polling._postpone_past_closed_market(t, include_extended=False)
        out_dt = _dt.datetime.fromtimestamp(out, tz=ET)
        assert out_dt.date() == _dt.date(2025, 5, 19)  # Monday
        assert out_dt.time() == _dt.time(9, 30)

    def test_sunday_postpones_to_monday_open(self):
        """Sun target ⇒ Mon open."""
        t = _epoch_at(2025, 5, 18, 12, 0)  # Sun
        out = _polling._postpone_past_closed_market(t, include_extended=False)
        out_dt = _dt.datetime.fromtimestamp(out, tz=ET)
        assert out_dt.date() == _dt.date(2025, 5, 19)
        assert out_dt.time() == _dt.time(9, 30)

    def test_extended_hours_window_keeps_late_target(self):
        """19:00 ET Tue with extended-hours = True ⇒ inside window."""
        t = _epoch_at(2025, 5, 13, 19, 0)
        out = _polling._postpone_past_closed_market(t, include_extended=True)
        assert out == t

    def test_friday_aftermarket_postpones_to_monday(self):
        """Fri 21:00 ET ⇒ skip weekend ⇒ Mon open."""
        # 2025-05-16 is Friday.
        t = _epoch_at(2025, 5, 16, 21, 0)
        out = _polling._postpone_past_closed_market(t, include_extended=False)
        out_dt = _dt.datetime.fromtimestamp(out, tz=ET)
        assert out_dt.date() == _dt.date(2025, 5, 19)  # Mon
        assert out_dt.time() == _dt.time(9, 30)

    def test_zoneinfo_import_failure_returns_input(self):
        """If zoneinfo cannot be loaded the helper is conservative —
        returns the input epoch unchanged so callers still schedule
        *something*."""
        t = _epoch_at(2025, 5, 13, 22, 0)
        # Patch the in-method import.
        import builtins
        real_import = builtins.__import__

        def _fail(name, *args, **kwargs):
            if name == "zoneinfo":
                raise ImportError("simulated missing zoneinfo")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", _fail):
            out = _polling._postpone_past_closed_market(t, include_extended=False)
        assert out == t


# ---------------------------------------------------------------------------
# 3. _next_daily_close_epoch
# ---------------------------------------------------------------------------


class TestNextDailyCloseEpoch:
    def test_morning_returns_same_day_close_plus_grace(self):
        """10:00 ET Tue ⇒ 16:05 same day."""
        now = _epoch_at(2025, 5, 13, 10, 0)
        out = _polling._next_daily_close_epoch(now, grace_s=300)
        out_dt = _dt.datetime.fromtimestamp(out, tz=ET)
        assert out_dt.date() == _dt.date(2025, 5, 13)
        assert out_dt.hour == 16 and out_dt.minute == 5

    def test_after_close_rolls_to_next_weekday(self):
        """20:00 ET Tue ⇒ Wed 16:05."""
        now = _epoch_at(2025, 5, 13, 20, 0)
        out = _polling._next_daily_close_epoch(now, grace_s=300)
        out_dt = _dt.datetime.fromtimestamp(out, tz=ET)
        assert out_dt.date() == _dt.date(2025, 5, 14)
        assert out_dt.hour == 16 and out_dt.minute == 5

    def test_friday_evening_skips_weekend(self):
        """Fri 20:00 ⇒ Mon 16:05."""
        now = _epoch_at(2025, 5, 16, 20, 0)
        out = _polling._next_daily_close_epoch(now, grace_s=300)
        out_dt = _dt.datetime.fromtimestamp(out, tz=ET)
        assert out_dt.date() == _dt.date(2025, 5, 19)  # Mon

    def test_saturday_skips_to_monday(self):
        now = _epoch_at(2025, 5, 17, 10, 0)  # Sat
        out = _polling._next_daily_close_epoch(now, grace_s=300)
        out_dt = _dt.datetime.fromtimestamp(out, tz=ET)
        assert out_dt.date() == _dt.date(2025, 5, 19)

    def test_custom_grace_seconds(self):
        now = _epoch_at(2025, 5, 13, 10, 0)
        out = _polling._next_daily_close_epoch(now, grace_s=600)
        out_dt = _dt.datetime.fromtimestamp(out, tz=ET)
        assert out_dt.hour == 16 and out_dt.minute == 10

    def test_zoneinfo_failure_returns_now_plus_one_day(self):
        now = _epoch_at(2025, 5, 13, 10, 0)
        import builtins
        real_import = builtins.__import__

        def _fail(name, *args, **kwargs):
            if name == "zoneinfo":
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", _fail):
            out = _polling._next_daily_close_epoch(now, grace_s=300)
        assert out == now + 86400


# ---------------------------------------------------------------------------
# 4. _compute_fetch_delay_ms
# ---------------------------------------------------------------------------


class TestComputeFetchDelayMs:
    def test_intraday_anchors_to_last_bar_plus_interval(self):
        """5m interval, last bar 10:00, now 10:01 ⇒ fetch at 10:05:05."""
        now = _epoch_at(2025, 5, 13, 10, 1, 0)
        last = _epoch_at(2025, 5, 13, 10, 0, 0)
        delay = _polling._compute_fetch_delay_ms(
            "5m", last, now, include_extended=False,
            min_backoff_ms=500, grace_intraday_s=5,
        )
        # ~4m 5s = 245s = 245000ms.
        assert 240_000 < delay < 250_000

    def test_intraday_past_due_uses_grace_window(self):
        """When last+interval is already past, schedule at now+grace."""
        now = _epoch_at(2025, 5, 13, 10, 20, 0)
        last = _epoch_at(2025, 5, 13, 10, 0, 0)
        delay = _polling._compute_fetch_delay_ms(
            "5m", last, now, include_extended=False,
            min_backoff_ms=500, grace_intraday_s=5,
        )
        # ~5s = 5000ms.
        assert 4_000 < delay < 7_000

    def test_intraday_no_last_bar_uses_full_interval_plus_grace(self):
        """No last bar known ⇒ now + interval + grace."""
        now = _epoch_at(2025, 5, 13, 10, 0, 0)
        delay = _polling._compute_fetch_delay_ms(
            "5m", None, now, include_extended=False,
            min_backoff_ms=500, grace_intraday_s=5,
        )
        # 5m + 5s = 305s = 305000ms.
        assert 300_000 < delay < 310_000

    def test_intraday_postpones_past_close(self):
        """Last bar 15:55, interval 1h, now 15:58 ⇒ target 16:55 ⇒
        postponed to next 09:30."""
        now = _epoch_at(2025, 5, 13, 15, 58, 0)
        last = _epoch_at(2025, 5, 13, 15, 55, 0)
        delay = _polling._compute_fetch_delay_ms(
            "1h", last, now, include_extended=False,
            min_backoff_ms=500, grace_intraday_s=5,
        )
        # From 15:58 Tue to 09:30 Wed = 17h 32m = 63,120s = 63_120_000ms.
        assert 62_000_000 < delay < 64_000_000

    def test_daily_uses_daily_helper(self):
        """1d interval ⇒ ignores last_bar, uses _next_daily_close_epoch."""
        now = _epoch_at(2025, 5, 13, 10, 0, 0)
        delay = _polling._compute_fetch_delay_ms(
            "1d", None, now, include_extended=False,
            min_backoff_ms=500, grace_intraday_s=5, grace_daily_s=300,
        )
        # From 10:00 to 16:05 = 6h 5m = 21,900s = 21,900_000ms.
        assert 21_800_000 < delay < 22_000_000

    def test_weekly_uses_daily_helper(self):
        now = _epoch_at(2025, 5, 13, 10, 0, 0)
        delay = _polling._compute_fetch_delay_ms(
            "1wk", None, now, include_extended=False,
            min_backoff_ms=500,
        )
        # Same as 1d.
        assert 21_800_000 < delay < 22_000_000

    def test_min_backoff_floor_applied(self):
        """If computed target is past, the min_backoff_ms floor kicks in."""
        now = _epoch_at(2025, 5, 13, 10, 0, 0)
        # Last bar = now (effectively); intraday with grace 0 ⇒ target = now.
        # Past-due branch resets target_s to now+grace=0, so delta=0;
        # min_backoff floor 5000ms should win.
        delay = _polling._compute_fetch_delay_ms(
            "5m", now - 600, now, include_extended=False,
            min_backoff_ms=5000, grace_intraday_s=0,
        )
        assert delay >= 5000


# ---------------------------------------------------------------------------
# 5. PollingMixin — _track_after + _schedule_reload with a fake harness
# ---------------------------------------------------------------------------


class _FakeAfter:
    """Recorder for ``after(delay, fn)`` / ``after_cancel(id)`` calls."""

    def __init__(self) -> None:
        self.scheduled: list[tuple[int, Any, str]] = []
        self.cancelled: list[str] = []
        self._next_id = 0

    def after(self, delay_ms: int, fn) -> str:
        self._next_id += 1
        jid = f"job-{self._next_id}"
        self.scheduled.append((delay_ms, fn, jid))
        return jid

    def after_cancel(self, jid: str) -> None:
        self.cancelled.append(jid)


class _PollingHarness(_polling.PollingMixin):
    """Minimal harness that satisfies the PollingMixin attribute contract
    without booting a real Tk interpreter."""

    _MIN_POLL_BACKOFF_MS = 500
    _POLL_RETRY_DELAY_MS = 1500
    _POLL_RETRY_MAX = 6

    def __init__(self) -> None:
        self._after_jobs: set[str] = set()
        self._reload_job: str | None = None
        self._stream_drain_after: str | None = None
        self._worker_inbox_after: str | None = None
        self._fake = _FakeAfter()
        self.reload_calls = 0

    # Forward to the recorder.
    def after(self, delay_ms: int, fn):
        return self._fake.after(delay_ms, fn)

    def after_cancel(self, jid: str) -> None:
        self._fake.after_cancel(jid)

    def _do_scheduled_reload(self) -> None:
        self.reload_calls += 1
        self._reload_job = None


class TestTrackAfter:
    def test_returns_job_id_and_tracks(self):
        h = _PollingHarness()
        called: list[bool] = []
        jid = h._track_after(100, lambda: called.append(True))
        assert jid in h._after_jobs
        # The fake recorder also saw the call.
        assert h._fake.scheduled[0][0] == 100

    def test_clamps_negative_delay_to_zero(self):
        h = _PollingHarness()
        h._track_after(-50, lambda: None)
        assert h._fake.scheduled[0][0] == 0

    def test_fired_callback_evicts_id_from_set(self):
        h = _PollingHarness()
        called: list[bool] = []
        jid = h._track_after(0, lambda: called.append(True))
        assert jid in h._after_jobs
        # Pull the wrapper from the recorder and fire it.
        wrapper = h._fake.scheduled[0][1]
        wrapper()
        assert jid not in h._after_jobs
        assert called == [True]

    def test_fired_callback_returns_underlying_value(self):
        h = _PollingHarness()
        jid = h._track_after(0, lambda: "result-value")
        wrapper = h._fake.scheduled[0][1]
        assert wrapper() == "result-value"

    def test_multiple_jobs_tracked_independently(self):
        h = _PollingHarness()
        ids = [h._track_after(i, lambda: None) for i in range(5)]
        assert h._after_jobs == set(ids)
        # Fire one — only that id evicts.
        h._fake.scheduled[2][1]()
        assert ids[2] not in h._after_jobs
        assert all(j in h._after_jobs for j in (ids[0], ids[1], ids[3], ids[4]))


class TestScheduleReload:
    def test_first_call_schedules_reload(self):
        h = _PollingHarness()
        h._schedule_reload(delay_ms=300)
        assert h._reload_job is not None
        assert h._fake.scheduled[0][0] == 300

    def test_second_call_cancels_first(self):
        h = _PollingHarness()
        h._schedule_reload(delay_ms=300)
        first_jid = h._reload_job
        h._schedule_reload(delay_ms=500)
        assert first_jid in h._fake.cancelled
        # New job id stored.
        assert h._reload_job != first_jid

    def test_fires_do_scheduled_reload(self):
        h = _PollingHarness()
        h._schedule_reload(delay_ms=100)
        wrapper = h._fake.scheduled[0][1]
        wrapper()
        assert h.reload_calls == 1
        # _reload_job cleared by _do_scheduled_reload.
        assert h._reload_job is None


# ---------------------------------------------------------------------------
# 6. PollingMixin — _drain_worker_inbox
# ---------------------------------------------------------------------------


import queue as _queue


class _InboxHarness(_PollingHarness):
    """Extends the polling harness with the worker-inbox surface."""

    def __init__(self) -> None:
        super().__init__()
        self._worker_inbox: _queue.Queue = _queue.Queue()
        self.stash_calls: list[tuple] = []
        self.prefetch_calls: list[tuple] = []
        self.reference_calls = 0
        self.watchlist_refresh_calls = 0

    def _stash_full_cache(self, key, bars):
        self.stash_calls.append((key, bars))

    def _apply_prefetch_result(self, key, bars):
        self.prefetch_calls.append((key, bars))

    def _reference_data_redraw(self):
        self.reference_calls += 1

    def _schedule_watchlist_tab_refresh(self):
        self.watchlist_refresh_calls += 1


class TestDrainWorkerInbox:
    def test_empty_inbox_just_re_arms(self):
        h = _InboxHarness()
        h._drain_worker_inbox()
        # Re-arm visible.
        assert h._worker_inbox_after is not None

    def test_stash_event_dispatches(self):
        h = _InboxHarness()
        h._worker_inbox.put(("stash", (("yf", "SPY", "1d"), [1, 2, 3])))
        h._drain_worker_inbox()
        assert h.stash_calls == [(("yf", "SPY", "1d"), [1, 2, 3])]

    def test_prefetch_event_dispatches(self):
        h = _InboxHarness()
        h._worker_inbox.put(("prefetch", (("yf", "MSFT", "5m"), [1, 2])))
        h._drain_worker_inbox()
        assert h.prefetch_calls == [(("yf", "MSFT", "5m"), [1, 2])]

    def test_refresh_event_calls_watchlist_refresh(self):
        h = _InboxHarness()
        h._worker_inbox.put(("refresh", None))
        h._drain_worker_inbox()
        assert h.watchlist_refresh_calls == 1

    def test_refresh_routes_to_watchlist_tab_when_present(self):
        h = _InboxHarness()

        class _WTab:
            def __init__(self):
                self.calls = 0

            def _schedule_watchlist_tab_refresh(self):
                self.calls += 1

        h.watchlist_tab = _WTab()
        h._worker_inbox.put(("refresh", None))
        h._drain_worker_inbox()
        assert h.watchlist_tab.calls == 1
        # Self path NOT called when the tab handled it.
        assert h.watchlist_refresh_calls == 0

    def test_reference_event_redraws(self):
        h = _InboxHarness()
        h._worker_inbox.put(("reference", None))
        h._drain_worker_inbox()
        assert h.reference_calls == 1

    def test_multiple_refresh_events_coalesce(self):
        """Spec §worker-inbox: multiple refresh events in one drain
        produce ONE call to the redraw helper."""
        h = _InboxHarness()
        h._worker_inbox.put(("refresh", None))
        h._worker_inbox.put(("refresh", None))
        h._worker_inbox.put(("refresh", None))
        h._drain_worker_inbox()
        assert h.watchlist_refresh_calls == 1

    def test_stash_exception_swallowed_continues_drain(self):
        h = _InboxHarness()

        def _boom(key, bars):
            raise RuntimeError("stash boom")

        h._stash_full_cache = _boom
        h._worker_inbox.put(("stash", (("yf", "SPY", "1d"), [1])))
        h._worker_inbox.put(("reference", None))
        h._drain_worker_inbox()
        # The broken stash didn't stop the second event from firing.
        assert h.reference_calls == 1

    def test_card_stash_dispatches_to_chartstack(self):
        h = _InboxHarness()

        class _CS:
            def __init__(self):
                self.calls: list[tuple] = []

            def apply_card_stash(self, slot, tok, sym, bars):
                self.calls.append((slot, tok, sym, bars))

        h._chartstack = _CS()
        h._worker_inbox.put(("card_stash", (2, 99, "AAPL", [1, 2, 3])))
        h._drain_worker_inbox()
        assert h._chartstack.calls == [(2, 99, "AAPL", [1, 2, 3])]

    def test_unknown_event_kind_is_ignored(self):
        h = _InboxHarness()
        h._worker_inbox.put(("not_a_real_kind", None))
        # Must not raise.
        h._drain_worker_inbox()


# ---------------------------------------------------------------------------
# 7. PollingMixin — _schedule_drain + _schedule_worker_inbox_drain
# ---------------------------------------------------------------------------


class TestScheduleDrains:
    def test_schedule_drain_stores_after_id(self):
        h = _InboxHarness()
        h._schedule_drain()
        assert h._stream_drain_after is not None

    def test_schedule_drain_uses_50ms_period(self):
        h = _InboxHarness()
        h._schedule_drain()
        assert h._fake.scheduled[0][0] == 50

    def test_schedule_worker_inbox_drain_uses_80ms_period(self):
        h = _InboxHarness()
        h._schedule_worker_inbox_drain()
        assert h._fake.scheduled[0][0] == 80


# ---------------------------------------------------------------------------
# 8. PollingMixin — _drain_stream_queue
# ---------------------------------------------------------------------------


class _StreamHarness(_InboxHarness):
    """Polling harness with stream-queue + tick/rollover stubs."""

    def __init__(self) -> None:
        super().__init__()
        self._stream_queue: _queue.Queue = _queue.Queue()
        self._stream_ctrl = None
        self.tick_calls: list = []
        self.rollover_calls: list = []
        self.refresh_after_append_calls: list[str] = []
        self.refresh_after_tick_calls: list[str] = []
        self.render_calls = 0
        self._primary = []
        self.candles = []
        self._full_cache: dict = {}

        class _Var:
            def __init__(self, v):
                self._v = v

            def get(self):
                return self._v

        self.source_var = _Var("yf")
        self.interval_var = _Var("5m")
        self.ticker_var = _Var("SPY")

    def _apply_stream_tick(self, evt):
        self.tick_calls.append(evt)
        return True  # signal "ticked"

    def _apply_stream_rollover(self, evt):
        self.rollover_calls.append(evt)
        return True  # signal "rolled"

    def _refresh_view_after_append(self, slot):
        self.refresh_after_append_calls.append(slot)

    def _refresh_view_after_tick(self, slot):
        self.refresh_after_tick_calls.append(slot)

    def _rewire_slot_candles(self, slot, raw):
        pass

    def _render(self):
        self.render_calls += 1


def _evt(token, slot, src, ticker, interval, kind, bar=None):
    return (token, slot, src, ticker, interval, kind, bar)


class TestDrainStreamQueue:
    def test_empty_queue_just_re_arms(self):
        h = _StreamHarness()
        h._drain_stream_queue()
        # Re-armed.
        assert h._stream_drain_after is not None

    def test_tick_event_dispatches_and_refreshes(self):
        h = _StreamHarness()
        h._stream_queue.put(_evt(1, "primary", "yf", "SPY", "5m", "tick"))
        h._drain_stream_queue()
        assert len(h.tick_calls) == 1
        # Tick path triggers _refresh_view_after_tick.
        assert h.refresh_after_tick_calls == ["primary"]

    def test_rollover_event_appends_and_refreshes(self):
        h = _StreamHarness()
        h._full_cache[("yf", "SPY", "5m")] = [object()]
        h._stream_queue.put(_evt(1, "primary", "yf", "SPY", "5m", "rollover"))
        h._drain_stream_queue()
        assert len(h.rollover_calls) == 1
        # Rollover path triggers _refresh_view_after_append.
        assert h.refresh_after_append_calls == ["primary"]

    def test_card_stream_event_routes_to_chartstack(self):
        h = _StreamHarness()

        class _CS:
            def __init__(self):
                self.calls: list = []

            def apply_stream_event(self, slot_index, token, kind, bar):
                self.calls.append((slot_index, token, kind, bar))

        h._chartstack = _CS()
        h._stream_queue.put(_evt(99, "card:2", "yf", "AAPL", "5m", "tick", "barobj"))
        h._drain_stream_queue()
        # Card event routed; main tick path NOT invoked.
        assert h._chartstack.calls == [(2, 99, "tick", "barobj")]
        assert h.tick_calls == []

    def test_card_event_with_bad_slot_index_is_dropped(self):
        h = _StreamHarness()

        class _CS:
            def __init__(self):
                self.calls: list = []

            def apply_stream_event(self, *args):
                self.calls.append(args)

        h._chartstack = _CS()
        h._stream_queue.put(_evt(1, "card:not_a_number", "yf", "X", "5m", "tick"))
        h._drain_stream_queue()
        # Malformed slot ⇒ dropped.
        assert h._chartstack.calls == []

    def test_card_event_with_no_chartstack_is_dropped(self):
        h = _StreamHarness()
        h._chartstack = None
        h._stream_queue.put(_evt(1, "card:0", "yf", "X", "5m", "tick"))
        # Must not raise.
        h._drain_stream_queue()

    def test_rollover_with_empty_cache_falls_back_to_render(self):
        """If the cache misses for the symbol, the rollover path
        eventually invokes _refresh_view_after_append which our stub
        records — no fallback render needed."""
        h = _StreamHarness()
        # No cache entry.
        h._stream_queue.put(_evt(1, "primary", "yf", "SPY", "5m", "rollover"))
        h._drain_stream_queue()
        # Rollover applied, append-refresh attempted.
        assert h.refresh_after_append_calls == ["primary"]
