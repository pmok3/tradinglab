"""Offline integration through the actual chart loaders and registry Auto delegate."""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tradinglab import disk_cache
from tradinglab.app import ChartApp
from tradinglab.core.lru_dict import LRUDict
from tradinglab.data import auto_source, hybrid_source
from tradinglab.data.base import DATA_SOURCES
from tradinglab.models import Candle


def _bars(start, stop, price):
    return [Candle(datetime(2024, 6, d, tzinfo=timezone.utc), price, price, price, price, 100)
            for d in range(start, stop)]


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Executor:
    def submit(self, work):
        future = Future()
        future.set_result(work())
        return future


class _Chart:
    _load_data = ChartApp._load_data
    _load_data_async = ChartApp._load_data_async
    _series_date_span = staticmethod(ChartApp._series_date_span)

    def __init__(self, source):
        self.source_var = _Var(source)
        self.interval_var = _Var("5m")
        self.ticker_var = _Var("AAPL")
        self.compare_var = _Var(True)
        self.compare_ticker_var = _Var("MSFT")
        self._confirmed_primary_ticker = "AAPL"
        self._view = SimpleNamespace(begin_completing_load=lambda: False)
        self._stream_ctrl = SimpleNamespace(matches=lambda *a, **k: True, subscribed=False)
        self.messages = []
        self._status = SimpleNamespace(
            info=lambda m: self.messages.append(("info", m)),
            warn=lambda m: self.messages.append(("warn", m)),
            error=lambda m: self.messages.append(("error", m)),
        )
        self._fetch_executor = _Executor()
        self._full_cache = OrderedDict()
        self._fetch_token = 0
        self._prefetched_raw = None
        self._primary = []
        self._compare = []
        self.renders = []

    def _bump_fetch_token(self):
        self._fetch_token += 1
        return self._fetch_token

    def _set_data_state(self, **state):
        for name, value in state.items():
            setattr(self, "_" + name, value)

    def _render(self):
        self.renders.append(self._primary.copy())

    _request_deferred_render = _render

    def _disk_load(self, key):
        return disk_cache.load(*key)

    def _await_future_on_tk(self, future, callback):
        callback(future.result())

    def _is_sandbox_active(self):
        return False

    def _cache_is_stale(self, *_):
        return True

    def _maybe_upsample_today_daily(self, bars, **_):
        return bars

    def _apply_pair_filter_and_align(self, primary, compare):
        return primary, compare

    def _noop(self, *args, **kwargs):
        pass

    _reresolve_symbols_for_source = _noop
    _prefetch_observe_soon = _noop
    _trim_full_cache = _noop
    _invalidate_focused_panels = _noop
    after_idle = _noop
    _schedule_next_bar_fetch = _noop
    _start_stream_if_applicable = _noop
    _preload_watchlist_events = _noop
    _preload_watchlist_signals = _noop


