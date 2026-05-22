# `updates.py` — Background GitHub Releases poll

## Purpose
Surface "a newer release is available" to users who never visit
GitHub directly. The redistributable is intended for end users
without a dev environment; an in-app update check is the only
discovery channel.

## Strictly RTH-suppressed
The poll **never** makes an outbound HTTPS call during US regular
trading hours (Monday–Friday, 09:30–16:00 America/New_York). A
slow DNS / TLS handshake on a coffee-shop Wi-Fi is exactly the
wrong thing to introduce during a live discretionary entry.
`_is_rth_now()` uses `zoneinfo("America/New_York")` and falls back
to "always suppress" if the lookup fails — a missing tzdb cannot
accidentally enable the call.

## Public API
- `UpdateResult` (frozen dataclass): `status`, `current`, `latest`,
  `url`, `error`. Status is one of:
  - `"disabled"` — `RELEASES_URL` is empty (private repo / no
    public release channel).
  - `"rth_suppressed"` — short-circuit during RTH.
  - `"up_to_date"` — successful poll, no newer version.
  - `"available"` — newer release, `latest` + `url` populated.
  - `"error"` — network / parse failure; `error` carries a short
    human-readable message.
- `check_now(*, force=False) -> UpdateResult` — synchronous probe.
  Honors the cache (`CACHE_TTL_SECONDS = 6h`) unless `force=True`.
  RTH suppression and disabled-URL short-circuits are policy, not
  caching — `force` does NOT bypass them.
- `schedule_check_async(after_fn, callback, *, force=False)` — fire
  `check_now` on a daemon thread; marshal the result back via
  `after_fn(0, lambda: callback(result))`. `after_fn` is typically
  `tk_root.after`. Never call Tk widget methods from the worker.
- `reset_cache_for_tests()` — clear the cache; test-only.

## Module configuration
- `RELEASES_URL: str` — GitHub Releases API endpoint. Defaults to
  empty (no-op). The TradingLab repo is currently private; when
  it goes public, set this to
  `https://api.github.com/repos/<owner>/<name>/releases/latest`.
- `CACHE_TTL_SECONDS: int = 6 * 3600` — re-opening Help inside this
  window reuses the cached result.
- `HTTP_TIMEOUT_SECONDS: float = 8.0` — hard timeout. Slower than
  this is functionally broken from the user's perspective.
- `USER_AGENT: str = "tradinglab-update-poll"` — GitHub rejects
  requests with no User-Agent.

## Version comparison
`_parse_version` strips leading `v` and any `+local` / ` (date)` tail, splits
on `.`, tries `int()` per piece. **Breaks on the first non-int piece** — so
`"0.1.x"` parses to `(0, 1)`, and partial-prefix tags sort below the same
prefix with a real patch (`_parse_version("0.1.x") < _parse_version("0.1.0")`).
Unparseable tags degrade to `(0,)`. We control tags (always pure-numeric) so
the partial-prefix case is theoretical.

## Error policy
Failures cache the same way successes do — a flapping network shouldn't spam
the GitHub API at one call per Help-menu open. Callers surface
`status="error"` as a passive messagebox at most.
