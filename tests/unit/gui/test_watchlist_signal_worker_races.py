"""Deterministic value ownership and stale-result tests using the real evaluator.

Submission is held at an executor boundary, not by sleeps. Cached Candle
values must already be owned before a worker can start; real stream ticks
must not alter them, including during Bars.from_candles conversion.
"""
from __future__ import annotations

import queue
import sys
import threading
from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tradinglab.core.bars import Bars
from tradinglab.data import DATA_SOURCES
from tradinglab.data.stream_controller import StreamController
from tradinglab.gui.polling import PollingMixin
from tradinglab.gui.watchlist_tab import WatchlistTabMixin
from tradinglab.models import Candle
from tradinglab.scanner.model import FieldRef
from tradinglab.watchlists.columns import KIND_SIGNAL, WatchlistColumn
from tradinglab.watchlists.signals import ColumnValue, WatchlistSignalEvaluator


def _bars(n=3):
    base = datetime(2026, 6, 10, 14, tzinfo=timezone.utc)
    return [
        Candle(base + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i, 1000)
        for i in range(n)
    ]


def _column(field="close", interval="5m"):
    return WatchlistColumn(KIND_SIGNAL, field, FieldRef.builtin(field, interval=interval))


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        assert threading.current_thread() is threading.main_thread()
        return self.value


