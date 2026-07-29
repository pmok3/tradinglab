# data/schwab_source.py — Spec

## Purpose
Charles Schwab Market Data API (`/pricehistory`) → `List[Candle]`. Two-layer module: a pure response-mapper that is fully testable offline, plus an OAuth-gated HTTP fetcher.

## Public API
- `candles_from_schwab_response(payload: dict, *, interval: str) -> List[Candle]` — pure mapper. Tolerates both the standard `{"candles": [...]}` envelope and a bare list (some streaming-adjacent endpoints). Honors `empty: true` (returns `[]`). Uses `candles_from_json_rows` with `ts_unit="ms"` **and `tz=core.timezones.ET`** (ms-epoch UTC → US-Eastern wall-clock, matching yfinance / Alpaca; else the intraday session is shifted +5h).
- `fetch_schwab_data(ticker="AAPL", interval="1d") -> Optional[List[Candle]]` — `DataFetcher`-compatible. Returns `None` on missing credentials, missing/expired refresh token, network error, or unsupported interval. **Never raises.**
- `SCHWAB_REGISTRATION_ENABLED: bool = False` — registry/UI gate kept false until `_http_get_pricehistory` is implemented and the `"schwab"` source is actually registered.
- `verify_schwab(creds=None, *, timeout, opener) -> VerifyResult` — registered as the `schwab` verifier so the credentials dialog renders a "Test connection" button for this section like every other vendor.

## Credential verification without a probe

`verify_schwab` makes **no network call**. `_http_get_pricehistory` still
raises `NotImplementedError` and the OAuth flow has not shipped, so there is
nothing to probe — a fabricated request would either fail for the wrong
reason or, worse, return `ok` for a provider that cannot fetch a bar.

It answers the two questions that *are* decidable locally: empty fields →
`not_configured` (same as every other vendor); fields present →
`unsupported`, with remediation naming the missing piece. `unsupported`
renders muted rather than red, because nothing is wrong with the key.

Registering it at all is the point. Leaving Schwab as the one vendor with no
button was itself a UX bug — the user cannot distinguish "this provider has
no check" from "the check is missing", and silence reads as "probably fine".

When OAuth lands, replace the `unsupported` branch with a real probe (token
refresh + a one-symbol price-history call) and flip
`SCHWAB_REGISTRATION_ENABLED`; the dialog needs no change.

## Dependencies
- Internal: `..models.Candle`, `.credentials.SchwabCredentials`, `.credentials.get_credentials`, `.normalize.candles_from_json_rows`, `.schwab_auth.get_access_token`.
- External: stdlib only at module level; `schwab_auth` brings `urllib` for the token endpoint.

## Design Decisions
- **OAuth tokens come from `schwab_auth`**: the fetcher calls `get_access_token(creds)` which transparently reads `~/.tradinglab/tokens/schwab.json`, refreshes if needed, and returns the bearer string. If no cached refresh token exists, returns `None` and the fetcher logs a "run `schwab_login`" hint.
- **Interval map**: Schwab speaks `(periodType, frequencyType, frequency)` triples. Intraday uses `periodType="day"`; daily+ uses `periodType="year"`. The `"1h"` slot is mapped to 30-minute bars (Schwab has no 60-minute frequency) — would need downsampling at the consumer for true hour bars; current callers tolerate 30-min.
- **`_http_get_pricehistory` is currently a `NotImplementedError` stub**. The OAuth lifecycle is complete (`schwab_login` + `schwab_auth`) but the REST GET against `/pricehistory` has not been wired. `data/__init__.py` deliberately leaves the `"schwab"` source de-registered even when credentials are configured, so users never see a broken option in the dropdown. Re-enable the `register_source("schwab", ...)` line once `_http_get_pricehistory` is implemented.
- **Layered responsibility**: the pure mapper (`candles_from_schwab_response`) is unit-tested with hand-rolled payload dicts; the HTTP path is exercised only in integration.
- **Non-finite OHLC rows are dropped by the shared normalizer**: `candles_from_json_rows` skips provider rows whose open/high/low/close are NaN or infinite before building `Candle` objects.

## Invariants
- Returns either `None` or a list of `Candle`. Never raises.
- `empty: true` on the payload coerces to `[]`, NOT `None` — consumers treat both as failure, but the distinction lets a debug session see "we did contact Schwab and they had nothing".
- The interval keyspace matches `_INTERVAL_TO_SCHWAB`; other intervals return `None` before any HTTP call.

## Testing
- Covered indirectly via integration smoke tests. Pure mapper is offline-testable with a fixture payload; recommended placement `tests/unit/data/test_schwab_response.py`.
- `tests/unit/data/test_credential_health.py` pins `verify_schwab`: `not_configured` when empty, `unsupported` when configured, and **no network call** either way.

