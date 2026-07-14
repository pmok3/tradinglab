"""Unit tests for the watchlist recurring poll loop.

Covers:
- ``_watchlist_poll_in_rth_now`` — RTH boundary detection with
  monkey-patched ``datetime.now``.
- ``_watchlist_poll_effective_delay_ms`` — interval × off-hours
  multiplier resolution, disabled (== 0) case, RTH vs off-hours.
- ``_start_watchlist_poll_loop`` — idempotency, ``after`` arming.
- ``_watchlist_poll_tick`` — re-fires preloads, sandbox guard, self
  re-arm.
- Orphan-snapshot recovery in ``_preload_watchlist`` /
  ``_preload_watchlist_daily``: cache fresh but snapshot row missing
  rebuilds the row directly without re-fetching.

Drives a minimal harness class (no Tk root) — the mixin's methods
that touch Tk vars / executor / after() are stubbed so we exercise
the pure scheduling logic in isolation.
"""
from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

from tradinglab import defaults as _d
from tradinglab.gui import watchlist_tab as _wl_tab
from tradinglab.gui.watchlist_tab import WatchlistTabMixin
from tradinglab.models import Candle

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


class _StubVar:
    def __init__(self, value: str) -> None:
        self._v = value

    def get(self) -> str:
        return self._v


class _StubExecutor:
    def __init__(self) -> None:
        self.submitted: list[tuple[Callable[..., Any], tuple[Any, ...]]] = []

    def submit(self, fn, *args, **kwargs):
        self.submitted.append((fn, args))

        class _Fut:
            def cancel(self):
                pass

        return _Fut()


class _StubInbox:
    def __init__(self) -> None:
        self.items: list[Any] = []

    def put_nowait(self, item: Any) -> None:
        self.items.append(item)


class _Harness(WatchlistTabMixin):
    """Minimal stand-in for ``ChartApp`` that exercises the mixin
    without booting a Tk root.

    Each instance starts with empty caches and a stub executor; tests
    populate ``_full_cache`` / ``_watchlist_snapshot`` directly and
    call the methods under test.
    """

    def __init__(
        self,
        *,
        tickers: list[str] | None = None,
        sandbox: bool = False,
        stale: bool = False,
        sandbox_clock: tuple[bool, int | None, object | None] | None = None,
    ) -> None:
        self._executor = _StubExecutor()
        self._full_cache: dict[tuple, list[Candle]] = {}
        self._watchlist_snapshot: dict[str, dict[str, Any]] = {}
        self._watchlist_preload_inflight: set = set()
        self._watchlist_poll_job: str | None = None
        self._events_cache: dict[str, Any] = {}
        self._worker_inbox = _StubInbox()
        self.source_var = _StubVar("yfinance")
        self.interval_var = _StubVar("5m")
        self._tickers = list(tickers or ["AAPL", "INTC"])
        self._sandbox = sandbox
        self._sandbox_clock = sandbox_clock or (False, None, None)
        self._stale = stale
        self.after_calls: list[tuple[int, Callable[..., Any]]] = []
        self.refresh_called: int = 0

    # ---- mixin-required stubs ----
    def _pinned_ticker_union(self) -> list[str]:
        return list(self._tickers)

    def _cache_is_stale(self, cached, itv) -> bool:  # noqa: ARG002
        return self._stale

    def _is_sandbox_active(self) -> bool:
        return self._sandbox

    def _sandbox_watchlist_clock(self) -> tuple[bool, int | None, object | None]:
        if not self._sandbox:
            return (False, None, None)
        return self._sandbox_clock

    def _track_after(self, ms: int, fn: Callable[..., Any]) -> str:
        self.after_calls.append((ms, fn))
        return f"after#{len(self.after_calls)}"

    def _schedule_watchlist_tab_refresh(self) -> None:
        self.refresh_called += 1

    def _stash_full_cache(self, key: tuple, bars: list[Candle]) -> None:
        self._full_cache[key] = bars

    def _preload_watchlist_events(self) -> None:  # neutralised
        return


def _candle(close: float, ts: float = 0.0) -> Candle:
    """Build a Candle with a sentinel date; tests only read .close."""
    d = _dt.datetime.fromtimestamp(ts or 1_700_000_000.0, tz=_dt.timezone.utc)
    return Candle(date=d, open=close, high=close, low=close,
                  close=close, volume=0)


