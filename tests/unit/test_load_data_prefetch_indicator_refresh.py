from __future__ import annotations

import datetime as dt
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

import tradinglab.app as app_mod
from tradinglab.app import ChartApp
from tradinglab.core.bars import Bars
from tradinglab.data.chart_load import ChartLoadCoordinator, ChartLoadResult, FetchedSide
from tradinglab.data.controller import DataController
from tradinglab.data.stream_controller import StreamController
from tradinglab.indicators.cache import IndicatorCache, config_hash
from tradinglab.indicators.moving_averages import SMA
from tradinglab.models import Candle


class _Var:
    def __init__(self, value: Any) -> None:
        self.value = value

    def get(self) -> Any:
        return self.value

    def set(self, value: Any) -> None:
        self.value = value


class _Status:
    def info(self, _msg: str) -> None:
        pass

    def error(self, _msg: str) -> None:
        pass


class _UnexpectedFetch(Exception):
    pass


def _bar(index: int, close: float) -> Candle:
    ts = dt.datetime(2024, 1, 2, 9, 30) + dt.timedelta(minutes=5 * index)
    return Candle(
        date=ts,
        open=close - 0.25,
        high=close + 0.5,
        low=close - 0.5,
        close=close,
        volume=1000 + index,
        session="regular",
    )


def _candles(base: float) -> list[Candle]:
    return [_bar(i, base + i) for i in range(5)]


def _changed_inside_same_fingerprint(old: list[Candle], close: float) -> list[Candle]:
    fresh = list(old)
    fresh[2] = _bar(2, close)
    return fresh


def _install_load_data_harness(app, *, primary, compare) -> None:
    from tradinglab.core.view_intent import ViewController
    app._view = ViewController()
    app._stream_ctrl = StreamController()
    app._primary = primary
    app._compare = compare
    app._primary_raw = primary
    app._compare_raw = compare
    app._indicator_cache = IndicatorCache(capacity=8)
    app._fetch_token = 0
    app._confirmed_primary_ticker = "AMD"
    app._confirmed_compare_ticker = "SPY"
    app._status = _Status()
    app.source_var = _Var("unit-source")
    app.interval_var = _Var("5m")
    app.ticker_var = _Var("AMD")
    app.compare_ticker_var = _Var("SPY")
    app.compare_var = _Var(True)
    app.prepost_var = _Var(False)

    app._is_sandbox_active = lambda: False
    app._stop_stream = lambda: None
    app._cache_is_stale = lambda _candles, _interval: True
    app._data_ctrl = DataController()
    app._data_ctrl.set_primary(primary, primary, compare_raw=compare, compare_filtered=compare)
    app._chart_loader = ChartLoadCoordinator(
        app._data_ctrl, app._stream_ctrl, app._view, is_stale=app._cache_is_stale,
    )
    app._sync_data_aliases()
    app._pinned_ticker_union = lambda: []
    app.after_idle = lambda _callback: None
    app._invalidate_focused_panels = ChartApp._invalidate_focused_panels.__get__(
        app,
        ChartApp,
    )
    app._request_deferred_render = lambda: None
    app._render = lambda: None
    app._load_events_async = lambda _symbol: None
    app._schedule_next_bar_fetch = lambda: None
    app._start_stream_if_applicable = lambda: None
    app._preload_watchlist_events = lambda: None
    app._preload_watchlist_signals = lambda: None


def test_prefetched_load_invalidates_prior_visible_indicator_entries(monkeypatch) -> None:
    old_primary = _candles(100.0)
    old_compare = _candles(200.0)
    new_primary = _changed_inside_same_fingerprint(old_primary, 150.0)
    new_compare = _changed_inside_same_fingerprint(old_compare, 250.0)

    app = ChartApp.__new__(ChartApp)
    _install_load_data_harness(app, primary=old_primary, compare=old_compare)

    h = config_hash("sma", {"length": 2})
    sma = SMA(length=2)
    old_primary_result = app._indicator_cache.get_or_compute_incremental(
        old_primary, h, sma, Bars.from_candles(old_primary)
    )
    old_compare_result = app._indicator_cache.get_or_compute_incremental(
        old_compare, h, sma, Bars.from_candles(old_compare)
    )

    def _fetcher(_ticker: str, _interval: str):
        raise _UnexpectedFetch("completion should consume its typed result")

    monkeypatch.setitem(app_mod.DATA_SOURCES, "unit-source", _fetcher)
    monkeypatch.setattr(
        app_mod.disk_cache,
        "merge_candles",
        lambda _cached, bars: list(bars or []),
    )
    monkeypatch.setattr(app_mod.disk_cache, "save", lambda *_args, **_kwargs: None)

    request = app._chart_loader.begin(app._chart_selection())
    result = ChartLoadResult(request, FetchedSide(new_primary), FetchedSide(new_compare))
    ChartApp._accept_chart_load(app, request, result)

    assert app._indicator_cache.get(old_primary, h) is None
    assert app._indicator_cache.get(old_compare, h) is None
    assert app._indicator_cache.get(app._primary, h) is None
    assert app._indicator_cache.get(app._compare, h) is None

    fresh_primary_result = app._indicator_cache.get_or_compute_incremental(
        app._primary, h, sma, Bars.from_candles(app._primary)
    )
    fresh_compare_result = app._indicator_cache.get_or_compute_incremental(
        app._compare, h, sma, Bars.from_candles(app._compare)
    )

    assert fresh_primary_result is not old_primary_result
    assert fresh_compare_result is not old_compare_result
    assert not np.array_equal(
        fresh_primary_result["sma"],
        old_primary_result["sma"],
        equal_nan=True,
    )
    assert not np.array_equal(
        fresh_compare_result["sma"],
        old_compare_result["sma"],
        equal_nan=True,
    )
    assert np.array_equal(
        fresh_primary_result["sma"],
        SMA(length=2).compute(app._primary)["sma"],
        equal_nan=True,
    )
    assert np.array_equal(
        fresh_compare_result["sma"],
        SMA(length=2).compute(app._compare)["sma"],
        equal_nan=True,
    )


