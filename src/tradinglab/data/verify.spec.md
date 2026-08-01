# `data/verify.py` — credential verification

## Purpose

Answer the question a new user actually asks after pasting an API key:
**"is this thing going to work?"**

`AlpacaCredentials.is_configured()` and friends only report whether the
fields are non-empty. That presence check is the correct gate for
*registration* — startup must never depend on the network — but it says
nothing about whether the key is accepted, whether the account is entitled
to the requested feed, or whether the vendor is even reachable. Before this
module a typo'd secret registered the source and then silently returned
`None` from every fetch, leaving the user with an empty chart and no
explanation.

This module is the vendor-agnostic half of the answer. The per-vendor
probes live with their sources (`alpaca_source.verify_alpaca`,
`polygon_source.verify_polygon`).

## Design

Verification is a **per-vendor capability in a side table**, registered via
`register_verifier(vendor, fn)` — deliberately mirroring the
`_PAGE_FETCHERS` / `_RANGE_CAPABLE` idiom in `data/base.py`. Consumers (the
credentials dialog today; a status bar or CLI diagnostic tomorrow) go
through `verify_vendor(vendor, creds)` and never branch per vendor. Adding a
future provider is one registration line, not a new UI branch.

## Status taxonomy

The taxonomy is the feature. A boolean would collapse three situations that
demand completely different user actions into one useless "failed".

| Status | Meaning | What the user must do |
|---|---|---|
| `ok` | Credentials work for the capability we need. | Nothing. |
| `not_configured` | Required fields empty. | Fill them in. |
| `invalid_credentials` | Vendor rejected the key (HTTP 401). | Re-copy key/secret. |
| `forbidden` | Authenticated but **not entitled** (HTTP 403). The key is *fine*. | Fix the plan setting, or upgrade. |
| `rate_limited` | HTTP 429. Credentials are fine. | Wait, retry. |
| `network_error` | DNS / TLS / timeout / 5xx. Says nothing about the key. | Check connectivity. |
| `unsupported` | No verifier registered for this vendor. | n/a |
| `error` | Reached the vendor, couldn't interpret the answer. | Retry / report. |

`ALL_STATUSES` pins the set; a test asserts the credentials dialog has a
render style for every member, so a newly-added status can't fall through
to a silent muted dash.

**403 is not `invalid_credentials`.** This is the single most important
mapping decision. On most vendors a 403 means the key is valid and the
plan is insufficient; telling the user to re-copy a correct key sends them
down the wrong path. `CREDENTIAL_PROBLEM_STATUSES` still includes
`forbidden` (the user must change *something* in the dialog) but the
rendered colour is amber, not red.

## `VerifyResult`

Frozen dataclass: `status`, `vendor`, `summary` (one user-facing line),
`detail` (remediation), `entitlements` (vendor facts worth showing or
caching — feed, plan, request budget), `latency_ms`, `http_status`.

* `ok` — `status == "ok"`.
* `is_credential_problem` — the user must edit a field to fix it.
* `as_log_line()` — compact, secret-free line for the app log.

**Secret-free by construction.** The field set is pinned by a test: a
`VerifyResult` is cached in memory and written to the log, so it must never
gain a field that could carry a key.

## Redaction

`redact(text, secrets)` scrubs vendor error bodies before they reach a GUI
label — some providers echo the submitted key back in the error message.
Values shorter than 8 characters are ignored: redacting a 3-character
"secret" would mangle unrelated prose and cannot be a real API key.

`read_error_body(exc, secrets)` reads at most `MAX_ERROR_BODY_BYTES` of an
`HTTPError` body, extracts a one-line message (`message` / `error` /
`detail` JSON keys, or plain text), and redacts it. Never raises — a
diagnostic nicety must never become the reason verification fails.

## Error translation

`result_from_exception(exc, vendor=, secrets=, latency_ms=)` is the shared
translation layer:

* `HTTPError` → status-code mapping + redacted vendor message + a default
  remediation `detail`.
* `URLError` / `TimeoutError` / `OSError` → `network_error`. **Never blame
  the credentials for a dead network.**
