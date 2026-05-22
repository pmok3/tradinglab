# data/alpaca_source.py — Spec

## Purpose
Alpaca Market Data v2 → `List[Candle]`. Two-layer module: a pure response-mapper for offline tests and an HTTP fetcher gated on credentials.

## Public API
- `candles_from_alpaca_response(payload: dict, *, interval: str) -> List[Candle]` — pure mapper. Accepts either the standard envelope `{"bars": [...]}` or a bare list. Uses `candles_from_json_rows` with `ts_unit="iso"` (Alpaca returns ISO-8601 `t` values).
- `fetch_alpaca_data(ticker="AAPL", interval="1d", *, lookback_days=None) -> Optional[List[Candle]]` — `DataFetcher`-compatible. Returns `None` on missing credentials, unsupported interval, or HTTP failure. Registered as `"alpaca"` in `DATA_SOURCES` when `AlpacaCredentials.is_configured()`.

## Dependencies
- Internal: `..models.Candle`, `.credentials.AlpacaCredentials`, `.credentials.get_credentials`, `.normalize.candles_from_json_rows`.
- External: stdlib `urllib`, `json`.

## Design Decisions
- **Static API-key auth** (no OAuth). Two headers — `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY` — are set on each request.
- **Shared `credentialed_opener()`** (security audit I4 / M5). HTTP
  call routed through `data._http.credentialed_opener` so cross-host
  30x redirects strip both `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY`
  before forwarding. The response read is bounded by
  `data._http.MAX_RESPONSE_BYTES` (8 MB) to defend against
  pathological server replies.
- **`feed` is part of credentials, defaults to `"iex"`**: free-tier feed. Configured via `ALPACA_FEED` env var if a paid SIP subscription is in play.
- **`adjustment=raw`**: returns un-split-adjusted prices. Different from yfinance, which is `auto_adjust=True`. Documented as a known divergence at the chart layer.
- **Default lookback**: 60 days for intraday intervals, 730 days for daily+. Matches the other vendor fetchers.
- **Interval map**: `{1m,5m,15m,30m,1h,1d,1wk,1mo} → Alpaca's `"1Min" / "1Hour" / "1Day"` etc.`
- **Never raises**: HTTP/JSON errors caught broadly; logged at WARNING; returns `None`.

## Invariants
- Returns either `None` or a list of `Candle`. Never raises.
- Timestamps stay in UTC (Alpaca returns ISO with `Z` suffix; normalizer parses to aware UTC).
- The interval keyspace matches `_INTERVAL_TO_ALPACA`; other intervals are rejected before HTTP.

## Testing
- Covered indirectly via integration smoke tests. Pure mapper is offline-testable with a sample payload.

