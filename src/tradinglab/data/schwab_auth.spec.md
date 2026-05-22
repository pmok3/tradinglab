# data/schwab_auth.py — Spec

## Purpose
Owns the **on-disk Schwab OAuth token cache** and the **POST-to-refresh** flow. Stdlib-only and importable from anywhere — does NOT own the browser-redirect login flow (that lives in `schwab_login.py`).

## Public API
- `token_cache_path() -> Path` — resolves the cache JSON path via `..paths.tokens_dir()`. Override via env: `TRADINGLAB_TOKEN_DIR` (narrow) or `TRADINGLAB_DATA_DIR` (broad).
- `load_token_cache(path=None) -> Optional[Dict[str, Any]]` — read + parse. Returns `None` on missing/unparseable.
- `save_token_cache(data, path=None) -> None` — atomic write through `core.io_helpers.atomic_write_json`, mode 0600 on POSIX (best-effort on Windows). Auto-adds `saved_at` ISO timestamp.
- `is_access_token_fresh(cache, *, now=None) -> bool` — true iff access token is set and not within `ACCESS_TOKEN_REFRESH_SKEW_SEC` (5 minutes) of nominal expiry.
- `is_refresh_token_alive(cache, *, now=None) -> bool` — true iff refresh token is set and not past its recorded `refresh_token_expires_at` (missing field treated as alive — server is authority).
- `build_token_cache(response: dict, *, now=None) -> dict` — translates Schwab's `/oauth/token` response (`access_token`, `refresh_token`, `expires_in`, ...) into the cache schema. Refresh-token lifetime defaults to 7 days when not returned.
- `refresh_access_token(creds, refresh_token, *, _post=None) -> dict` — POST to `/oauth/token` with `grant_type=refresh_token`. `_post` is a test injection hook.
- `get_access_token(creds, *, path=None, _now=None, _post=None) -> Optional[str]` — the public entrypoint. Returns a valid access token (refreshing on-disk cache if needed) or `None` if the user must re-login or credentials aren't set.
- Constants: `TOKEN_URL`, `AUTHORIZE_URL`, `ACCESS_TOKEN_REFRESH_SKEW_SEC`.

## Dependencies
- Internal: `..core.io_helpers.atomic_write_json`, `.credentials.SchwabCredentials`.
- External: stdlib only (`base64`, `urllib`, `json`, `threading`).

## Design Decisions
- **In-process lock around refresh**: two threads can't both race a refresh and invalidate each other. NO filesystem lock — multiple-process sharing of the cache is rare and Schwab handles the older-token-invalidated case server-side.
- **Refresh skew of 5 minutes**: a long-running request that started near the boundary doesn't 401.
- **Cache schema is forward-compatible**: caches written before `refresh_token_expires_at` existed get one chance to refresh (the server will 400 if truly stale). Avoids forcing a re-login when bumping schema.
- **Split from `schwab_login.py`**: this module is pure stdlib + JSON. No `webbrowser`, no `input()`, no UI risk. Safe to import from any context.
- **HTTP Basic auth with `app_key:app_secret`** on token POSTs is Schwab's required scheme (not Bearer for token endpoints).
- **Shared `credentialed_opener()`** (security audit I4 / M5). The
  `_post_token` call routes through `data._http.credentialed_opener`
  so cross-host 30x redirects strip the `Authorization: Basic …`
  header before forwarding. Response read is bounded by
  `data._http.MAX_RESPONSE_BYTES` (8 MB) — the real Schwab token
  reply is ~1 KB.
- **Never raises on happy paths**: missing creds, missing cache, network error during refresh — all return `None` so the fetcher / streamer fail gracefully.

## Invariants
- Cache file mode is 0600 on POSIX (best-effort on Windows where `os.chmod` is a no-op).
- `get_access_token` is thread-safe (guarded by module `_lock`).
- The cache file's directory is created on first save via `core.io_helpers.atomic_write_json`.
- Token cache always contains both `access_token` and `refresh_token` after a successful refresh — partial writes are impossible (atomic rename).

## Testing
- Pure helpers (`build_token_cache`, `is_access_token_fresh`, `is_refresh_token_alive`) are unit-testable with hand-rolled dicts.
- `refresh_access_token` and `get_access_token` accept a `_post` injection hook for test parity.
- HTTP path (`_post_token`) is `# pragma: no cover` — exercised via manual integration only.

