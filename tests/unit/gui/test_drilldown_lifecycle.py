"""Drilldown request decisions without Tk, worker threads, or network access.

Timers and future delivery are driven explicitly, so stale callbacks and
late completions are reproducible rather than dependent on sleeps.
"""
from __future__ import annotations

from concurrent.futures import Future
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from tradinglab.gui import drilldown
from tradinglab.gui.drilldown import DrilldownMixin, _DrilldownRequest
from tradinglab.models import Candle

DAY = date(2026, 6, 10)
KEY = ("fixture", "AMD", "5m")


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def _bar(day=DAY, *, minute=0, close=10.5):
    return Candle(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
                  + timedelta(hours=14, minutes=30 + minute),
                  10, 11, 9, close, 100)


class _App(DrilldownMixin):
    _DRILLDOWN_PREFETCH_GRACE_MS = 1500
    _DRILLDOWN_SYNC_UI_TIMEOUT_MS = 5000

    def __init__(self):
        self.source_var = _Var("fixture")
        self.ticker_var = _Var(" amd ")
        self.interval_var = _Var("1d")
        self._fetch_token = 1
        self._drilldown_request_seq = 0
        self._drilldown_request = None
        self._drilldown_day = None
        self._full_cache = {}
        self._prefetch_futures = {}
        self._after_jobs = set()
        self.timers = {}
        self._next_timer = 0
        self.after_cancel = Mock(side_effect=self.timers.pop)
        self.config = Mock()
        self._status = Mock(spec=["info", "warn", "error"])
        self._executor = Mock(spec=["submit"])
        self._executor.submit.return_value = Future()
        self._await_future_on_tk = Mock()
        self._disk_load = Mock(return_value=[])
        self._trim_full_cache = Mock()
        self._do_drilldown = Mock(return_value=True)
        self._day_within_intraday_fetch_window = Mock(return_value=True)
        self._panel_state = {}
        self._primary = []
        self._ensure_rendered_for_view = Mock()
        self._autoscale_y_to_visible = Mock()
        self._canvas = Mock(spec=["draw_idle"])
        self._render = Mock()

    def _track_after(self, delay, fn, *args):
        self._next_timer += 1
        job = f"job-{self._next_timer}"
        self.timers[job] = (delay, fn, args)
        self._after_jobs.add(job)
        return job

    def fire(self, job):
        _delay, fn, args = self.timers.pop(job)
        self._after_jobs.discard(job)
        fn(*args)

    def deliver(self, bars):
        future, callback = self._await_future_on_tk.call_args.args
        future.set_result(bars)
        callback(bars)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(drilldown, "DATA_SOURCES", {"fixture": Mock(return_value=[])})
    monkeypatch.setattr(drilldown, "source_supports_range", lambda _src: False)
    monkeypatch.setattr(drilldown.disk_cache, "save", Mock())
    return _App()


@pytest.mark.parametrize("day,source,ticker", [
    (None, "fixture", "AMD"), ("2026-06-10", "fixture", "AMD"),
    (DAY, "", "AMD"), (DAY, "fixture", "  "),
])
def test_invalid_selection_does_not_schedule_or_fetch(app, day, source, ticker):
    app.source_var.set(source)
    app.ticker_var.set(ticker)
    assert app._zoom_5m_for_date(day) is False
    assert app._drilldown_request is None
    assert not app.timers
    app._executor.submit.assert_not_called()


def test_request_validity_is_identity_and_context_not_fetch_token(app):
    req = _DrilldownRequest(1, 1, "fixture", "AMD", DAY)
    app._drilldown_request = req
    app._fetch_token = 99
    assert app._drilldown_request_is_valid(req)
    assert not app._drilldown_request_is_valid(None)
    assert not app._drilldown_request_is_valid(
        _DrilldownRequest(1, 1, "fixture", "AMD", DAY))
    app.source_var.set("new-source")
    assert not app._drilldown_request_is_valid(req)
    app.source_var.set("fixture")
    app.ticker_var.set("NVDA")
    assert not app._drilldown_request_is_valid(req)


