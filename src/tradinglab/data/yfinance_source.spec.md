# data/yfinance_source.py — Spec

Last updated: 2026-09-23

## Purpose
Live-data fetcher backed by yfinance. Thin adapter: pulls a DataFrame via `yf.Ticker(t).history(...)` and delegates to the vectorized `candles_from_dataframe` normalizer.

## Public API
- `fetch_live_data(ticker="AMD", interval="1d") -> Optional[List[Candle]]`. Returns `None` on any failure (import error — yfinance isn't installed; network error; empty frame). May return `[]` when the frame was non-empty but every provider row was dropped by normalization (for example non-finite OHLC). Registered as `"yfinance"` in `DATA_SOURCES`.
  - **Ratio pseudo-symbols resolved FIRST.** If `parse_ratio_symbol(ticker)` matches (e.g. `AMD/NVDA` — see `data/ratio_source.spec.md`), the function returns `fetch_ratio(ticker, interval, leg_fetcher=fetch_live_data)` before any yfinance call, recursing on the two legs through this same fetcher. This makes a ratio symbol work as primary / compare / watchlist / prefetch / synth-today-bar ticker anywhere a real symbol does, with no per-surface wiring.

## Dependencies
- Internal: `..constants.INTERVAL_PERIODS`, `..constants.is_intraday`, `..models.Candle`, `.normalize.candles_from_dataframe`, `.ratio_source.{fetch_ratio, parse_ratio_symbol}`.
- External: `yfinance` (lazy-imported inside the function so the package can import successfully even without yfinance installed — smoke tests run on the synthetic source).

## Design Decisions
- **OHLC is split- and dividend-adjusted** — `Ticker().history(...)` is invoked without overriding `auto_adjust` (yfinance default is `True`). Pre-split prices are scaled retroactively. There is no option to fetch raw prices today. Trader implication: a backtest spanning a split or dividend will show smoothly continuous prices but will NOT reflect the actual cash a position would have realised at the time.
- **Lazy `import yfinance` inside the function**, not at module top. Keeps the package importable for users who only ever use the synthetic source (dev, tests).
- **`prepost=intraday`**: intraday fetches include pre/post bars; daily+ fetches don't. Session tagging is delegated to `candles_from_dataframe` → `classify_session`.
- **`period` chosen from `INTERVAL_PERIODS`** (e.g. `"5m"→"60d"`, `"1h"→"730d"`): maximizes history within yfinance's per-interval caps. Fallback `"2y"` for unknown intervals.
- **Uses `candles_from_dataframe` (not iterrows)**: 5–20× faster on typical intraday fetches; also populates the prebuilt-arrays side channel so the subsequent `SeriesArrays` build skips extraction.
- **Non-finite OHLC rows are dropped by the shared normalizer**: Yahoo can emit a phantom current-session row before any trade prints (NaN OHLC, sometimes stray volume). `candles_from_dataframe` filters those rows before `Candle` construction; NaN volume on otherwise-valid bars is still coerced to `0`.
- **Errors are caught at the source layer, never propagated** — a broad `except Exception` catches yfinance's varied HTTP/JSON/KeyError failures that reach the adapter and returns `None`. Diagnostics go via `print()` in `fetch_live_data` (no `_status` available in this stateless module). This honours the `data/base.py` contract that fetchers MUST NOT raise. yfinance may instead handle an error internally and return an empty frame, which is also coerced to `None`.
- **Explicit 10 s history-request timeout** — `Ticker().history(...)` is called with `timeout=YFINANCE_TIMEOUT_S` (`10`), preserving yfinance 1.3.0's existing finite default rather than adding a previously missing timeout. The constant is read at call time (not bound as a default arg) so tests can monkeypatch it. This is not a whole-operation deadline: timezone/cookie bootstrap requests and internal retries/backoff are outside a single history-request timeout budget, so the complete fetch can take longer.

## Invariants
- `fetch_live_data(t, i)` returns either `None` or a `List[Candle]` (possibly empty after non-finite-OHLC filtering). Empty frames are coerced to `None`.
- Returned bars carry US/Eastern tz for US equities (yfinance default for that asset class).
- For intraday calls, the result may contain pre/post bars (with correct session tags) if the ticker has them.

## Testing
- Test conftest pins startup to `"yfinance"` and stubs the yfinance fetcher with deterministic offline candles, so the smoke suite exercises the registry path without network. Live fetch is exercised manually. `check_c6_bad_ticker` covers the failure path.
- `tests/unit/data/test_yfinance_timeout.py` uses a fake yfinance module to pin the explicit `timeout=10` argument for daily and intraday history calls, call-time constant overrides, unchanged request options and candle normalization, diagnostic/`None` handling for raised errors, empty-frame/`None` handling, all-invalid-OHLC/`[]` handling, and the missing-import path. These offline adapter tests do not measure live transport timing or establish a whole-fetch deadline.

## Known limitations
- **Asset-class scope** — Tested with US equities and ETFs only (USD-denominated). yfinance accepts crypto / FX / international tickers but our normalisation, session classification, and ET timestamping all assume US-equity conventions. Do not rely on those asset classes.
- **Yahoo lookback caps** — Supported intervals: 1m, 2m, 5m, 15m, 30m, 60m/1h, 1d, 1wk, 1mo. Yahoo enforces lookback limits per interval (1m: ~7 days; 2–30m: ~60 days; 60m: ~730 days; daily+: full history). Requests beyond these silently return empty.
- **Pre/post-market data is sparse** — `prepost=True` is set, but TRF / dark-pool prints often have NaN volume and individual sub-15:00 ET pre-market trades may be aggregated. Volume in extended hours is NOT a reliable liquidity signal.
- **Single-ticker only** — Batch downloads return a `MultiIndex` columns DataFrame; downstream code does not handle that shape. Use one fetcher call per ticker.
- yfinance occasionally rate-limits or returns empty frames for transient reasons. This adapter adds no retries of its own (yfinance may retry internally); the app-level fallback uses disk cache or stale memory cache.
- No `prepost=False` override for users who want to avoid extended-hours bars at fetch time rather than filter-time.
