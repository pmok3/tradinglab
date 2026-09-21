"""``disk_cache.save`` failure handling at the call sites.

Companion to ``test_disk_cache_save_contract.py`` (which pins the
``save() -> True/False`` contract itself): every production caller that
branches on a ``False`` return must log the failure and keep going.
These tests execute the log-and-continue lines the CI changed-line
gate flagged as uncovered — a failed persist must never fail the
surrounding operation.
"""
from __future__ import annotations

import datetime as _dt
import logging
import queue
import threading
from types import SimpleNamespace

import pytest

from tradinglab.models import Candle


def _candles(n: int, *, start_day: int = 1) -> list[Candle]:
    out = []
    for i in range(n):
        d = _dt.datetime(2026, 6, start_day + i, 14, 30, tzinfo=_dt.timezone.utc)
        out.append(Candle(date=d, open=10.0 + i, high=11.0 + i, low=9.0 + i,
                          close=10.5 + i, volume=100))
    return out


def _logged(caplog, fragment: str) -> bool:
    return any(fragment in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 1. data/stream_controller._mutate — failed persist warns, mutation stands
# ---------------------------------------------------------------------------


def test_mutate_save_failure_warns_and_keeps_correction(monkeypatch, caplog) -> None:
    from tradinglab.data.stream_controller import StreamController, StreamMutation

    ctl = StreamController()
    key = ("yfinance", "AMD", "5m")
    cache = {key: _candles(1)}
    corrected = _candles(1)
    corrected[0].close = 99.0

    saves: list[tuple] = []

    def _save(*args):
        saves.append(args)
        return False

    with caplog.at_level(logging.WARNING, logger="tradinglab.data.stream_controller"):
        mutation = ctl._mutate(key, corrected[0], "closed", cache, None, _save, None)

    assert len(saves) == 1
    # The correction still landed in the live series...
    assert cache[key][0].close == 99.0
    assert mutation is StreamMutation.LAST
    # ...and the failed persist is observable.
    assert _logged(caplog, "Could not persist stream correction")


def test_mutate_save_none_return_is_not_failure(caplog) -> None:
    """A ``None``-returning save double keeps working: only an explicit
    ``False`` counts as failure."""
    from tradinglab.data.stream_controller import StreamController

    ctl = StreamController()
    key = ("yfinance", "AMD", "5m")
    cache = {key: _candles(1)}
    corrected = _candles(1)
    corrected[0].close = 99.0

    with caplog.at_level(logging.WARNING, logger="tradinglab.data.stream_controller"):
        ctl._mutate(key, corrected[0], "closed", cache, None, lambda *a: None, None)

    assert cache[key][0].close == 99.0
    assert not _logged(caplog, "Could not persist stream correction")


# ---------------------------------------------------------------------------
# 2. gui/drilldown._targeted_range_fetch — save failure is a debug, not an error
# ---------------------------------------------------------------------------


def test_targeted_range_fetch_save_failure_logged(monkeypatch, caplog) -> None:
    from tradinglab.gui import drilldown as dd

    bars = _candles(3)

    class _Coverage:
        segments = [object()]

        def load(self, *a):
            return self

        def data_start(self, cov):
            return None

        def covered(self, cov, start_ts, end_ts):
            return False

        def record_fetch(self, *a):
            pass

    monkeypatch.setattr(dd, "coverage", _Coverage())
    monkeypatch.setattr(dd, "fetch_range", lambda *a, **k: (bars, "ok"))
    monkeypatch.setattr("tradinglab.disk_cache.load", lambda *a: [])
    monkeypatch.setattr("tradinglab.disk_cache.save", lambda *a: False)

    mixin = object.__new__(dd.DrilldownMixin)
    day = _dt.date(2026, 6, 8)
    now_ts = int(_dt.datetime(2026, 6, 9, tzinfo=_dt.timezone.utc).timestamp())
    with caplog.at_level(logging.DEBUG, logger="tradinglab.gui.drilldown"):
        got = dd.DrilldownMixin._targeted_range_fetch(
            mixin, "yfinance", "AMD", "5m", day, now_ts, merge_to_disk=True)

    # The drill still lands with the fetched bars.
    assert [c.close for c in got] == [c.close for c in bars]
    assert _logged(caplog, "drilldown: disk_cache save failed for yfinance/AMD/5m")


# ---------------------------------------------------------------------------
# 3. gui/drilldown._on_drilldown_fetch_done — merge-path save failure
# ---------------------------------------------------------------------------


class _Var:
    def __init__(self, value: str) -> None:
        self._value = value

    def get(self) -> str:
        return self._value


class _Status:
    def __init__(self) -> None:
        self.warns: list[str] = []

    def warn(self, msg: str) -> None:
        self.warns.append(msg)


class _DrilldownHarness:
    """Minimal state for ``_on_drilldown_fetch_done`` (no Tk)."""

    def __init__(self) -> None:
        self._drilldown_request = None
        self.source_var = _Var("yfinance")
        self.ticker_var = _Var("amd")
        self._full_cache: dict = {}
        self._after_jobs: set = set()
        self._status = _Status()

    def _disk_load(self, key):
        return []

    def _trim_full_cache(self) -> None:
        pass

    def after_cancel(self, jid) -> None:
        pass


def test_drilldown_fetch_done_save_failure_logged(monkeypatch, caplog) -> None:
    from tradinglab.gui import drilldown as dd

    class _Harness(dd.DrilldownMixin, _DrilldownHarness):
        pass

    bars = _candles(3)
    monkeypatch.setattr("tradinglab.disk_cache.save", lambda *a: False)

    h = _Harness()
    req = dd._DrilldownRequest(
        request_id=7, fetch_token=1, src="yfinance", ticker="AMD",
        day=_dt.date(2020, 1, 1))
    h._drilldown_request = req

    with caplog.at_level(logging.DEBUG, logger="tradinglab.gui.drilldown"):
        dd.DrilldownMixin._on_drilldown_fetch_done(h, 7, bars)

    key = ("yfinance", "AMD", "5m")
    # The merged series still lands in the memory cache...
    assert [c.close for c in h._full_cache[key]] == [c.close for c in bars]
    # ...and the failed persist is logged, not raised.
    assert _logged(caplog, "drilldown: disk_cache save failed")
    # Request slot cleared; day not covered by 2026 bars -> status warn.
    assert h._drilldown_request is None
    assert h._status.warns


# ---------------------------------------------------------------------------
# 4. gui/universe_prepare_dialog._run_filter_prepass — persist failure
# ---------------------------------------------------------------------------


def test_prepass_save_failure_logged_and_screen_continues(monkeypatch, caplog) -> None:
    from tradinglab.gui import universe_prepare_dialog as upd

    bars = _candles(5)
    monkeypatch.setattr("tradinglab.disk_cache.load", lambda *a: None)
    monkeypatch.setattr("tradinglab.disk_cache.save", lambda *a: False)
    monkeypatch.setattr(upd, "passes_fundamental_filter", lambda b, spec: True)

    stub = SimpleNamespace(_cancel_event=threading.Event(), _event_queue=queue.Queue())
    with caplog.at_level(logging.DEBUG, logger="tradinglab.gui.universe_prepare_dialog"):
        matched = upd.UniversePrepareDialog._run_filter_prepass(
            stub, ("AMD",), "yfinance", object(), lambda sym, itv: bars)

    # A failed persist must not fail the screen.
    assert matched == ["AMD"]
    assert _logged(caplog, "universe_prepare: disk_cache save failed")


# ---------------------------------------------------------------------------
# 5. strategy_tester/runner.fetch_candles_for_symbol — save / merge failures
# ---------------------------------------------------------------------------


def test_fetch_candles_save_failure_logged_returns_merged(monkeypatch, caplog) -> None:
    from tradinglab.data.base import DATA_SOURCES
    from tradinglab.strategy_tester import runner

    bars = _candles(3)
    monkeypatch.setattr("tradinglab.disk_cache.load", lambda *a: [])
    monkeypatch.setattr(
        "tradinglab.disk_cache.merge_candles", lambda old, new: list(new))
    monkeypatch.setattr("tradinglab.disk_cache.save", lambda *a: False)
    monkeypatch.setitem(DATA_SOURCES, "yfinance", lambda sym, itv: bars)

    with caplog.at_level(logging.DEBUG, logger="tradinglab.strategy_tester.runner"):
        got = runner.fetch_candles_for_symbol("amd", "5m")

    assert [c.close for c in got] == [c.close for c in bars]
    assert _logged(caplog, "strategy_tester: disk_cache save failed")


def test_fetch_candles_merge_failure_logged_returns_fetched(monkeypatch, caplog) -> None:
    """A cache merge blow-up must not break the Run: the fetched bars are
    returned as-is."""
    from tradinglab.data.base import DATA_SOURCES
    from tradinglab.strategy_tester import runner

    bars = _candles(3)
    monkeypatch.setattr("tradinglab.disk_cache.load", lambda *a: [])

    def _boom(old, new):
        raise RuntimeError("merge exploded")

    monkeypatch.setattr("tradinglab.disk_cache.merge_candles", _boom)
    monkeypatch.setitem(DATA_SOURCES, "yfinance", lambda sym, itv: bars)

    with caplog.at_level(logging.DEBUG, logger="tradinglab.strategy_tester.runner"):
        got = runner.fetch_candles_for_symbol("amd", "5m")

    assert [c.close for c in got] == [c.close for c in bars]
    assert _logged(caplog, "strategy_tester: disk_cache merge failed")