def _dated_candle(day: _dt.date, close: float, *, hour: int = 0, minute: int = 0) -> Candle:
    d = _dt.datetime(day.year, day.month, day.day, hour, minute,
                     tzinfo=_dt.timezone.utc)
    return Candle(date=d, open=close, high=close, low=close,
                  close=close, volume=0)


# ---------------------------------------------------------------------------
# 1. _watchlist_poll_in_rth_now
# ---------------------------------------------------------------------------


class TestPollInRTHNow:
    """``_watchlist_poll_in_rth_now`` uses inline ``from datetime import
    datetime`` + ``ZoneInfo("America/New_York")``. We patch the stdlib
    ``datetime.datetime`` to a subclass with a fixed ``now()`` so the
    function-local import picks it up.
    """

    @staticmethod
    def _fake_datetime_class(when: _dt.datetime):
        real = _dt.datetime

        class _FakeDT(real):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return when
                return when.astimezone(tz)

        return _FakeDT

    def _et(self, *parts) -> _dt.datetime:
        from zoneinfo import ZoneInfo
        return _dt.datetime(*parts, tzinfo=ZoneInfo("America/New_York"))

    def test_midday_weekday_true(self):
        h = _Harness()
        fake = self._fake_datetime_class(self._et(2025, 5, 13, 13, 0))
        with patch("datetime.datetime", fake):
            assert h._watchlist_poll_in_rth_now() is True

    def test_weekend_false(self):
        h = _Harness()
        # 2025-05-17 is Saturday.
        fake = self._fake_datetime_class(self._et(2025, 5, 17, 13, 0))
        with patch("datetime.datetime", fake):
            assert h._watchlist_poll_in_rth_now() is False

    def test_premarket_false(self):
        h = _Harness()
        fake = self._fake_datetime_class(self._et(2025, 5, 13, 6, 0))
        with patch("datetime.datetime", fake):
            assert h._watchlist_poll_in_rth_now() is False

    def test_afterhours_false(self):
        h = _Harness()
        fake = self._fake_datetime_class(self._et(2025, 5, 13, 19, 0))
        with patch("datetime.datetime", fake):
            assert h._watchlist_poll_in_rth_now() is False

    def test_at_rth_open_inclusive(self):
        h = _Harness()
        fake = self._fake_datetime_class(self._et(2025, 5, 13, 9, 30))
        with patch("datetime.datetime", fake):
            assert h._watchlist_poll_in_rth_now() is True

    def test_at_rth_close_exclusive(self):
        h = _Harness()
        fake = self._fake_datetime_class(self._et(2025, 5, 13, 16, 0))
        with patch("datetime.datetime", fake):
            assert h._watchlist_poll_in_rth_now() is False

    def test_zoneinfo_failure_returns_true(self):
        """Missing tzdata ⇒ conservative True (don't silently
        downgrade to off-hours cadence). After CLAUDE.md §7.23
        the ET helper is imported from core.timezones; simulating
        a missing-tzdata environment means monkey-patching the
        cached ET constant to None."""
        h = _Harness()
        from tradinglab.core import timezones as _tz
        with patch.object(_tz, "ET", None):
            assert h._watchlist_poll_in_rth_now() is True


# ---------------------------------------------------------------------------
# 2. _watchlist_poll_effective_delay_ms
# ---------------------------------------------------------------------------


