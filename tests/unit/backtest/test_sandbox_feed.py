"""``SandboxFeedWarmer`` — the sandbox market feed.

A replay session only advances symbols it has *registered*. Before the
feed, a session registered three (reference, focus, compare), so the
replay clock moved a three-symbol market: the watchlist showed nothing
and a scan had nothing to scan. The warmer registers the trader's whole
observable universe from the disk cache.

Covered here:
- universe resolution = pinned watchlists ∪ prepared universe;
- the session's pinned ``data_source`` is what gets read, not the
  toolbar's ``source_var``;
- registration uses ``prefetch_events=False`` (a per-symbol event fetch
  at universe scale is hundreds of network round-trips);
- batching hops through ``_track_after`` instead of blocking the loop;
- ``cancel()`` stops registration;
- ``request()`` is an idempotent top-up, not a reload;
- symbols with no cached data are reported once, not retried.

See ``backtest/sandbox_feed.spec.md``.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

import pytest

from tradinglab.backtest.sandbox_feed import SandboxFeedWarmer
from tradinglab.models import Candle

SESSION_DATE = _dt.date(2026, 6, 10)


def _candles(n: int = 5, *, base: float = 100.0) -> list[Candle]:
    out = []
    for i in range(n):
        d = _dt.datetime(2026, 6, 10, 14, 30, tzinfo=_dt.timezone.utc) \
            + _dt.timedelta(minutes=5 * i)
        px = base + i
        out.append(Candle(date=d, open=px, high=px + 1, low=px - 1,
                          close=px, volume=1000))
    return out


class _Status:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, msg: str) -> None:
        self.messages.append(msg)

    warn = info
    error = info


class _Controller:
    """Minimal stand-in for the parts of SandboxController the feed uses."""

    def __init__(self, *, data_source: str = "yfinance") -> None:
        self.active = True
        self.interval = "5m"
        self.session_date = SESSION_DATE
        self.lookback_days = 2
        self.daily_lookback_bars = 100
        self.data_source = data_source
        self.full_candles_by_symbol: dict[str, list] = {}
        self.daily_full_by_symbol: dict[str, list] = {}
        self.registered: list[tuple[str, int, bool]] = []
        self.raise_value_error_for: set[str] = set()

    def register_ticker(self, symbol, candles, *, prefetch_events=True):
        if symbol in self.raise_value_error_for:
            raise ValueError("different content")
        self.registered.append((symbol, len(candles), prefetch_events))
        self.full_candles_by_symbol[symbol] = list(candles)
        return []

    def register_daily_for(self, symbol, daily):
        self.daily_full_by_symbol[symbol] = list(daily)


class _Var:
    def __init__(self, v: str) -> None:
        self._v = v

    def get(self) -> str:
        return self._v


class _App:
    """Headless app seam. No executor → the warmer runs loads inline."""

    def __init__(self, *, pinned=(), universe=(), with_track_after=True) -> None:
        self._status = _Status()
        self._pinned = list(pinned)
        self._sandbox_universe = frozenset(universe)
        self.source_var = _Var("Auto")
        self.after_calls: list[tuple] = []
        self.watchlist_refreshes = 0
        self.scanner_refreshes = 0
        self._with_track_after = with_track_after

    def _pinned_ticker_union(self) -> list[str]:
        return list(self._pinned)

    def _refresh_watchlist_for_sandbox(self) -> None:
        self.watchlist_refreshes += 1

    def _refresh_scanner_for_sandbox(self) -> None:
        self.scanner_refreshes += 1

    def _track_after(self, delay, fn, *args):
        if not self._with_track_after:
            raise RuntimeError("no track_after")
        self.after_calls.append((delay, fn, args))
        return "after#1"

    def drain_after(self) -> None:
        """Run queued ``_track_after`` callbacks to completion."""
        guard = 0
        while self.after_calls:
            guard += 1
            assert guard < 1000, "after-callback loop did not terminate"
            _delay, fn, args = self.after_calls.pop(0)
            fn(*args)


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    """Redirect the disk cache and expose a writer."""
    monkeypatch.setattr("tradinglab.disk_cache._cache_dir", lambda: tmp_path)
    from tradinglab import disk_cache

    def _write(source, ticker, interval, candles):
        disk_cache.save(source, ticker, interval, candles)

    return _write


# ---------------------------------------------------------------------------
# Universe resolution
# ---------------------------------------------------------------------------


def test_universe_is_watchlists_union_prepared_universe(cache):
    cache("yfinance", "AMD", "5m", _candles())
    cache("yfinance", "NVDA", "5m", _candles())
    cache("yfinance", "TSLA", "5m", _candles())
    app = _App(pinned=["AMD", "NVDA"], universe=["NVDA", "TSLA"])
    ctrl = _Controller()
    warmer = SandboxFeedWarmer(app=app, controller=ctrl)

    assert warmer.request() == 3
    app.drain_after()
    assert sorted(s for s, _n, _e in ctrl.registered) == ["AMD", "NVDA", "TSLA"]


def test_already_registered_symbols_are_skipped(cache):
    cache("yfinance", "AMD", "5m", _candles())
    cache("yfinance", "NVDA", "5m", _candles())
    app = _App(pinned=["AMD", "NVDA"])
    ctrl = _Controller()
    ctrl.full_candles_by_symbol["AMD"] = _candles()
    warmer = SandboxFeedWarmer(app=app, controller=ctrl)

    assert warmer.request() == 1
    app.drain_after()
    assert [s for s, _n, _e in ctrl.registered] == ["NVDA"]


def test_repeat_request_is_a_topup_not_a_reload(cache):
    cache("yfinance", "AMD", "5m", _candles())
    cache("yfinance", "NVDA", "5m", _candles())
    app = _App(pinned=["AMD"])
    ctrl = _Controller()
    warmer = SandboxFeedWarmer(app=app, controller=ctrl)

    warmer.request()
    app.drain_after()
    assert len(ctrl.registered) == 1

    app._pinned = ["AMD", "NVDA"]          # user pins another ticker
    assert warmer.request() == 1
    app.drain_after()
    assert [s for s, _n, _e in ctrl.registered] == ["AMD", "NVDA"]


def test_inactive_session_warms_nothing(cache):
    app = _App(pinned=["AMD"])
    ctrl = _Controller()
    ctrl.active = False
    assert SandboxFeedWarmer(app=app, controller=ctrl).request() == 0


# ---------------------------------------------------------------------------
# Source resolution — the reported bug, at the feed layer
# ---------------------------------------------------------------------------


def test_reads_the_sessions_pinned_source_not_the_toolbar(cache):
    """Toolbar says "Auto"; the tape lives under the pinned vendor."""
    cache("yfinance", "AMD", "5m", _candles())
    cache("Auto", "AMD", "5m", [])           # nothing under the toolbar key
    app = _App(pinned=["AMD"])
    assert app.source_var.get() == "Auto"
    ctrl = _Controller(data_source="yfinance")
    warmer = SandboxFeedWarmer(app=app, controller=ctrl)

    warmer.request()
    app.drain_after()
    assert [s for s, _n, _e in ctrl.registered] == ["AMD"]


def test_falls_back_to_toolbar_source_when_nothing_pinned(cache):
    cache("Auto", "AMD", "5m", _candles())
    app = _App(pinned=["AMD"])
    ctrl = _Controller(data_source="")
    warmer = SandboxFeedWarmer(app=app, controller=ctrl)

    warmer.request()
    app.drain_after()
    assert [s for s, _n, _e in ctrl.registered] == ["AMD"]


# ---------------------------------------------------------------------------
# Registration behaviour
# ---------------------------------------------------------------------------


def test_registers_without_prefetching_events(cache):
    cache("yfinance", "AMD", "5m", _candles())
    app = _App(pinned=["AMD"])
    ctrl = _Controller()
    SandboxFeedWarmer(app=app, controller=ctrl).request()
    app.drain_after()
    assert ctrl.registered == [("AMD", 5, False)]


def test_registers_daily_context_when_session_wants_it(cache):
    cache("yfinance", "AMD", "5m", _candles())
    cache("yfinance", "AMD", "1d", _candles(3, base=50.0))
    app = _App(pinned=["AMD"])
    ctrl = _Controller()
    SandboxFeedWarmer(app=app, controller=ctrl).request()
    app.drain_after()
    assert len(ctrl.daily_full_by_symbol["AMD"]) == 3


def test_skips_daily_when_session_has_no_daily_context(cache):
    cache("yfinance", "AMD", "5m", _candles())
    cache("yfinance", "AMD", "1d", _candles(3, base=50.0))
    app = _App(pinned=["AMD"])
    ctrl = _Controller()
    ctrl.daily_lookback_bars = 0
    SandboxFeedWarmer(app=app, controller=ctrl).request()
    app.drain_after()
    assert ctrl.daily_full_by_symbol == {}


def test_value_error_is_skipped_not_retried(cache):
    """Already registered with different content → live registration wins."""
    cache("yfinance", "AMD", "5m", _candles())
    cache("yfinance", "NVDA", "5m", _candles())
    app = _App(pinned=["AMD", "NVDA"])
    ctrl = _Controller()
    ctrl.raise_value_error_for = {"AMD"}
    SandboxFeedWarmer(app=app, controller=ctrl).request()
    app.drain_after()
    assert [s for s, _n, _e in ctrl.registered] == ["NVDA"]


def test_symbol_with_no_cached_data_is_reported_once(cache):
    cache("yfinance", "AMD", "5m", _candles())
    app = _App(pinned=["AMD", "GHOST"])
    ctrl = _Controller()
    warmer = SandboxFeedWarmer(app=app, controller=ctrl)
    warmer.request()
    app.drain_after()

    assert [s for s, _n, _e in ctrl.registered] == ["AMD"]
    assert warmer.progress() == (2, 2)
    assert any("Download Replay Data" in m for m in app._status.messages)
    # Not retried on the next top-up.
    assert warmer.request() == 0


def test_out_of_window_bars_are_not_registered(cache):
    """A symbol whose only cached bars predate the window registers as empty."""
    old = [Candle(date=_dt.datetime(2020, 1, 2, 14, 30, tzinfo=_dt.timezone.utc),
                  open=1.0, high=2.0, low=0.5, close=1.5, volume=10)]
    cache("yfinance", "OLD", "5m", old)
    app = _App(pinned=["OLD"])
    ctrl = _Controller()
    SandboxFeedWarmer(app=app, controller=ctrl).request()
    app.drain_after()
    assert ctrl.registered == []


# ---------------------------------------------------------------------------
# Batching + cancellation
# ---------------------------------------------------------------------------


def test_registration_yields_to_tk_between_batches(cache):
    from tradinglab.backtest import sandbox_feed

    # One disk-read chunk, more symbols than one registration batch, so
    # the split is unambiguously the registration batching.
    n = sandbox_feed._REGISTER_BATCH + 3
    assert n <= sandbox_feed._LOAD_BATCH
    symbols = [f"SYM{i:03d}" for i in range(n)]
    for sym in symbols:
        cache("yfinance", sym, "5m", _candles())
    app = _App(pinned=symbols)
    ctrl = _Controller()
    warmer = SandboxFeedWarmer(app=app, controller=ctrl)
    warmer.request()

    # First batch registered synchronously; the rest are deferred.
    assert len(ctrl.registered) == sandbox_feed._REGISTER_BATCH
    assert app.after_calls, "expected a deferred continuation"
    app.drain_after()
    assert len(ctrl.registered) == n


def test_cancel_stops_further_registration(cache):
    from tradinglab.backtest import sandbox_feed

    n = sandbox_feed._REGISTER_BATCH + 5
    assert n <= sandbox_feed._LOAD_BATCH
    symbols = [f"SYM{i:03d}" for i in range(n)]
    for sym in symbols:
        cache("yfinance", sym, "5m", _candles())
    app = _App(pinned=symbols)
    ctrl = _Controller()
    warmer = SandboxFeedWarmer(app=app, controller=ctrl)
    warmer.request()
    registered_before = len(ctrl.registered)

    warmer.cancel()
    app.drain_after()
    assert len(ctrl.registered) == registered_before
    assert warmer.request() == 0


def test_repaints_watchlist_and_scanner_when_the_warm_lands(cache):
    cache("yfinance", "AMD", "5m", _candles())
    app = _App(pinned=["AMD"])
    ctrl = _Controller()
    SandboxFeedWarmer(app=app, controller=ctrl).request()
    app.drain_after()
    assert app.watchlist_refreshes >= 1
    assert app.scanner_refreshes >= 1


def test_works_without_track_after(cache):
    """Headless harnesses have no Tk; registration must still complete."""
    from tradinglab.backtest import sandbox_feed

    n = sandbox_feed._REGISTER_BATCH + 2
    symbols = [f"SYM{i:03d}" for i in range(n)]
    for sym in symbols:
        cache("yfinance", sym, "5m", _candles())
    app = _App(pinned=symbols, with_track_after=False)
    ctrl = _Controller()
    SandboxFeedWarmer(app=app, controller=ctrl).request()
    assert len(ctrl.registered) == n


# ---------------------------------------------------------------------------
# The executor path — ``_await_future_on_tk`` hands over the RESOLVED VALUE
# ---------------------------------------------------------------------------


class _ExecutorApp(_App):
    """App seam that takes the real off-thread path.

    ``_await_future_on_tk`` invokes its callback with the future's
    *result*, not the future (see ``data/fetch_service``). A callback
    that assumed a future silently swallowed every batch and the warm
    registered nothing — caught only by smoke until this test existed.
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        import concurrent.futures as _cf

        self._fetch_executor = _cf.ThreadPoolExecutor(max_workers=2)
        self.awaited = 0

    def _await_future_on_tk(self, fut, on_done, *, poll_ms=5):  # noqa: ARG002
        self.awaited += 1
        try:
            result = fut.result(timeout=10)
        except Exception:  # noqa: BLE001
            result = None
        on_done(result)

    def shutdown(self) -> None:
        self._fetch_executor.shutdown(wait=True)


