# `updates.py` — Background GitHub Releases update checks

Last updated: 2026-09-23

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
`https://` endpoints with a host are accepted at the transport layer;
invalid schemes fail before the private urllib opener is reached.

## HTTPS-only overrides
Any user-/config-supplied override — the `update_check_url` tunable or
`TRADINGLAB_UPDATE_URL` — must be an `https://` URL with a host. A
non-HTTPS override (`http://`, `file://`, a bare hostname, …) is
**rejected**, not silently ignored: `check_now()` returns
`UpdateResult(status="error")` naming the refused scheme, before any
cache lookup or network I/O, so a misconfigured endpoint can never
fall back to the built-in default unnoticed and release metadata is
never fetched over plaintext HTTP. The built-in default is already
`https://`. `_resolve_url()` runs once per check and `_is_https_url()` is
the shared predicate for endpoints, redirect destinations and release
links. A private opener with `_HTTPSOnlyRedirectHandler` rejects downgrade
redirects before urllib follows them; HTTPS redirects remain supported.

## Strictly RTH-suppressed
The poll never makes an outbound HTTPS call during US regular trading hours
(Monday–Friday, half-open `[09:30, 16:00)` America/New_York). A fresh cache
entry may be returned during RTH because it is local-only; otherwise the check
returns `status="rth_suppressed"` before network setup. If the ET timezone
cannot be resolved, the helper fails closed and treats the moment as RTH.
`force=True` does not bypass RTH policy.

The predicate itself is **not implemented here**: `_is_rth_now()` is a thin
delegate to `core.session_calendar.is_rth_now`, the single owner of the RTH
question. Don't re-inline the `datetime.now(ET)` + weekday + time-window
arithmetic — this module used to carry its own copy, and that duplication is
what the `session_calendar` consolidation retired. `session_calendar`
resolves `datetime` and `ET` function-locally on purpose so this module's
tests can keep patching them at call time.

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
surfaces as the release link when available, only if HTTPS with a host.
An invalid non-empty link turns the check into an error rather than
advertising an unsafe browser target. Missing links remain valid.

## Cache
Network outcomes (`up_to_date`, `available`, `error`) are cached for six hours
in memory and in `<app_data>/update_check_cache.json`. The cache key includes
the resolved endpoint URL and the current local version, so changing forks or
upgrading TradingLab does not reuse stale release state. `force=True` bypasses
both caches but still honors disabled/RTH policy.
Memory and disk cache results revalidate release URLs before being returned
to the UI, so legacy HTTP cache entries become errors without network I/O.

## Security
- Response body reads are capped at 64 KiB (`_MAX_RESPONSE_BYTES`).
- Endpoints and every redirect destination must use HTTPS before fetching.
- Returned and cached release links must use HTTPS before reaching the UI.
- User-/config-supplied endpoint overrides must be `https://`
  (see "HTTPS-only overrides"); anything else fails closed with
  `status="error"` before cache or network work.
- `HTTP_TIMEOUT_SECONDS = 8.0`; no retries.
- All check failures become `UpdateResult(status="error")`; callers never see
  exceptions.

## UI integration
- `ChartApp.__init__` schedules a startup check when
  `update_check_on_startup` is true (default). Only `status="available"` shows
  the passive, dismissable banner.
- Help → Check for Updates calls the same async scheduler and displays a
  messagebox for all statuses.