class TestEffectiveDelayMs:
    def test_default_rth(self):
        h = _Harness()
        with patch.object(_Harness, "_watchlist_poll_in_rth_now", return_value=True):
            ms = h._watchlist_poll_effective_delay_ms()
        # Default is 60 seconds.
        assert ms == 60_000

    def test_default_offhours_multiplied(self):
        h = _Harness()
        with patch.object(_Harness, "_watchlist_poll_in_rth_now", return_value=False):
            ms = h._watchlist_poll_effective_delay_ms()
        # Default 60s × 5 = 300s.
        assert ms == 300_000

    def test_disabled_returns_none(self):
        h = _Harness()
        with patch.object(_d, "get", side_effect=lambda k: 0 if "interval" in k else 5.0):
            assert h._watchlist_poll_effective_delay_ms() is None

    def test_clamped_to_5s_minimum(self):
        """Even with a custom 1s interval, delay never drops below 5s
        (defends against tight-loop spam from a bug or aggressive
        user override). Patched RTH=True so we go through the
        ``max(5, interval_s)`` clamp without the off-hours
        multiplier."""
        h = _Harness()
        with patch.object(_Harness, "_watchlist_poll_in_rth_now", return_value=True), \
             patch.object(_d, "get",
                          side_effect=lambda k: 1 if "interval" in k else 1.0):
            ms = h._watchlist_poll_effective_delay_ms()
        assert ms == 5_000


# ---------------------------------------------------------------------------
# 3. _start_watchlist_poll_loop
# ---------------------------------------------------------------------------


class TestStartPollLoop:
    def test_arms_initial_after(self):
        h = _Harness()
        with patch.object(_Harness, "_watchlist_poll_in_rth_now", return_value=True):
            h._start_watchlist_poll_loop()
        assert len(h.after_calls) == 1
        ms, fn = h.after_calls[0]
        assert ms == 60_000
        assert fn == h._watchlist_poll_tick
        assert h._watchlist_poll_job is not None

    def test_idempotent(self):
        h = _Harness()
        with patch.object(_Harness, "_watchlist_poll_in_rth_now", return_value=True):
            h._start_watchlist_poll_loop()
            h._start_watchlist_poll_loop()
            h._start_watchlist_poll_loop()
        # Only one after() arm.
        assert len(h.after_calls) == 1

    def test_disabled_no_arm(self):
        h = _Harness()
        with patch.object(_d, "get",
                          side_effect=lambda k: 0 if "interval" in k else 5.0):
            h._start_watchlist_poll_loop()
        assert h.after_calls == []
        assert h._watchlist_poll_job is None


# ---------------------------------------------------------------------------
# 4. _watchlist_poll_tick
# ---------------------------------------------------------------------------


class TestPollTick:
    def test_tick_fires_preloads_and_rearms(self):
        h = _Harness(stale=True)  # force re-fetch path
        with patch.object(_Harness, "_watchlist_poll_in_rth_now", return_value=True):
            h._watchlist_poll_tick()
        # Both AAPL and INTC submitted (intraday + daily = 4 tasks).
        assert len(h._executor.submitted) == 4
        # Re-armed.
        assert len(h.after_calls) == 1
        assert h.after_calls[0][1] == h._watchlist_poll_tick

    def test_sandbox_skips_preload_but_rearms(self):
        h = _Harness(sandbox=True, stale=True)
        with patch.object(_Harness, "_watchlist_poll_in_rth_now", return_value=True):
            h._watchlist_poll_tick()
        # No submissions while sandbox active.
        assert h._executor.submitted == []
        # But re-armed so it resumes on sandbox exit.
        assert len(h.after_calls) == 1

    def test_hidden_tab_skips_preload_but_rearms(self):
        # qw-watchlist-visguard: when the Watchlist outer tab is off
        # screen the preload body is skipped, but the tick still re-arms
        # so the data refreshes within one interval of returning.
        h = _Harness(stale=True)
        with patch.object(_Harness, "_watchlist_poll_in_rth_now", return_value=True), \
                patch.object(_Harness, "_watchlist_tab_visible", return_value=False):
            h._watchlist_poll_tick()
        assert h._executor.submitted == []
        assert len(h.after_calls) == 1
        assert h.after_calls[0][1] == h._watchlist_poll_tick

    def test_visible_tab_runs_preload(self):
        # Default visibility (no outer frame) resolves True, so the
        # preloads run as before — guards against the helper accidentally
        # starving a genuinely visible watchlist.
        h = _Harness(stale=True)
        assert h._watchlist_tab_visible() is True
        with patch.object(_Harness, "_watchlist_poll_in_rth_now", return_value=True):
            h._watchlist_poll_tick()
        assert len(h._executor.submitted) == 4
        assert len(h.after_calls) == 1

    def test_disabled_no_rearm(self):
        h = _Harness(stale=True)
        with patch.object(_d, "get",
                          side_effect=lambda k: 0 if "interval" in k else 5.0):
            h._watchlist_poll_tick()
        # Preloads still ran (the disable only short-circuits the
        # re-arm — but defaults.get inside preload reads other keys
        # and we patched all to 0; should not error).
        assert h.after_calls == []
        assert h._watchlist_poll_job is None