class _Executor:
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        assert threading.current_thread() is threading.main_thread()
        future = Future()
        self.jobs.append((future, fn, args))
        return future

    def run(self):
        future, fn, args = self.jobs.pop(0)
        errors = []

        def worker():
            if not future.set_running_or_notify_cancel():
                return
            try:
                fn(*args)
                future.set_result(None)
            except BaseException as exc:
                errors.append(exc)
                future.set_exception(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(10)
        assert not thread.is_alive()
        assert not errors


class _App(WatchlistTabMixin, PollingMixin):
    def __init__(self):
        self._full_cache = {("yfinance", "AMD", "5m"): _bars()}
        self._watchlist_snapshot = {}
        self._worker_inbox = queue.Queue()
        self._executor = _Executor()
        self.source_var = _Var("yfinance")
        self.interval_var = _Var("5m")
        self._sandbox = None
        self.tickers = ["AMD"]
        self.columns = [_column()]
        self._after_jobs = set()
        self.refresh_calls = 0

    def _pinned_ticker_union(self):
        assert threading.current_thread() is threading.main_thread()
        return self.tickers

    def _pinned_signal_columns(self):
        assert threading.current_thread() is threading.main_thread()
        return self.columns

    def _is_sandbox_active(self):
        assert threading.current_thread() is threading.main_thread()
        return self._sandbox is not None

    def _schedule_watchlist_tab_refresh(self):
        self.refresh_calls += 1

    def after(self, *args):
        return "drain"

    def complete(self):
        self._executor.run()
        self._drain_worker_inbox()
        self._drain_worker_inbox()

    def cell(self, field="close"):
        return self._watchlist_snapshot["AMD"]["_sig"][field]


def _replay(app, index=1):
    bars = app._full_cache[("yfinance", "AMD", "5m")]
    session = SimpleNamespace(
        data_source="yfinance", interval="5m",
        visible_candles_by_symbol={"AMD": bars[:index + 1]},
        daily_full_by_symbol={},
        timestamp=int(bars[index].date.timestamp()),
    )
    session.clock_ts = lambda: session.timestamp
    session.current_session_date = lambda: bars[index].date.date()
    app._sandbox = session
    return session


def _tick(app, value=200):
    controller = StreamController()
    live = app._full_cache[("yfinance", "AMD", "5m")]
    assert controller.apply_tick(
        (controller.token, "primary", "yfinance", "AMD", "5m", "tick",
         replace(live[-1], high=value, close=value, volume=2000)),
        app._full_cache, None,
    )
    assert live[-1].close == value


def test_signal_bars_owns_values_after_real_stream_tick():
    app = _App()
    snapshot = app._signal_bars("yfinance", "AMD", "5m")
    _tick(app)
    cells = WatchlistSignalEvaluator(
        bars_provider=lambda *_: snapshot,
    ).evaluate(["AMD"], [_column(field) for field in ("high", "close", "volume")])["AMD"]
    assert [cells[field].raw for field in ("high", "close", "volume")] == [104, 103, 1000]


@pytest.mark.parametrize("replay", [False, True])
def test_submission_owns_all_values_before_worker_starts(replay):
    app = _App()
    app.columns = [_column(field) for field in ("high", "close", "volume")]
    if replay:
        _replay(app, index=2)
    live = app._full_cache[("yfinance", "AMD", "5m")]
    app._preload_watchlist_signals()
    _tick(app)
    live.append(replace(live[-1], date=live[-1].date + timedelta(minutes=5), close=300))
    app.complete()
    assert [app.cell(field).raw for field in ("high", "close", "volume")] == [104, 103, 1000]
    assert not app._watchlist_signals_inflight
    assert app.refresh_calls == 1


def test_real_tick_between_high_and_close_conversion_cannot_tear_values(monkeypatch):
    app = _App()
    app.columns = [_column("high"), _column("close")]
    app._preload_watchlist_signals()
    high_read = threading.Event()
    proceed = threading.Event()
    original = Bars.from_candles.__func__

    def convert(cls, candles):
        # Pause the REAL converter immediately before its close assignment.
        # This reproduces the reported high(old)/close(new) torn candle.
        paused = False

        def trace(frame, event, arg):
            nonlocal paused
            if (not paused and event == "line"
                    and frame.f_code is original.__code__
                    and frame.f_locals.get("i") == len(candles) - 1):
                import linecache
                line = linecache.getline(frame.f_code.co_filename, frame.f_lineno).strip()
                if line == "close[i] = c.close":
                    paused = True
                    high_read.set()
                    assert proceed.wait(10)
            return trace

        previous_trace = sys.gettrace()
        sys.settrace(trace)
        try:
            return original(cls, candles)
        finally:
            sys.settrace(previous_trace)

    monkeypatch.setattr(Bars, "from_candles", classmethod(convert))
    future, fn, args = app._executor.jobs.pop(0)
    thread = threading.Thread(target=fn, args=args)
    thread.start()
    try:
        assert high_read.wait(10)
        _tick(app)
    finally:
        proceed.set()
        thread.join(10)
    assert not thread.is_alive()
    app._drain_worker_inbox()
    assert (app.cell("high").raw, app.cell().raw) == (104, 103)


def test_worker_never_mutates_snapshot_or_releases_inflight_before_drain():
    app = _App()
    app._preload_watchlist_signals()
    old_sig = {"other": ColumnValue(42, "42")}
    app._watchlist_snapshot["AMD"] = {"last": 7, "_sig": old_sig}
    app._executor.run()
    assert app._watchlist_snapshot["AMD"]["_sig"] is old_sig
    assert set(old_sig) == {"other"}
    assert app._watchlist_signals_inflight
    app._preload_watchlist_signals()
    assert not app._executor.jobs
    app._drain_worker_inbox()
    assert app._watchlist_snapshot["AMD"]["last"] == 7
    assert app.cell("other").raw == 42
    assert app.cell().raw == 103


@pytest.mark.parametrize("change", ["source", "columns", "tickers", "enter", "exit", "clock", "session"])
def test_stale_context_is_rejected_and_latest_context_resubmitted(change):
    app = _App()
    if change in ("exit", "clock", "session"):
        _replay(app)
    app._preload_watchlist_signals()
    app._executor.run()
    if change == "source":
        app.source_var.value = "other"
        app._full_cache[("other", "AMD", "5m")] = _bars()
    elif change == "columns":
        app.columns = [_column("high")]
    elif change == "tickers":
        app.tickers = ["MSFT"]
        app._full_cache[("yfinance", "MSFT", "5m")] = _bars()
    elif change == "enter":
        _replay(app)
    elif change == "exit":
        app._sandbox = None
    elif change == "clock":
        app._sandbox.timestamp -= 300
    else:
        _replay(app)
    app._drain_worker_inbox()
    assert not any(snap.get("_sig") for snap in app._watchlist_snapshot.values())
    assert app._watchlist_signals_inflight
    assert len(app._executor.jobs) == 1
    app.complete()
    assert any(snap.get("_sig") for snap in app._watchlist_snapshot.values())
    assert not app._watchlist_signals_inflight


def test_old_generation_cannot_retire_a_new_job_even_after_context_returns():
    app = _App()
    app._preload_watchlist_signals()
    app._executor.run()
    item = app._worker_inbox.get_nowait()
    app.source_var.value = "other"
    app._preload_watchlist_signals()
    app.source_var.value = "yfinance"
    app._preload_watchlist_signals()
    app._worker_inbox.put_nowait(item)
    app._drain_worker_inbox()
    assert len(app._executor.jobs) == 1
    app._worker_inbox.put_nowait(item)
    app._drain_worker_inbox()
    assert app._watchlist_signals_inflight
    assert len(app._executor.jobs) == 1
    assert not app._watchlist_snapshot
    app.complete()
    assert app.cell().raw == 103


def test_column_parameters_are_owned_and_changes_invalidate_results():
    app = _App()
    params = {"nested": {"value": 1}}
    app.columns = [replace(app.columns[0], ref=FieldRef("builtin", "close", params=params, interval="5m"))]
    app._preload_watchlist_signals()
    context = app._executor.jobs[0][2][1]
    params["nested"]["value"] = 2
    assert context.columns[0].ref.params["nested"]["value"] == 1
    app.complete()
    assert not app._watchlist_snapshot
    assert app._executor.jobs
    app.complete()
    assert app.cell().raw == 103


def test_same_timestamp_correction_recomputes_on_next_job():
    app = _App()
    app._preload_watchlist_signals()
    app.complete()
    assert app.cell().raw == 103
    _tick(app)
    app._preload_watchlist_signals()
    app.complete()
    assert app.cell().raw == 200


def test_duplicate_completion_cannot_overwrite_or_retire_later_same_context_job():
    app = _App()
    app._preload_watchlist_signals()
    app._executor.run()
    item = app._worker_inbox.get_nowait()
    app._worker_inbox.put_nowait(item)
    app._drain_worker_inbox()
    _tick(app)
    app._preload_watchlist_signals()
    app._worker_inbox.put_nowait(item)
    app._drain_worker_inbox()
    assert app._watchlist_signals_inflight
    app.complete()
    assert app.cell().raw == 200
    app._worker_inbox.put_nowait(item)
    app._drain_worker_inbox()
    assert app.cell().raw == 200


@pytest.mark.parametrize("cached", [True, False])
def test_replay_uses_pinned_source_clock_and_correct_interval(monkeypatch, cached):
    app = _App()
    session = _replay(app)
    app.source_var.value = "Auto"
    app.columns = [_column(), _column("high", interval=None)]
    today = _bars()[0]
    daily = [replace(today, date=today.date - timedelta(days=1), high=77),
             replace(today, high=999)]
    calls = []

    def fetch(symbol, interval):
        assert threading.current_thread() is not threading.main_thread()
        calls.append((symbol, interval))
        return _bars() if interval == "5m" else daily

    monkeypatch.setitem(DATA_SOURCES, "yfinance", fetch)
    if cached:
        session.daily_full_by_symbol["AMD"] = daily
    else:
        session.visible_candles_by_symbol.clear()
        app._full_cache.clear()
    app._preload_watchlist_signals()
    assert not calls
    app.complete()
    assert app.cell().raw == 102
    assert app.cell("high").raw == 77
    assert set(calls) == (set() if cached else {("AMD", "5m"), ("AMD", "1d")})


def test_replay_without_clock_never_uses_or_fetches_live_data(monkeypatch):
    app = _App()
    session = _replay(app)
    session.clock_ts = lambda: None
    calls = []
    monkeypatch.setitem(DATA_SOURCES, "yfinance", lambda *args: calls.append(args) or _bars())
    app._preload_watchlist_signals()
    app.complete()
    assert app.cell().state == "insufficient"
    assert not calls


def test_worker_uses_frozen_fetcher_and_clock_after_replay_advances(monkeypatch):
    app = _App()
    session = _replay(app)
    session.visible_candles_by_symbol.clear()
    app._full_cache.clear()
    calls = []
    monkeypatch.setitem(DATA_SOURCES, "yfinance", lambda *args: calls.append(args) or _bars())
    app._preload_watchlist_signals()
    session.timestamp += 300
    monkeypatch.setitem(DATA_SOURCES, "yfinance", lambda *_: pytest.fail("new fetcher used by old job"))
    app._executor.run()
    _, payload = app._worker_inbox.get_nowait()
    assert payload[2]["AMD"]["close"].raw == 102
    assert calls == [("AMD", "5m")]


@pytest.mark.parametrize("failure", ["submit", "cancel", "evaluate", "fetch"])
def test_failure_retires_job_and_allows_retry(monkeypatch, caplog, failure):
    app = _App()
    if failure == "submit":
        monkeypatch.setattr(app._executor, "submit", lambda *_: (_ for _ in ()).throw(RuntimeError("closed")))
    elif failure == "evaluate":
        monkeypatch.setattr(WatchlistSignalEvaluator, "evaluate",
                            lambda *_: (_ for _ in ()).throw(RuntimeError("bad evaluator")))
    elif failure == "fetch":
        app._full_cache.clear()
        monkeypatch.setitem(DATA_SOURCES, "yfinance",
                            lambda *_: (_ for _ in ()).throw(RuntimeError("offline")))
    app._preload_watchlist_signals()
    if failure == "cancel":
        assert app._executor.jobs[0][0].cancel()
    if failure != "submit":
        app.complete()
    assert not app._watchlist_signals_inflight
    assert app._watchlist_signal_job is None
    if failure != "cancel":
        assert "Could not" in caplog.text
    monkeypatch.undo()
    app._full_cache[("yfinance", "AMD", "5m")] = _bars()
    app._preload_watchlist_signals()
    app.complete()
    assert app.cell().raw == 103


def test_removed_columns_discard_pending_results_without_resubmission():
    app = _App()
    app._preload_watchlist_signals()
    app.columns = []
    app.complete()
    assert not app._watchlist_snapshot
    assert not app._executor.jobs
    assert not app._watchlist_signals_inflight
