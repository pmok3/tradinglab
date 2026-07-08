# core/pairing.py — Spec

## Purpose
Compare-mode primitive: given primary + compare raw candle lists, coordinate the Pre/Post toggle across the pair and timestamp-align the two series so they share an index. Pure data, no Tk/mpl. Used by `ChartApp._apply_pair_filter_and_align` and any headless replay/backtest.

## Public API
- `apply_pair_filter(primary_raw, compare_raw, interval, extended_hours) -> (primary, compare)` — drops extended-hours bars unless both sides have them. Identity-preserving on no-op.
- `align_pair(primary, compare, interval=None, *, keep_window=None) -> (primary_aligned, compare_aligned)` — equal-length lists with shared slot keys; missing slots filled with `Candle.gap(date)`. Real bars are the **same objects** as in inputs. **Grain depends on `interval`**: intraday (or `interval=None`, the back-compat default) keys on the exact tz-normalized timestamp; daily and coarser (`1d`/`1wk`/`1mo`) key on the **calendar date** so a synthesized today bar (session-open time, e.g. 09:30 ET) aligns with the other side's midnight provider bar for the same day. `keep_window=(lo_ts, hi_ts)` (opt-in, epoch seconds) additionally retains **primary** bars inside that window even if they predate the compare's first bar.
- `apply_pair_filter_and_align(primary_raw, compare_raw, interval, extended_hours, *, keep_window=None)` — composition; forwards `keep_window` to `align_pair`.

## Dependencies
Internal: `..constants.is_intraday`, `..models.Candle`. External: none.

## Design Decisions
- **Extended-hours coordinated across the pair**: toggle only takes effect when interval is intraday AND both sides have pre/post bars. Otherwise silently fall back to RTH-only on both sides — prevents right-edge alignment mismatching an extended bar with an RTH bar (phantom correlation).
- **Identity preservation on no-op filter**: `apply_pair_filter` returns the original list object (not a copy) when no bars need dropping. Streaming relies on object identity for in-place tick updates to remain observable through the aligned view.
- **Gap placeholders**, not skipping slots: `align_pair` pads missing slots with `Candle.gap(date)`. Rendering/hover/autoscale short-circuit on `is_gap`, but the slot is preserved so both panels keep matching X indices (essential for `sharex=`).
- **Asymmetric day-range clip — LOW end intersects, HIGH end unions.** `lo_day = max(p[0].date.date(), c[0].date.date())` keeps the legacy low-end intersection: one side may go further back (e.g. new IPO vs. SPY), and clipping the start avoids a long leading run of `Candle.gap`. `hi_day = max(p[-1].date.date(), c[-1].date.date())` is the **union** on the top end so neither side's trailing bars are clipped — critically the primary's TODAY bars when the compare ticker's intraday cache still lags a calendar day behind (stale cache / provider lag). The old `hi_day = min` dropped those bars; under a drilldown-to-today the preserved index-based xlim then pointed past the now-shorter primary list and **every candle vanished** (audit `compare-today-drilldown-clip`). The lagging side gets `Candle.gap` placeholders for the days it doesn't cover (usually ≤1 day → no long gap-run). The overlap guard (`if lo_day > min(p[-1].date.date(), c[-1].date.date()): return as-is`) still leaves genuinely day-disjoint series unaligned.
- **`keep_window` opt-in retains an OLD on-screen primary window the compare doesn't cover yet** (audit `compare-toggle-drilldown-preserve`). The default low-end intersection (`lo_day = max`) DROPS primary bars older than the compare's first bar. That is correct for the steady state (avoids a multi-year leading gap-run), but it is exactly what makes the **compare toggle jump the view**: when the user has drilled/panned a 5m chart into an old day (e.g. 2021-12-16) whose day the compare's recent-only intraday cache doesn't reach, aligning drops the drilled-day primary bars → the preserved index xlim now points into recent data → the chart snaps to ~now. `keep_window=(lo_ts, hi_ts)` (epoch seconds, typically the current visible window) is OR-ed into the `by_p` filter in **both** the daily and intraday branches (`(lo_day <= day <= hi_day) or _in_keep(ts)`), so those specific primary bars survive; the compare side is gap-filled inside the window until a background targeted fetch supplies its bars. The retained set is bounded to the on-screen window — never a full leading gap run — so it does NOT reintroduce `compare-today-drilldown-clip`'s inverse. `keep_window=None` (default) is byte-for-byte the legacy behaviour. Only `ChartApp._on_compare_toggle` passes it, and only on the drilldown + historical + intraday + recent-only-compare path.
- **`align_pair` short-circuits** when either side is empty or date ranges don't overlap — returns shallow `list(...)` copies of inputs (real bars share identity).
- **Daily+ align by calendar date, intraday by exact timestamp** (`compare-daily-today-align`): `data.today_upsample` synthesizes today's 1d bar with the session-open timestamp (`matches[0].date`, e.g. 09:30 ET) — intentionally, so hover/event joins still resolve a real intraday bar. The other side's today bar may be a provider partial at midnight (when its intraday isn't cached, no synth runs). Under exact-timestamp keying those two same-day bars split into two slots → a spurious gap before today on one panel and a blank "tomorrow" on the other (reported for MU in compare mode). Keying daily+ on `c.date.date()` snaps both today bars into one slot. Intraday keeps exact-timestamp keying (sub-day bars are genuinely distinct). Gated on `interval`; `align_pair(p, c)` with no interval keeps the legacy exact-timestamp path (used by some headless callers/tests).
- **Tz-mixed inputs are normalized**, not rejected: disk-cache JSONL preserves provider tz (e.g. `America/New_York`) while in-memory fake/streaming/stubbed data is often tz-naive. Mixing inside `set | set` raises `TypeError`. `_normalize_pairing_key` strips tzinfo for use as a dict/sort key (both sides represent the same exchange wall clock). Output candles retain their original `.date`. (Daily+ keys on `.date.date()` which is tz-irrelevant by construction.)

## Invariants
- After `apply_pair_filter(raw, None, ...)`, primary is filtered correctly regardless of `compare_raw=None` (single-chart mode still honors Pre/Post).
- After `align_pair(p, c, interval)`: `len(p_out) == len(c_out)`. For **intraday**, each pair shares the same normalized timestamp key; real bars retain their original `.date` (including tz-awareness), while a gap placeholder **borrows a real bar's `.date`** from the other side at that slot (NOT the tz-stripped normalized key) so the output list stays uniformly tz-aware/naive — a naive gap injected into a tz-aware series (e.g. Alpaca's ET-localized bars) would produce a mixed-tz list that later trips `can't compare offset-naive and offset-aware` in downstream timestamp math (audit `compare-intraday-gap-tz`). For **daily+**, `p_out[i].date.date() == c_out[i].date.date()` (same calendar day) — the time-of-day may differ when one side is a synth-today bar (09:30) and the other a midnight provider bar; gap placeholders likewise borrow a real bar's `.date` for the slot.
- Real (non-gap) output bars share `id()` with input bars.
- `apply_pair_filter` returns the input list object unchanged when no filtering needed.
- **`keep_window` never drops or reorders bars the legacy path kept** — it only ADDS primary bars (those inside the window that fall below `lo_day`); with `keep_window=None` the output is byte-identical to the legacy path. Retained primary bars still emit a `Candle.gap` on the compare side for their slot, preserving `len(p_out) == len(c_out)`.

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

