# data/prefetch/live.py — Spec

## Purpose
Live-mode fetch translation for the prefetch scheduler: map a `FetchWindow` to a
concrete registry fetch, and derive the deepening `oldest_ts`. The app's live
`_prefetch_submit` seam runs these on the dedicated prefetch worker pool.

## Public API
- `FetchOutcome = tuple[list, BaseException | None, float | None]` — `(bars,
  error, retry_after_s)`.
- `oldest_ts(bars) -> float | None` — epoch-seconds of the OLDEST bar (`min`
  over `b.date.timestamp()`, defensive against mis-ordering); `None` for empty /
  un-timestamped input.
- `fetch_window(source, symbol, interval, window) -> FetchOutcome` — never
  raises.

## Contract
- **range** window → `base.fetch_page(source, symbol, interval, end_ts=window.end,
  limit=window.limit or 10_000)`:
  - `ok` → `(bars, None, None)`; `empty` → `([], None, None)`;
  - `error` → `([], error, retry_after_s)` (scheduler owns retry/poison/AIMD);
  - `unsupported` → fall through to the trailing fetcher.
- **period** window (and the range-`unsupported` fallback) →
  `DATA_SOURCES[source](symbol, interval)` trailing window; a missing fetcher →
  `([], None, None)`; a raising fetcher → `([], exc, None)`.
- `oldest_ts` uses `min` (not `bars[0]`) so a mis-ordered page still yields the
  true oldest bar for the band step-back; any bar lacking `.date.timestamp()` →
  `None` (→ scheduler treats as "no older data" → exhausted).

## Design Decisions
- **Pure apart from the registry dispatch.** `DATA_SOURCES` / `fetch_page` are
  module-level and monkeypatch-friendly, so the translation is unit-tested
  offline with no Tk / network.
- **Never raises.** Errors are returned in the tuple so the app seam can feed
  them straight into `PrefetchDriver.complete(error=…, retry_after_s=…)` and the
  scheduler owns the backoff/poison decision (single-owner rate/retry).

## Testing
`tests/unit/data/prefetch/test_live.py` — `oldest_ts` (empty/min/unordered/bad
bar); `fetch_window` period-trailing, range ok/empty/error-propagate/
unsupported-fallback, missing-fetcher, raising-fetcher.
