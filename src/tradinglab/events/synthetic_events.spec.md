# events/synthetic_events.py — Spec

## Purpose
Deterministic in-memory events fetcher for headless smoke tests (no network, no provider drift) and as fallback when the configured provider is unavailable. Seed derives from the ticker so different tickers yield different — but reproducible — timelines.

## Public API
- `fetch_synthetic_events(ticker: str) -> EventBundle | None` — returns `None` for a blank ticker.

## Dependencies
Internal: `.base` (`EarningsRecord`, `DividendRecord`, `EventBundle`). External: `datetime`, `random`, `math`.

## Design Decisions
- **Seed = `hash((ticker, "events")) & 0xFFFFFFFF`.** Same pattern as the synthetic candle source; the `, "events"` salt prevents correlation with the price timeline.
- **Quarterly cadence ~91 days, BMO/AMC alternating.** EPS estimate/actual drawn from a stable per-ticker normal distribution so surprise percentages are realistic.
- **One special dividend Q1 2022, optional 2:1 split mid-2021 for ~30% of seeds.** Deterministic non-cash events to exercise the engine's quantity-adjustment path without flooding the timeline.
- **`is_future` rows have NaN actuals** so the gating layer's defensive redaction has something real to wipe.

## Invariants
- Returned bundle has ≥1 earnings record and ≥1 dividend record for any non-empty ticker; blank tickers return `None`.
- Lists sorted (enforced by `EventBundle.__post_init__`).
- Identical non-empty tickers return identical earnings/dividend timelines (Python `hash` seeded by `PYTHONHASHSEED`; smoke tests pin it). `fetched_at` is the current clock in ms.

## Algorithm
1. Seed a `random.Random` from `hash((ticker, "events"))`.
2. Walk quarterly calendar from a ticker-seeded 2018 start date forward, emitting `EarningsRecord` every ~91 days with EPS draws.
3. Walk quarterly calendar offset by ~45 days, emitting `DividendRecord` with a small per-ticker base amount.
4. Insert one special dividend and optionally one split.
5. Return `EventBundle(symbol=ticker, earnings=..., dividends=..., fetched_at=now_ms)`.
