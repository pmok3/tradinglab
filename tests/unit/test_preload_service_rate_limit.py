"""Tests for the inter-op rate-limit sleep in ``preload_universe``.

Pins the skeptic-found fix: ``service.py`` originally only slept
between retries inside ``_run_one``. The happy path (a single-call
success) returned with zero delay, which at full-exchange scale
(2,000+ symbols × multiple intervals) sustained ~5,000 unbroken
yfinance hits and tripped the CDN throttle.

After the fix, the main ``preload_universe`` loop sleeps
``rate_limit_s`` after every ``fetched`` outcome — but NOT after
``l1_hit`` / ``disk_hit`` (local, no network) nor ``failed`` /
``cancelled`` (already consumed its retry budget or aborted).
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import List, Optional, Tuple

from tradinglab.models import Candle
from tradinglab.preload.service import ProgressEvent, preload_universe


def _candle() -> Candle:
    return Candle(
        date=datetime(2024, 1, 2, 9, 30),
        open=1.0, high=2.0, low=0.5, close=1.5, volume=100,
    )


class _CountingSleep:
    """``sleep_fn`` that records each call and is otherwise inert."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, _evt: threading.Event, secs: float) -> None:
        self.calls.append(secs)


def _no_l1(_src: str, _sym: str, _itv: str) -> list[Candle] | None:
    return None


def _merge_newer_wins(
    _old: list[Candle] | None, new: list[Candle] | None,
) -> list[Candle]:
    return list(new or [])


class _InMemDisk:
    def __init__(self) -> None:
        self.store: dict = {}

    def load(self, src, sym, itv):
        return list(self.store.get((src, sym, itv), []))

    def save(self, src, sym, itv, candles):
        self.store[(src, sym, itv)] = list(candles)


