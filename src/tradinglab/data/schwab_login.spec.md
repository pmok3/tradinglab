# data/schwab_login.py — Spec

## Purpose
One-time Schwab OAuth-code login CLI. Walks the user through the browser-redirect dance, exchanges the resulting auth code for tokens, and writes the persistent cache that `schwab_auth` then auto-refreshes for ~7 days.

## Public API
- `build_authorize_url(app_key: str, redirect_uri: str, *, state: Optional[str] = None) -> str` —
  constructs the consent URL the user opens. When `state` is
  provided, it's appended as `&state=<value>` so the OAuth `state`
  CSRF check can be verified at code-exchange time.
- `extract_code(redirect_url: str) -> str` — pulls `?code=` out of the URL the user pastes back. Raises `ValueError` with a friendly message if missing.
- `extract_state(redirect_url: str) -> Optional[str]` — pulls `?state=`
  out of the URL the user pastes back. Returns `None` if absent.
  Used by `main()` for CSRF defence.
- `exchange_code_for_tokens(creds, redirect_uri, code, *, _post=None) -> dict` — POST `grant_type=authorization_code` to `/oauth/token`. `_post` is a test injection hook.
- `main(argv=None) -> int` — CLI entry. Invoked via `python -m tradinglab.data.schwab_login`. Optional `--redirect-url` flag skips the interactive prompt (useful for scripted runs).

## Dependencies
- Internal: `.credentials.get_credentials`, `.schwab_auth.{AUTHORIZE_URL, _post_token, build_token_cache, save_token_cache}`.
- External: stdlib only (`argparse`, `secrets`, `urllib.parse`, `sys`).

## Design Decisions
- **No local HTTP listener for the redirect**: the browser will land on a `"site can't be reached"` page; that's expected. User copies the URL back into the terminal. Keeps this script dependency-free and works behind corporate firewalls.
- **Module is `python -m`-runnable**: pragma `if __name__ == "__main__"` calls `main()`. The chart app does not invoke this — it's a one-time-per-7-days operator task.
- **Default redirect_uri is `https://127.0.0.1`** when
  `SchwabCredentials.redirect_uri` is missing, matching Schwab's
  most common dev registration.
- **Code is single-use, short-lived (~30s)**: docstring warns the operator to paste promptly.
- **OAuth `state` CSRF check** (security audit M4). `main()`
  generates a fresh `secrets.token_urlsafe(24)` per login attempt
  and threads it through `build_authorize_url(..., state=…)`. After
  the user pastes the redirect URL, `main()` extracts the echoed
  state, compares it with `secrets.compare_digest`, and aborts the
  token exchange on mismatch. Mitigates the classic OAuth login-CSRF
  where an attacker tricks the operator into pasting an attacker-
  initiated code, binding the attacker's Schwab account to the
  operator's local cache.
- **Returns shell exit codes**: 0 success, 1 token-exchange or parse failure, 2 missing credentials, 130 user-aborted. State mismatch returns 1.

## Invariants
- After a successful run, `token_cache_path()` exists and contains both `access_token` and `refresh_token` (atomic write via `schwab_auth.save_token_cache`).
- `main()` is the only function that performs I/O / writes the cache. The other helpers are pure.
- When `state` is generated, it is verified before any token
  exchange occurs. A mismatched or missing state aborts the run.

## Testing
- `build_authorize_url`, `extract_code`, `extract_state` are
  unit-testable with no I/O. The `state`-mismatch abort path is
  covered in `tests/unit/data/test_schwab_login_state.py`. Network
  path is `# pragma: no cover` (exercised manually).

