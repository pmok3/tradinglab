# preload/service.py — Spec

## Purpose
Pure-logic batch fetch loop for the sandbox universe-preload feature. Serial, cancellable, retry-aware. All dependencies are injected (fetcher, cache_load, cache_save, merge, sleep, progress callback) so the service can be unit-tested without Tk, network, or filesystem.

## Public API
- `Fetcher`, `CacheLoad`, `CacheSave`, `Merger`, `SleepFn`, `ProgressCb` — type aliases for the injectable callables.
- `@dataclass(frozen=True) class IntervalOutcome` — `interval`, `status` (`"l1_hit"` | `"disk_hit"` | `"fetched"` | `"failed"` | `"cancelled"`), `bars`, `error`.
- `@dataclass(frozen=True) class SymbolOutcome` — `symbol`, `intervals: Tuple[IntervalOutcome,...]`. `loaded_intervals()` returns the tuple of intervals where bars are persisted and non-empty.
- `@dataclass(frozen=True) class PreloadResult` — `per_symbol`, `cancelled`, `started_at`, `finished_at`. Helpers: `loaded_per_symbol() -> Dict[str, Tuple[str,...]]` (manifest-builder shape), `fully_loaded() -> Tuple[str,...]`, `failed() -> Tuple[Tuple[str,str,str],...]`.
- `@dataclass(frozen=True) class ProgressEvent` — `kind` (`"start"` | `"symbol"` | `"finish"`), `symbol`, `interval`, `status`, `bars`, `error`, `index`, `total`.
- `cancellable_sleep(cancel_event, seconds)` — `time.sleep` replacement that wakes early on cancel. Used as default `sleep_fn`.
- `preload_universe(symbols, intervals, *, source_name, fetcher, cache_load, cache_save, merge, cancel_event, progress_cb, l1_check=None, sleep_fn=cancellable_sleep, rate_limit_s=0.6, max_retries=3) -> PreloadResult`.

## Algorithm (per (symbol, interval))
1. **L1 hit** — if `l1_check(source, sym, itv)` returns a non-empty list, status = `"l1_hit"`, skip both disk and network.
2. **Disk hit** — if `cache_load(source, sym, itv)` returns a non-empty list, status = `"disk_hit"`, skip network. (Sealed OHLCV bars are immutable; aggressive re-fetching wastes the yfinance rate-limit budget.)
3. **Live fetch** with up to `max_retries` attempts, using `sleep_fn(cancel_event, rate_limit_s)` between retries. Success → continue; exception or empty → retry.
4. **Merge + persist + verify**: `merge(cache_load(...), fetched) → cache_save(...) → cache_load(...)`. The follow-up `cache_load` is a verification step because `disk_cache.save()` swallows OSErrors silently.
5. **Inter-op rate-limit** — after `_run_one` returns from the main loop with `status == "fetched"`, the loop calls `sleep_fn(cancel_event, rate_limit_s)` before moving on. This is the explicit fix for the gap that existed in earlier versions: the retry-internal sleep only fired between retries, so a sequence of N first-try successes would back-to-back-fire N HTTP requests with no inter-op delay and cliff into the yfinance CDN throttle at full-exchange scale (~5,000 unbroken requests). The sleep is gated on the `"fetched"` status so it does NOT fire after `"l1_hit"` or `"disk_hit"` (local, no network) nor after `"failed"` (already paid its retry-budget sleeps) nor after `"cancelled"`.

## Cancellation contract
- `cancel_event.is_set()` is checked at the top of every symbol, every interval, and every retry attempt.
- `sleep_fn` is `cancellable_sleep` by default — `Event.wait(timeout)` returns early on set.
- Worst-case latency from Cancel click = one in-flight HTTP round-trip (typically <5 s for yfinance).
- Cancelled symbol-interval ops are reported with status `"cancelled"`, NOT silently dropped — so the GUI can render them in the final summary.

## Threading contract
- The service does NOT use threads internally. It is a synchronous function — caller (the GUI dialog) runs it on a worker `threading.Thread`.
- `progress_cb` is invoked from the same thread that called `preload_universe` — i.e. the worker, NOT the Tk thread. The GUI wrapper marshals events onto the Tk thread via `queue.Queue` + `after()` poller.
- The service MUST NOT touch any app-owned state directly. All in-memory cache reads happen via `l1_check`; mutations are out of scope here (the GUI applies them on the Tk thread after `progress_cb` arrives).

## Dependencies
- Internal: `..models.Candle` (type-hint only).
- External: `threading`, `time`, `dataclasses`.

## Design Decisions
- **Pure-logic with injection**, not a class with instance state, because every dependency (fetcher, cache, sleep) needs to be substitutable for tests. A function with kwargs is the smallest interface that supports that.
- **Disk-cache hit short-circuits live fetch.** Sealed bars are immutable, so a present cache means "we already have this." Re-fetching costs network budget for no payoff. If the user wants to refresh, they can delete the manifest + cache files; future work may add a "refresh" toggle.
- **Verify-after-save**, because `disk_cache.save()` is `try: ... except: pass`. Without verification the service would falsely claim success on disk-full / permission-denied / corrupt-pickle write paths.
- **Retry on persist errors too**, not just fetch errors. The fetch succeeded; if persist fails, retrying the whole op (re-fetching) burns budget but also resyncs against potential transient FS issues. Conservative.
- **Status is a string enum, not an `enum.Enum`**, to keep `to_dict` round-tripping trivial for downstream consumers (the dialog log + future metrics aggregators).
- **Index/total in every ProgressEvent**, so the GUI can render `42 / 1006` without tracking state itself.
- **Cancelled ops still emit a ProgressEvent**, so the cancel-summary count matches the planned-op count.
- **`l1_check` is optional**, because the service is reused outside the GUI (e.g. CLI tools) where there is no `_full_cache` to consult.

## Invariants
- `result.per_symbol` length ≤ `len(symbols)` (cancellation may end the loop early, in which case some trailing symbols have no SymbolOutcome).
- For every emitted `IntervalOutcome`, `bars >= 0` and `error == ""` whenever `status != "failed"`.
- `loaded_per_symbol()` keys ⊆ `set(symbols)`; values are tuples of strings ⊆ `set(intervals)`.
- The total number of `kind="symbol"` ProgressEvents equals `sum(len(so.intervals) for so in result.per_symbol)`.
- Exactly one `kind="start"` and one `kind="finish"` event are emitted per call.