def test_latest_click_resets_grace_and_obsolete_timer_cannot_fetch(app):
    assert app._zoom_5m_for_date(DAY) is False
    req = app._drilldown_request
    old_job = req.timer_job
    old_id = req.request_id
    later = DAY + timedelta(days=1)
    app._zoom_5m_for_date(later)
    assert app._drilldown_request is req
    assert req.day == later
    assert req.request_id != old_id
    app.after_cancel.assert_called_once_with(old_job)
    assert old_job not in app._after_jobs
    app._retry_drilldown_after_prefetch(old_id)
    assert req.timer_job in app.timers
    app._executor.submit.assert_not_called()
    app._full_cache[KEY] = [_bar(later)]
    app.fire(req.timer_job)
    app._do_drilldown.assert_called_once_with(later)
    assert app._drilldown_request is None
    assert not app._after_jobs


def test_cache_hit_finishes_pending_request_before_drilling(app):
    app._zoom_5m_for_date(DAY)
    req = app._drilldown_request
    job = req.timer_job
    req.cursor_set = True
    app._full_cache[KEY] = [_bar()]
    assert app._zoom_5m_for_date(DAY) is True
    app.after_cancel.assert_called_once_with(job)
    app.config.assert_called_once_with(cursor="")
    assert app._drilldown_request is None
    assert not app._after_jobs
    app._do_drilldown.assert_called_once_with(DAY)


@pytest.mark.parametrize("reachable", [True, False])
def test_gap_on_requested_day_is_not_coverage(app, reachable):
    app._zoom_5m_for_date(DAY)
    req = app._drilldown_request
    app._full_cache[KEY] = [_bar(DAY - timedelta(days=1)), Candle.gap(_bar().date)]
    app._day_within_intraday_fetch_window.return_value = reachable
    app.fire(req.timer_job)
    app._do_drilldown.assert_not_called()
    if reachable:
        app._executor.submit.assert_called_once()
        assert app._drilldown_request is req
    else:
        app._executor.submit.assert_not_called()
        assert app._drilldown_request is None
        assert str(DAY) in app._status.warn.call_args.args[0]


def test_ticker_change_discards_grace_request_without_fetching(app):
    app._zoom_5m_for_date(DAY)
    job = app._drilldown_request.timer_job
    app.ticker_var.set("NVDA")
    app.fire(job)
    assert app._drilldown_request is None
    app._executor.submit.assert_not_called()
    app._do_drilldown.assert_not_called()


def test_existing_prefetch_is_reused_and_late_result_lands_after_ui_timeout(app):
    future = Future()
    app._prefetch_futures[KEY] = future
    app._zoom_5m_for_date(DAY)
    req = app._drilldown_request
    app.fire(req.timer_job)
    assert req.future is future
    app._executor.submit.assert_not_called()
    assert app.timers[req.ui_timeout_job][0] == 5000
    app.fire(req.ui_timeout_job)
    assert app._drilldown_request is req
    assert not future.cancelled() and not future.done()
    assert not req.cursor_set
    app.config.assert_has_calls([call(cursor="watch"), call(cursor="")])
    assert "taking >5s" in app._status.error.call_args.args[0]
    app.deliver([_bar()])
    assert app._drilldown_request is None
    app._do_drilldown.assert_called_once_with(DAY)
    assert app._full_cache[KEY] == [_bar()]
    assert not app._after_jobs


@pytest.mark.parametrize("timeout_first", [False, True])
def test_retarget_during_fetch_keeps_completion_and_timeout_attached(app, timeout_first):
    app._zoom_5m_for_date(DAY)
    req = app._drilldown_request
    app.fire(req.timer_job)
    request_id = req.request_id
    for offset in (1, 2, 3):
        latest = DAY + timedelta(days=offset)
        app._zoom_5m_for_date(latest)
        assert req.request_id == request_id
    assert app._drilldown_request is req
    if timeout_first:
        app.fire(req.ui_timeout_job)
        assert not req.cursor_set
    app.deliver([_bar(DAY), _bar(latest)])
    app._do_drilldown.assert_called_once_with(latest)
    assert app._drilldown_request is None
    assert not req.cursor_set
    assert not app._after_jobs
    app._executor.submit.assert_called_once()


@pytest.mark.parametrize("failure,fragment", [
    ("executor", "executor unavailable"),
    ("source", "data source is unavailable"),
    ("submit", "executor rejected"),
])
def test_fetch_setup_failure_clears_request_and_reports_error(app, monkeypatch, failure, fragment):
    if failure == "executor":
        app._executor = None
    elif failure == "source":
        monkeypatch.setattr(drilldown, "DATA_SOURCES", {})
    else:
        app._executor.submit.side_effect = RuntimeError("pool shut down")
    app._zoom_5m_for_date(DAY)
    app.fire(app._drilldown_request.timer_job)
    assert app._drilldown_request is None
    assert not app._after_jobs
    assert fragment in app._status.error.call_args.args[0]
    app._await_future_on_tk.assert_not_called()


