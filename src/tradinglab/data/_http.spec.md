# `data/_http.py` — Shared credential-safe HTTP opener

## Purpose
Single source of truth for the HTTP details that every vendor
fetcher must get right:
1. **Cross-host 30x redirects must strip credential headers**
   (`Authorization`, `APCA-API-KEY-ID`, etc.). Otherwise an
   attacker who can control a vendor-host DNS / TLS chain (or a
   compromised intermediary) can redirect the call to their own
   server and harvest the key.
2. **Response reads must be bounded.** A malicious or buggy server
   that streams unbounded JSON can OOM the chart. 8 MB is far
   above any real vendor payload and far below the OOM threshold.

Created during the security-audit remediation (audit ID **I4** /
contributing to fixes for **H2**, **M5**).

## Public API
- `MAX_RESPONSE_BYTES: int = 8 * 1024 * 1024` — read cap. Polygon
  aggregate replies (`limit=50000`) are ~3 MB; Schwab streamer-info
  ~2 KB; Alpaca bar pages ~500 KB. 8 MB has plenty of headroom for
  real growth.
- `credentialed_opener() -> urllib.request.OpenerDirector` —
  lazy singleton. Returns an `OpenerDirector` whose
  `HTTPRedirectHandler` has been replaced with
  `_StripCredentialsOnRedirect`. Callers use
  `credentialed_opener().open(req)` instead of `urllib.request.urlopen`.

## Internal helpers
- `_StripCredentialsOnRedirect(HTTPRedirectHandler)` — overrides
  `redirect_request`. Delegates to the stdlib redirect builder first,
  then, when the new URL points to a different hostname
  (case-insensitive compare) than the original request, strips every
  credential-shaped header from both `headers` and `unredirected_hdrs`
  on the returned request. Same-host redirects (e.g. path-only changes
  on a vendor load balancer) leave headers intact so auth doesn't break
  on benign 30x.
- `_is_credential_header(name: str) -> bool` — case-insensitive
  substring test. Returns `True` if the header name contains any
  of `"authorization"`, `"apca"`, `"key"`, `"secret"`, `"token"`.
  Conservative on purpose: false positives strip a harmless header,
  false negatives leak a credential. The header-name set covers
  every credential header used by the current urllib vendor/auth
  call sites (Schwab, Alpaca, Polygon).

## Callers
- `data/polygon_source.py::_http_get_aggs` — Bearer Authorization
  header.
- `data/alpaca_source.py::_http_get_bars` — `APCA-API-KEY-ID` +
  `APCA-API-SECRET-KEY` headers.
- `data/schwab_auth.py::_post_token` — `Authorization: Basic …`
  for OAuth token refresh.
- `streaming/schwab.py::fetch_streamer_info` — Bearer for
  `/userPreference`.

## Design Decisions
- **Lazy singleton, not module-level call at import time.**
  Building the opener at import would lock in test-time monkey
  patches of `urllib.request`. The lazy global is initialised on
  first `credentialed_opener()` call and reused thereafter; tests
  reset it by mutating the module-level `_OPENER` directly.
- **Case-insensitive hostname compare.** `urllib` normalises
  hostnames to lowercase in most code paths but not all; an
  explicit `.lower()` on both sides removes any ambiguity around
  `Api.Polygon.IO` vs `api.polygon.io`.
- **Strip from both `headers` and `unredirected_hdrs`.** The
  stdlib `Request` stores some headers (notably the ones set via
  `add_unredirected_header`) in a separate dict that
  `HTTPRedirectHandler` would re-attach on the next request. Both
  dicts must be scrubbed.
- **Substring match, not exact match.** Vendors invent their own
  header names (`X-APCA-API-KEY-ID`, `APCA-Auth-Token`, etc.). A
  substring test catches the family without needing to maintain
  an exact allow/deny list.
- **No retry logic here.** This module owns auth-safety and
  read-bounds. Retries belong in the per-vendor fetcher because
  they need vendor-specific knowledge of which status codes are
  retryable.

## Invariants
- `credentialed_opener()` returns the same `OpenerDirector`
  object across calls within a process.
- A 30x redirect that crosses hostnames strips every header whose
  lowercased name contains any of the substrings in
  `_is_credential_header`.
- Same-host 30x preserves all headers.
- `MAX_RESPONSE_BYTES` is consumed via `resp.read(MAX_RESPONSE_BYTES)`
  at the caller — this module does not own response reading, only
  the cap constant.

## Testing
- `tests/unit/data/test_http_redirect_strip.py` — 10 tests
  covering: cross-host strip of `Authorization`, `APCA-API-KEY-ID`,
  `APCA-API-SECRET-KEY`, `X-Custom-Token`; case-insensitive header
  name match; same-host preserves; cross-host vs same-host
  decision uses lowercased hostnames; both `headers` and
  `unredirected_hdrs` are scrubbed; opener is a singleton.
- `tests/unit/data/test_polygon_bearer.py` — exercises the
  Polygon fetcher's use of the opener.

## Known limitations
- Hostname-only compare. A redirect from
  `https://api.polygon.io/foo` → `https://evil.api.polygon.io/foo`
  is considered cross-host because the literal hostname differs;
  but `https://api.polygon.io/foo` → `https://api.polygon.io.evil.com/foo`
  is also caught because the hostnames likewise differ. We do NOT
  do registrable-domain (eTLD+1) compare, so a redirect within the
  same effective site but with a different subdomain (e.g.
  `api.polygon.io` → `data.polygon.io`) WOULD strip credentials.
  Polygon and the other supported vendors don't do this in practice;
  if they start to, the fix is to add per-vendor allow-lists.
- Bound is a global constant, not per-vendor. If a future vendor
  legitimately needs > 8 MB responses, we'd need a per-call
  override.
