"""Unit tests for ``PrefetchAppMixin`` live-mode submit/pump seam.

Exercised via a ``SimpleNamespace`` fake ``self`` (the established pattern for
ChartApp mixin methods): the live seam runs the fetch + worker-side merge on the
prefetch pool, then on the Tk thread stashes into the in-memory working set (when
the job's cache policy allows), feeds ``driver.complete``, and re-pumps.
"""
from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
from types import SimpleNamespace

import tradinglab.data.base as base
from tradinglab.data.prefetch import (
    CACHE_DISK_ONLY,
    CACHE_MEMORY_AND_DISK,
)
from tradinglab.data.prefetch.planner import FetchWindow
from tradinglab.data.prefetch.priority import FetchJob
from tradinglab.gui.prefetch_app import PrefetchAppMixin


class _Bar:
    def __init__(self, ts: float):
        self.date = datetime.fromtimestamp(ts, timezone.utc)


def _job(band_index=0, source="alpaca", symbol="AMD", interval="5m",
         tier_rank=10):
    return FetchJob(source=source, symbol=symbol, interval=interval,
                    band_index=band_index, tier_rank=tier_rank, interval_rank=0,
                    generation=0)


def _fake_app(*, window, policy=CACHE_MEMORY_AND_DISK, submit_ok=True,
              watchlist=()):
    calls: dict = {"complete": [], "stash": [], "pump": 0, "apply": None}
    wl_set = {str(s).strip().upper() for s in watchlist}

    def _submit_prefetch(fn):
        if not submit_ok:
            return None
        fut: concurrent.futures.Future = concurrent.futures.Future()
        fut.set_result(fn())          # run the worker body synchronously
        return fut

    def _await(fut, on_done):
        on_done(fut.result())

    def _apply(key, bars, fc, disk, stash, *, memory_allowed, stale_guard):
        calls["apply"] = dict(key=key, n=len(bars),
                              memory_allowed=memory_allowed,
                              stale_guard=stale_guard)
        return list(bars)             # merged == fetched (opaque here)

    driver = SimpleNamespace(
        scheduler=SimpleNamespace(
            window_for=lambda j: window,
            cache_policy_for=lambda j: policy,
        ),
        complete=lambda job, **kw: calls["complete"].append(kw),
        shadow=False,
        shadow_log=[],
    )
    fake = SimpleNamespace(
        _prefetch_driver=driver,
        _fetch_svc=SimpleNamespace(
            submit_prefetch=_submit_prefetch,
            apply_prefetch_result=_apply,
            _prefetch_futures={},
        ),
        _full_cache={},
        _stash_full_cache=lambda k, b: calls["stash"].append((k, len(b))),
        _await_future_on_tk=_await,
        _prefetch_pump=lambda: calls.__setitem__("pump", calls["pump"] + 1),
        _refresh_daily_synth_for_active_view=(
            lambda *, prefetched_symbol=None: calls.__setitem__(
                "synth", calls.get("synth", []) + [prefetched_symbol])),
        _apply_watchlist_snapshot_from_bars=(
            lambda t, src, itv, bars: calls.__setitem__(
                "wl_snap", calls.get("wl_snap", []) + [(t, src, itv, len(bars))])),
        _job_symbol_is_watchlisted=(
            lambda sym: str(sym or "").strip().upper() in wl_set),
    )
    return fake, calls


def _range_window(end=1000.0, limit=500):
    return FetchWindow(interval="5m", kind="range", end=end, limit=limit)


# ------------------------------------------------------------------- submit
def test_submit_window_none_completes_zero_and_pumps():
    fake, calls = _fake_app(window=None)
    PrefetchAppMixin._prefetch_submit(fake, _job())
    assert calls["complete"] == [{"bars_count": 0}]
    assert calls["pump"] == 1
    assert calls["apply"] is None          # no fetch attempted


def test_submit_live_fetch_merges_stashes_completes(monkeypatch):
    bars = [_Bar(1000.0), _Bar(1300.0)]
    monkeypatch.setattr(
        base, "fetch_page",
        lambda *a, **k: base.FetchPageResult(bars, "ok"),
    )
    fake, calls = _fake_app(window=_range_window(), policy=CACHE_MEMORY_AND_DISK)
    PrefetchAppMixin._prefetch_submit(fake, _job(band_index=0))
    # worker merged with memory_allowed=False + stale_guard=True (band 0)
    assert calls["apply"]["memory_allowed"] is False
    assert calls["apply"]["stale_guard"] is True
    # memory policy → Tk stash of the merged series
    assert calls["stash"] == [(("alpaca", "AMD", "5m"), 2)]
    # driver.complete carries the page count + oldest_ts + no error
    (kw,) = calls["complete"]
    assert kw["bars_count"] == 2 and kw["error"] is None
    assert kw["oldest_ts"] == 1000.0
    assert calls["pump"] == 1
    # active + intraday warm → the daily-synth today-bar re-renders (Issue 1).
    assert calls.get("synth") == ["AMD"]


def test_submit_watchlist_tier_updates_snapshot(monkeypatch):
    """A watchlist-tier completion repopulates Last/Change via the snapshot seam
    (end-to-end wiring the reactive preloads used to own)."""
    from tradinglab.data.prefetch.tiers import TIER_FOCUSED_WL
    bars = [_Bar(1000.0), _Bar(1300.0), _Bar(1600.0)]
    monkeypatch.setattr(
        base, "fetch_page",
        lambda *a, **k: base.FetchPageResult(bars, "ok"),
    )
    # WL tiers are disk-only (no memory stash), so use CACHE_DISK_ONLY.
    fake, calls = _fake_app(window=_range_window(), policy=CACHE_DISK_ONLY,
                            watchlist=("NVDA",))
    PrefetchAppMixin._prefetch_submit(
        fake, _job(band_index=0, symbol="NVDA", tier_rank=TIER_FOCUSED_WL))
    # the scheduler's WL-tier completion drove the snapshot updater.
    assert calls.get("wl_snap") == [("NVDA", "alpaca", "5m", 3)]
    assert calls["stash"] == []                     # disk-only → no memory stash