def test_context_changed_completion_cannot_pollute_cache_or_zoom(app):
    app._zoom_5m_for_date(DAY)
    req = app._drilldown_request
    app.fire(req.timer_job)
    app.source_var.set("other")
    app.deliver([_bar()])
    assert app._drilldown_request is None
    assert not app._full_cache
    assert not app._after_jobs
    app._do_drilldown.assert_not_called()
    drilldown.disk_cache.save.assert_not_called()


def test_superseded_completion_cannot_finish_the_new_request(app):
    app._zoom_5m_for_date(DAY)
    old = app._drilldown_request
    app.fire(old.timer_job)
    app.ticker_var.set("NVDA")
    app._zoom_5m_for_date(DAY)
    new = app._drilldown_request
    app.deliver([_bar()])
    assert new is app._drilldown_request and new is not old
    assert new.timer_job in app._after_jobs
    assert not app._full_cache
    app._do_drilldown.assert_not_called()


@pytest.mark.parametrize("bars,level,fragment", [
    (None, "error", "returned no bars"),
    ([_bar(DAY - timedelta(days=1)), Candle.gap(_bar().date)], "warn", "does not cover"),
])
def test_completion_without_real_target_bars_finishes_without_zoom(app, bars, level, fragment):
    app._zoom_5m_for_date(DAY)
    app.fire(app._drilldown_request.timer_job)
    app.deliver(bars)
    assert app._drilldown_request is None
    assert not app._after_jobs
    assert fragment in getattr(app._status, level).call_args.args[0]
    app._do_drilldown.assert_not_called()


def test_completion_merges_disk_memory_and_refreshed_compare_before_zoom(app):
    old, current, incoming = _bar(DAY - timedelta(days=1)), _bar(), _bar(close=10.8)
    compare = _bar(close=20)
    app._full_cache[KEY] = [current]
    app._disk_load.side_effect = lambda key: [compare] if key[1] == "SPY" else [old, current]
    req = _DrilldownRequest(1, 1, "fixture", "AMD", DAY, compare_ticker="SPY")
    app._drilldown_request = req

    def assert_ready(day):
        assert day == DAY
        assert app._drilldown_request is None
        assert app._full_cache[KEY] == [old, incoming]
        assert app._full_cache[("fixture", "SPY", "5m")] == [compare]

    app._do_drilldown.side_effect = assert_ready
    app._on_drilldown_fetch_done(1, [incoming])
    app._do_drilldown.assert_called_once()
    drilldown.disk_cache.save.assert_called_once_with(*KEY, [old, incoming])


def test_zoom_uses_primary_series_not_stale_render_slice(app):
    ax, vol = Mock(spec=["set_xlim"]), Mock(spec=["set_xlim"])
    app._primary = [_bar(DAY - timedelta(days=1)), Candle.gap(_bar().date),
                    _bar(), _bar(minute=5), _bar(DAY + timedelta(days=1))]
    app._panel_state = {
        "primary": {"candles": [_bar()], "price_ax": ax, "vol_ax": vol},
        "compare": {},
    }
    assert app._zoom_primary_to_date(DAY) is True
    ax.set_xlim.assert_called_once_with(1.5, 3.5)
    vol.set_xlim.assert_called_once_with(1.5, 3.5)
    assert app._drilldown_day == DAY
    app._ensure_rendered_for_view.assert_has_calls([call("primary"), call("compare")])
    app._autoscale_y_to_visible.assert_called_once()
    app._canvas.draw_idle.assert_called_once()


@pytest.mark.parametrize("has_real_bars", [True, False])
def test_reload_falls_back_to_latest_real_day_or_releases_pin(app, has_real_bars):
    latest = DAY + timedelta(days=1)
    app._drilldown_day = DAY
    bars = [_bar(latest)] if has_real_bars else [Candle.gap(_bar(latest).date)]

    def load():
        app._primary = bars
        app._panel_state = {"primary": {"candles": bars}}

    app._reload_preserving_drilldown(load)
    assert app._drilldown_day == (latest if has_real_bars else None)
    assert app._preserve_xlim_on_render is has_real_bars
    assert app._poll_retry_count == 0
    assert app._poll_retry_expected_min_ts is None
    assert app._render.call_count == (0 if has_real_bars else 1)