def test_executor_path_registers_symbols(cache):
    cache("yfinance", "AMD", "5m", _candles())
    cache("yfinance", "NVDA", "5m", _candles())
    app = _ExecutorApp(pinned=["AMD", "NVDA"])
    ctrl = _Controller()
    try:
        SandboxFeedWarmer(app=app, controller=ctrl).request()
        app.drain_after()
    finally:
        app.shutdown()
    assert app.awaited == 1
    assert sorted(s for s, _n, _e in ctrl.registered) == ["AMD", "NVDA"]


def test_executor_path_reports_completion(cache):
    cache("yfinance", "AMD", "5m", _candles())
    app = _ExecutorApp(pinned=["AMD"])
    ctrl = _Controller()
    warmer = SandboxFeedWarmer(app=app, controller=ctrl)
    try:
        warmer.request()
        app.drain_after()
    finally:
        app.shutdown()
    assert warmer.progress() == (1, 1)
    assert any("live on the replay clock" in m for m in app._status.messages)


def test_executor_path_survives_a_worker_failure(cache, monkeypatch):
    """``on_done(None)`` is the documented failure shape — don't crash."""
    from tradinglab import disk_cache

    def _boom(*a, **k):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(disk_cache, "load_window", _boom)
    app = _ExecutorApp(pinned=["AMD"])
    ctrl = _Controller()
    warmer = SandboxFeedWarmer(app=app, controller=ctrl)
    try:
        warmer.request()
        app.drain_after()
    finally:
        app.shutdown()
    assert ctrl.registered == []


