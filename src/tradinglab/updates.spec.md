# `updates.py` — Background GitHub Releases update checks

## Purpose
Surface "a newer release is available" to users who never visit GitHub
directly. This module is the single source of truth for both startup
auto-checks and Help → Check for Updates.

## URL resolution
`check_now()` resolves the endpoint in this order:

1. `defaults.get("update_check_url")` when non-empty.
2. `TRADINGLAB_UPDATE_URL` for power users / test harnesses.
3. `RELEASES_URL`, whose built-in value is
   `https://api.github.com/repos/pmok3/tradinglab/releases/latest`.

Blank across all three returns `UpdateResult(status="disabled")`. Only
`http://` and `https://` schemes are accepted; invalid schemes fail before
`urlopen` is reached.

## Strictly RTH-suppressed
The poll never makes an outbound HTTPS call during US regular trading hours
(Monday–Friday, 09:30–16:00 America/New_York). A fresh cache entry may be
returned during RTH because it is local-only; otherwise the check returns
`status="rth_suppressed"` before network setup. If the ET timezone cannot be
resolved, the helper fails closed and treats the moment as RTH. `force=True`
does not bypass RTH policy.

## Public API
- Constants exported for tests/configuration: `ENV_URL`, `DEFAULT_RELEASES_URL`,
  `RELEASES_URL`, `CACHE_TTL_SECONDS`, `HTTP_TIMEOUT_SECONDS`.
- `UpdateResult` (frozen dataclass): `status`, `current`, `latest`, `url`,
  `error`. Status is one of `"disabled"`, `"rth_suppressed"`, `"up_to_date"`,
  `"available"`, `"error"`.
- `check_now(*, force=False) -> UpdateResult` — synchronous probe.
- `schedule_check_async(after_fn, callback, *, force=False)` — run `check_now`
  on a daemon thread and marshal the result back via `after_fn(0, ...)`.
- `compare_versions(current, advertised) -> Optional[str]` — tolerant
  `MAJOR.MINOR.PATCH` comparison used by smoke tests and the poll.
- `reset_cache_for_tests(clear_disk=False)` — clear in-memory cache; tests can
  also remove the isolated on-disk cache.

## Payloads
Two release payload shapes are accepted:

- Plain manifest: `{ "version": "0.2.3" }`.
- GitHub Releases: `{ "tag_name": "v0.2.3", "html_url": "..." }`.

`version` wins if both keys are present. `html_url` (or `url`) is passed to UI
surfaces as the release link when available.

## Cache
Network outcomes (`up_to_date`, `available`, `error`) are cached for six hours
in memory and in `<app_data>/update_check_cache.json`. The cache key includes
the resolved endpoint URL and the current local version, so changing forks or
upgrading TradingLab does not reuse stale release state. `force=True` bypasses
both caches but still honors disabled/RTH policy.

## Security
- Response body reads are capped at 64 KiB (`_MAX_RESPONSE_BYTES`).
- URL schemes are allow-listed to `http`/`https` before `urlopen`.
- `HTTP_TIMEOUT_SECONDS = 8.0`; no retries.
- All check failures become `UpdateResult(status="error")`; callers never see
  exceptions.

## UI integration
- `ChartApp.__init__` schedules a startup check when
  `update_check_on_startup` is true (default). Only `status="available"` shows
  the passive, dismissable banner.
- Help → Check for Updates calls the same async scheduler and displays a
  messagebox for all statuses.
