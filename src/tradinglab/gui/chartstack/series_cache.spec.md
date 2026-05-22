# `chartstack/series_cache.py` — Bounded per-card bar buffer

## Purpose
Tiny, pure-Python buffer for one card's recent OHLCV bars (default
60 — the §2.1 sparkline window). M2 wires this to fetched data;
M1 just nails down the data structure with a tested upsert /
rollover / eviction contract.

## Public API
- `Bar(ts, open, high, low, close, volume)` — dataclass.
- `CardSeriesCache(maxlen=60)`:
  - `upsert_tick(ts, ohlcv)` — mutate the trailing bar in-place when
    `ts` matches; otherwise append + evict oldest at capacity.
  - `append_rollover(bar)` — explicit append for finalized bars.
  - `snapshot() -> list[Bar]` — copy of the internal list.
  - `invalidate()` — clear all bars.
  - `latest() -> Bar | None`.
  - `len(cache)` — current bar count.
  - `maxlen` — configured cap.

## Design decisions
- **Plain `list`, not `collections.deque`.** Sparkline rendering
  reads the buffer end-to-end every frame; `list` indexing is
  fastest and the eviction cost (`del slice`) only matters at the
  cap edge.
- **Mutable in-place upsert** keeps live-tick redraws allocation-
  free — the M3 blit budget is 2 ms/card.
- **Decoupled from `models.Candle`** so unit tests don't need to
  build the bigger model graph.