def test_warm_never_calls_a_data_source(cache, monkeypatch):
    """Cache-only by contract — a warm must not hit the network."""
    from tradinglab.data import DATA_SOURCES

    called: list[Any] = []

    def _boom(*a, **k):
        called.append(a)
        raise AssertionError("feed warm must not fetch")

    monkeypatch.setitem(DATA_SOURCES, "yfinance", _boom)
    cache("yfinance", "AMD", "5m", _candles())
    app = _App(pinned=["AMD", "GHOST"])
    ctrl = _Controller()
    SandboxFeedWarmer(app=app, controller=ctrl).request()
    app.drain_after()
    assert called == []


# ---------------------------------------------------------------------------
# The "Auto" cache alias
#
# The disk cache is keyed by source name and "Auto" caches under the literal
# "Auto" — the key records no provider. "Auto" is also the shipped default
# chart source, so a trader's everyday history accumulates under
# ``Auto__SYM__5m.jsonl`` while a session pins a concrete vendor. Without the
# alias, symbols charted daily read as "never downloaded".
# ---------------------------------------------------------------------------


@pytest.fixture()
def auto_resolves_to(monkeypatch):
    def _set(name: str):
        monkeypatch.setattr(
            "tradinglab.data.auto_source.resolve_auto_source", lambda **k: name)
    return _set