def _fetcher_always_returns_one() -> tuple[callable, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []

    def fetch(sym, itv):
        calls.append((sym, itv))
        return [_candle()]

    return fetch, calls


# ---------------------------------------------------------------------------
# Happy-path: every successful fetch must be followed by a sleep
# ---------------------------------------------------------------------------

def test_rate_limit_sleep_fires_after_every_fetched_outcome() -> None:
    """3 symbols × 1 interval that all succeed → 3 sleeps (one per
    fetched op). The pre-fix behaviour was 0 sleeps because the retry
    loop's between-attempts sleep never fires on a first-try success.
    """
    fetch, _calls = _fetcher_always_returns_one()
    sleep = _CountingSleep()
    disk = _InMemDisk()
    events: list[ProgressEvent] = []

    result = preload_universe(
        ["AAPL", "MSFT", "GOOG"], ["5m"],
        source_name="yfinance",
        fetcher=fetch,
        cache_load=disk.load,
        cache_save=disk.save,
        merge=_merge_newer_wins,
        cancel_event=threading.Event(),
        progress_cb=events.append,
        l1_check=_no_l1,
        sleep_fn=sleep,
        rate_limit_s=0.25,
        max_retries=3,
    )

    fetched_events = [e for e in events
                      if e.kind == "symbol" and e.status == "fetched"]
    assert len(fetched_events) == 3
    assert sleep.calls == [0.25, 0.25, 0.25], (
        f"expected 3 sleeps of 0.25 s, got {sleep.calls}"
    )
    assert not result.cancelled


def test_rate_limit_sleep_uses_caller_supplied_value() -> None:
    """The sleep duration must be the ``rate_limit_s`` arg passed by
    the caller (the dialog), not a service-baked constant."""
    fetch, _ = _fetcher_always_returns_one()
    sleep = _CountingSleep()
    disk = _InMemDisk()

    preload_universe(
        ["AAPL"], ["5m"],
        source_name="yfinance",
        fetcher=fetch,
        cache_load=disk.load,
        cache_save=disk.save,
        merge=_merge_newer_wins,
        cancel_event=threading.Event(),
        progress_cb=lambda _e: None,
        l1_check=_no_l1,
        sleep_fn=sleep,
        rate_limit_s=1.75,
        max_retries=3,
    )
    assert sleep.calls == [1.75]


# ---------------------------------------------------------------------------
# Cache-hit paths must NOT pay the rate-limit cost
# ---------------------------------------------------------------------------

def test_l1_hit_does_not_incur_rate_limit_sleep() -> None:
    """L1 hits never touch the network — they must skip the
    post-success sleep entirely, otherwise a fully-cached run would
    incur N × rate_limit_s of pointless wait."""
    sleep = _CountingSleep()
    disk = _InMemDisk()

    def l1_always_hit(_src, _sym, _itv):
        return [_candle(), _candle()]

    fetch_calls = []

    def fetch_never(*a):
        fetch_calls.append(a)
        raise AssertionError("fetcher should not be called on L1 hit")

    preload_universe(
        ["AAPL", "MSFT"], ["5m"],
        source_name="yfinance",
        fetcher=fetch_never,
        cache_load=disk.load,
        cache_save=disk.save,
        merge=_merge_newer_wins,
        cancel_event=threading.Event(),
        progress_cb=lambda _e: None,
        l1_check=l1_always_hit,
        sleep_fn=sleep,
        rate_limit_s=10.0,
        max_retries=3,
    )

    assert fetch_calls == []
    assert sleep.calls == [], (
        f"L1 hits must not sleep; got {sleep.calls}"
    )


def test_disk_hit_does_not_incur_rate_limit_sleep() -> None:
    """Disk hits also never touch the network — same reasoning as
    L1 hit. Sleep must remain zero."""
    sleep = _CountingSleep()
    disk = _InMemDisk()
    # Prime disk so the load returns non-empty for both symbols.
    disk.store[("yfinance", "AAPL", "5m")] = [_candle()]
    disk.store[("yfinance", "MSFT", "5m")] = [_candle()]

    fetch_calls = []

    def fetch_never(*a):
        fetch_calls.append(a)
        raise AssertionError("fetcher should not be called on disk hit")

    preload_universe(
        ["AAPL", "MSFT"], ["5m"],
        source_name="yfinance",
        fetcher=fetch_never,
        cache_load=disk.load,
        cache_save=disk.save,
        merge=_merge_newer_wins,
        cancel_event=threading.Event(),
        progress_cb=lambda _e: None,
        l1_check=_no_l1,
        sleep_fn=sleep,
        rate_limit_s=10.0,
        max_retries=3,
    )

    assert fetch_calls == []
    assert sleep.calls == []


# ---------------------------------------------------------------------------
# Failed paths must NOT add an extra inter-op sleep on top of the
# retry-loop sleeps they already paid
# ---------------------------------------------------------------------------

def test_failed_outcome_does_not_add_post_op_sleep() -> None:
    """A symbol that exhausts its retry budget already slept
    ``max_retries - 1`` times inside ``_run_one``. The outer loop must
    NOT add another post-op sleep on top of that — the symbol failed,
    we move on.
    """
    sleep = _CountingSleep()
    disk = _InMemDisk()

    def fetch_always_raises(_sym, _itv):
        raise OSError("upstream down")

    preload_universe(
        ["AAPL"], ["5m"],
        source_name="yfinance",
        fetcher=fetch_always_raises,
        cache_load=disk.load,
        cache_save=disk.save,
        merge=_merge_newer_wins,
        cancel_event=threading.Event(),
        progress_cb=lambda _e: None,
        l1_check=_no_l1,
        sleep_fn=sleep,
        rate_limit_s=0.5,
        max_retries=3,
    )

    # _run_one slept (max_retries - 1) = 2 times between its 3 attempts.
    # The outer loop must add ZERO further sleeps on the failed path.
    assert sleep.calls == [0.5, 0.5], (
        f"expected exactly the 2 between-retry sleeps, got {sleep.calls}"
    )
