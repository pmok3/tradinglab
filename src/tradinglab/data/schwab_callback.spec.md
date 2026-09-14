# data/schwab_callback.py — Temporary HTTPS OAuth return

Last updated: 2026-09-14

## Purpose
Capture one OAuth browser redirect on loopback without a web framework,
browser automation, embedded webview, or changes to certificate trust stores.
The receiver never exchanges or saves tokens and never calls Tk.

## Public API
- `callback_target(uri)` validates the exact configured HTTPS URI. Only literal
  `127.0.0.1`, `localhost` (bound directly to IPv4 loopback), and `[::1]` are
  supported. Ports 1–65535 and an exact path are supported; omitted port is 443.
  User information, query/fragment, whitespace and non-loopback hosts fail.
- `callback_response(query, state)` validates a single matching state and
  exactly one authorization code or provider error. Duplicate fields, blank codes
  and unbounded responses fail. Provider error text is never reflected.
- `CallbackListener(uri, state, timeout=300)` has `start`, nonblocking `poll`,
  and nonblocking `close`. Immutable events are `ready`, `authorized` (code),
  or `error` (safe message). Only `ready` permits opening the browser.

## Lifecycle and privacy
The daemon worker creates a unique, ten-minute self-signed ECDSA certificate
using the optional `cryptography` dependency, then binds only loopback. TLS 1.2
is the minimum. Temporary private PEM files are removed immediately after
loading the TLS context; the certificate is never installed or trusted globally.
The browser may prompt about the local certificate. This is not a warning on
Schwab's website, and the application never disables browser certificate checks.

Host and path must match the target. Wrong-state/path probes do not consume the
attempt. A valid response or denial is single-use. Receive/handshake operations
have a one-second inactivity limit and an absolute two-second request deadline.
Cancellation interrupts the active socket, including incomplete trickled headers;
the idle server checks cancellation every 100 ms. A separate attempt deadline
also interrupts an active request, rather than waiting for a socket idle timeout.
The attempt expires after five minutes (at most ten if explicitly configured).
All paths close sockets and remove temporary certificate files.

No request URL, authorization code, provider error body or secrets are logged.
The fixed completion page sends no-store/no-referrer/CSP headers and clears
the query from browser history without sending data elsewhere. Success here
means callback received, not that token exchange or persistence succeeded.

## Tests
`tests/unit/data/test_schwab_callback.py`: exact-URI/parser matrix, live local
TLS callback with fake codes, wrong host/path/state, duplicate/denied responses,
timeout, cancellation, occupied port, missing crypto and certificate cleanup.
Slow-header regressions prove both cancellation and absolute deadlines release
the listener without needing the peer to finish its request.
