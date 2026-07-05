# data/alpaca_source.py — Spec

## Purpose
Alpaca Market Data v2 → `List[Candle]`. Two-layer module: a pure response-mapper for offline tests and an HTTP fetcher gated on credentials.

## Public API
- `candles_from_alpaca_response(payload: dict, *, interval: str) -> List[Candle]` — pure mapper. Accepts either the standard envelope `{"bars": [...]}` or a bare list. Uses `candles_from_json_rows` with `ts_unit="iso"` (Alpaca returns ISO-8601 `t` values) **and `tz=core.timezones.ET`** so UTC bars are converted to US-Eastern wall-clock (matching yfinance); non-finite OHLC rows are skipped by that shared normalizer.
- `fetch_alpaca_data(ticker="AAPL", interval="1d", *, lookback_days=None, start=None, end=None) -> Optional[List[Candle]]` — `DataFetcher`-compatible. Without `start`/`end`, fetches a trailing window (`lookback_days` or `provider_lookback_days`). Passing kw-only `start`/`end` (aware datetimes) fetches that **explicit range** instead — the targeted intraday fetch path; this is what marks Alpaca `supports_range=True` at registration. Returns `None` on missing credentials, unsupported interval, or HTTP failure. Registered as `"alpaca"` in `DATA_SOURCES` (with `supports_range=True`) when `AlpacaCredentials.is_configured()`.

## Dependencies
- Internal: `..models.Candle`, `.credentials.AlpacaCredentials`, `.credentials.get_credentials`, `.normalize.candles_from_json_rows`, `._http.{MAX_RESPONSE_BYTES, credentialed_opener}`.
- External: stdlib `urllib`, `json`.

## Design Decisions
- **Static API-key auth** (no OAuth). Two headers — `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY` — are set on each request.
- **Pagination via `next_page_token`**. `_http_get_page(...)` fetches one page (`limit=10000`, optional `page_token`); `_accumulate_bars(fetch_page)` walks pages until the payload has no `next_page_token`, concatenating every page's `bars` into one `{"bars": [...]}` envelope handed to the mapper. `fetch_page` is injected so the pagination loop is offline-testable. A `_MAX_PAGES=200` safety cap prevents an infinite loop if the vendor mis-paginates (never-null token); hitting it logs a WARNING and returns the truncated result. Without this, a 60-day 1-minute request (~23k bars) silently truncated at the first 10k-bar page.
- **Shared `credentialed_opener()`** (security audit I4 / M5). HTTP
  call routed through `data._http.credentialed_opener` so cross-host
  30x redirects strip both `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY`
  before forwarding. The response read is bounded by
  `data._http.MAX_RESPONSE_BYTES` (8 MB) to defend against
  pathological server replies.
- **`feed` is part of credentials, defaults to `"iex"`**: free-tier feed. Configured via `ALPACA_FEED` env var if a paid SIP subscription is in play.
- **`adjustment=raw`**: returns un-split-adjusted prices. Different from yfinance, which is `auto_adjust=True`. Documented as a known divergence at the chart layer.
- **Default lookback** via `constants.provider_lookback_days("alpaca", interval)`: Alpaca has no yfinance 60-day intraday cap, but each intraday window is **fetch-speed-bounded** (the whole series loads up front, so 5m ≈ 4 months / ~120d, 1h ≈ 4y, 1m ≈ 1mo — each ~1 API page / ≲3s to clear the 5s drilldown deadline). Daily requests ~15y (the server caps to the plan's availability — free IEX ≈ 6y for AAPL). Replaces the old 730d/60d yfinance-matched cap that truncated daily history to ~2 years and drilldown to ~60 days.
- **Interval map**: `{1m,5m,15m,30m,1h,1d,1wk,1mo} → Alpaca's `"1Min" / "1Hour" / "1Day"` etc.`
- **Non-finite OHLC rows are dropped by the shared normalizer** before `Candle` construction.
- **Never raises**: HTTP/JSON errors caught broadly; logged at WARNING; returns `None`.

## Invariants
- Returns either `None` or a (possibly empty) list of `Candle`. Never raises.
- Timestamps arrive UTC (ISO with `Z` suffix) and are converted to **US Eastern** (`core.timezones.ET`) by the mapper, so `classify_session` and the chart read correct exchange wall-clock. Without this a 09:30 ET open bar (14:30Z) is mis-classified and the intraday session is shifted +5h (the "5m data only shows 14:30–16:00" bug). Falls back to UTC if `tzdata`/`ET` is unavailable.
- The interval keyspace matches `_INTERVAL_TO_ALPACA`; other intervals are rejected before HTTP.

## Testing
- `tests/unit/data/test_alpaca_source.py` — offline: the pure mapper (envelope + bare-list + empty + non-finite-drop + ET-localized timestamps/session labels + daily-date preservation) and the `_accumulate_bars` pagination loop (single page, multi-page token walk, non-dict stop, empty-token stop, `max_pages` cap, accumulate→map round-trip). Live fetch (`_http_get_page`) is `# pragma: no cover` (network).

