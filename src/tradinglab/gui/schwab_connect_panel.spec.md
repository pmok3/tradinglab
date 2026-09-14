# gui/schwab_connect_panel.py — Inline account sign-in

Last updated: 2026-09-14

## Purpose
Schwab account authorization inside the Credentials window, below its developer
app settings. This is a `ttk.Frame`, not a separate Tools command or modal.
Schwab's site in the system browser owns passwords, MFA and account consent.

## Public API
- `SchwabConnectPanel(parent, on_connection_changed=None, on_prepare=None)`.
  Optional preparation runs on Tk before login, letting the parent save only
  Schwab app settings without closing or saving another vendor's draft.
- `cancel()` stops pending callback/exchange publication without clearing
  already-saved tokens. Widget destruction also cancels.
- Disconnect cancels pending work, clears both token-cache formats, and notifies
  the application only after successful deletion.

## Browser flow
Automatic HTTPS loopback return is the default. `CallbackListener` first binds
the exact saved URI; only its ready event opens the authorization URL with
`webbrowser.open(new=1, autoraise=True)`. Window versus tab remains browser
policy. No embedded webview or browser automation is used.

The panel explains the temporary local certificate prompt; no certificate trust
settings are changed. Bind/TLS/dependency failures are visible and manual
paste-back is explicitly selectable. Manual mode preserves the existing
dependency-free recovery flow, with the exact return origin/path and OAuth
state checked before exchanging a code.
Return and keypad Enter inside the paste-back entry finish sign-in and consume
the key event; they never invoke the Credentials window's Save & Close action.

## Threading and publication
Callback and exchange workers never touch Tk. The exchange writes only its
attempt-private result dictionary. Tk polls it and atomically saves both tokens
using the existing protected token cache, then invokes the lifecycle callback.
The app-key/secret/redirect identity and cache generation are captured BEFORE
opening the browser and checked before exchange and save. External clear,
credential edits, cancellation, destruction, or a newer attempt cannot publish
an old grant. A state nonce is consumed once at submission.

Refresh/persistence uses `data/schwab_auth`; the panel never manages a second
token store. Provider failure messages are sanitized; network protocol errors
also produce a visible failure rather than an abandoned worker.

## Tests
`tests/unit/gui/test_schwab_connect_panel.py`: callback readiness/browser order,
exact URI/state, automatic token publication and lifecycle notification, errors,
generation/credential changes, manual fallback, and cancellation/destruction.
`test_credentials_schwab_sign_in.py` checks the actual embedded parent flow.