align_pair(p, c, interval=None, *, keep_window=None):
    lo_day = max(p[0].date.date(), c[0].date.date())
    overlap_hi = min(p[-1].date.date(), c[-1].date.date())
    if lo_day > overlap_hi: return as-is        # no shared calendar day
    hi_day = max(p[-1].date.date(), c[-1].date.date())   # UNION top end
    _in_keep(ts) = keep_window is not None and keep_window[0] <= ts <= keep_window[1]
    if interval is not None and not is_intraday(interval):   # daily+
        by_p = {c.date.date(): c for c in p
                if (lo_day <= c.date.date() <= hi_day) or _in_keep(c.date.timestamp())}
        by_c = {c.date.date(): c for c in c_in if lo_day <= c.date.date() <= hi_day}
        for day in sorted(set(by_p) | set(by_c)):
            ref = (by_p.get(day) or by_c.get(day)).date   # a real bar's ts
            emit (by_p.get(day) or Candle.gap(ref), by_c.get(day) or Candle.gap(ref))
        return
    by_p = {_normalize_pairing_key(c.date): c for c in p
            if (lo_day <= c.date.date() <= hi_day) or _in_keep(c.date.timestamp())}
    by_c = {_normalize_pairing_key(c.date): c for c in c_in if lo_day <= c.date.date() <= hi_day}
    merged_dates = sorted(set(by_p) | set(by_c))
    for d in merged_dates:
        pbar, cbar = by_p.get(d), by_c.get(d)
        ref = (pbar or cbar).date          # borrow a REAL bar's tz-aware date
        emit (pbar or Candle.gap(ref), cbar or Candle.gap(ref))
```
