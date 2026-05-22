# data/schwab_source.py — Spec

## Purpose
Charles Schwab Market Data API (`/pricehistory`) → `List[Candle]`. Two-layer module: a pure response-mapper that is fully testable offline, plus an OAuth-gated HTTP fetcher.

## Public API
- `candles_from_schwab_response(payload: dict, *, interval: str) -> List[Candle]` — pure mapper. Tolerates both the standard `{"candles": [...]}` envelope and a bare list (some streaming-adjacent endpoints). Honors `empty: true` (returns `[]`). Uses `candles_from_json_rows` with `ts_unit="ms"`.
- `fetch_schwab_data(ticker="AAPL", interval="1d") -> Optional[List[Candle]]` — `DataFetcher`-compatible. Returns `None` on missing credentials, missing/expired refresh token, network error, or unsupported interval. **Never raises.**

## Dependencies
- Internal: `..models.Candle`, `.credentials.SchwabCredentials`, `.credentials.get_credentials`, `.normalize.candles_from_json_rows`, `.schwab_auth.get_access_token`.
- External: stdlib only at module level; `schwab_auth` brings `urllib` for the token endpoint.

## Design Decisions
- **OAuth tokens come from `schwab_auth`**: the fetcher calls `get_access_token(creds)` which transparently reads `~/.tradinglab/tokens/schwab.json`, refreshes if needed, and returns the bearer string. If no cached refresh token exists, returns `None` and the fetcher logs a "run `schwab_login`" hint.
- **Interval map**: Schwab speaks `(periodType, frequencyType, frequency)` triples. Intraday uses `periodType="day"`; daily+ uses `periodType="year"`. The `"1h"` slot is mapped to 30-minute bars (Schwab has no 60-minute frequency) — would need downsampling at the consumer for true hour bars; current callers tolerate 30-min.
- **`_http_get_pricehistory` is currently a `NotImplementedError` stub**. The OAuth lifecycle is complete (`schwab_login` + `schwab_auth`) but the REST GET against `/pricehistory` has not been wired. `data/__init__.py` deliberately leaves the `"schwab"` source de-registered even when credentials are configured, so users never see a broken option in the dropdown. Re-enable the `register_source("schwab", ...)` line once `_http_get_pricehistory` is implemented.
- **Layered responsibility**: the pure mapper (`candles_from_schwab_response`) is unit-tested with hand-rolled payload dicts; the HTTP path is exercised only in integration.

## Invariants
- Returns either `None` or a list of `Candle`. Never raises.
- `empty: true` on the payload coerces to `[]`, NOT `None` — consumers treat both as failure, but the distinction lets a debug session see "we did contact Schwab and they had nothing".
- The interval keyspace matches `_INTERVAL_TO_SCHWAB`; other intervals return `None` before any HTTP call.

## Testing
- Covered indirectly via integration smoke tests. Pure mapper is offline-testable with a fixture payload; recommended placement `tests/unit/data/test_schwab_response.py`.