# ---------------------------------------------------------------------------
# 5. Orphan-snapshot recovery
# ---------------------------------------------------------------------------


class TestOrphanRecovery:
    def test_preload_watchlist_repairs_missing_last_from_fresh_cache(self):
        """Cache has fresh INTC bars but snapshot lacks ``last``;
        ``_preload_watchlist`` should set last from cached[-1].close
        without re-submitting a fetch."""
        h = _Harness(tickers=["INTC"], stale=False)
        h._full_cache[("yfinance", "INTC", "5m")] = [
            _candle(20.0), _candle(21.5),
        ]
        h._preload_watchlist()
        assert h._watchlist_snapshot["INTC"]["last"] == 21.5
        # No fetch submitted for INTC (cache was fresh).
        assert h._executor.submitted == []
        # Repaint was nudged.
        assert h.refresh_called == 1

    def test_preload_watchlist_does_not_overwrite_existing_last(self):
        h = _Harness(tickers=["INTC"], stale=False)
        h._full_cache[("yfinance", "INTC", "5m")] = [_candle(99.0)]
        h._watchlist_snapshot["INTC"] = {"last": 50.0}
        h._preload_watchlist()
        # Preserved.
        assert h._watchlist_snapshot["INTC"]["last"] == 50.0
        assert h.refresh_called == 0

    def test_preload_daily_repairs_missing_change_from_fresh_cache(self):
        h = _Harness(tickers=["INTC"], stale=False)
        h._full_cache[("yfinance", "INTC", "1d")] = [
            _candle(20.0), _candle(22.0),
        ]
        h._preload_watchlist_daily()
        snap = h._watchlist_snapshot["INTC"]
        assert snap["change_1d"] == pytest.approx(2.0)
        assert snap["pct_1d"] == pytest.approx(10.0)
        assert snap["chg"] == pytest.approx(2.0)
        assert snap["last"] == 22.0
        assert h._executor.submitted == []
        assert h.refresh_called == 1

    def test_preload_daily_skips_when_too_few_bars(self):
        h = _Harness(tickers=["INTC"], stale=False)
        h._full_cache[("yfinance", "INTC", "1d")] = [_candle(20.0)]
        h._preload_watchlist_daily()
        # No change_1d derivable from a single bar.
        assert "change_1d" not in h._watchlist_snapshot.get("INTC", {})
        assert h.refresh_called == 0

    def test_preload_handles_zero_prior_close_gracefully(self):
        """Division by zero must not blow up the loop."""
        h = _Harness(tickers=["INTC"], stale=False)
        h._full_cache[("yfinance", "INTC", "1d")] = [
            _candle(0.0), _candle(1.0),
        ]
        h._preload_watchlist_daily()
        snap = h._watchlist_snapshot["INTC"]
        assert snap["change_1d"] == 1.0
        assert snap["pct_1d"] == 0.0

    def test_preload_watchlist_overwrites_daily_fallback_last(self):
        h = _Harness(tickers=["INTC"], stale=False)
        h._full_cache[("yfinance", "INTC", "5m")] = [
            _dated_candle(_dt.date(2025, 5, 14), 23.5, hour=15, minute=55)
        ]
        h._watchlist_snapshot["INTC"] = {
            "last": 22.0,
            "_last_source": "daily",
        }
        h._preload_watchlist()
        assert h._watchlist_snapshot["INTC"]["last"] == pytest.approx(23.5)
        assert h._watchlist_snapshot["INTC"]["_last_source"] == "intraday"


# ---------------------------------------------------------------------------
# 6. Watchlist Change anchors
# ---------------------------------------------------------------------------


