# data/schwab_auth.py — Spec

Last updated: 2026-09-08

## Purpose
Owns the **on-disk Schwab OAuth token cache** and the **POST-to-refresh** flow. Stdlib-only and importable from anywhere — does NOT own the browser-redirect login flow (that lives in `schwab_login.py`).

## Public API
- `token_cache_path() -> Path` — resolves the legacy/logical `schwab.json` path via `..paths.tokens_dir()`. Override via env: `TRADINGLAB_TOKEN_DIR` (narrow) or `TRADINGLAB_DATA_DIR` (broad). Windows storage is its `.dat` sibling; the logical path stays stable for callers.
- `load_token_cache(path=None) -> Optional[Dict[str, Any]]` — read/validate, preferring the protected sibling. Missing returns `None`; corrupt, inaccessible, or unmigratable caches raise `TokenCacheError`, never silently fall back.
- `save_token_cache(data, path=None, *, expected_generation=None) -> None` — atomic DPAPI JSON-object write on Windows; atomic 0600 JSON elsewhere. Auto-adds `saved_at`. Windows encryption failures never write plaintext. Explicit `.dat` paths are accepted as well as logical JSON paths.
- `clear_token_cache(path=None) -> None` — invalidates pending authorization and attempts deletion of both JSON and protected versions. Missing files are fine; partial deletion raises `TokenCacheError`.
- `token_cache_generation() -> int` — process-local revision for authorization attempts. Passing it to `save_token_cache` rejects stale work with `TokenCacheChangedError`.
- `is_access_token_fresh(cache, *, now=None) -> bool` — true iff access token is set and not within `ACCESS_TOKEN_REFRESH_SKEW_SEC` (5 minutes) of nominal expiry.
- `is_refresh_token_alive(cache, *, now=None) -> bool` — true iff refresh token is set and not past its recorded `refresh_token_expires_at` (missing field treated as alive — server is authority).
- `build_token_cache(response: dict, *, now=None, previous=None, creds=None) -> dict` — validates and translates Schwab's response. Only new browser authorization starts a default seven-day refresh lifetime. `previous` preserves that deadline across refresh/rotation; a shorter server lifetime may shorten it. An omitted refresh token retains the previous token, while missing/empty tokens on initial authorization are rejected.
- `cache_matches_credentials(cache, creds) -> bool` — new caches carry a SHA-256 fingerprint of app key/secret (not raw values). Changed credentials require reconnect. Legacy unbound caches remain readable/usable.
- `refresh_access_token(creds, refresh_token, *, _post=None) -> dict` — POST to `/oauth/token` with `grant_type=refresh_token`. `_post` is a test injection hook.
- `get_access_token(creds, *, path=None, _now=None, _post=None, raise_errors=False) -> Optional[str]` — returns a valid access token, refreshing if needed, or `None` for missing auth/expected operational failure. `raise_errors=True` preserves cache/HTTP errors for explicit verification.
- `schwab_failure_result(exc) -> VerifyResult` — shared secret-free failure classification for auth, REST, CLI and GUI. HTTP status taxonomy is retained (403 is forbidden, never invalid keys); exception text, URLs and response bodies are never displayed/logged. HTTP error responses are closed without reading their bodies.
- Constants: `TOKEN_URL`, `AUTHORIZE_URL`, `ACCESS_TOKEN_REFRESH_SKEW_SEC`.

## Dependencies
- Internal: `.._dpapi.{save_json_object,load_json_object}`, `..core.io_helpers.atomic_write_json`, `.credentials.SchwabCredentials`, `.verify`.
- External: stdlib only (`base64`, `urllib`, `json`, `threading`).

## Design Decisions
- **Separate refresh and cache locks**: refreshes serialize, but disk locking never spans HTTP. Clear/replacement does not wait for a slow refresh; generation-checked publication prevents the old response from resurrecting tokens. There is no filesystem lock; concurrent processes sharing one cache are unsupported.
- **Refresh skew of 5 minutes**: a long-running request that started near the boundary doesn't 401.
- **Legacy deadlines**: missing refresh expiry permits a refresh attempt. Its resulting deadline uses the original aware `saved_at` plus seven days. With no trustworthy age, only that access-token refresh is granted; the next refresh requires login. Migration does not stamp a new age. A recorded deadline never moves later during refresh.
- **Safe legacy migration**: on Windows, validate legacy JSON, atomically write the DPAPI object, then unlink plaintext. Failure to encrypt leaves legacy untouched and raises; failure to unlink leaves the recoverable protected copy and raises. A present protected file is authoritative, even if corrupt. Successful protected reads remove leftover legacy JSON; no rollback to stale plaintext occurs.
- **Schema checks**: objects only, nonempty string tokens, finite numeric expiry fields (not booleans). Numeric/structured metadata round-trips through DPAPI unchanged. Freshness helpers reject nonfinite/malformed expiry.
- **Split from `schwab_login.py`**: this module is pure stdlib + JSON. No `webbrowser`, no `input()`, no UI risk. Safe to import from any context.
- **HTTP Basic auth with `app_key:app_secret`** on token POSTs is Schwab's required scheme (not Bearer for token endpoints).
- **Shared `credentialed_opener()`** (security audit I4 / M5). The
  `_post_token` call routes through `data._http.credentialed_opener`
  so cross-host 30x redirects strip the `Authorization: Basic …`
  header before forwarding. Response read is bounded by
  `data._http.MAX_RESPONSE_BYTES + 1` (8 MB plus overflow sentinel);
  oversized responses are rejected rather than parsed as truncated JSON.
- **Public token lookup fails soft by default** with sanitized logging. Lower-level `refresh_access_token` still raises `RuntimeError` for unconfigured credentials. Explicit verifiers opt into error propagation, so refresh 401/403/429/timeout retains its actual category.

## Invariants
- Windows tokens use current-user DPAPI, never plaintext fallback. Non-Windows uses 0600 JSON; chmod failures are surfaced.
- `get_access_token` is thread-safe (guarded by module `_lock`).
- The cache file's directory is created on first save by the selected atomic writer.
- Token cache always contains both `access_token` and `refresh_token` after a successful refresh — partial writes are impossible (atomic rename).

## Testing
- Pure helpers (`build_token_cache`, `is_access_token_fresh`, `is_refresh_token_alive`) are unit-testable with hand-rolled dicts.
- `refresh_access_token` and `get_access_token` accept a `_post` injection hook for test parity.
- `tests/unit/test_schwab_auth.py`, `tests/unit/data/test_schwab_token_cache.py`, and `tests/unit/data/test_schwab_source.py` cover schema/expiry, mocked DPAPI round-trip/failures/migration, POSIX permissions, deletion/replacement during refresh, serialized refresh, and bounded HTTP with injected openers. No real credentials or network.
- Live token rotation and account-specific authorization remain uncommissioned.
