# events/yfinance_events.py — Spec

## Purpose
yfinance-backed events fetcher. Calls `yfinance.Ticker(symbol)` and materialises `EarningsRecord` / `DividendRecord` lists from `Ticker.earnings_dates` + `Ticker.actions`. The column-tolerant DataFrame → records translation is delegated to `events.normalize` so the variant matrix is unit-testable without yfinance installed.

## Public API
- `fetch_yfinance_events(ticker: str) -> EventBundle | None`.

`events/__init__.py` registers this fetcher as the `"yfinance"` source unconditionally; the fetcher itself returns `None` when yfinance cannot import or fetch.

## Dependencies
Internal: `.base`, `.normalize`. External: `yfinance` (optional), `pandas` (via yfinance), **`lxml`** — yfinance's `Ticker.earnings_dates` parses an HTML calendar page via `pandas.read_html`, which raises `ImportError` without lxml (or html5lib). lxml is pinned as a direct dependency in `pyproject.toml` because yfinance>=1.3 dropped it from its declared deps. Audit `next-earn-lxml`.

## Design Decisions
- **Thin shell post-refactor** (~110 lines): owns the yfinance import, `Ticker(symbol)` construction, two property accesses, and bundle assembly. Decode lives in `events.normalize`.
- **Single Ticker per call.** `Ticker.earnings_dates` and `Ticker.actions` reuse the same yfinance session.
- **Partial success is useful.** Import failure, `Ticker` construction failure, or two empty decoded lists return `None`; one populated axis still returns an `EventBundle`.
- **Slot inference from hour.** yfinance's earnings-date column is tz-aware; `normalize._extract_index_hour_et` converts to ET, nudges 09:30–09:59 to hour 10, then `slot_from_hour` maps `<9` to `BMO`, `>=16` to `AMC`, and the rest to `DMH`. Approximate.
- **Adjusted bars stay on the price side** (Decision 9). This module does NOT back-out adjustments from dividend amounts; engine's `CorporateAction` consumer treats amounts as nominal per-share cash.
- **One-shot WARN log when `earnings_dates` raises** — module-level `_logged_earnings_dates_failure` set keys on exception class name so a chronic install issue (most commonly missing lxml) surfaces exactly once in the log instead of on every watchlist tick. The fetch continues with an empty earnings frame so dividends/splits can still return. Audit `next-earn-lxml`.

## Invariants
- Returned bundle's `fetched_at` is non-zero ms-since-epoch.
- Earnings rows have NaN actuals when future, finite actuals when landed.
- Dividend rows always have `ex_ts > 0` and `amount >= 0` (or NaN for split rows).

## Algorithm
1. Import yfinance; bail to `None` on `ImportError`.
2. Construct `Ticker(symbol)`.
3. Hand `Ticker.earnings_dates` to `normalize.normalize_earnings_df`.
4. Hand `Ticker.actions` to `normalize.normalize_actions_df`.
5. If both lists empty, return `None`.
6. Otherwise return `EventBundle(symbol=ticker, earnings=..., dividends=..., fetched_at=now_ms)`.

## Known limitations
- Slot inference is heuristic. A published earnings-time source could refine.
- yfinance schema drift: `normalize._resolve_column` silently returns `None` for unknown column names → NaN proliferation until a release adds the alias. Worth a smoke regression that asserts ≥30% of past earnings rows have non-NaN EPS.
