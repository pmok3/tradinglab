"""Unit tests for ``tradinglab.preload.service`` and ``preload.manifest``.

Covers the L1 → disk → fetch ladder, retry-budget exhaustion,
cancellation-between-retries, plus a handful of pure manifest helpers
(``_safe_filename``, ``coverage_for_date`` empty-manifest, and
``build_from_loaded`` empty-interval pruning).

All I/O is faked: no real disk_cache, no network, no Tk.
"""

from __future__ import annotations

import datetime as _dt
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pytest

from tradinglab.models import Candle
from tradinglab.preload import manifest as _manifest
from tradinglab.preload.service import (
    IntervalOutcome,
    ProgressEvent,
    _run_one,
    preload_universe,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _candle(ts: datetime = datetime(2024, 1, 2, 9, 30)) -> Candle:
    return Candle(date=ts, open=1.0, high=2.0, low=0.5, close=1.5, volume=100)


@dataclass
class FakeFetcher:
    """Deterministic stand-in for the live yfinance fetcher.

    Behavior is selected by the constructor:

    * ``returns``: fixed list returned on every call (default: one candle).
    * ``raises``: exception raised on every call (e.g. ``IOError``).
    * ``side_effect``: callable ``(sym, itv, call_no)`` invoked **before**
      the raise/return decision — used by the cancellation test to flip
      a ``threading.Event`` mid-flight.
    """

    returns: list[Candle] | None = None
    raises: BaseException | None = None
    side_effect: Callable[[str, str, int], None] | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, sym: str, itv: str) -> list[Candle] | None:
        self.calls.append((sym, itv))
        if self.side_effect is not None:
            self.side_effect(sym, itv, len(self.calls))
        if self.raises is not None:
            raise self.raises
        return self.returns


@dataclass
class FakeDiskCache:
    """In-memory stand-in for ``tradinglab.disk_cache``.

    Tracks every ``load`` / ``save`` call so tests can assert ladder
    semantics. ``prefill`` seeds the store with ``(source, sym, itv) ->
    candles`` entries to simulate disk-cache hits.
    """

    prefill: dict[tuple[str, str, str], list[Candle]] = field(
        default_factory=dict)
    reads: list[tuple[str, str, str]] = field(default_factory=list)
    saves: list[tuple[str, str, str, list[Candle]]] = field(
        default_factory=list)
    _store: dict[tuple[str, str, str], list[Candle]] = field(init=False)

    def __post_init__(self) -> None:
        self._store = {k: list(v) for k, v in self.prefill.items()}

    def load(
        self, source: str, sym: str, itv: str,
    ) -> list[Candle] | None:
        self.reads.append((source, sym, itv))
        return list(self._store.get((source, sym, itv), []))

    def save(
        self, source: str, sym: str, itv: str, candles: list[Candle],
    ) -> None:
        self.saves.append((source, sym, itv, list(candles)))
        self._store[(source, sym, itv)] = list(candles)


def _no_sleep(_evt: threading.Event, _s: float) -> None:
    """Deterministic ``sleep_fn`` — skip the rate-limit gap entirely."""
    return None


def _newer_wins_merge(
    old: list[Candle] | None, new: list[Candle] | None,
) -> list[Candle]:
    """Simplest valid merge for the service contract: just take ``new``.

    Production uses a timestamp-keyed dedupe; the service only needs the
    callable to return a non-empty list when ``new`` is non-empty.
    """
    return list(new or [])


# ---------------------------------------------------------------------------
# 1. _run_one ladder: L1 -> disk -> fetch
# ---------------------------------------------------------------------------


def _call_run_one(
    *,
    fetcher: FakeFetcher,
    disk: FakeDiskCache,
    l1_check: Callable[[str, str, str], list[Candle] | None] | None = None,
    cancel: threading.Event | None = None,
    max_retries: int = 3,
) -> IntervalOutcome:
    return _run_one(
        "AAPL", "5m",
        source_name="yfinance",
        fetcher=fetcher,
        cache_load=disk.load,
        cache_save=disk.save,
        merge=_newer_wins_merge,
        cancel_event=cancel or threading.Event(),
        l1_check=l1_check,
        sleep_fn=_no_sleep,
        rate_limit_s=0.0,
        max_retries=max_retries,
    )


