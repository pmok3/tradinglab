# events/yfinance_events.py — Spec

## Purpose
yfinance-backed events fetcher. Calls `yfinance.Ticker(symbol)` and materialises `EarningsRecord` / `DividendRecord` lists from `Ticker.earnings_dates` + `Ticker.actions`. The column-tolerant DataFrame → records translation is delegated to `events.normalize` so the variant matrix is unit-testable without yfinance installed.

## Public API
- `fetch_yfinance_events(ticker: str) -> Optional[EventBundle]`.

Registered as the `"yfinance"` source in `EVENT_SOURCES` when yfinance imports cleanly; package falls back silently to synthetic otherwise.

## Dependencies
Internal: `.base`, `.normalize`. External: `yfinance` (optional), `pandas` (via yfinance).

## Design Decisions
- **Thin shell post-refactor** (~80 lines): owns the yfinance import, `Ticker(symbol)` construction, two property accesses, and bundle assembly. Decode lives in `events.normalize`.
- **Single Ticker per call.** `Ticker.earnings_dates` and `Ticker.actions` reuse the same yfinance session.
- **Returns `None` on any failure** (import error, network, empty response). Events cache then keeps serving the prior bundle.
- **Slot inference from hour.** yfinance's earnings-date column is tz-aware; `normalize.slot_from_hour` checks local-ET time-of-day against 09:30 / 16:00 to assign `BMO` / `DMH` / `AMC`. Approximate.
- **Adjusted bars stay on the price side** (Decision 9). This module does NOT back-out adjustments from dividend amounts; engine's `CorporateAction` consumer treats amounts as nominal per-share cash.

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
