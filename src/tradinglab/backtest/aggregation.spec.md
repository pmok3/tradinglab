# backtest/aggregation.py — Spec

## Purpose
Pure-Python aggregator that derives higher-timeframe candles from a single primary intraday series. Consumed by the sandbox controller so the user can fetch one tick interval (e.g. `5m`) and view it as `15m`, `30m`, or `1h` without a second yfinance round-trip. Buckets are session-anchored so a `5m → 1h` aggregation of regular-trading-hours data produces a leading bar at 09:30 → 10:30 (matching how Yahoo / TradingView present 1h RTH bars), not the 09:00 → 10:00 a fixed UTC-modulo bucketing would emit.

## Public API
- `INTERVAL_MINUTES: Dict[str, int]` — supported intervals: `1m`, `2m`, `5m`, `15m`, `30m`, `1h`, `60m`.
- `interval_minutes(itv: str) -> int` — lookup; raises `ValueError` on unknown intervals.
- `divides_evenly(primary: str, target: str) -> bool` — cheap precondition. `True` iff `target_minutes % primary_minutes == 0`. Used by the dialog and the controller to validate selectable timeframe combos before calling `aggregate`. Returns `False` when either string is unknown or when `target < primary`.
- `aggregate(primary_candles, primary_interval, target_interval) -> List[Candle]` — main entry point. Returns a fresh `List[Candle]`. Identity-aggregation (same interval) returns a shallow copy. Raises `ValueError` if `target_interval` does not divide `primary_interval` evenly or either string is unsupported.

## Dependencies
- Internal: `..models.Candle`.
- External: standard library only (`datetime`).

## Design Decisions
- **Session-anchored bucketing.** On every new calendar date the anchor resets to that date's first primary-bar timestamp. Bucket index = `(d - anchor) // target_minutes`, so a 1h bucket starting at the session's first 5m bar (typically 09:30 ET in tz-aware data) produces `[09:30, 10:30)` rather than the UTC-clock-aligned `[09:00, 10:00)`. Pre/post sessions get their own anchor on the same calendar date if a calendar-date check coincidentally aligns; in practice tz-aware market data + a single calendar-date reset handles all RTH cases.
- **Trailing bucket is in-progress.** The last emitted bar is implicitly partial whenever the last primary bar's bucket is not yet full. Callers re-aggregate after every new primary bar; the trailing higher-TF bar mutates in place (volume sums, high/low extend, close follows latest primary close, timestamp stable until the bucket boundary is crossed).
- **Fresh `Candle` instances per call.** `aggregate` builds new objects rather than mutating the source. Series-list identity changes each call (intentional) so downstream cache invalidation is straightforward — the sandbox controller already nukes its `_series_cache` and indicator cache on each install.
- **Session-tag widening.** If any constituent of a higher-TF bucket is `regular`, the aggregated bar is `regular`. Pre/post stays only when the entire bucket is pre/post. This matches the chart's pre/post shading semantics (a regular-session bar drives a higher-TF bar regular, even if pre-market constituents are mixed in).
- **Bucket reset is keyed off bar calendar date in the bar's own timezone.** For ET-normalised candles this aligns naturally with the trading day; for UTC-normalised feeds the rollover happens at 00:00 UTC = 19:00 / 20:00 ET, which will split the trading day mid-session. Caller is responsible for feeding ET-normalised timestamps when session-anchoring matters.

## Invariants
- `aggregate(primary, p, t)` for `t == p` returns `list(primary)` (shallow copy; same content, distinct list object). **`Candle` references are shared** — callers that mutate a returned candle will mutate the source list's candle too. In practice `Candle` is a frozen dataclass so this is moot, but if it ever becomes mutable, callers must `dataclasses.replace(...)` instead of in-place mutation.
- `aggregate(primary, p, t)` for `t % p != 0` raises `ValueError` (never silently returns wrong-cadence bars).
- For valid `(p, t)`, `len(out) == ceil(span / t_minutes)` per session, where `span` covers all primary bars in that calendar date. The trailing bar's timestamp is stable across re-aggregation calls until the bucket fills.
- `volume` is summed as `int(c.volume)` per constituent (avoids float drift on long sessions).

## Testing
- `check_b10_sandbox_multi_interval`:
  - `divides_evenly` truth table (5m→15m, 5m→1h true; 2m→5m, 15m→5m false).
  - 6 5m bars → 2 15m buckets with correct OHLCV arithmetic.
  - 12 5m bars at 09:30 ET → exactly 1 1h bucket dated at 09:30 (session-anchor verification).
  - Forming-bar growth: appending one extra 5m bar to a partial 15m bucket extends in place (timestamp stable, high/close/volume update).

