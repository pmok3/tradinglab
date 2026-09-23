"""Exercise the real async worker, completion callback and synchronous loader.

Only rendering/Tk services and the provider are stubbed. Disk writes stay in a
temporary directory and the merge/save spies record which thread paid the cost.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

import tradinglab.app as app_mod
from tests.unit.test_load_data_prefetch_indicator_refresh import (
    _bar,
    _install_load_data_harness,
)
from tradinglab.app import ChartApp


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    app = ChartApp.__new__(ChartApp)
    _install_load_data_harness(app, primary=[], compare=[])
    app._reresolve_symbols_for_source = lambda: None
    app._prefetch_observe_soon = lambda: None

    def bump_token():
        app._fetch_token += 1
        return app._fetch_token

    app._bump_fetch_token = bump_token
    app._preserve_xlim_on_render = False
    app.after_idle = lambda _callback: None
    app._preload_watchlist_events = lambda: None
    app._preload_watchlist_signals = lambda: None
    pending = []
    app._await_future_on_tk = lambda future, callback: pending.append((future, callback))
    app._render = Mock()
    monkeypatch.setattr(app_mod.disk_cache, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(app_mod.disk_cache, "_NO_PERSIST", set())
    monkeypatch.setitem(app_mod.DATA_SOURCES, "unit-source", lambda *_: [_bar(2, 102), _bar(3, 103)])
    app._disk_load = lambda key: app_mod.disk_cache.load(*key)
    with ThreadPoolExecutor(max_workers=1) as executor:
        app._fetch_executor = executor
        yield app, pending, tmp_path


def _complete(app, pending):
    future, callback = pending.pop(0)
    callback(future.result(timeout=10))
    assert app._prefetched_raw is None


@pytest.mark.parametrize("failed_symbols", [{"AMD"}, {"SPY"}, {"AMD", "SPY"}, set()])
@pytest.mark.parametrize("failure_kind", ["false", "exception"])
def test_worker_save_failure_retains_each_merge_off_tk(
    handoff, monkeypatch, caplog, failed_symbols, failure_kind,
):
    app, pending, _ = handoff
    failures = set(failed_symbols)
    main_thread = threading.get_ident()
    disk_cache = app_mod.disk_cache
    old = [_bar(0, 100), _bar(1, 101), _bar(2, 99)]
    for symbol in ("AMD", "SPY"):
        assert disk_cache.save("unit-source", symbol, "5m", old)
    real_save, real_merge = disk_cache.save, disk_cache.merge_candles
    saves, merges = [], []
    payloads = []

    def save(source, symbol, interval, bars):
        saves.append((symbol, threading.get_ident()))
        if symbol in failures:
            if failure_kind == "exception":
                raise OSError("disk full")
            return False
        return real_save(source, symbol, interval, bars)

    def merge(*args, **kwargs):
        merges.append(threading.get_ident())
        return real_merge(*args, **kwargs)

    app._render.side_effect = lambda: payloads.append(dict(app._prefetched_raw))
    monkeypatch.setattr(disk_cache, "save", save)
    monkeypatch.setattr(disk_cache, "merge_candles", merge)
    app._load_data_async()
    _complete(app, pending)

    assert len(merges) == 2
    assert all(thread != main_thread for thread in merges)
    assert [symbol for symbol, _ in saves] == ["AMD", "SPY"]
    assert all(thread != main_thread for _, thread in saves)
    assert app._render.call_count == 1
    for symbol, side in (("AMD", "primary"), ("SPY", "compare")):
        key = ("unit-source", symbol, "5m")
        assert [bar.close for bar in app._full_cache[key]] == [100, 101, 102, 103]
        assert [bar.close for bar in getattr(app, f"_{side}")] == [100, 101, 102, 103]
        assert payloads[0][f"{side}_merged"] is not None
        assert payloads[0][f"{side}_save_result"] is (symbol not in failed_symbols)
        expected_disk = [100, 101, 99] if symbol in failed_symbols else [100, 101, 102, 103]
        assert [bar.close for bar in disk_cache.load(*key)] == expected_disk
        assert (f"failed for unit-source/{symbol}/5m" in caplog.text) == (
            symbol in failed_symbols
        )

    # A later independent refresh retries on the worker, without recursion or
    # repeating the already-successful side's write.
    failures.clear()
    saves.clear()
    merges.clear()
    app._load_data_async()
    _complete(app, pending)
    assert len(merges) == 2
    assert all(thread != main_thread for thread in merges)
    assert all(thread != main_thread for _, thread in saves)
    assert {symbol for symbol, _ in saves} == failed_symbols
    assert app._render.call_count == 2
    assert payloads[1]["primary_save_result"] is (True if "AMD" in failed_symbols else None)
    assert payloads[1]["compare_save_result"] is (True if "SPY" in failed_symbols else None)
    for symbol in ("AMD", "SPY"):
        assert [bar.close for bar in disk_cache.load("unit-source", symbol, "5m")] == [100, 101, 102, 103]


@pytest.mark.parametrize("kind", ["unchanged", "byod", "ratio"])
def test_worker_no_write_paths_keep_merged_data(handoff, monkeypatch, kind):
    app, pending, root = handoff
    app.compare_var.set(False)
    disk_cache = app_mod.disk_cache
    payloads = []
    app._render.side_effect = lambda: payloads.append(dict(app._prefetched_raw))
    expected = [_bar(2, 102), _bar(3, 103)]
    if kind == "unchanged":
        assert disk_cache.save("unit-source", "AMD", "5m", expected)
    elif kind == "byod":
        disk_cache.mark_no_persist("unit-source")
    else:
        app.ticker_var.set("AMD/SPY")
    replace = Mock(side_effect=AssertionError("no physical write expected"))
    monkeypatch.setattr(disk_cache.os, "replace", replace)
    merge = Mock(wraps=disk_cache.merge_candles)
    monkeypatch.setattr(disk_cache, "merge_candles", merge)
    app._load_data_async()
    _complete(app, pending)
    assert [bar.close for bar in app._primary] == [102, 103]
    assert merge.call_count == 1
    assert payloads[0]["primary_save_result"] is (None if kind == "unchanged" else True)
    assert payloads[0]["compare_save_result"] is None
    replace.assert_not_called()
    if kind != "unchanged":
        assert list(root.iterdir()) == []


def test_async_stale_callback_does_not_replace_visible_data(handoff):
    app, pending, _ = handoff
    app._load_data_async()
    app._bump_fetch_token()
    _complete(app, pending)
    assert not app._full_cache
    app._render.assert_not_called()


@pytest.mark.parametrize("memory_fallback", [False, True])
def test_worker_empty_fetch_does_not_merge_or_write_fallback_on_tk(handoff, monkeypatch, memory_fallback):
    app, pending, _ = handoff
    app.compare_var.set(False)
    key = ("unit-source", "AMD", "5m")
    fallback = [_bar(0, 100)]
    assert app_mod.disk_cache.save(*key, fallback)
    if memory_fallback:
        fallback = [_bar(0, 101)]
        app._full_cache[key] = fallback
    monkeypatch.setitem(app_mod.DATA_SOURCES, "unit-source", lambda *_: [])
    merge = Mock(wraps=app_mod.disk_cache.merge_candles)
    save = Mock(wraps=app_mod.disk_cache.save)
    monkeypatch.setattr(app_mod.disk_cache, "merge_candles", merge)
    monkeypatch.setattr(app_mod.disk_cache, "save", save)
    app._load_data_async()
    _complete(app, pending)
    assert app._primary == fallback
    assert app._full_cache[key] == fallback
    merge.assert_not_called()
    save.assert_not_called()


def test_memory_hit_does_not_write_cache_on_tk(handoff, monkeypatch):
    app, pending, _ = handoff
    app.compare_var.set(False)
    app._full_cache[("unit-source", "AMD", "5m")] = [_bar(0, 100)]
    app._cache_is_stale = lambda *_: False
    save = Mock(wraps=app_mod.disk_cache.save)
    monkeypatch.setattr(app_mod.disk_cache, "save", save)
    app._load_data_async()
    assert pending == []
    save.assert_not_called()
    assert [bar.close for bar in app._primary] == [100]


@pytest.mark.parametrize("shape", [4, 6])
def test_legacy_worker_payload_does_not_invent_save_success(handoff, shape):
    app, pending, _ = handoff
    payloads = []
    app._render.side_effect = lambda: payloads.append(dict(app._prefetched_raw))
    app._load_data_async()
    future, callback = pending.pop()
    callback(future.result(timeout=10)[:shape])
    assert app._prefetched_raw is None
    assert [bar.close for bar in app._primary] == [102, 103]
    assert [bar.close for bar in app._compare] == [102, 103]
    assert payloads[0]["primary_save_result"] is None
    assert payloads[0]["compare_save_result"] is None