def test_submit_active_tier_pinned_symbol_updates_snapshot(monkeypatch):
    """Regression (qw-active-overlap-snapshot): the active/compare symbol that is
    ALSO a pinned watchlist member must still populate its Last/Change row.

    ``expand_all`` dedups AMD (the default startup chart symbol AND the first
    Default-watchlist row) to TIER_ACTIVE, so no watchlist-tier job is ever
    emitted for it. The completion handler must gate on watchlist MEMBERSHIP,
    not tier — else AMD's row is blank on launch while NVDA/INTC/… populate.
    """
    from tradinglab.data.prefetch.tiers import TIER_ACTIVE
    bars = [_Bar(1000.0), _Bar(1300.0), _Bar(1600.0)]
    monkeypatch.setattr(
        base, "fetch_page",
        lambda *a, **k: base.FetchPageResult(bars, "ok"),
    )
    fake, calls = _fake_app(window=_range_window(), policy=CACHE_MEMORY_AND_DISK,
                            watchlist=("AMD",))
    PrefetchAppMixin._prefetch_submit(
        fake, _job(band_index=0, symbol="AMD", tier_rank=TIER_ACTIVE))
    # active-tier completion for a pinned symbol still drives the snapshot.
    assert calls.get("wl_snap") == [("AMD", "alpaca", "5m", 3)]


def test_submit_active_tier_unpinned_symbol_skips_snapshot(monkeypatch):
    """A non-watchlisted active symbol must NOT touch the watchlist snapshot —
    the membership gate guards against spraying a row for every fetched symbol.
    """
    from tradinglab.data.prefetch.tiers import TIER_ACTIVE
    bars = [_Bar(1000.0), _Bar(1300.0)]
    monkeypatch.setattr(
        base, "fetch_page",
        lambda *a, **k: base.FetchPageResult(bars, "ok"),
    )
    fake, calls = _fake_app(window=_range_window(), policy=CACHE_MEMORY_AND_DISK,
                            watchlist=("AMD",))
    PrefetchAppMixin._prefetch_submit(
        fake, _job(band_index=0, symbol="TSLA", tier_rank=TIER_ACTIVE))
    assert calls.get("wl_snap") is None


def test_submit_disk_only_deep_band_no_stash(monkeypatch):
    bars = [_Bar(500.0), _Bar(800.0)]
    monkeypatch.setattr(
        base, "fetch_page",
        lambda *a, **k: base.FetchPageResult(bars, "ok"),
    )
    fake, calls = _fake_app(window=_range_window(end=900.0),
                            policy=CACHE_DISK_ONLY)
    PrefetchAppMixin._prefetch_submit(fake, _job(band_index=2))
    assert calls["apply"]["stale_guard"] is False   # deep band bypasses guard
    assert calls["stash"] == []                     # disk-only → no memory stash
    (kw,) = calls["complete"]
    assert kw["bars_count"] == 2 and kw["oldest_ts"] == 500.0


def test_submit_error_completes_with_error_and_retry_after(monkeypatch):
    boom = RuntimeError("429 too many requests")
    monkeypatch.setattr(
        base, "fetch_page",
        lambda *a, **k: base.FetchPageResult(None, "error", error=boom,
                                             retry_after_s=4.0),
    )
    fake, calls = _fake_app(window=_range_window())
    PrefetchAppMixin._prefetch_submit(fake, _job())
    (kw,) = calls["complete"]
    assert kw["error"] is boom and kw["retry_after_s"] == 4.0
    assert kw["bars_count"] == 0
    assert calls["stash"] == []


def test_submit_returns_none_future_completes_zero(monkeypatch):
    monkeypatch.setattr(
        base, "fetch_page",
        lambda *a, **k: base.FetchPageResult([_Bar(1.0)], "ok"),
    )
    fake, calls = _fake_app(window=_range_window(), submit_ok=False)
    PrefetchAppMixin._prefetch_submit(fake, _job())
    assert calls["complete"] == [{"bars_count": 0}]
    assert calls["stash"] == []


# --------------------------------------------------------------------- pump
def _pump_app(retry_after):
    calls: dict = {"after": []}
    driver = SimpleNamespace(pump=lambda: retry_after)
    fake = SimpleNamespace(
        _prefetch_driver=driver,
        _track_after=lambda ms, fn: calls["after"].append(ms),
        _prefetch_pump=lambda: None,   # the reschedule callback (identity only)
    )
    return fake, calls


def test_pump_idle_does_not_reschedule():
    fake, calls = _pump_app(None)
    PrefetchAppMixin._prefetch_pump(fake)
    assert calls["after"] == []


def test_pump_hit_bound_reschedules_immediately():
    fake, calls = _pump_app(0.0)
    PrefetchAppMixin._prefetch_pump(fake)
    assert calls["after"] == [1]        # 0.0 → re-pump next tick (1 ms)


def test_pump_rate_gated_reschedules_at_delay():
    fake, calls = _pump_app(0.75)
    PrefetchAppMixin._prefetch_pump(fake)
    assert calls["after"] == [750]      # 0.75s → 750 ms


def test_pump_none_driver_is_noop():
    fake = SimpleNamespace(_prefetch_driver=None)
    PrefetchAppMixin._prefetch_pump(fake)   # must not raise