def test_run_one_l1_disk_fetch_ladder() -> None:
    """Three sub-cases of the cache ladder, sharing a hand-rolled
    ``FakeFetcher`` / ``FakeDiskCache`` so we can assert call counts.
    """
    # ---- Sub-case 1: L1 hit -> no disk read, no fetch.
    fetcher_a = FakeFetcher(returns=[_candle()])
    disk_a = FakeDiskCache()
    l1_hit = [_candle(), _candle(datetime(2024, 1, 2, 9, 35))]
    l1_calls: list[tuple[str, str, str]] = []

    def l1_check_a(src: str, sym: str, itv: str) -> list[Candle] | None:
        l1_calls.append((src, sym, itv))
        return l1_hit

    outcome_a = _call_run_one(
        fetcher=fetcher_a, disk=disk_a, l1_check=l1_check_a)
    assert outcome_a.status == "l1_hit"
    assert outcome_a.bars == 2
    assert l1_calls == [("yfinance", "AAPL", "5m")]
    assert disk_a.reads == []      # never touched disk
    assert disk_a.saves == []
    assert fetcher_a.calls == []   # never reached fetcher

    # ---- Sub-case 2: L1 miss + disk hit -> one disk read, no fetch.
    fetcher_b = FakeFetcher(returns=[_candle()])
    disk_b = FakeDiskCache(prefill={
        ("yfinance", "AAPL", "5m"): [_candle()],
    })

    def l1_miss(src: str, sym: str, itv: str) -> list[Candle] | None:
        return None  # L1 miss

    outcome_b = _call_run_one(
        fetcher=fetcher_b, disk=disk_b, l1_check=l1_miss)
    assert outcome_b.status == "disk_hit"
    assert outcome_b.bars == 1
    assert len(disk_b.reads) == 1
    assert disk_b.saves == []
    assert fetcher_b.calls == []   # never reached fetcher

    # ---- Sub-case 3: L1 miss + disk miss -> one fetch, one save.
    fetched_candles = [
        _candle(datetime(2024, 1, 2, 9, 30)),
        _candle(datetime(2024, 1, 2, 9, 35)),
    ]
    fetcher_c = FakeFetcher(returns=fetched_candles)
    disk_c = FakeDiskCache()      # empty disk

    outcome_c = _call_run_one(
        fetcher=fetcher_c, disk=disk_c, l1_check=l1_miss)
    assert outcome_c.status == "fetched"
    assert outcome_c.bars == 2
    assert len(fetcher_c.calls) == 1
    assert len(disk_c.saves) == 1
    # Saved payload is what the merge produced (here: just `new`).
    saved_source, saved_sym, saved_itv, saved_payload = disk_c.saves[0]
    assert (saved_source, saved_sym, saved_itv) == (
        "yfinance", "AAPL", "5m")
    assert len(saved_payload) == 2


# ---------------------------------------------------------------------------
# 2. Retry budget exhaustion
# ---------------------------------------------------------------------------


def test_retry_budget_exhaustion() -> None:
    """Fetcher that always raises ``IOError`` must be called exactly
    ``max_retries`` times, the symbol must end up in ``failed()``, and
    the ``loaded_per_symbol`` entry for it must be the empty tuple
    (loaded-count == 0).
    """
    fetcher = FakeFetcher(raises=OSError("network down"))
    disk = FakeDiskCache()  # empty -> ladder falls through to fetch
    events: list[ProgressEvent] = []

    result = preload_universe(
        ["AAPL"], ["5m"],
        source_name="yfinance",
        fetcher=fetcher,
        cache_load=disk.load,
        cache_save=disk.save,
        merge=_newer_wins_merge,
        cancel_event=threading.Event(),
        progress_cb=events.append,
        l1_check=None,
        sleep_fn=_no_sleep,
        rate_limit_s=0.0,
        max_retries=3,
    )

    assert len(fetcher.calls) == 3
    assert result.cancelled is False

    failed = result.failed()
    assert len(failed) == 1
    sym, itv, err = failed[0]
    assert (sym, itv) == ("AAPL", "5m")
    assert "IOError" in err or "network down" in err

    loaded = result.loaded_per_symbol()
    assert loaded["AAPL"] == ()        # no intervals loaded
    assert len(loaded["AAPL"]) == 0    # belt-and-suspenders: count is 0


# ---------------------------------------------------------------------------
# 3. Cancellation between retries
# ---------------------------------------------------------------------------