@pytest.fixture
def series(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(hybrid_source, "_RECOVERY", LRUDict(maxsize=128))
    monkeypatch.setattr(disk_cache, "_HISTORY_REVISIONS", LRUDict(maxsize=128))
    monkeypatch.setattr(auto_source, "resolve_auto_source", lambda: hybrid_source.HYBRID_SOURCE_NAME)
    monkeypatch.setattr(auto_source, "_last_resolved", None)
    recent = _bars(15, 26, 100)
    old = _bars(1, 21, 400)
    monkeypatch.setattr(hybrid_source, "fetch_live_data", lambda *_: recent)
    monkeypatch.setattr(hybrid_source, "fetch_alpaca_data", lambda *_: None)
    monkeypatch.setitem(DATA_SOURCES, hybrid_source.HYBRID_SOURCE_NAME, hybrid_source.fetch_hybrid_data)
    monkeypatch.setitem(DATA_SOURCES, "Auto", auto_source.fetch_auto_data)
    for ticker in ("AAPL", "MSFT"):
        disk_cache.save("alpaca", ticker, "5m", old)
        for source in ("Auto", hybrid_source.HYBRID_SOURCE_NAME):
            disk_cache.save(source, ticker, "5m", old)
    return old, recent


@pytest.mark.parametrize("source", ["Auto", hybrid_source.HYBRID_SOURCE_NAME])
@pytest.mark.parametrize("method", ["_load_data", "_load_data_async"])
def test_chart_load_replaces_both_slots_and_explains_shorter_history(series, source, method):
    _, recent = series
    app = _Chart(source)
    for ticker in ("AAPL", "MSFT"):
        key = (source, ticker, "5m")
        app._full_cache[key] = disk_cache.load(*key)
    getattr(app, method)()
    assert app._primary == recent
    assert app._compare == recent
    for ticker in ("AAPL", "MSFT"):
        key = (source, ticker, "5m")
        assert isinstance(app._full_cache[key], disk_cache.HistorySnapshot)
        assert app._full_cache[key] == disk_cache.load(*key) == recent
        assert any(level == "warn" and ticker in msg and "deep history withheld" in msg
                   for level, msg in app.messages)
    assert app.renders[-1] == recent


def test_quarantined_outage_clears_obsolete_chart_instead_of_fallback(series, monkeypatch):
    old, _ = series
    app = _Chart("Auto")
    stale = disk_cache.load("Auto", "AAPL", "5m")
    hybrid_source.fetch_hybrid_data("AAPL", "5m")
    app._full_cache[("Auto", "AAPL", "5m")] = stale
    app._primary = old
    monkeypatch.setattr(hybrid_source, "fetch_live_data", lambda *_: None)
    app._load_data()
    assert app._primary == []
    assert app.renders[-1] == []
    assert any(level == "error" and "deep history withheld" in msg for level, msg in app.messages)


def test_restatement_only_in_outer_cache_keeps_async_raw_handoff_usable(series):
    _, recent = series
    for ticker in ("AAPL", "MSFT"):
        disk_cache.save("alpaca", ticker, "5m", _bars(1, 21, 100))
    app = _Chart("Auto")
    app._load_data_async()
    assert len(app._primary) == len(app._compare) == 25
    assert all(c.close == 100 for c in app._primary)
    assert app._primary[-1] == recent[-1]
    assert not any(level == "error" for level, _ in app.messages)
    assert disk_cache.load("Auto", "AAPL", "5m") == app._primary


def test_scheduler_handoffs_preserve_fence_end_to_end(series, monkeypatch):
    from tradinglab.data.fetch_service import FetchService
    from tradinglab.data.prefetch import CACHE_MEMORY_AND_DISK
    from tradinglab.data.prefetch.planner import FetchWindow
    from tradinglab.data.prefetch.priority import FetchJob
    from tradinglab.gui.prefetch_app import PrefetchAppMixin

    _, recent = series
    app = _Chart("Auto")
    key = ("Auto", "AAPL", "5m")
    app._full_cache[key] = disk_cache.load(*key)
    service = FetchService(worker_count=1)
    app._fetch_svc = service
    app._prefetch_driver = SimpleNamespace(
        scheduler=SimpleNamespace(
            window_for=lambda _: FetchWindow(interval="5m", kind="period"),
            cache_policy_for=lambda _: CACHE_MEMORY_AND_DISK,
        ),
        complete=lambda *a, **k: None,
    )
    monkeypatch.setattr(service, "submit_prefetch", _Executor().submit)
    app._stash_full_cache = lambda k, bars: app._full_cache.__setitem__(k, bars)
    app._prefetch_pump = app._noop
    app._job_symbol_is_watchlisted = lambda _: False
    app._refresh_daily_synth_for_active_view = app._noop
    job = FetchJob(source="Auto", symbol="AAPL", interval="5m", band_index=0,
                   tier_rank=10, interval_rank=0, generation=0)
    try:
        PrefetchAppMixin._prefetch_submit(app, job)
        assert app._full_cache[key] == disk_cache.load(*key) == recent
        assert isinstance(app._full_cache[key], disk_cache.HistorySnapshot)
        disk_cache.invalidate_history("AAPL", "5m")
        assert not app._full_cache[key]
    finally:
        service.shutdown()