def test_auto_is_a_candidate_when_it_resolves_to_the_pinned_source(auto_resolves_to):
    from tradinglab.backtest.sandbox_feed import source_candidates

    auto_resolves_to("yfinance")
    assert source_candidates("yfinance") == ["yfinance", "Auto"]


def test_auto_is_not_a_candidate_for_a_different_vendor(auto_resolves_to):
    """Auto's bars are some other vendor's — mixing tapes is the bug."""
    from tradinglab.backtest.sandbox_feed import source_candidates

    auto_resolves_to("alpaca")
    assert source_candidates("yfinance") == ["yfinance"]


def test_auto_pinned_does_not_duplicate_itself(auto_resolves_to):
    from tradinglab.backtest.sandbox_feed import source_candidates

    auto_resolves_to("yfinance")
    assert source_candidates("Auto") == ["Auto"]


def test_empty_source_has_no_candidates():
    from tradinglab.backtest.sandbox_feed import source_candidates

    assert source_candidates("") == []


def test_warm_recovers_bars_cached_under_auto(cache, auto_resolves_to):
    """The real-world case: charted on Auto, replayed on the resolved vendor."""
    auto_resolves_to("yfinance")
    cache("Auto", "GLD", "5m", _candles())      # only under the Auto key
    app = _App(pinned=["GLD"])
    ctrl = _Controller(data_source="yfinance")
    SandboxFeedWarmer(app=app, controller=ctrl).request()
    app.drain_after()
    assert [s for s, _n, _e in ctrl.registered] == ["GLD"]


