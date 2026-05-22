"""Universe-preload service: pure-logic batch fetch loop.

Driven by an explicit ``cancel_event`` and ``progress_cb`` so the GUI
dialog can wrap it thinly and unit tests can drive it deterministically
with fakes. No Tk imports, no app state, no module-level mutable
globals.

Per-symbol-per-interval contract:
    1. Try in-process L1 (caller-supplied via ``l1_check``); if hit and
       non-empty, mark interval as already loaded, skip.
    2. Try the disk cache (``cache_load``); if hit, the data is already
       persistent — mark interval loaded, skip live fetch. (We do NOT
       refresh aggressively — sealed OHLCV bars are immutable, and
       re-fetching daily blows yfinance rate-limit budget needlessly.)
    3. Live ``fetcher(symbol, interval)`` with up to ``max_retries``
       attempts on transient failure. Sleep ``rate_limit_s`` (via the
       cancellation-aware ``sleep_fn``) between retries.
    4. On success, ``merge`` against any existing disk cache, then
       ``cache_save``, then verify with a follow-up ``cache_load`` so
       a silent disk-cache failure surfaces in the result.
    5. ``rate_limit_s`` cancellation-aware sleep before the next call.

Cancellation is checked at every retry boundary and between symbols, so
the worst-case latency from a Cancel click is one in-flight HTTP
request (typically <5 s) — *not* the full 0.6 s rate-limit gap.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..models import Candle

# Type aliases — keep the long Callables readable in signatures.
Fetcher = Callable[[str, str], list[Candle] | None]
CacheLoad = Callable[[str, str, str], list[Candle] | None]
CacheSave = Callable[[str, str, str, list[Candle]], None]
Merger = Callable[[list[Candle] | None, list[Candle] | None],
                  list[Candle]]
SleepFn = Callable[[threading.Event, float], None]
ProgressCb = Callable[["ProgressEvent"], None]


# ---------------------------------------------------------------------------
# Result + progress dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntervalOutcome:
    """One symbol × one interval outcome record."""
    interval: str
    status: str          # "l1_hit" | "disk_hit" | "fetched" | "failed" | "cancelled"
    bars: int            # 0 unless we actually have data persisted
    error: str = ""      # non-empty only when status == "failed"


@dataclass(frozen=True)
class SymbolOutcome:
    """All interval outcomes for one symbol."""
    symbol: str
    intervals: tuple[IntervalOutcome, ...]

    def loaded_intervals(self) -> tuple[str, ...]:
        """Intervals where bars are persisted and non-empty.

        Used by :func:`tradinglab.preload.manifest.build_from_loaded`
        — symbols with no loaded intervals are dropped from the
        manifest entirely.
        """
        return tuple(
            io.interval for io in self.intervals
            if io.status in ("l1_hit", "disk_hit", "fetched") and io.bars > 0
        )


@dataclass(frozen=True)
class PreloadResult:
    """Aggregate output of one ``preload_universe`` call."""
    per_symbol: tuple[SymbolOutcome, ...]
    cancelled: bool
    started_at: float
    finished_at: float

    def loaded_per_symbol(self) -> dict[str, tuple[str, ...]]:
        """Map of symbol -> tuple of intervals successfully loaded.

        Shape matches the ``per_symbol`` parameter of
        :func:`manifest.build_from_loaded`.
        """
        return {so.symbol: so.loaded_intervals() for so in self.per_symbol}

    def fully_loaded(self) -> tuple[str, ...]:
        """Symbols where every requested interval is loaded."""
        return tuple(
            so.symbol for so in self.per_symbol
            if all(
                io.status in ("l1_hit", "disk_hit", "fetched") and io.bars > 0
                for io in so.intervals
            )
        )

    def failed(self) -> tuple[tuple[str, str, str], ...]:
        """Tuples of (symbol, interval, error) for every failed fetch."""
        out: list[tuple[str, str, str]] = []
        for so in self.per_symbol:
            for io in so.intervals:
                if io.status == "failed":
                    out.append((so.symbol, io.interval, io.error))
        return tuple(out)


@dataclass(frozen=True)
class ProgressEvent:
    """One emit from the worker thread to the GUI poller.

    ``kind`` enumerations:
        ``"start"``    - emitted once at preload start
        ``"symbol"``   - emitted once per (symbol, interval) outcome
        ``"finish"``   - emitted once at the end (cancelled or not)
    """
    kind: str
    symbol: str = ""
    interval: str = ""
    status: str = ""
    bars: int = 0
    error: str = ""
    index: int = 0       # 0-based index of the (symbol, interval) op
    total: int = 0       # total (symbol, interval) ops planned


# ---------------------------------------------------------------------------
# Cancel-aware sleep
# ---------------------------------------------------------------------------


def cancellable_sleep(cancel_event: threading.Event, seconds: float) -> None:
    """``time.sleep`` replacement that wakes early on ``cancel_event``.

    Used as the default ``sleep_fn`` so a Cancel click during the
    inter-request rate-limit pause is reflected immediately. Tests
    inject a no-op fake to make the loop deterministic.
    """
    if seconds <= 0:
        return
    cancel_event.wait(seconds)


# ---------------------------------------------------------------------------
# Core service entry point
# ---------------------------------------------------------------------------


def preload_universe(
    symbols: list[str],
    intervals: list[str],
    *,
    source_name: str,
    fetcher: Fetcher,
    cache_load: CacheLoad,
    cache_save: CacheSave,
    merge: Merger,
    cancel_event: threading.Event,
    progress_cb: ProgressCb,
    l1_check: Callable[[str, str, str], list[Candle] | None] | None = None,
    sleep_fn: SleepFn = cancellable_sleep,
    rate_limit_s: float = 0.6,
    max_retries: int = 3,
) -> PreloadResult:
    """Serial fetch loop with retry, cancel, and merge-on-write.

    Args:
        symbols: ordered list of ticker symbols (deduplication is
            the caller's responsibility — done at the GUI layer
            before invocation).
        intervals: ordered list of interval strings (e.g.
            ``["5m", "1d"]``). Each (symbol, interval) is its own
            cache key.
        source_name: data-source identifier, threaded through to
            ``cache_load`` / ``cache_save`` so we always read/write
            the same key the runtime does.
        fetcher: callable ``(symbol, interval) -> Optional[List[Candle]]``.
            Synchronous; may raise; may return ``None`` or empty list
            on no-data.
        cache_load: ``(source, sym, interval) -> Optional[List[Candle]]``.
        cache_save: ``(source, sym, interval, candles) -> None``.
        merge: ``(old, new) -> List[Candle]`` — must implement
            newer-wins-on-overlap semantics so accumulating fetches
            extend past the provider window cap.
        cancel_event: set by the GUI Cancel button.
        progress_cb: called from the worker thread (NOT the Tk
            thread). Caller is responsible for marshalling onto the
            UI thread (via ``queue.Queue`` + ``after()`` poller).
        l1_check: optional in-memory cache hit probe. Returning a
            non-empty candle list bypasses both disk_cache load and
            the network. Used by the GUI to share the app's
            ``_full_cache``.
        sleep_fn: cancel-aware sleep injection point for tests.
        rate_limit_s: pause between successive provider hits.
        max_retries: total attempts per (symbol, interval), including
            the first.

    Returns:
        A :class:`PreloadResult` whose ``per_symbol`` records the
        outcome of every (symbol, interval) op, in the order they
        ran.
    """
    started_at = time.time()
    total_ops = len(symbols) * len(intervals)
    progress_cb(ProgressEvent(kind="start", total=total_ops))

    out_symbols: list[SymbolOutcome] = []
    op_index = 0

    cancelled = False
    for sym in symbols:
        if cancel_event.is_set():
            cancelled = True
            break
        per_interval: list[IntervalOutcome] = []
        for itv in intervals:
            if cancel_event.is_set():
                cancelled = True
                per_interval.append(
                    IntervalOutcome(interval=itv, status="cancelled", bars=0))
                progress_cb(ProgressEvent(
                    kind="symbol", symbol=sym, interval=itv,
                    status="cancelled", bars=0,
                    index=op_index, total=total_ops))
                op_index += 1
                continue
            outcome = _run_one(
                sym, itv, source_name=source_name, fetcher=fetcher,
                cache_load=cache_load, cache_save=cache_save,
                merge=merge, cancel_event=cancel_event,
                l1_check=l1_check, sleep_fn=sleep_fn,
                rate_limit_s=rate_limit_s, max_retries=max_retries,
            )
            per_interval.append(outcome)
            progress_cb(ProgressEvent(
                kind="symbol", symbol=sym, interval=itv,
                status=outcome.status, bars=outcome.bars,
                error=outcome.error,
                index=op_index, total=total_ops))
            op_index += 1
            if outcome.status == "cancelled":
                cancelled = True
            elif outcome.status == "fetched":
                # Inter-op rate limit on the happy path. The retry
                # loop inside ``_run_one`` only sleeps *between*
                # retries — on a first-try success the function
                # returns immediately. Without this sleep, a 5,000-
                # symbol full-exchange preload would hit yfinance's
                # CDN throttle (un-documented but real, ~500-2,000
                # consecutive requests) and cliff into a wall of
                # 429s.  We only sleep after ``fetched`` (a real
                # network round-trip happened); ``l1_hit`` and
                # ``disk_hit`` are local and consume no budget.
                # ``failed`` already burned its retry budget — no
                # extra sleep needed.
                sleep_fn(cancel_event, rate_limit_s)
        out_symbols.append(SymbolOutcome(
            symbol=sym, intervals=tuple(per_interval)))
        if cancelled:
            break

    finished_at = time.time()
    result = PreloadResult(
        per_symbol=tuple(out_symbols),
        cancelled=cancelled,
        started_at=started_at,
        finished_at=finished_at,
    )
    progress_cb(ProgressEvent(
        kind="finish", index=op_index, total=total_ops))
    return result


def _run_one(
    sym: str,
    itv: str,
    *,
    source_name: str,
    fetcher: Fetcher,
    cache_load: CacheLoad,
    cache_save: CacheSave,
    merge: Merger,
    cancel_event: threading.Event,
    l1_check: Callable[[str, str, str], list[Candle] | None] | None,
    sleep_fn: SleepFn,
    rate_limit_s: float,
    max_retries: int,
) -> IntervalOutcome:
    """Run the load-then-fetch ladder for one (symbol, interval)."""
    # Step 1: L1 (in-process) hit.
    if l1_check is not None:
        try:
            l1 = l1_check(source_name, sym, itv)
        except Exception:  # noqa: BLE001
            l1 = None
        if l1:
            return IntervalOutcome(
                interval=itv, status="l1_hit", bars=len(l1))

    # Step 2: disk cache hit.
    cached = cache_load(source_name, sym, itv) or []
    if cached:
        return IntervalOutcome(
            interval=itv, status="disk_hit", bars=len(cached))

    # Step 3: live fetch with retry.
    last_err = ""
    for attempt in range(max_retries):
        if cancel_event.is_set():
            return IntervalOutcome(
                interval=itv, status="cancelled", bars=0,
                error=last_err)
        try:
            fetched = fetcher(sym, itv) or []
        except Exception as exc:  # noqa: BLE001
            last_err = repr(exc)
            fetched = []
        if fetched:
            # Step 4: merge + persist + verify.
            try:
                old = cache_load(source_name, sym, itv) or []
                merged = merge(old, fetched)
                cache_save(source_name, sym, itv, merged)
                # Verify the save actually landed — disk_cache.save
                # swallows OS errors silently, so without this check
                # we could falsely report success.
                verify = cache_load(source_name, sym, itv) or []
                if not verify:
                    return IntervalOutcome(
                        interval=itv, status="failed", bars=0,
                        error="persistence verification failed")
                return IntervalOutcome(
                    interval=itv, status="fetched", bars=len(verify))
            except Exception as exc:  # noqa: BLE001
                last_err = f"persist error: {exc!r}"
                # Fall through to retry — the underlying fetch
                # succeeded, so the retry budget is for the persist
                # path. Conservative.
        if attempt + 1 < max_retries:
            sleep_fn(cancel_event, rate_limit_s)
    return IntervalOutcome(
        interval=itv, status="failed", bars=0,
        error=last_err or "no data after retries")
