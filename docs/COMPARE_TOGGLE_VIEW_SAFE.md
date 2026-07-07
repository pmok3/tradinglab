# Compare-toggle view-safety + async targeted compare fetch

**Audit tag:** `compare-toggle-drilldown-preserve`
**Status:** design (council-approved) → implementation
**Owner:** pmok3

## Problem

In compare mode, toggling **Compare ON** while the primary 5m chart is viewing
an **old day** (reached by double-click drilldown OR manual pan/zoom) that the
compare ticker's cached 5m history does **not** cover snaps the view to a recent
date (≈ "June 5th") and loses the studied day.

### Root cause (verified against real disk cache)

- Primary (e.g. `AMD`) 5m disk HAS the old day (from prior targeted drill
  fetches). Compare (e.g. `SPY`) 5m disk only holds the recent **~120-day**
  Alpaca trailing window (`constants._DEEP_HISTORY_INTRADAY_DAYS["5m"] = 120`)
  — no old-day data.
- `core.pairing.align_pair` uses `lo_day = max(primary[0], compare[0])`
  (low-end **intersection**). When compare starts AFTER the viewed day, align
  DROPS every primary bar before compare's first bar — including the viewed
  day. The preserved **index**-based xlim then points into the recent window →
  the chart jumps.

Two prior fixes failed: disk-preloading the compare can't work (disk lacks the
old data); a `_zoom_primary_to_date` re-zoom raced in-flight companion
prefetches and broke smoke `check_d34` in the session-scoped suite.

## Council decisions (unanimous)

1. **The primary view must NEVER jump** on a compare toggle — the viewed
   day/zoom is the workspace; compare is a secondary overlay.
2. **Two layers:** (L1) a *view-safe toggle* that never jumps even with zero
   compare data, then (L2) an *async targeted fetch* to populate the compare's
   old-day bars.
3. **Non-blocking** — no wait cursor; fetch in the background with a subtle
   loading indicator; app stays interactive.
4. **Don't silently degrade RS** — keep Compare ON, show a labelled empty /
   loading compare panel, and status-message why.
5. Cover the **pan** case too (not just `_drilldown_day`). Ship drilldown fix
   first; universe-data repair second.

## Design

### L1 — view-safe toggle (no jump, drill + pan)

- **`align_pair` opt-in `keep_window`** (`core.pairing`): a `(lo_ts, hi_ts)`
  epoch-second window. Primary bars whose timestamp falls in `keep_window` are
  retained **even if they predate the compare's first bar** (compare gets
  `Candle.gap` placeholders there). Default `None` = legacy `lo_day = max`
  behaviour. Threaded through `apply_pair_filter_and_align` and
  `data.controller.apply_pair_filter`. This keeps the aligned primary SMALL
  (visible-window bars + compare's real range — not a multi-year continuous
  gap run) so it never regresses the IPO "long leading gap run" the intersection
  was protecting against.
- **Time-based view preservation:** the toggle sets
  `_preserve_xlim_by_time_on_render = True` so `_render` remaps the previous
  primary's time window onto the new aligned series' bar-index axis via
  `core.viewport.remap_window_by_time`. (Index-based preserve is fragile once
  the align changes list length; the re-zoom path caused the d34 race.)
- **Visible-window capture:** `_current_visible_window_ts()` reads the primary
  price axes xlim + candles, clamps to indices, and returns the min/max non-gap
  timestamp of the on-screen bars. Works for BOTH drilldown and manual pan —
  no reliance on `_drilldown_day`.

### L2 — async targeted compare fetch

- On compare-ON, if the visible window is not covered by the compare's cache,
  schedule `DrilldownMixin._targeted_range_fetch(src, cmp, "5m", day, now,
  merge_to_disk=True)` on `_fetch_executor` (reuses `targeted_window` +
  coverage sidecar + `fetch_range`). `day` = the first visible non-gap day.
- Marshal completion via `_await_future_on_tk` (never cross-thread `after`).
- **Token + coalesce:** bump `_compare_fetch_token` before submit; the
  completion callback no-ops if superseded. A per-`(cmp, day)` in-flight guard
  coalesces repeated toggles so a pending fetch never re-fires or jumps.
- **On completion:** reload the compare from disk into `_full_cache` (mirrors
  `_on_drilldown_fetch_done`), then re-render the compare with time-preserve +
  `keep_window` so the newly-arrived bars fill the panel WITHOUT moving the
  primary.

### UX / status strings

- On toggle (uncovered): `Fetching {CMP} 5m for {day}… (primary view held)`
- On success: `Compare {CMP} loaded for {day} (N bars on 5m)`
- No data: `No {CMP} 5m bars found for {day}; primary view kept`
- Error: `Could not load {CMP} 5m for {day}; primary view kept`
- The compare panel appears immediately (single expected 1→2-panel relayout);
  it is empty/gaps until data lands, then fills with no second relayout. No
  wait cursor.

## Scope / non-goals (v1)

- Visible-range only. NO pan-back lazy-fetch storm (only the day the user is
  looking at when they toggle).
- `1d`+ unaffected (targeted fetch is intraday-only; daily must not regress).
- Sandbox/replay: `_on_compare_toggle`'s sandbox branch returns BEFORE this
  path, so sandbox stays offline/deterministic.
- Provider limits honoured: coalesced, one range request per toggle; Alpaca
  401 / rate-limit / no-data all degrade to "primary kept, compare empty".

## Tests

- `core.pairing`: `keep_window` keeps a pre-compare-start primary bar with a
  compare gap; default `None` unchanged; small aligned length (no giant gap
  run).
- Smoke `check_d87`: reproduce REAL conditions — primary old-day coverage +
  compare recent-only — drill to the old day, toggle compare, assert the view
  stays AND (with a stubbed range fetcher) the compare fills the day. Save /
  restore `_full_cache`, vars, preserve flags; token/poll the fetch by id to
  avoid session-scoped smoke leaks.
