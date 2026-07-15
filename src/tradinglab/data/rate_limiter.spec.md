# data/rate_limiter.py — Spec

## Purpose
A small, thread-safe **token-bucket rate limiter** used to *proactively* pace
outbound vendor API calls so we stay under a per-minute quota instead of
reactively absorbing HTTP 429s. Currently the shared limiter for Alpaca (wired
in `alpaca_source._http_get_page`), whose free "Basic" plan allows 200
requests/min and paid "Algo Trader Plus" is treated as effectively unlimited
(`prefetch.buckets.UNLIMITED_RATE`).

**Why a token bucket, not exponential backoff, for a fixed quota:** a
per-minute limit has a *knowable* recovery time, so the right primary tool is
proactive traffic shaping; the reactive `Retry-After` handling in
`alpaca_source._request_with_retry` is the rare-overshoot safety net. (See the
rate-limiting discussion in `alpaca_source.spec.md`.)

## Public API
- `TokenBucket(rate_per_min, *, burst=None, safety=0.9, clock=time.monotonic)` — construct; starts **full** (allows an initial burst up to capacity).
- `.try_acquire(n=1) -> bool` — non-blocking; consume `n` tokens if available. Deterministic under an injected `clock` — the unit of test coverage for the pacing math.
- `.acquire(n=1, *, cancel=None, poll=0.02) -> bool` — block until `n` tokens consumed; returns True, or False only if `cancel` (a `threading.Event`) is set while waiting. `n` is clamped to capacity (asking for more than the bucket can hold would otherwise wait forever). Polls every `poll` s so a cancel is honoured promptly.
- `.time_until_available(n=1) -> float` — seconds until `n` tokens would be available (0.0 if already).
- `.configure(rate_per_min, *, burst=None, safety=0.9)` — re-set the rate/capacity live (banks accrued tokens first). Used for an Alpaca free→paid tier change without restart.
- `.rate_per_min` (property).

## Dependencies
- External: stdlib only (`threading`, `time`, `collections.abc`).

## Design Decisions
- **Sizing (safety margin):** sustained refill = `safety` (default 0.9) of the nominal limit; capacity (burst) = the remaining headroom (`round(rate_per_min*(1-safety))`). So the worst-case in any rolling 60 s window — `capacity + refill_per_sec*60` — stays at/under the nominal limit. For 200/min: ~3 tokens/s + a 20-token burst. Alpaca's paid tier configures the bucket to `UNLIMITED_RATE`, which is an intentionally huge sentinel rather than a real vendor quota. The `round` avoids float warts (`200*(1-0.9)=19.9999` → a clean 20).
- **Continuous (fractional) refill** via `time.monotonic` deltas — no background thread; refill is computed lazily on each acquire under the lock.
- **Thread-safe** via a single `threading.Lock`; `acquire` is a poll loop over `try_acquire` (releases the lock while sleeping). Correct under concurrency (multiple preload workers + the fetch executor); the shared bucket serialises token accounting.
- **`try_acquire` is the testable core** (deterministic with a fake clock, no real sleeps); `acquire` is a thin blocking wrapper. This is why the pacing tests inject a clock and exercise `try_acquire`.
- **Blocking is safe in practice:** all Alpaca callers run on background/worker threads; continuous refill keeps a single-token wait short (≈ 1/refill). `cancel` is supported for future preloader Stop-responsiveness.
- **Live reconfigure** (`configure`) supports the tier change AND a future header-driven auto-detect (adjust rate from `X-RateLimit-Limit`).

## Invariants
- Requests admitted in any rolling 60 s ≤ nominal `rate_per_min` (worst case = `capacity + refill*60` ≤ limit).
- `try_acquire(n)` consumes exactly `n` tokens on success, 0 on failure; tokens never exceed capacity.
- `acquire` returns True unless `cancel` is set; `n` is clamped to capacity so it can't deadlock.
- `configure` never loses more than the accrued tokens; changing the rate takes effect immediately for subsequent acquires.

## Testing
- `tests/unit/data/test_rate_limiter.py` — clean burst capacity, continuous refill, `time_until_available`, refill caps at capacity, the **rolling-minute budget bound** (greedy-drain simulation stays ≤ limit), live free→paid reconfigure, `acquire` success/cancel/`n`-clamp.
