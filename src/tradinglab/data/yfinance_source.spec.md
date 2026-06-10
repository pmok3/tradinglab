# data/yfinance_source.py — Spec

## Purpose
Live-data fetcher backed by yfinance. Thin adapter: pulls a DataFrame via `yf.Ticker(t).history(...)` and delegates to the vectorized `candles_from_dataframe` normalizer.

## Public API
- `fetch_live_data(ticker="AMD", interval="1d") -> Optional[List[Candle]]`. Returns `None` on any failure (import error — yfinance isn't installed; network error; empty frame). May return `[]` when the frame was non-empty but every provider row was dropped by normalization (for example non-finite OHLC). Registered as `"yfinance"` in `DATA_SOURCES`.

## Dependencies
- Internal: `..constants.INTERVAL_PERIODS`, `..constants.is_intraday`, `..models.Candle`, `.normalize.candles_from_dataframe`.
- External: `yfinance` (lazy-imported inside the function so the package can import successfully even without yfinance installed — smoke tests run on the synthetic source).

## Design Decisions
- **OHLC is split- and dividend-adjusted** — `Ticker().history(...)` is invoked without overriding `auto_adjust` (yfinance default is `True`). Pre-split prices are scaled retroactively. There is no option to fetch raw prices today. Trader implication: a backtest spanning a split or dividend will show smoothly continuous prices but will NOT reflect the actual cash a position would have realised at the time.
- **Lazy `import yfinance` inside the function**, not at module top. Keeps the package importable for users who only ever use the synthetic source (dev, tests).
- **`prepost=intraday`**: intraday fetches include pre/post bars; daily+ fetches don't. Session tagging is delegated to `candles_from_dataframe` → `classify_session`.
- **`period` chosen from `INTERVAL_PERIODS`** (e.g. `"5m"→"60d"`, `"1h"→"730d"`): maximizes history within yfinance's per-interval caps. Fallback `"2y"` for unknown intervals.
- **Uses `candles_from_dataframe` (not iterrows)**: 5–20× faster on typical intraday fetches; also populates the prebuilt-arrays side channel so the subsequent `SeriesArrays` build skips extraction.
- **Non-finite OHLC rows are dropped by the shared normalizer**: Yahoo can emit a phantom current-session row before any trade prints (NaN OHLC, sometimes stray volume). `candles_from_dataframe` filters those rows before `Candle` construction; NaN volume on otherwise-valid bars is still coerced to `0`.
- **Errors are caught at the source layer, never propagated** — a broad `except Exception` swallows yfinance's varied HTTP/JSON/KeyError failures and returns `None`. Diagnostics go via `print()` at `yfinance_source.py:43` (no `_status` available in this stateless module). This honours the `data/base.py` contract that fetchers MUST NOT raise.

## Invariants
- `fetch_live_data(t, i)` returns either `None` or a `List[Candle]` (possibly empty after non-finite-OHLC filtering). Empty frames are coerced to `None`.
- Returned bars carry US/Eastern tz for US equities (yfinance default for that asset class).
- For intraday calls, the result may contain pre/post bars (with correct session tags) if the ticker has them.

## Testing
- Smoke suite uses synthetic source by default; live fetch is exercised manually. `check_c6_bad_ticker` covers the failure path.

## Known limitations
- **Asset-class scope** — Tested with US equities and ETFs only (USD-denominated). yfinance accepts crypto / FX / international tickers but our normalisation, session classification, and ET timestamping all assume US-equity conventions. Do not rely on those asset classes.
- **Yahoo lookback caps** — Supported intervals: 1m, 2m, 5m, 15m, 30m, 60m/1h, 1d, 1wk, 1mo. Yahoo enforces lookback limits per interval (1m: ~7 days; 2–30m: ~60 days; 60m: ~730 days; daily+: full history). Requests beyond these silently return empty.
- **Pre/post-market data is sparse** — `prepost=True` is set, but TRF / dark-pool prints often have NaN volume and individual sub-15:00 ET pre-market trades may be aggregated. Volume in extended hours is NOT a reliable liquidity signal.
- **Single-ticker only** — Batch downloads return a `MultiIndex` columns DataFrame; downstream code does not handle that shape. Use one fetcher call per ticker.
- yfinance occasionally rate-limits or returns empty frames for transient reasons. No retry; the app-level fallback (disk cache or stale memory cache) papers over this.
- No `prepost=False` override for users who want to avoid extended-hours bars at fetch time rather than filter-time.

