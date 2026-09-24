"""Real headless chart transactions with controlled completion order and I/O."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from tradinglab.core.timezones import ET
from tradinglab.core.view_intent import ViewController, ViewMode
from tradinglab.data.base import DATA_SOURCES
from tradinglab.data.chart_load import ChartLoadCoordinator, ChartLoadResult, ChartSelection, FetchedSide
from tradinglab.data.controller import DataController
from tradinglab.data.stream_controller import StreamController
from tradinglab.models import Candle


def bars(close=100, *, session="regular"):
    return [
        Candle(datetime(2026, 9, 8, 9, 30, tzinfo=ET) + timedelta(minutes=5 * i),
               close, close + 2, close - 2, close + i, 100, session)
        for i in range(3)
    ]


class Store:
    def __init__(self):
        self.rows = {}
        self.reads = []
        self.writes = []

    def load(self, *key):
        self.reads.append(key)
        return self.rows.get(key)

    def save(self, *args):
        key, values = args[:3], args[3]
        self.writes.append((key, list(values)))
        self.rows[key] = list(values)


@pytest.fixture
def loader(monkeypatch):
    data = DataController()
    store = Store()
    clock = [datetime(2026, 9, 8, 10, 0, tzinfo=ET).timestamp()]
    result = ChartLoadCoordinator(
        data, StreamController(), ViewController(), store=store,
        is_stale=lambda values, interval: data.is_stale(values, interval, now_s=clock[0], session_open=True),
    )
    monkeypatch.setitem(DATA_SOURCES, "test-chart", lambda *_: bars())
    result.test_clock = clock
    return result


def selection(**changes):
    return replace(ChartSelection("test-chart", "AMD", "5m"), **changes)


def completed(request, primary=None, compare=None):
    future = Future()
    future.set_result(ChartLoadResult(
        request, FetchedSide(bars() if primary is None else primary),
        FetchedSide([] if compare is None else compare),
    ))
    return future.result()


def test_out_of_order_completion_never_publishes_old_symbol(loader):
    old = loader.begin(selection(ticker="AAPL"))
    new = loader.begin(selection(ticker="MSFT"))
    loader.complete(new, completed(new, bars(200)), current=new.selection)
    installed = loader.data.primary
    writes = list(loader._store.writes)
    assert installed[0].close == 200
    assert loader.complete(old, completed(old, bars(300)), current=new.selection) is None
    assert loader.data.primary is installed
    assert loader._store.writes == writes


@pytest.mark.parametrize("changes", [
    {"ticker": "MSFT"}, {"source": "new-source"}, {"interval": "15m"},
    {"compare_on": True, "compare_ticker": "SPY"}, {"prepost": True},
])
def test_selection_changes_reject_completion_even_without_new_generation(loader, changes):
    request = loader.begin(selection())
    assert loader.complete(request, completed(request), current=selection(**changes)) is None
    assert loader.data.primary == []
    assert not loader._store.reads and not loader._store.writes


def test_primary_and_compare_publish_together_for_matching_request(loader):
    old = loader.begin(selection(compare_on=True, compare_ticker="SPY"))
    new = loader.begin(selection(compare_on=True, compare_ticker="QQQ"))
    assert loader.complete(old, completed(old, bars(10), bars(20)), current=new.selection) is None
    assert not loader.data.primary and not loader.data.compare
    loader.complete(new, completed(new, bars(30), bars(40)), current=new.selection)
    assert loader.data.primary[0].close == 30
    assert loader.data.compare[0].close == 40
    assert not loader.pending
    assert loader.complete(new, completed(new), current=new.selection) is None


def test_old_completion_does_not_release_source_switch_view_intent(loader):
    old = loader.begin(selection())
    loader.view.request(ViewMode.KEEP_DATES, load_pending=True)
    new = loader.begin(selection(ticker="MSFT"))
    assert loader.complete(old, completed(old), current=new.selection) is None
    assert loader.view.load_pending
    loader.view.arm_keep_bars()
    result = loader.complete(new, completed(new), current=new.selection)
    assert result.completing_switch
    assert not loader.view.load_pending
    assert loader.view.by_time and not loader.view.preserve


def test_close_and_external_generation_cutover_reject_pending_work(loader):
    request = loader.begin(selection())
    original = bars(55)
    loader.data.set_primary(original, original)
    loader.data.bump_token()  # Replay and stream takeover share this gate.
    assert loader.complete(request, completed(request), current=request.selection) is None
    assert loader.data.primary is original
    request = loader.begin(selection())
    loader.close()
    assert loader.complete(request, completed(request), current=request.selection) is None
    assert loader.data.primary is original
    assert not loader._store.writes
    with pytest.raises(RuntimeError, match="closed"):
        loader.begin(selection())


def test_cache_hit_does_not_fetch_and_preserves_render_classification(loader, monkeypatch):
    cached = bars()
    loader.test_clock[0] = cached[-1].date.timestamp()
    loader.data._full_cache[selection().primary_key] = cached
    calls = []
    monkeypatch.setitem(DATA_SOURCES, "test-chart", lambda *_: calls.append("network"))
    request = loader.begin(selection())
    assert loader.cache_hit(request)
    result = loader.complete(request, current=request.selection)
    assert result.cache_hit_only and not calls
    assert loader.data.primary_raw == cached
    assert all(a is b for a, b in zip(loader.data.primary_raw, cached, strict=True))


@pytest.mark.parametrize("fallback", ["memory", "disk", "none"])
@pytest.mark.parametrize("async_result", [False, True])
def test_empty_fetch_keeps_existing_fallback_policy(loader, monkeypatch, fallback, async_result):
    prior = bars(90)
    loader.data.set_primary(prior, prior)
    key = selection().primary_key
    if fallback == "memory":
        loader.data._full_cache[key] = prior
    elif fallback == "disk":
        loader._store.rows[key] = prior
    monkeypatch.setitem(DATA_SOURCES, "test-chart", lambda *_: [])
    request = loader.begin(selection())
    payload = completed(request, []) if async_result else None
    result = loader.complete(request, payload, current=request.selection)
    assert result.primary_failed == (fallback == "none")
    assert loader.data.primary == prior
    if fallback == "none":
        assert loader.data.primary is prior
        assert not loader._store.writes


def test_failed_compare_still_publishes_primary(loader):
    request = loader.begin(selection(compare_on=True, compare_ticker="BAD"))
    outcome = loader.complete(request, completed(request), current=request.selection)
    assert outcome.compare_failed and not outcome.primary_failed
    assert loader.data.primary and loader.data.compare == []


def test_worker_captures_provider_and_does_not_publish(loader, monkeypatch):
    def old_fetcher(*_):
        return bars(123)
    monkeypatch.setitem(DATA_SOURCES, "test-chart", old_fetcher)
    request = loader.begin(selection())
    monkeypatch.setitem(DATA_SOURCES, "test-chart", lambda *_: bars(999))
    result = loader.fetch(request)
    assert result.primary.bars[0].close == 123
    assert loader.data.primary == []
    assert loader._store.writes
    writes = list(loader._store.writes)
    loader.complete(request, result, current=request.selection)
    assert loader.data.primary[0].close == 123
    assert loader._store.writes == writes, "No duplicate UI-thread save of premerged data"


def test_poll_worker_only_reads_disk_until_completion(loader):
    request = loader.begin(selection(), refresh=True)
    result = loader.fetch(request)
    assert not loader._store.writes
    assert result.disk_preloaded and not result.merge_preloaded
    loader.complete(request, result, current=request.selection)
    assert loader._store.writes


def test_merge_retains_history_and_fresh_provider_revision(loader):
    key = selection().primary_key
    older = replace(bars()[0], date=bars()[0].date - timedelta(minutes=5))
    loader._store.rows[key] = [older, *bars(90)]
    request = loader.begin(selection())
    loader.complete(request, completed(request, bars(110)), current=request.selection)
    assert loader.data.primary_raw == [older, *bars(110)]
    assert loader._store.rows[key] == loader.data.primary_raw


def test_prepost_filter_uses_request_and_keeps_cache_truthful(loader):
    mixed = bars()
    mixed[0].session = "pre"
    request = loader.begin(selection(prepost=False))
    loader.complete(request, completed(request, mixed), current=request.selection)
    assert loader.data.primary == mixed[1:]
    assert loader.data.primary_raw == mixed
    assert loader._store.rows[selection().primary_key] == mixed


def test_foreign_result_is_explicit_error(loader):
    first = loader.begin(selection())
    second = loader.begin(selection())
    with pytest.raises(ValueError, match="does not belong"):
        loader.complete(second, completed(first), current=second.selection)


def test_provider_and_store_failures_are_logged(loader, monkeypatch, caplog):
    def fail(*_):
        raise OSError("unavailable")

    monkeypatch.setitem(DATA_SOURCES, "test-chart", fail)
    monkeypatch.setattr(loader._store, "load", fail)
    request = loader.begin(selection())
    result = loader.fetch(request)
    assert not result.primary.bars
    assert "Chart fetch failed" in caplog.text and "Chart cache read failed" in caplog.text