def test_cancellation_between_retries() -> None:
    """If the cancel event flips after the first failed fetch, the
    second-iteration cancel check must short-circuit and the outcome
    must be ``cancelled`` (NOT ``failed``).
    """
    cancel = threading.Event()

    def flip_on_first_call(_sym: str, _itv: str, call_no: int) -> None:
        if call_no == 1:
            cancel.set()

    fetcher = FakeFetcher(
        raises=OSError("transient"),
        side_effect=flip_on_first_call,
    )
    disk = FakeDiskCache()  # empty -> falls through to fetch
    events: list[ProgressEvent] = []

    result = preload_universe(
        ["AAPL"], ["5m"],
        source_name="yfinance",
        fetcher=fetcher,
        cache_load=disk.load,
        cache_save=disk.save,
        merge=_newer_wins_merge,
        cancel_event=cancel,
        progress_cb=events.append,
        l1_check=None,
        sleep_fn=_no_sleep,
        rate_limit_s=0.0,
        max_retries=3,
    )

    # Fetcher hit exactly once: attempt 0 raised + flipped cancel, so
    # attempt 1 hits the cancel-event check first and returns early.
    assert len(fetcher.calls) == 1

    # The outcome must be reported as cancelled, not failed. The
    # spec distinguishes user-cancel from data-source failure so the
    # GUI can color the symbol grey vs. red.
    assert result.cancelled is True
    assert result.failed() == ()       # nothing in the failed bucket
    assert len(result.per_symbol) == 1
    interval_outcomes = result.per_symbol[0].intervals
    assert len(interval_outcomes) == 1
    assert interval_outcomes[0].status == "cancelled"


# ---------------------------------------------------------------------------
# 4. Manifest helpers: _safe_filename / coverage / build_from_loaded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uid, expected",
    [
        # Each reserved char individually maps to "__".
        ("AAPL/MSFT:1m?", "AAPL__MSFT__1m__"),
        ('foo<bar>baz', "foo__bar__baz"),
        ('quote"in"name', "quote__in__name"),
        (r"path\to\stuff", "path__to__stuff"),
        ("pipe|wild*", "pipe__wild__"),
        # Stable IDs from the real codepath round-trip unchanged.
        ("watchlist:Mega Caps", "watchlist__Mega Caps"),
        ("sp500", "sp500"),  # already-safe IDs are untouched
    ],
)
def test_safe_filename_munges_reserved_chars(uid: str, expected: str) -> None:
    """``_safe_filename`` replaces each character in ``<>:"/\\|?*``
    with ``__`` and leaves everything else (including spaces) alone.
    """
    assert _manifest._safe_filename(uid) == expected


def test_coverage_for_date_empty_manifest_returns_zero_zero() -> None:
    """A manifest with zero symbols cannot have any covered or missing
    entries — ``coverage_for_date`` must return a report with both
    counts equal to zero, regardless of the target date.
    """
    empty = _manifest.UniverseManifest(
        id="empty",
        name="Empty",
        kind="basket",
        source="yfinance",
        intervals=("5m",),
        symbols=(),
        prepared_at=0.0,
    )

    report = _manifest.coverage_for_date(
        empty, _dt.date(2024, 3, 15), "5m")

    assert report.covered == ()
    assert report.missing == ()
    assert report.covered_count == 0
    assert report.total_count == 0
    # The (0, 0) shape promised by the audit:
    assert (report.covered_count, report.total_count) == (0, 0)


def test_build_from_loaded_drops_empty_interval_symbols() -> None:
    """``build_from_loaded`` must drop symbols whose loaded-interval
    tuple is empty — strict-offline gating cannot let a symbol with
    no actual bars sneak into the manifest.
    """
    per_symbol: dict[str, tuple[str, ...]] = {
        "AAPL": ("5m", "1d"),
        "MSFT": (),            # loaded nothing -> must be dropped
        "NVDA": ("5m",),
        "TSLA": (),            # loaded nothing -> must be dropped
    }

    manifest = _manifest.build_from_loaded(
        uid="test",
        name="Test Universe",
        kind="basket",
        source="yfinance",
        intervals=("5m", "1d"),
        per_symbol=per_symbol,
    )

    symbols = manifest.symbol_set()
    assert symbols == {"AAPL", "NVDA"}
    assert "MSFT" not in symbols
    assert "TSLA" not in symbols
    # Loaded-symbol order is deterministic (sorted) per the builder.
    assert [e.symbol for e in manifest.symbols] == ["AAPL", "NVDA"]
    # Carried metadata is preserved.
    assert manifest.id == "test"
    assert manifest.intervals == ("5m", "1d")
    assert manifest.source == "yfinance"
