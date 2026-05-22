# core/pairing.py — Spec

## Purpose
Compare-mode primitive: given primary + compare raw candle lists, coordinate the Pre/Post toggle across the pair and timestamp-align the two series so they share an index. Pure data, no Tk/mpl. Used by `ChartApp._apply_pair_filter_and_align` and any headless replay/backtest.

## Public API
- `apply_pair_filter(primary_raw, compare_raw, interval, extended_hours) -> (primary, compare)` — drops extended-hours bars unless both sides have them. Identity-preserving on no-op.
- `align_pair(primary, compare) -> (primary_aligned, compare_aligned)` — equal-length lists with shared `date` keys; missing slots filled with `Candle.gap(date)`. Real bars are the **same objects** as in inputs.
- `apply_pair_filter_and_align(primary_raw, compare_raw, interval, extended_hours)` — composition.

## Dependencies
Internal: `..constants.is_intraday`, `..models.Candle`. External: none.

## Design Decisions
- **Extended-hours coordinated across the pair**: toggle only takes effect when interval is intraday AND both sides have pre/post bars. Otherwise silently fall back to RTH-only on both sides — prevents right-edge alignment mismatching an extended bar with an RTH bar (phantom correlation).
- **Identity preservation on no-op filter**: `apply_pair_filter` returns the original list object (not a copy) when no bars need dropping. Streaming relies on object identity for in-place tick updates to remain observable through the aligned view.
- **Gap placeholders**, not skipping slots: `align_pair` pads missing slots with `Candle.gap(date)`. Rendering/hover/autoscale short-circuit on `is_gap`, but the slot is preserved so both panels keep matching X indices (essential for `sharex=`).
- **Intersection by calendar date** (`lo_day = max(p[0].date.date(), c[0].date.date())`): one side may go further back (e.g. new IPO vs. SPY). Restricting to the overlap avoids long stretches of `Candle.gap`.
- **`align_pair` short-circuits** when either side is empty or date ranges don't overlap — returns shallow `list(...)` copies of inputs (real bars share identity).
- **Tz-mixed inputs are normalized**, not rejected: disk-cache pickles preserve provider tz (e.g. `America/New_York`) while in-memory fake/streaming/stubbed data is often tz-naive. Mixing inside `set | set` raises `TypeError`. `_normalize_pairing_key` strips tzinfo for use as a dict/sort key (both sides represent the same exchange wall clock). Output candles retain their original `.date`.

## Invariants
- After `apply_pair_filter(raw, None, ...)`, primary is filtered correctly regardless of `compare_raw=None` (single-chart mode still honors Pre/Post).
- After `align_pair(p, c)`: `len(p_out) == len(c_out)`; `p_out[i].date == c_out[i].date` for all i.
- Real (non-gap) output bars share `id()` with input bars.
- `apply_pair_filter` returns the input list object unchanged when no filtering needed.

## Algorithm
```
apply_pair_filter(p_raw, c_raw, interval, extended_hours):
    want_ext = is_intraday(interval) and extended_hours
    if want_ext and c_raw:
        p_has_ext = bool(p_raw) and any(c.is_extended for c in p_raw)
        c_has_ext = any(c.is_extended for c in c_raw)
        if not (p_has_ext and c_has_ext):
            want_ext = False          # fall back: RTH on both sides
    return (p_filtered, c_filtered)   # identity-return when no filter

align_pair(p, c):
    lo_day = max(p[0].date.date(), c[0].date.date())
    hi_day = min(p[-1].date.date(), c[-1].date.date())
    if lo_day > hi_day: return as-is
    by_p = {_normalize_pairing_key(c.date): c for c in p if lo_day <= c.date.date() <= hi_day}
    by_c = {_normalize_pairing_key(c.date): c for c in c_in if lo_day <= c.date.date() <= hi_day}
    merged_dates = sorted(set(by_p) | set(by_c))
    for d in merged_dates:
        emit (by_p.get(d) or Candle.gap(d), by_c.get(d) or Candle.gap(d))
```
