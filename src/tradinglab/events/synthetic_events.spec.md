# events/synthetic_events.py — Spec

## Purpose
Deterministic in-memory events fetcher for headless smoke tests (no network, no provider drift) and as fallback when the configured provider is unavailable. Seed derives from the ticker so different tickers yield different — but reproducible — timelines.

## Public API
- `fetch_synthetic_events(ticker: str) -> EventBundle`.

## Dependencies
Internal: `.base` (`EarningsRecord`, `DividendRecord`, `EventBundle`, `register_event_source`). External: `datetime`, `random`, `math`.

## Design Decisions
- **Seed = `hash((ticker, "events")) & 0xFFFFFFFF`.** Same pattern as the synthetic candle source; the `, "events"` salt prevents correlation with the price timeline.
- **Quarterly cadence ~91 days, BMO/AMC alternating.** EPS estimate/actual drawn from a stable per-ticker normal distribution so surprise percentages are realistic.
- **One special dividend Q1 2022, optional 2:1 split mid-2021 for ~30% of seeds.** Deterministic non-cash events to exercise the engine's quantity-adjustment path without flooding the timeline.
- **`is_future` rows have NaN actuals** so the gating layer's defensive redaction has something real to wipe.

## Invariants
- Returned bundle always has ≥1 earnings record and ≥1 dividend record for any non-empty ticker.
- Lists sorted (enforced by `EventBundle.__post_init__`).
- Identical inputs return identical outputs (Python `hash` seeded by `PYTHONHASHSEED`; smoke tests pin it).

## Algorithm
1. Seed a `random.Random` from `hash((ticker, "events"))`.
2. Walk quarterly calendar from 2018-01-15 forward, emitting `EarningsRecord` every ~91 days with EPS draws.
3. Walk quarterly calendar offset by ~45 days, emitting `DividendRecord` with a small per-ticker base amount.
4. Optionally insert one special dividend and one split.
5. Return `EventBundle(symbol=ticker, earnings=..., dividends=..., fetched_at=now_ms)`.
