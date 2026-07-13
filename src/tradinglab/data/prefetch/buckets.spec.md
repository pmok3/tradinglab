# data/prefetch/buckets.py — Spec

## Purpose
Per-source rate limiting for the scheduler: a **single accounting gate**
(`SourceBucketRegistry`, Decision 1) plus a self-tuning `AIMDRateController`
for yfinance (Decision 10).

## Public API
- Constants: `UNLIMITED_RATE = 1_000_000.0`, `CONSERVATIVE_DEFAULT_RATE = 60.0`,
  `DEFAULT_RATES` (yfinance 100, alpaca 200, polygon 100, synthetic/-stream/
  testdata unlimited).
- `looks_throttled(error, *, latency_s=None, latency_threshold_s=5.0) -> bool`.
- `SourceBucketRegistry(*, defaults=None, clock=time.monotonic)`:
  `bucket_for(source) -> TokenBucket` (lazy + cached), `configure(source,
  rate_per_min, *, burst=None)`, `rate_for(source) -> float`.
- `AIMDRateController(*, initial, min_rate, max_rate, increase_step=10.0,
  decrease_factor=0.5, increase_every=20, bucket=None)`: `rate`,
  `on_success()`, `on_throttle()`.

## Contract
- **One bucket per source, cached** — the same `TokenBucket` instance is returned
  per (normalized) source name, so all fetch paths share one budget.
- Unknown source → `CONSERVATIVE_DEFAULT_RATE`; internal sources → `UNLIMITED_RATE`
  (a long burst all succeeds). Source names are normalized (`strip().lower()`).
- `looks_throttled` is True on explicit throttle text (`429` / `999` /
  `too many requests` / `rate limit`) or a latency spike; **False** on ordinary
  errors and on a single empty result (empty → poison path, not throttle).
- AIMD: `on_throttle` → `rate = max(min, rate*decrease_factor)` + reset streak;
  `on_success` → after `increase_every` successes, `rate = min(max,
  rate+increase_step)`. With a `bucket`, changes apply live via `configure`.

## Design Decisions
- Reuses the existing `data/rate_limiter.TokenBucket` (thread-safe, injectable
  clock, live `configure`) rather than a new primitive.
- Empty-result is deliberately NOT a throttle signal here to avoid punishing the
  rate on delisted/bad symbols; burst-empty detection is a scheduler concern.

## Testing
`tests/unit/data/prefetch/test_buckets.py` — default/cached/unknown/unlimited/
override/normalize registry; throttle classifier (explicit + latency + negatives);
AIMD initial/decrease/increase-after-N/clamps/streak-reset/bucket-apply.
