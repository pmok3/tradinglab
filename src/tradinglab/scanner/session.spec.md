# scanner/session.py — Spec

## Purpose

Session-boundary helper for the within-last-N-bars look-back walk
(`Condition.within_last_*`). Given a bar series and an index, returns
the lowest index that shares the same UTC calendar date as the bar at
`current_index`. The walk uses this to clamp its lower bound when any
`FieldRef` in the condition is daily-resetting (VWAP, HOD/LOD,
time-of-day RVOL, …) so a 9:35 AM "VWAP reclaim within last 5 bars"
doesn't peek at yesterday's close.

## Public API

- `find_session_open_index(bars: BarsNp, current_index: int) -> int`
  — pure, no I/O. Returns:
  - the lowest index `j` such that
    `bars.timestamps[j..current_index]` share one UTC date,
  - `current_index` unchanged on out-of-range / empty inputs,
  - `current_index` unchanged for daily-and-above intervals (each bar
    lives on its own UTC date → no clamp).

## Dependencies

- `numpy` (for `datetime64[D]` date truncation).
- `scanner.fields.BarsNp` (TYPE_CHECKING only; structural duck typing).

## Design Decisions

- **UTC dates only.** US-equity RTH (9:30 AM – 4:00 PM ET) fits inside
  one UTC day under both EST and EDT, so no `zoneinfo` arithmetic is
  needed. Matches the convention of `_today_mask` in `scanner.fields`.
- **No cache.** The dominant cost in the within-last walk is the
  per-bar condition evaluation, already memoized by `IndicatorMemo`.
  A per-bar cache here would be premature.
- **Out-of-range degrades gracefully.** `current_index < 0` or
  `≥ len(bars)` returns `current_index` unchanged so callers can chain
  `max(walk_low, returned_index)` without special-casing.

## Invariants

- Return value `j` satisfies `0 ≤ j ≤ current_index` whenever
  `0 ≤ current_index < len(bars)`.
- For daily-and-above bars, return `== current_index`.
- Never raises for in-range inputs.

## Testing

`tests/scanner/test_session.py` covers intraday clamping, daily
no-clamp, out-of-range fallback, empty input.