* anything else → `error`.

Pure apart from reading the error body; unit-tested with synthetic
exceptions and no network.

## `verify_vendor` contract

```python
verify_vendor(vendor, creds=None, *, timeout=DEFAULT_TIMEOUT_S, opener=None)
    -> VerifyResult
```

* **Never raises.** A verifier that throws is caught and reported as
  `error` — the caller is a Tk callback or a worker thread, and an escaping
  exception there is a hang or a crash.
* `creds=None` falls back to the process-wide credentials. Passing explicit
  creds is what lets the credentials dialog verify values the user has
  **typed but not yet saved**, so they can fix a typo and re-test without
  committing a bad blob to the DPAPI store.
* `opener` is the injection seam that keeps every verifier test offline.
* `DEFAULT_TIMEOUT_S = 10.0` — shorter than the 15 s fetch timeout. A user
  sitting in front of a "Test connection" button wants a fast answer, and a
  slow provider is itself useful information.

## Result cache

`record_result` / `last_result` / `clear_results` — in-process, keyed by
vendor. Lets the credentials dialog (and the vendor-header chips) answer "is
Alpaca ready?" without re-probing. `clear_results()` is called when
credentials change, so a stale "verified" can never outlive the keys it was
measured against.

### Persisted verdicts

`record_result` also writes `(status, checked_at, summary)` into the
credential store via `credential_store.record_verification` (pass
`persist=False` to opt out). Persistence is what lets the app render a
vendor's health **at launch with no network call** — previously every restart
started from "unknown" and the user had to open a dialog and click Test to
learn anything.

Only those three inert fields are stored, never key material. That restraint
is the whole reason it is safe to keep a verdict beside the secrets.

`persisted_result(vendor)` reads it back; `known_status(vendor)` returns the
in-process verdict when there is one (it was measured against the credentials
currently loaded) and otherwise falls back to the persisted one.

Invalidation happens at the storage layer, not here:
`credential_store.save_vendor` drops the verdict when new key material lands,
and `clear_vendor` drops it with the fields.

## Runtime failure routing

`note_runtime_failure(vendor, exc, secrets=...)` classifies a **live fetch**
failure through the same taxonomy the explicit Test-connection button uses,
and records it when — and only when — the status is in
`CREDENTIAL_PROBLEM_STATUSES`.

A revoked key or a downgraded plan does not announce itself. Without this the
only symptom is an empty chart and a generic "no data", and nothing points at
the credentials: `is_configured()` still reports `True`, because presence
never stopped being true. Now a mid-session 401 flips the vendor to
`invalid_credentials` and a 403 to `forbidden`.

Transient failures (429, 5xx, timeouts, parse errors) return `None` and are
**not** recorded — they say nothing about the key, and letting them poison a
vendor's status would train the user to ignore the indicator. Called from
`alpaca_source.fetch_alpaca_data` and `polygon_source.fetch_polygon_data`.

## Non-goals

* **Not a registration gate.** `data.register_vendor_sources()` stays on the
  presence check. Making startup network-bound would break offline
  configuration and add latency to every launch.
* **Not automatic.** Probes are user-initiated (or explicitly triggered).
  No background polling — a credentials dialog that silently makes network
  requests on open would be a surprise. `note_runtime_failure` is not a
  probe: it only classifies a request the app was making anyway.

## Tests

`tests/unit/data/test_verify.py` — taxonomy mapping, redaction, exception
translation, registry dispatch and isolation, the secret-free field-set
invariant, result cache.
`tests/unit/data/test_credential_health.py` — verdict persistence +
`known_status` precedence, runtime-failure routing (credential vs transient),
redaction on the runtime path, and the Schwab verifier.

## See also

* `data/alpaca_source.spec.md` — the Alpaca probe and its SIP→IEX
  disambiguation.
* `data/polygon_source.spec.md` — the Polygon probe.
* `data/__init__.spec.md` — `register_vendor_sources()`.
* `gui/credentials_dialog.spec.md` — the "Test connection" UI.