class TestWatchlistChangeAnchors:
    def _install_fetcher(
        self,
        monkeypatch,
        *,
        daily: list[Candle],
        intraday: list[Candle],
    ) -> None:
        def _fake_fetch(ticker: str, interval: str):
            if ticker != "INTC":
                return []
            if interval == "1d":
                return list(daily)
            return list(intraday)

        monkeypatch.setitem(_wl_tab.DATA_SOURCES, "yfinance", _fake_fetch)

    def test_live_change_uses_intraday_last_minus_prior_close(self, monkeypatch):
        day_2 = _dt.date(2025, 5, 12)
        day_1 = _dt.date(2025, 5, 13)
        today = _dt.date(2025, 5, 14)
        daily = [_dated_candle(day_2, 100.0), _dated_candle(day_1, 110.0)]
        intraday = [_dated_candle(today, 113.0, hour=15, minute=55)]
        self._install_fetcher(monkeypatch, daily=daily, intraday=intraday)
        h = _Harness(tickers=["INTC"])

        h._preload_one_last("INTC", "yfinance", "5m")
        h._preload_one_daily("INTC", "yfinance")

        snap = h._watchlist_snapshot["INTC"]
        assert snap["last"] == pytest.approx(113.0)
        assert snap["change_1d"] == pytest.approx(3.0)
        assert snap["pct_1d"] == pytest.approx(3.0 / 110.0 * 100.0)


# ---------------------------------------------------------------------------
# 7. Scheduler snapshot seam
# ---------------------------------------------------------------------------


