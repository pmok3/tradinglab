from __future__ import annotations

import datetime as dt
from collections import OrderedDict
from typing import Any

import numpy as np

import tradinglab.app as app_mod
from tradinglab.app import ChartApp
from tradinglab.core.bars import Bars
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
    app._primary = primary
    app._compare = compare
    app._primary_raw = primary
    app._compare_raw = compare
    app._full_cache = OrderedDict()
    app._series_cache = {}
    app._indicator_cache = IndicatorCache(capacity=8)
    app._prefetched_raw = None
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
    app._bump_fetch_token = lambda: 1
    app._stop_stream = lambda: None
    app._cache_is_stale = lambda _candles, _interval: True
    app._prefetch_companion_intervals = lambda _tickers: None
    app._disk_load = lambda _key: None
    app._trim_full_cache = lambda: None
    app._maybe_upsample_today_daily = lambda candles, **_kwargs: candles
    app._apply_pair_filter_and_align = (
        lambda primary_raw, compare_raw: (primary_raw, compare_raw or [])
    )

    def _set_data_state(*, primary_raw, primary, compare_raw, compare) -> None:
        app._primary_raw = primary_raw
        app._primary = primary
        app._compare_raw = compare_raw
        app._compare = compare

    app._set_data_state = _set_data_state
    app._invalidate_focused_panels = ChartApp._invalidate_focused_panels.__get__(
        app,
        ChartApp,
    )
    app._request_deferred_render = lambda: None
    app._render = lambda: None
    app._load_events_async = lambda _symbol: None
    app._schedule_next_bar_fetch = lambda: None
    app._start_stream_if_applicable = lambda: None
    app._ensure_compare_prefetched = lambda: None
    app._preload_watchlist = lambda: None
    app._preload_watchlist_daily = lambda: None


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
        raise _UnexpectedFetch("_load_data should consume _prefetched_raw")

    monkeypatch.setitem(app_mod.DATA_SOURCES, "unit-source", _fetcher)
    monkeypatch.setattr(
        app_mod.disk_cache,
        "merge_candles",
        lambda _cached, bars: list(bars or []),
    )
    monkeypatch.setattr(app_mod.disk_cache, "save", lambda *_args, **_kwargs: None)

    app._prefetched_raw = {
        "src": "unit-source",
        "interval": "5m",
        "primary_ticker": "AMD",
        "compare_ticker": "SPY",
        "primary": new_primary,
        "compare": new_compare,
    }

    ChartApp._load_data(app)

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
