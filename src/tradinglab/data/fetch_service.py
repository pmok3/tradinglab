"""Thread-pool ownership and background fetch orchestration."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, MutableMapping
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from ..core import reference_data as _reference_data
from ..core.bars import Bars
from ..models import Candle
from .base import DATA_SOURCES

CacheKey = tuple[str, str, str]
StatusFn = Callable[[str], None]
StashFn = Callable[[CacheKey, list[Candle]], None]
PrefetchArrivalFn = Callable[[CacheKey, list[Candle]], None]
WorkerInboxFn = Callable[[str, Any], None]


class FetchService:
    """Own thread pools and background fetch/prefetch orchestration."""

    def __init__(self, worker_count: int = 4):
        self._executor: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="tradinglab",
        )
        self._fetch_executor: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="tradinglab-fetch",
        )
        self._prefetch_inflight: set[CacheKey] = set()
        self._prefetch_futures: dict[CacheKey, Future[list[Candle]]] = {}
        self._poll_job: str | None = None
        self._reload_job: str | None = None
        self._poll_retry_count = 0
        self._poll_retry_expected_min_ts: float | None = None

    @staticmethod
    def _status(status_fn: StatusFn | None, message: str) -> None:
        if status_fn is None:
            return
        try:
            status_fn(message)
        except Exception:  # noqa: BLE001
            pass

    def prefetch(
        self,
        source: str,
        ticker: str,
        interval: str,
        full_cache: MutableMapping[CacheKey, list[Candle]],
        disk_cache_mod: Any,
        stash_fn: StashFn,
        *,
        cache_is_stale: Callable[[list[Candle], str], bool],
        on_arrival: PrefetchArrivalFn | None = None,
        status_fn: StatusFn | None = None,
        force: bool = False,
        inflight_max: int = 4,
    ) -> Future[list[Candle]] | None:
        """Warm ``full_cache`` with ``(source, ticker, interval)`` off-thread."""
        executor = self._executor
        if executor is None:
            return None
        source = str(source or "")
        ticker = str(ticker or "").strip().upper()
        interval = str(interval or "")
        if not source or not ticker or not interval:
            return None
        key = (source, ticker, interval)
        if not force:
            existing = full_cache.get(key)
            if existing and not cache_is_stale(existing, interval):
                return None
        if key in self._prefetch_inflight:
            return None
        if len(self._prefetch_inflight) >= max(0, int(inflight_max)):
            return None
        if key not in full_cache:
            try:
                disk_bars = disk_cache_mod.load(*key) or []
            except Exception:  # noqa: BLE001
                disk_bars = []
            if disk_bars:
                try:
                    stash_fn(key, disk_bars)
                except Exception:  # noqa: BLE001
                    pass
        fetcher = DATA_SOURCES.get(source)
        if fetcher is None:
            return None
        self._prefetch_inflight.add(key)
        self._status(status_fn, f"Prefetch start: {ticker}/{interval}")

        def _work() -> list[Candle]:
            try:
                return fetcher(ticker, interval) or []
            except Exception:  # noqa: BLE001
                return []

        def _done(fut: Future[list[Candle]]) -> None:
            try:
                fetched = fut.result() or []
            except Exception:  # noqa: BLE001
                fetched = []
            if on_arrival is None:
                self._prefetch_inflight.discard(key)
                self._prefetch_futures.pop(key, None)
                return
            try:
                on_arrival(key, fetched)
            except Exception:  # noqa: BLE001
                self._prefetch_inflight.discard(key)
                self._prefetch_futures.pop(key, None)

        try:
            fut = executor.submit(_work)
            self._prefetch_futures[key] = fut
            fut.add_done_callback(_done)
            return fut
        except Exception:  # noqa: BLE001
            self._prefetch_inflight.discard(key)
            self._prefetch_futures.pop(key, None)
            return None

    def apply_prefetch_result(
        self,
        key: CacheKey,
        fetched: list[Candle],
        full_cache: Mapping[CacheKey, list[Candle]],
        disk_cache_mod: Any,
        stash_fn: StashFn,
        *,
        status_fn: StatusFn | None = None,
    ) -> None:
        """Merge a completed prefetch onto the Tk-thread-owned cache."""
        ticker = key[1]
        interval = key[2]
        self._prefetch_inflight.discard(key)
        self._prefetch_futures.pop(key, None)
        if not fetched:
            self._status(
                status_fn,
                f"Prefetch empty: {ticker}/{interval} (fetcher returned no bars)",
            )
            return
        try:
            current = full_cache.get(key)
            if current and fetched:
                try:
                    cur_last = current[-1].date.timestamp()
                    new_last = fetched[-1].date.timestamp()
                    if cur_last > new_last:
                        self._status(
                            status_fn,
                            f"Prefetch skipped (stale-guard): {ticker}/{interval}",
                        )
                        return
                except Exception:  # noqa: BLE001
                    pass
            merged = disk_cache_mod.merge_candles(disk_cache_mod.load(*key), fetched)
            if current:
                merged = disk_cache_mod.merge_candles(current, merged)
            stash_fn(key, merged)
            try:
                disk_cache_mod.save(*key, merged)
            except Exception:  # noqa: BLE001
                pass
            first = merged[0].date if merged else None
            last = merged[-1].date if merged else None
            self._status(
                status_fn,
                f"Prefetch done: {ticker}/{interval} ({len(merged)} bars, {first} → {last})",
            )
        except Exception:  # noqa: BLE001
            pass

    def prefetch_compare(
        self,
        ticker: str,
        interval: str,
        *,
        prefetch_fn: Callable[..., Any],
        force: bool = False,
    ) -> None:
        """Normalize a compare symbol and delegate to ``prefetch_fn``."""
        raw = str(ticker or "").strip().upper()
        if not raw:
            return
        prefetch_fn(raw, interval, force=force)

    def fetch_reference(
        self,
        source: str,
        symbol: str,
        interval: str,
        *,
        full_cache: Mapping[CacheKey, list[Candle]],
    ) -> None:
        """Schedule a background fetch of a reference symbol's bars."""
        if not source or not symbol or not interval:
            return
        cached = full_cache.get((source, symbol, interval))
        if cached:
            try:
                _reference_data.set_reference_bars(
                    source,
                    symbol,
                    interval,
                    Bars.from_candles(cached),
                )
            except Exception:  # noqa: BLE001
                _reference_data.mark_fetch_failed(source, symbol, interval)
            return
        executor = self._executor
        if executor is None:
            _reference_data.mark_fetch_failed(source, symbol, interval)
            return
        fetcher = DATA_SOURCES.get(source)
        if fetcher is None:
            _reference_data.mark_fetch_failed(source, symbol, interval)
            return

        def _work() -> list[Candle]:
            try:
                return fetcher(symbol, interval) or []
            except Exception:  # noqa: BLE001
                return []

        def _done(fut: Future[list[Candle]]) -> None:
            try:
                candles = fut.result() or []
            except Exception:  # noqa: BLE001
                candles = []
            if not candles:
                try:
                    _reference_data.mark_fetch_failed(source, symbol, interval)
                except Exception:  # noqa: BLE001
                    pass
                return
            try:
                _reference_data.set_reference_bars(
                    source,
                    symbol,
                    interval,
                    Bars.from_candles(candles),
                )
            except Exception:  # noqa: BLE001
                try:
                    _reference_data.mark_fetch_failed(source, symbol, interval)
                except Exception:  # noqa: BLE001
                    pass

        try:
            fut = executor.submit(_work)
            fut.add_done_callback(_done)
        except Exception:  # noqa: BLE001
            try:
                _reference_data.mark_fetch_failed(source, symbol, interval)
            except Exception:  # noqa: BLE001
                pass

    def on_reference_data_arrived(self, *, worker_inbox_fn: WorkerInboxFn) -> None:
        """Queue a reference-data arrival marker for the Tk-thread inbox."""
        try:
            worker_inbox_fn("reference", None)
        except Exception:  # noqa: BLE001
            pass

    def prefetch_companion_intervals(
        self,
        tickers: Iterable[str],
        *,
        active_interval: str,
        all_intervals: Iterable[str],
        prefetch_fn: Callable[..., Any],
    ) -> None:
        """Warm companion intervals for each non-empty unique ticker."""
        seen: set[str] = set()
        for raw in tickers:
            if not raw:
                continue
            ticker = str(raw).strip().upper()
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            for interval in all_intervals:
                if interval == active_interval:
                    continue
                try:
                    prefetch_fn(ticker, interval)
                except Exception:  # noqa: BLE001
                    pass

    def await_future_on_tk(
        self,
        fut: Future[Any],
        on_done: Callable[[Any], None],
        *,
        track_after: Callable[..., Any],
        poll_ms: int = 5,
    ) -> None:
        """Poll ``fut`` from the Tk thread via ``track_after``.

        ``poll_ms`` defaults to 5 ms — minimum useful Tk-event-loop
        resolution. Lower latency = ticker-switch UI feels snappier
        (saves ~15 ms per cache-miss switch vs the prior 20 ms
        default). The trade-off is more CPU spent on idle polling
        when the future hasn't completed yet; in practice the worker
        completes in <50 ms for typical fetches so the poll count
        per switch stays in the single digits.
        """

        def _check() -> None:
            if fut.done():
                try:
                    result = fut.result()
                except Exception:  # noqa: BLE001
                    result = None
                try:
                    on_done(result)
                except Exception:  # noqa: BLE001
                    pass
                return
            try:
                track_after(poll_ms, _check)
            except Exception:  # noqa: BLE001
                pass

        try:
            track_after(poll_ms, _check)
        except Exception:  # noqa: BLE001
            pass

    def shutdown(self) -> None:
        """Best-effort shutdown of both executors and related fetch state."""
        executor = self._executor
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
        fetch_executor = self._fetch_executor
        if fetch_executor is not None:
            try:
                fetch_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                fetch_executor.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
        self._executor = None
        self._fetch_executor = None
        self._prefetch_inflight.clear()
        self._prefetch_futures.clear()
        self._poll_job = None
        self._reload_job = None
        self._poll_retry_count = 0
        self._poll_retry_expected_min_ts = None
