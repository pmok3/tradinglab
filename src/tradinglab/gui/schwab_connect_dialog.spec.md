# `gui/schwab_connect_dialog.py` — Interactive Schwab OAuth sign-in

## Purpose
In-app, guided replacement for the terminal-only
`python -m tradinglab.data.schwab_login` flow. Opened via
**Tools → "Connect to Schwab…"**. Lets an end user running the frozen
`.exe` complete the Schwab OAuth handshake without a console.

## Why not an embedded webview
The dialog deliberately does **NOT** embed Schwab's login page in a webview.
Embedded webviews for the authorization request are an OAuth anti-pattern
(RFC 8252: native apps must use the system browser); Schwab — like most
providers — blocks them, and they break the user's password manager / 2FA /
passkeys. There is also no Tkinter webview, and the only options (CEF — no
Windows-ARM64 build; Edge WebView2 — large PyInstaller bloat) would still hit
the provider block. Instead the flow uses the **system browser + paste-back**.

## Flow
1. **Step 1 — Open sign-in.** `_on_open_browser` checks
   `get_credentials().schwab.is_configured()`; if not, points the user at
   *Configure Credentials*. Otherwise it mints a fresh single-use CSRF nonce
   (`secrets.token_urlsafe(24)`), builds the consent URL via
   `schwab_login.build_authorize_url(app_key, redirect_uri, state=nonce)`,
   stores the nonce + redirect URI, shows the URL (read-only + **Copy URL**),
   and opens it with `webbrowser.open`.
2. **Step 2 — Paste back.** Schwab redirects to the registered
   `https://127.0.0.1` (the browser shows "can't reach this page" — expected,
   no local listener). The user pastes the full redirected URL into the
   dialog and clicks **Connect**.
3. **Validate + exchange.** `_verify_and_extract` (pure, static) verifies the
   echoed `state` against the stored nonce with `secrets.compare_digest`
   (CSRF), then `extract_code`. The token exchange
   (`exchange_code_for_tokens` → `build_token_cache` → `save_token_cache`)
   runs on a **daemon thread** (`_exchange_worker`); the Tk main thread polls
   the `_exchange_result` dict via `after` (§7.15 — never call `after` from
   the worker). On success the DPAPI-protected token cache is written and the
   status flips to "Connected ✓".

## Public API
- `SchwabConnectDialog(parent)` — `BaseModalDialog`; Return = Connect, ESC =
  Close. ttk-only widgets (no classic Tk widgets → no `native_theme` needed).
- `open_schwab_connect_dialog(parent) -> SchwabConnectDialog | None` —
  construct + `wait_window`; `None` on `TclError`.
- `SchwabConnectDialog._verify_and_extract(pasted_url, nonce) ->
  (code | None, error | None)` — static, pure, unit-testable.
- **Disconnect** — `token_cache_path().unlink()` after a confirm prompt.

## Status line
`_compute_status_text` reads `load_token_cache` + `is_access_token_fresh` /
`is_refresh_token_alive` (no network): "Not configured" / "Configured, not
connected" / "Connected ✓" / "Tokens expired".

## Dependencies
- Internal: `..data.credentials.get_credentials`,
  `..data.schwab_login.{build_authorize_url, extract_code, extract_state,
  exchange_code_for_tokens}`,
  `..data.schwab_auth.{build_token_cache, save_token_cache, load_token_cache,
  is_access_token_fresh, is_refresh_token_alive, token_cache_path}`,
  `._modal_base.{BaseModalDialog, protect_combobox_wheel}`, `.colors`.
- Stdlib: `secrets`, `threading`, `webbrowser`, `tkinter`.

## Wiring
`gui/menu_builder.py` adds **Tools → "Connect to Schwab…"** →
`HelpMenuMixin._on_help_connect_schwab` (`gui/help_menu.py`), a guarded import
+ `open_schwab_connect_dialog(self)`. Not gated on
`SCHWAB_REGISTRATION_ENABLED` — obtaining tokens ahead of the data source
landing is harmless (they sit in the cache until the source reads them).

## Threat model
Same as the OAuth CLI: the auth code is single-use + short-lived; the `state`
nonce is verified constant-time before any exchange. Tokens are written via
`schwab_auth.save_token_cache` (atomic, 0600 on POSIX). The browser — not the
app — ever sees the user's Schwab password.

## Tests
`tests/unit/gui/test_schwab_connect_dialog.py`: pure-validator matrix
(match / state-mismatch / missing-state / missing-code / no-nonce / empty),
status when unconfigured, open-browser (nonce + authorize URL + browser
opened), unconfigured-blocked, exchange worker success/failure, and the
"Connect before Open is a no-op" guard.
