# data/schwab_source.py — Spec

Last updated: 2026-09-08

## Purpose
Charles Schwab Market Data API (`/pricehistory`) → `List[Candle]`. Two-layer module: a pure response-mapper that is fully testable offline, plus an OAuth-gated HTTP fetcher.

## Public API
- `candles_from_schwab_response(payload, *, interval, start=None, end=None) -> List[Candle]` — pure mapper. Tolerates standard `{"candles": [...]}` and bare lists. Honors `empty: true`. Shared normalization uses milliseconds UTC converted to **`core.timezones.ET`**. Sorts/deduplicates timestamps (last duplicate wins); optional range clips contributions and output buckets to `[start,end)`. For `1h`, input is genuine one-minute bars, aggregated through the shared resampler.
- `price_history_params(ticker, interval, *, start=None, end=None) -> dict` — pure validated mapping of symbol/interval/range to Schwab query parameters.
- `fetch_schwab_data(ticker="AAPL", interval="1d", *, start=None, end=None) -> Optional[List[Candle]]` — compatible with `DataFetcher` and `base.fetch_range`'s paired aware datetime convention. Returns `None` for missing auth, unsupported requests, and expected HTTP/cache/parse failures; logs sanitized diagnostics and records runtime credential failures. Unexpected programming errors are not swallowed.
- `SCHWAB_REGISTRATION_ENABLED: bool = False` — remains false pending live commissioning. Offline implementation or a successful explicit probe never enables registration.
- `verify_schwab(creds=None, *, timeout, opener) -> VerifyResult` — registered as the `schwab` verifier so the credentials dialog renders a "Test connection" button for this section like every other vendor.

## Explicit credential verification

Import/registration makes no network calls. Explicit `verify_schwab` returns
`not_configured` for empty keys, and `unsupported` with a truthful OAuth-required
message for missing/expired/mismatched authorization. Unsaved typed credentials
cannot be verified by an existing account's token: values must match the currently
configured pair. New token caches additionally bind the app pair by fingerprint.

With valid local authorization it probes AAPL daily history over the last seven
calendar days. Stale access tokens may refresh first; both operations use the
supplied timeout/opener. Success requires usable candles and means only
**OAuth price-history access verified**, not validation of app secrets by a
fresh OAuth grant, other entitlements, streaming, or source commissioning.
Empty/malformed successful responses are not reported as ready.

HTTP 401 is rejected authorization with reconnect guidance, 403 is forbidden,
429 is rate-limited, and timeout/5xx/HTTP protocol errors (including truncated
chunked replies) are network-error. Both the high-level fetcher and explicit
verifier catch `http.client.HTTPException`, including during token refresh.
Raw response bodies,
exception strings, and tokens never enter logs/status messages.

## Dependencies
- Internal: `..models.Candle`, `.credentials`, `.normalize`, `.schwab_auth`, `.verify`, `._http`, and `..streaming.resampler`.
- External: stdlib HTTP (`urllib`); existing shared normalization dependencies only.

## Design Decisions
- **OAuth**: `schwab_auth.get_access_token(..., raise_errors=True)` retains refresh failure taxonomy. No cached authorization produces a Connect-to-Schwab hint, not a broken-source exception.
- **Interval map**: `1m/5m/10m/15m/30m` use `periodType=day`, `frequencyType=minute`, native frequency; `1d/1wk/1mo` use `periodType=year` and `daily/weekly/monthly`, frequency 1. `1h` fetches 1m and aggregates (never relabels 30m).
- **Hourly alignment**: `BarResampler("1h", session_segments=True)` owns ET date/session boundaries via shared `session_anchor`/calendar constants; no vendor-local segmentation rules. Anchors are 04:00/09:30/16:00 ET. Final partial buckets are retained, including the short regular-session 15:30 bucket. Empty gaps are not fabricated. A range beginning within an hour does not emit a mislabeled partial bucket before its lower bound; source bars at/after its exclusive upper bound cannot affect OHLCV. Intraday data outside the supported 04:00-20:00 equity session is rejected rather than misbucketed.
- **REST GET**: `https://api.schwabapi.com/marketdata/v1/pricehistory`, symbol in encoded query, bearer only in Authorization header. The shared credential-safe opener strips credentials on cross-host redirects. Response reads use the shared 8 MB cap plus one-byte overflow sentinel; oversized, non-object or missing-candle responses are rejected.
- **Windows/ranges**: default lookback is day/10 intraday and year/1 daily+. Explicit bounds must be paired, aware, ascending; sent as UTC epoch milliseconds with `endDate=end_ms-1`, omitting `period`. Extended hours explicitly included, previous-close metadata not requested. No pagination, retries or polling are created here.
- **Non-finite OHLC rows are dropped by the shared normalizer**: `candles_from_json_rows` skips provider rows whose open/high/low/close are NaN or infinite before building `Candle` objects.
- **Array-stash ownership**: consume `pop_prebuilt_arrays(original)` before replacing the normalized list. Native intervals apply the same sorted/deduplicated/range-filtered indices to all five columns and stash only the final nonempty list; hourly conversion discards the original minute arrays, including on aggregation failure. Verification consumes its own probe's final stash because no chart-series consumer follows. Discarded input lists must never remain pinned by the normalization side channel.

## Invariants
- The high-level fetcher returns `None` or candles for expected operational outcomes; lower-level HTTP/request helpers raise precise failures.
- `empty: true` on the payload coerces to `[]`, NOT `None` — consumers treat both as failure, but the distinction lets a debug session see "we did contact Schwab and they had nothing".
- The interval keyspace matches `_INTERVAL_TO_SCHWAB`; other intervals return `None` before any HTTP call.

## Testing
- `tests/unit/data/test_schwab_source.py`: native interval/request mapping, encoded endpoint/auth/timeout/body bounds, HTTP taxonomy, explicit refresh+probe, empty/error handling, disabled registration, real hourly OHLCV, DST/session boundaries, exclusive range clipping.
- `tests/unit/data/test_credential_health.py`: keys alone never imply OAuth authorization.
- `tests/unit/test_vendor_mappers.py`: shared vendor normalization compatibility.
- `test_schwab_source.py` also reproduces real stdlib chunked-body truncation without a socket, checks native list/array identity and column alignment, and repeats 32 conversions of 7,680 minute rows while asserting zero retained original minute lists/candles. Empty ranges and failed aggregation also leave no original stash.

## Reference and commissioning limits
Parameter names, endpoint, native frequencies and allowed period combinations
were checked against the public
[schwab-py client source](https://github.com/alexgolec/schwab-py/blob/master/schwab/client/base.py)
(`PriceHistory`, `get_price_history`, interval helpers) on 2026-09-08.
Provider retention limits vary by interval/account; this adapter does not claim
full requested-range coverage, paginate older history, or synthesize unavailable
bars. In particular 1h inherits one-minute retention. No live Schwab account has
been used to commission this implementation.