class _ManualExecutor:
    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args):
        future = Future()
        self.jobs.append((future, fn, args))
        return future

    def finish(self, index):
        future, fn, args = self.jobs[index]
        future.set_result(fn(*args))
        return future


@pytest.fixture
def load_app(monkeypatch):
    app = ChartApp.__new__(ChartApp)
    _install_load_data_harness(app, primary=_candles(50), compare=[])
    app.compare_var.set(False)
    app._prefetch_observe_soon = lambda: None
    app._prefetch_observe_compare = lambda: None
    app._fetch_executor = _ManualExecutor()
    app.deliveries = {}
    app.renders = []
    app._render = lambda: app.renders.append("sync")
    app._request_deferred_render = lambda: app.renders.append("deferred")
    app._await_future_on_tk = lambda future, callback: app.deliveries.setdefault(future, callback)
    monkeypatch.setattr(app_mod.disk_cache, "load", lambda *_: None)
    monkeypatch.setattr(app_mod.disk_cache, "save", lambda *_: None)
    monkeypatch.setitem(
        app_mod.DATA_SOURCES, "unit-source",
        lambda ticker, _interval: _candles({"AMD": 100, "MSFT": 200, "SPY": 300}[ticker]),
    )
    return app


def _deliver(app, index):
    future = app._fetch_executor.finish(index)
    app.deliveries[future](future.result())


def test_real_async_adapter_discards_out_of_order_completion(load_app):
    app = load_app
    app._load_data_async()
    assert not app.renders
    app.ticker_var.set("MSFT")
    app._load_data_async()
    _deliver(app, 1)
    installed = app._primary
    assert installed[0].close == 200
    assert app._confirmed_primary_ticker == "MSFT"
    _deliver(app, 0)
    assert app._primary is installed
    assert app.renders == ["sync"]


def test_compare_toggle_replaces_inflight_pair(load_app):
    app = load_app
    app._load_data_async()
    app.compare_var.set(True)
    app._on_compare_toggle()
    assert len(app._fetch_executor.jobs) == 2
    _deliver(app, 0)
    assert not app.renders
    _deliver(app, 1)
    assert app._primary[0].close == 100
    assert app._compare[0].close == 300
    assert app.renders == ["sync"]


def test_cache_hit_uses_same_acceptance_without_executor(load_app):
    app = load_app
    app._chart_loader._is_stale = lambda *_: False
    app._full_cache[("unit-source", "AMD", "5m")] = _candles(150)
    app._load_data_async()
    assert not app._fetch_executor.jobs
    assert app._primary[0].close == 150
    assert app.renders == ["deferred"]


def test_accepted_source_switch_renders_synchronously(load_app):
    from tradinglab.core.view_intent import ViewMode

    app = load_app
    app._view.request(ViewMode.KEEP_DATES, load_pending=True)
    app._load_data_async()
    app._view.arm_keep_bars()
    assert app._view.load_pending
    _deliver(app, 0)
    assert app._view.by_time and not app._view.load_pending
    assert app.renders == ["sync"]


def test_closed_completion_does_not_even_read_widgets(load_app):
    app = load_app
    app._load_data_async()
    app._chart_loader.close()

    def fail():
        pytest.fail("closed completion touched Tk controls")

    app._chart_selection = fail
    _deliver(app, 0)
    assert not app.renders
    assert app._primary[0].close == 50


def test_replay_owns_lists_and_live_entrypoints_do_not_bump_token(load_app):
    app = load_app
    app._is_sandbox_active = lambda: True
    registrations = []
    app._sandbox_register_and_focus = registrations.append
    app._sandbox_sync_compare_to_var = lambda: None
    prior = app._primary
    token = app._fetch_token
    app._load_data()
    app._load_data_async()
    assert registrations == ["AMD", "AMD"]
    assert app._primary is prior and app._fetch_token == token
    assert not app._fetch_executor.jobs


def test_rejected_submission_preserves_synchronous_fallback(load_app):
    app = load_app

    def reject(*_):
        raise RuntimeError("executor shut down")

    app._fetch_executor.submit = reject
    app._load_data_async()
    assert app._primary[0].close == 100
    assert app.renders == ["sync"]


def test_close_confirmation_controls_load_invalidation(load_app):
    app = load_app
    app._load_data_async()
    owner = app._chart_loader
    shell = SimpleNamespace(
        _confirm_close_when_dirty=lambda: False,
        _chart_loader=owner,
        _after_jobs=set(),
        destroy=lambda: None,
    )
    ChartApp._on_close(shell)
    assert owner.pending and not owner.closed
    shell._confirm_close_when_dirty = lambda: True
    ChartApp._on_close(shell)
    assert owner.closed and not owner.pending
    _deliver(app, 0)
    assert not app.renders


def test_sync_cache_load_supersedes_an_outstanding_fetch(load_app):
    app = load_app
    app._load_data_async()
    app.ticker_var.set("MSFT")
    app._chart_loader._is_stale = lambda *_: False
    app._full_cache[("unit-source", "MSFT", "5m")] = _candles(450)
    app._load_data()
    installed = app._primary
    _deliver(app, 0)
    assert app._primary is installed
    assert installed[0].close == 450
    assert app.renders == ["deferred"]
