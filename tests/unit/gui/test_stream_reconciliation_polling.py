"""Real polling/readiness hooks with manually completed provider futures."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tradinglab.core.timezones import ET
from tradinglab.core.view_intent import ViewController
from tradinglab.data import DATA_SOURCES
from tradinglab.data.stream_controller import StreamController
from tradinglab.gui.polling import PollingMixin
from tradinglab.models import Candle
from tradinglab.streaming.base import StreamState, StreamStatus


def _bar(minute, *, volume=100, close=101):
    return Candle(datetime(2026, 9, 8, 9, 30, tzinfo=ET) + timedelta(minutes=minute),
                  100, max(102, close), 99, close, volume)


class _Source:
    def get_status(self, ticker=None):
        return StreamStatus(StreamState.LIVE, "Live", 0, generation=1)

    def subscribe(self, ticker, interval, callback):
        self.callback = callback
        return lambda: None


class _Executor:
    def __init__(self):
        self.works = []

    def submit(self, work):
        self.works.append(work)
        return Future()


class _Harness(PollingMixin):
    _MIN_POLL_BACKOFF_MS = 1000
    _POLL_RETRY_DELAY_MS = 1000
    _POLL_RETRY_MAX = 3

    def __init__(self):
        self.key = ("schwab", "AMD", "5m")
        self._full_cache = {self.key: [_bar(0, volume=900)]}
        self._primary = self._full_cache[self.key]
        self._view = ViewController()
        self._fetch_token = 0
        self._fetch_executor = _Executor()
        self._poll_job = None
        self._poll_retry_count = 0
        self._poll_retry_expected_min_ts = None
        self._after_jobs = set()
        self._last_stream_price = {}
        self.jobs = {}
        self.completed = []
        self.loads = []
        self._status = SimpleNamespace(info=lambda message: None)
        for name, value in (("source_var", "schwab"), ("ticker_var", "AMD"),
                            ("interval_var", "5m"), ("compare_var", False),
                            ("compare_ticker_var", ""), ("prepost_var", True)):
            setattr(self, name, SimpleNamespace(get=lambda value=value: value))
        self.source = _Source()
        self._stream_ctrl = StreamController()
        self._start_stream()
        self._sync_stream_aliases()

    def _start_stream(self):
        self._stream_ctrl.start(
            *self.key, compare_on=False, compare_ticker="", full_cache=self._full_cache,
            stream_sources={"schwab-stream": self.source}, is_intraday_fn=lambda interval: True)

    def _sync_stream_aliases(self):
        self._stream_active = self._stream_ctrl.active

    def _is_sandbox_active(self):
        return False

    def _user_has_panned_x(self):
        return False

    def _bump_fetch_token(self):
        self._fetch_token += 1
        return self._fetch_token

    def after(self, delay, callback):
        job = f"job-{len(self._after_jobs)}-{self._fetch_token}"
        self.jobs[job] = (delay, callback)
        return job

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def _await_future_on_tk(self, future, callback):
        self.completed.append((future, callback))

    def _load_data(self):
        bars = self._prefetched_raw["primary"] or self._full_cache[self.key]
        self.loads.append([replace(bar) for bar in bars])
        self._full_cache[self.key] = [replace(bar) for bar in bars]
        self._primary = self._full_cache[self.key]
        self._bump_fetch_token()
        self._start_stream()
        self._sync_stream_aliases()
        self._schedule_next_bar_fetch()

    def emit(self, kind, minute):
        self.source.callback(kind, _bar(minute))
        self._stream_ctrl.apply_tick(self._stream_ctrl.drain()[0], self._full_cache, None)
        self._update_stream_health()

    def complete(self, index, bars):
        future, callback = self.completed[index]
        future.set_result((bars, [], None, None))
        callback(future.result())


def test_new_bucket_cannot_cancel_prior_bucket_post_boundary_backfill(monkeypatch):
    monkeypatch.setitem(DATA_SOURCES, "schwab", lambda *_args: [])
    monkeypatch.setattr("tradinglab.gui.polling._compute_fetch_delay_ms", lambda **kwargs: 1000)
    app = _Harness()
    app._schedule_next_bar_fetch()
    app.emit("rollover", 3)
    app.emit("closed", 3)
    app.emit("closed", 4)
    app._next_bar_fetch_tick()  # Request begins before the 09:35 boundary.
    old_token = app._fetch_token
    app.emit("rollover", 5)
    assert app._stream_ctrl.needs_reconcile
    assert not app._stream_active
    assert app._fetch_token == old_token, "New-bucket readiness must not invalidate required REST"
    assert app._poll_job is not None
    assert app.jobs[app._poll_job][0] == 0

    # Even an old request completed after the boundary cannot acknowledge debt.
    app.complete(0, [_bar(0, volume=950)])
    assert app.loads[-1][0].volume == 950
    assert app._stream_ctrl.needs_reconcile
    assert not app._stream_active
    app._next_bar_fetch_tick()  # This request carries the post-boundary debt revision.
    app.complete(1, [_bar(0, volume=1400), _bar(5)])
    assert app._full_cache[app.key][0].volume == 1400
    assert not app._stream_ctrl.needs_reconcile
    assert not app._stream_active  # Newly fetched current REST bucket still needs full coverage.

    for minute in range(5, 10):
        app.emit("closed", minute)
    assert app._stream_active
    assert app._poll_job is None
    assert app._full_cache[app.key][0].volume == 1400


@pytest.mark.parametrize("raises", [False, True])
def test_failed_post_boundary_fetch_cannot_discharge_debt_via_cached_fallback(monkeypatch, raises):
    def fetch(*_args):
        if raises:
            raise OSError("Fake provider unavailable")
        return None

    monkeypatch.setitem(DATA_SOURCES, "schwab", fetch)
    monkeypatch.setattr("tradinglab.gui.polling._disk_cache.load", lambda *_args: None)
    monkeypatch.setattr("tradinglab.gui.polling._compute_fetch_delay_ms", lambda **kwargs: 1000)
    app = _Harness()
    for kind, minute in (("rollover", 3), ("closed", 3), ("closed", 4), ("rollover", 5)):
        app.emit(kind, minute)
    app._next_bar_fetch_tick()
    result = app._fetch_executor.works[0]()  # Exercise the real worker's None/error handling.
    future, callback = app.completed[0]
    future.set_result(result)
    callback(result)
    assert app.loads, "The load hook must actually have consumed its cached fallback"
    for minute in range(5, 10):
        app.emit("closed", minute)
    assert app._full_cache[app.key][0].volume == 900
    assert app._stream_ctrl.needs_reconcile
    assert not app._stream_active
    assert app._poll_job is not None
