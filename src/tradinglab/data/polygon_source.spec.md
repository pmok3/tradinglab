# data/polygon_source.py — Spec

## Purpose
Polygon.io Aggregates v2 → `List[Candle]`. Two-layer module: a pure response-mapper for offline tests and an HTTP fetcher gated on credentials.

## Public API
- `candles_from_polygon_response(payload: dict, *, interval: str) -> List[Candle]` — pure mapper. Accepts either the standard envelope `{"results": [...]}` or a bare list. Delegates to `candles_from_json_rows` with `ts_unit="ms"` **and `tz=core.timezones.ET`** (ms-epoch UTC → US-Eastern wall-clock, matching yfinance); non-finite OHLC rows are skipped by that shared normalizer.
- `fetch_polygon_data(ticker="AAPL", interval="1d", *, lookback_days=None) -> Optional[List[Candle]]` — `DataFetcher`-compatible. Returns `None` whenever credentials are missing, the interval is unsupported, or the HTTP call fails. Registered as `"polygon"` in `DATA_SOURCES` when `PolygonCredentials.is_configured()` is True.

## Dependencies
- Internal: `..models.Candle`, `.credentials.PolygonCredentials`, `.credentials.get_credentials`, `.normalize.candles_from_json_rows`, `._http.{MAX_RESPONSE_BYTES, credentialed_opener}`.
- External: stdlib `urllib`, `json` (no external HTTP client).

## Design Decisions
- **Authenticates via `Authorization: Bearer <key>` HTTP header**
  (security audit H2). The legacy `?apiKey=…` query-string flow was
  removed — it leaked the API key into every URL written to
  diagnostics bundles, crash dumps, and (worst case) HTTP server
  logs of any 30x-redirect target. Polygon's docs equally support
  Bearer; the migration is invisible to the API. Tests in
  `tests/unit/data/test_polygon_bearer.py` lock this in.
- **Shared `credentialed_opener()`** (security audit I4). Both the
  HTTP call and the response read go through
  `data._http.credentialed_opener` so cross-host 30x redirects strip
  the `Authorization` header before forwarding. See `data/_http.spec.md`.
- **Capped response read** (`MAX_RESPONSE_BYTES = 8 MB` from
  `data/_http`) bounds a malicious or buggy server's ability to OOM
  the chart with an infinite stream. Polygon's largest realistic
  response (`limit=50000` aggregates) is ~3 MB.
- **Default lookback** via `constants.provider_lookback_days("polygon", interval)`: generous but fetch-speed-bounded per-interval windows with no yfinance 60-day intraday cap (5m ≈ 4mo, daily ≈ 15y) — see `constants.spec.md`.
- **Interval map**: `1m/5m/15m/30m → (n, minute)`, `1h → (1, hour)`, `1d/1wk/1mo → (1, day|week|month)`. Unsupported intervals return `None` rather than raise.
- **Never raises**: all HTTP/JSON errors caught in a broad `except Exception` and logged at WARNING. The app-level fallback handles `None`.
- **`adjusted=true, sort=asc, limit=50000`** baked into the URL — that's what the chart expects (chronological, split-adjusted bars).

## Invariants
- `fetch_polygon_data` returns either `None` or a (possibly empty) list of `Candle`. Never raises.
- Timestamps come back as ms-epoch UTC; the mapper converts them to **US Eastern** (`core.timezones.ET`) so session labels + intraday times are correct exchange wall-clock (else the session is shifted +5h). Falls back to UTC if `tzdata`/`ET` is unavailable.
- The interval keyspace matches `_INTERVAL_TO_POLYGON`; other intervals are rejected before HTTP.

## Testing
- Covered indirectly via integration smoke tests. The pure mapper `candles_from_polygon_response` is offline-testable with a sample payload (key-mapper parity check sits in `tests/unit/data/`).