class TestApplyWatchlistSnapshotFromBars:
    def _install_fetcher(
        self,
        monkeypatch,
        *,
        daily: list[Candle],
        intraday: list[Candle],
    ) -> None:
        def _fake_fetch(ticker: str, interval: str):
            if ticker != "INTC":
                return []
            if interval == "1d":
                return list(daily)
            return list(intraday)

        monkeypatch.setitem(_wl_tab.DATA_SOURCES, "yfinance", _fake_fetch)

    def test_intraday_sets_last_recomputes_change_and_queues_refresh(self):
        day_1 = _dt.date(2025, 5, 13)
        today = _dt.date(2025, 5, 14)
        h = _Harness(tickers=["INTC"])
        h._full_cache[("yfinance", "INTC", "1d")] = [
            _dated_candle(day_1, 110.0),
            _dated_candle(today, 111.0),
        ]

        changed = h._apply_watchlist_snapshot_from_bars(
            "intc", "yfinance", "5m",
            [_dated_candle(today, 113.0, hour=15, minute=55)],
        )

        assert changed is True
        snap = h._watchlist_snapshot["INTC"]
        assert snap["last"] == pytest.approx(113.0)
        assert snap["_last_source"] == "intraday"
        assert snap["_last_day"] == today
        assert snap["change_1d"] == pytest.approx(3.0)
        assert h._worker_inbox.items[-1] == ("refresh", None)

    def test_sandbox_intraday_slices_to_replay_clock(self):
        replay_day = _dt.date(2025, 5, 14)
        bars = [
            _dated_candle(replay_day, 112.0, hour=10, minute=0),
            _dated_candle(replay_day, 113.0, hour=10, minute=5),
            _dated_candle(replay_day, 130.0, hour=15, minute=55),
        ]
        replay_ts = int(bars[1].date.timestamp())
        h = _Harness(
            tickers=["INTC"],
            sandbox=True,
            sandbox_clock=(True, replay_ts, replay_day),
        )

        changed = h._apply_watchlist_snapshot_from_bars(
            "INTC", "yfinance", "5m", bars)

        assert changed is True
        assert h._watchlist_snapshot["INTC"]["last"] == pytest.approx(113.0)
        assert h._worker_inbox.items[-1] == ("refresh", None)

    def test_daily_updates_change_and_queues_refresh(self):
        day_2 = _dt.date(2025, 5, 12)
        day_1 = _dt.date(2025, 5, 13)
        h = _Harness(tickers=["INTC"])

        changed = h._apply_watchlist_snapshot_from_bars(
            "INTC", "yfinance", "1d",
            [_dated_candle(day_2, 100.0), _dated_candle(day_1, 110.0)],
        )

        assert changed is True
        snap = h._watchlist_snapshot["INTC"]
        assert snap["last"] == pytest.approx(110.0)
        assert snap["_last_source"] == "daily"
        assert snap["change_1d"] == pytest.approx(10.0)
        assert h._worker_inbox.items[-1] == ("refresh", None)

    def test_live_change_excludes_today_partial_daily_bar(self, monkeypatch):
        day_2 = _dt.date(2025, 5, 12)
        day_1 = _dt.date(2025, 5, 13)
        today = _dt.date(2025, 5, 14)
        daily = [
            _dated_candle(day_2, 100.0),
            _dated_candle(day_1, 110.0),
            _dated_candle(today, 111.0),
        ]
        intraday = [_dated_candle(today, 113.0, hour=15, minute=55)]
        self._install_fetcher(monkeypatch, daily=daily, intraday=intraday)
        h = _Harness(tickers=["INTC"])

        h._preload_one_last("INTC", "yfinance", "5m")
        h._preload_one_daily("INTC", "yfinance")

        snap = h._watchlist_snapshot["INTC"]
        assert snap["last"] == pytest.approx(113.0)
        assert snap["change_1d"] == pytest.approx(3.0)
        assert snap["pct_1d"] == pytest.approx(3.0 / 110.0 * 100.0)

    def test_daily_then_intraday_recomputes_change(self, monkeypatch):
        day_2 = _dt.date(2025, 5, 12)
        day_1 = _dt.date(2025, 5, 13)
        today = _dt.date(2025, 5, 14)
        daily = [_dated_candle(day_2, 100.0), _dated_candle(day_1, 110.0)]
        intraday = [_dated_candle(today, 113.0, hour=15, minute=55)]
        self._install_fetcher(monkeypatch, daily=daily, intraday=intraday)
        h = _Harness(tickers=["INTC"])

        h._preload_one_daily("INTC", "yfinance")
        assert h._watchlist_snapshot["INTC"]["change_1d"] == pytest.approx(10.0)
        h._preload_one_last("INTC", "yfinance", "5m")

        snap = h._watchlist_snapshot["INTC"]
        assert snap["last"] == pytest.approx(113.0)
        assert snap["change_1d"] == pytest.approx(3.0)
        assert snap["pct_1d"] == pytest.approx(3.0 / 110.0 * 100.0)

    def test_daily_change_falls_back_without_intraday(self, monkeypatch):
        day_2 = _dt.date(2025, 5, 12)
        day_1 = _dt.date(2025, 5, 13)
        daily = [_dated_candle(day_2, 100.0), _dated_candle(day_1, 110.0)]
        self._install_fetcher(monkeypatch, daily=daily, intraday=[])
        h = _Harness(tickers=["INTC"])

        h._preload_one_daily("INTC", "yfinance")

        snap = h._watchlist_snapshot["INTC"]
        assert snap["last"] == pytest.approx(110.0)
        assert snap["_last_source"] == "daily"
        assert snap["change_1d"] == pytest.approx(10.0)
        assert snap["pct_1d"] == pytest.approx(10.0)

    def test_sandbox_change_uses_replay_last_minus_prior(self, monkeypatch):
        day_2 = _dt.date(2025, 5, 12)
        day_1 = _dt.date(2025, 5, 13)
        replay_day = _dt.date(2025, 5, 14)
        daily = [
            _dated_candle(day_2, 100.0),
            _dated_candle(day_1, 110.0),
            _dated_candle(replay_day, 120.0),
        ]
        intraday = [
            _dated_candle(replay_day, 112.0, hour=10, minute=0),
            _dated_candle(replay_day, 113.0, hour=10, minute=5),
            _dated_candle(replay_day, 130.0, hour=15, minute=55),
        ]
        self._install_fetcher(monkeypatch, daily=daily, intraday=intraday)
        replay_ts = int(intraday[1].date.timestamp())
        h = _Harness(
            tickers=["INTC"],
            sandbox=True,
            sandbox_clock=(True, replay_ts, replay_day),
        )

        h._preload_one_last("INTC", "yfinance", "5m")
        h._preload_one_daily("INTC", "yfinance")

        snap = h._watchlist_snapshot["INTC"]
        assert snap["last"] == pytest.approx(113.0)
        assert snap["change_1d"] == pytest.approx(3.0)
        assert snap["pct_1d"] == pytest.approx(3.0 / 110.0 * 100.0)
