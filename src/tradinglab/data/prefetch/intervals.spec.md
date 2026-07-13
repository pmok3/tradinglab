# data/prefetch/intervals.py — Spec

## Purpose
The **dual-interval policy**: map the on-screen interval to the ordered set of
intervals a symbol should warm, so the two one-click escape hatches
(Reset-View → `1d`, drill-down → `5m`) are already cached.

## Public API
- `dual_interval(active_interval: str | None) -> list[str]`

## Contract
1. **Active first.** The on-screen interval is position 0.
2. **Escape hatch next.** A daily (`1d`) chart's escape is the drill-down
   target `5m`; any intraday chart's escape is the Reset-View target `1d`.
3. **Remaining of `{5m, 1d}`** appended after.
4. **`5m` and `1d` are always present** (they back the escape hatches).
5. Normalizes case + surrounding whitespace (`" 5M "` → `5m`).
6. Blank / `None` active → daily default `["1d", "5m"]` (never raises).
7. Returns a freshly-constructed list each call (safe for callers to mutate).

Examples: `5m→[5m,1d]`, `1d→[1d,5m]`, `15m→[15m,1d,5m]`, `1m→[1m,1d,5m]`.

The list index of an interval is its `interval_rank` in the scheduler's
`PriorityKey`.

## Design Decisions
- Pure function, no state — trivially unit-testable and reusable by every tier
  provider (Decision 15: dual-interval applies to all tiers).

## Testing
`tests/unit/data/prefetch/test_intervals.py` (order per active interval,
always-present invariant, dedup, normalization, blank default, fresh-list).