def test_pinned_source_wins_over_the_auto_alias(cache, auto_resolves_to):
    auto_resolves_to("yfinance")
    cache("yfinance", "AMD", "5m", _candles(5, base=100.0))
    cache("Auto", "AMD", "5m", _candles(9, base=500.0))
    app = _App(pinned=["AMD"])
    ctrl = _Controller(data_source="yfinance")
    SandboxFeedWarmer(app=app, controller=ctrl).request()
    app.drain_after()
    assert ctrl.registered == [("AMD", 5, False)]


def test_warm_ignores_auto_bars_for_a_different_vendor(cache, auto_resolves_to):
    auto_resolves_to("alpaca")
    cache("Auto", "GLD", "5m", _candles())
    app = _App(pinned=["GLD"])
    ctrl = _Controller(data_source="yfinance")
    warmer = SandboxFeedWarmer(app=app, controller=ctrl)
    warmer.request()
    app.drain_after()
    assert ctrl.registered == []
    assert warmer.missing() == ["GLD"]


# ---------------------------------------------------------------------------
# has_cached_bars — the pre-flight probe
# ---------------------------------------------------------------------------


def test_has_cached_bars_finds_a_downloaded_symbol(cache):
    from tradinglab.backtest.sandbox_feed import has_cached_bars

    cache("yfinance", "AMD", "5m", _candles())
    assert has_cached_bars("AMD", sources=["yfinance"], interval="5m")
    assert not has_cached_bars("AMD", sources=["yfinance"], interval="1m")
    assert not has_cached_bars("GHOST", sources=["yfinance"], interval="5m")


def test_has_cached_bars_checks_every_candidate(cache):
    from tradinglab.backtest.sandbox_feed import has_cached_bars

    cache("Auto", "GLD", "5m", _candles())
    assert not has_cached_bars("GLD", sources=["yfinance"], interval="5m")
    assert has_cached_bars("GLD", sources=["yfinance", "Auto"], interval="5m")


def test_has_cached_bars_ignores_no_persist_sources(cache):
    from tradinglab import disk_cache
    from tradinglab.backtest.sandbox_feed import has_cached_bars

    cache("yfinance", "AMD", "5m", _candles())
    disk_cache.mark_no_persist("yfinance")
    try:
        assert not has_cached_bars("AMD", sources=["yfinance"], interval="5m")
    finally:
        disk_cache.clear_no_persist()
