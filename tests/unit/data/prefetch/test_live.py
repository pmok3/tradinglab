"""Unit tests for ``data.prefetch.live`` — window→fetch translation + oldest_ts.

The live seam runs these on the prefetch worker pool: ``fetch_window`` turns a
scheduler ``FetchWindow`` into a concrete registry fetch (range→``fetch_page``,
period→trailing fetcher), and ``oldest_ts`` derives the deepening step-back
timestamp. Pure apart from the monkeypatch-friendly ``DATA_SOURCES`` /
``fetch_page`` dispatch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import tradinglab.data.base as base
from tradinglab.data.prefetch import live
from tradinglab.data.prefetch.planner import FetchWindow


class _Bar:
    def __init__(self, ts: float):
        self.date = datetime.fromtimestamp(ts, timezone.utc)


# ------------------------------------------------------------------ oldest_ts
def test_oldest_ts_empty_is_none():
    assert live.oldest_ts(None) is None
    assert live.oldest_ts([]) is None


def test_oldest_ts_returns_min():
    base_ts = datetime(2024, 3, 1, tzinfo=timezone.utc).timestamp()
    bars = [_Bar(base_ts), _Bar(base_ts + 300), _Bar(base_ts + 600)]
    assert live.oldest_ts(bars) == base_ts


def test_oldest_ts_defensive_on_unordered():
    base_ts = datetime(2024, 3, 1, tzinfo=timezone.utc).timestamp()
    bars = [_Bar(base_ts + 600), _Bar(base_ts)]     # descending
    assert live.oldest_ts(bars) == base_ts          # min wins


def test_oldest_ts_bad_bar_is_none():
    assert live.oldest_ts([object()]) is None


# ---------------------------------------------------------------- fetch_window
def _period_window():
    return FetchWindow(interval="1d", kind="period", period="max")


def _range_window(end=1000.0, limit=500):
    return FetchWindow(interval="5m", kind="range", end=end, limit=limit)


def test_fetch_window_period_uses_trailing_fetcher(monkeypatch):
    calls = {}

    def _fetcher(t, i):
        calls["args"] = (t, i)
        return [_Bar(1.0)]

    monkeypatch.setitem(base.DATA_SOURCES, "src", _fetcher)
    bars, err, ra = live.fetch_window("src", "AMD", "1d", _period_window())
    assert err is None and ra is None
    assert len(bars) == 1 and calls["args"] == ("AMD", "1d")


def test_fetch_window_range_ok(monkeypatch):
    captured = {}

    def fake_fetch_page(source, ticker, interval, *, end_ts=None, limit=None):
        captured.update(source=source, end_ts=end_ts, limit=limit)
        return base.FetchPageResult([_Bar(1.0), _Bar(2.0)], "ok")

    monkeypatch.setattr(base, "fetch_page", fake_fetch_page)
    bars, err, ra = live.fetch_window("src", "AMD", "5m", _range_window(end=1000.0, limit=500))
    assert err is None and ra is None and len(bars) == 2
    assert captured == {"source": "src", "end_ts": 1000.0, "limit": 500}


def test_fetch_window_range_empty(monkeypatch):
    monkeypatch.setattr(
        base, "fetch_page",
        lambda *a, **k: base.FetchPageResult([], "empty"),
    )
    bars, err, ra = live.fetch_window("src", "AMD", "5m", _range_window())
    assert bars == [] and err is None and ra is None


def test_fetch_window_range_error_propagates(monkeypatch):
    boom = RuntimeError("429")
    monkeypatch.setattr(
        base, "fetch_page",
        lambda *a, **k: base.FetchPageResult(None, "error", error=boom, retry_after_s=5.0),
    )
    bars, err, ra = live.fetch_window("src", "AMD", "5m", _range_window())
    assert bars == [] and err is boom and ra == 5.0


def test_fetch_window_range_unsupported_falls_back_to_trailing(monkeypatch):
    monkeypatch.setattr(
        base, "fetch_page",
        lambda *a, **k: base.FetchPageResult(None, "unsupported"),
    )
    monkeypatch.setitem(base.DATA_SOURCES, "src", lambda t, i: [_Bar(9.0)])
    bars, err, ra = live.fetch_window("src", "AMD", "5m", _range_window())
    assert err is None and len(bars) == 1        # trailing fetcher used


def test_fetch_window_no_fetcher_is_empty(monkeypatch):
    base.DATA_SOURCES.pop("ghost", None)
    bars, err, ra = live.fetch_window("ghost", "AMD", "1d", _period_window())
    assert bars == [] and err is None and ra is None


def test_fetch_window_fetcher_raises_returns_error(monkeypatch):
    def boom(t, i):
        raise ValueError("net down")

    monkeypatch.setitem(base.DATA_SOURCES, "src", boom)
    bars, err, ra = live.fetch_window("src", "AMD", "1d", _period_window())
    assert bars == [] and isinstance(err, ValueError) and ra is None
