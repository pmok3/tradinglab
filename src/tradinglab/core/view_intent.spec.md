# `core/view_intent` — chart X-window preservation intent

Single source of truth for what happens to the chart's visible X window
(matplotlib `xlim`) on the next render. Replaces the scattered `ChartApp`
booleans (`_preserve_xlim_on_render`, `_preserve_xlim_by_time_on_render`,
`_slide_xlim_to_right_edge`, `_axis_switch_inflight`,
`_pending_axis_switch_time_preserve`) that were set/cleared/consumed at ~40
sites and whose fragile precedence + one-shot leakage caused the recurring
view-preservation bug class (compare-toggle creep, ticker-switch misalign,
source-switch "jump to 2021"). Pure — no Tk/matplotlib — so it is fully
unit-testable.

## Vocabulary — `ViewMode`

| Mode | Meaning | Requested by | one-shot? |
|---|---|---|---|
| `DEFAULT` | right-edge default window | fresh load, reset-view, new ticker at default view, interval change | n/a (sticky no-preserve) |
| `KEEP_BARS` | preserve the exact bar-INDEX window | pan, wheel/rubber-band zoom, drilldown, same-series redraw | STICKY |
| `KEEP_DATES` | remap the calendar (date) window onto the new series | source-only switch, historical ticker/compare switch | ONE-SHOT |
| `SNAP_RIGHT` | keep width, shift to the newest bar | live poll tick at the right edge | ONE-SHOT |

`mode_to_flags(mode) -> (preserve_index, preserve_by_time, slide)` is the ONLY
translation from the intent vocabulary to the legacy render-directive triple
that `ChartApp._compute_slot_window` consumes:
`KEEP_BARS→(T,F,F)`, `KEEP_DATES→(F,T,F)`, `SNAP_RIGHT→(T,F,T)`, `DEFAULT→(F,F,F)`.
`is_one_shot(mode)` → True for `KEEP_DATES` / `SNAP_RIGHT`.

## `ViewController`

Canonical state is the three legacy render directives (`_preserve` sticky index,
`_by_time` one-shot time-remap, `_slide` one-shot snap-right) plus `_load_pending`
(an explicit async source/interval switch is loading). `ChartApp` exposes each as
a thin bridging property so the historical flag NAMES keep working for the large
existing test surface while the DECISION logic lives here.

### API
- `request(mode, *, load_pending=False)` — set the directives from `mode`; when
  `load_pending` mark that an async switch is in flight. Entry points call this
  instead of poking booleans. `arm_keep_bars()` is sugar for the pan/zoom path.
- `load_pending` (property) — True while a switch is loading; the live poll tick
  bails while set so it can't re-arm index-preserve or launch a competing fetch.
- `begin_completing_load() -> bool` — called at the TOP of the load servicing the
  render; lowers `load_pending` and returns whether this load completes an
  explicit switch (caller then renders SYNCHRONOUSLY).
- `render_directives() -> (preserve, by_time, slide)` — called at the TOP of
  `_render`. Contract below.
- `snapshot()/restore()` — opaque save/restore for tests.

### `render_directives` contract (the three structural guarantees)
1. **HOLD during a pending switch.** When `load_pending` is True, return
   `(preserve, False, False)` and CONSUME NOTHING. An intervening render (poll
   tick, prefetch daily-synth refresh, reference-data redraw, deferred idle
   render) therefore keeps the current view and cannot eat the one-shot
   `by_time`/`slide` intent nor let a racing index-preserve re-arm win. Only the
   switch's own completing render (after `begin_completing_load`) applies +
   consumes it. This is the generic replacement for the per-bug
   `_pending_axis_switch_time_preserve` fix.
2. **`by_time` wins over index-preserve.** Outside a pending load, if `by_time`
   is set the returned `preserve` is forced False AND the stored sticky
   `_preserve` is cleared — so a stale bar-index window can never clobber the
   calendar remap (the source-switch "jump to 2021" root cause).
3. **One-shot consumption.** `by_time` and `slide` reset to False after being
   returned; `preserve` is sticky (a pan/zoom persists across later renders).

## Invariants pinned by tests (`tests/core/test_view_intent.py`)
- `mode_to_flags` mapping is exact for all four modes; `is_one_shot` correct.
- `request` sets the expected triple; `load_pending` only set when asked.
- `render_directives` consumes one-shots but not `preserve`.
- `render_directives` forces `preserve=False` (and clears stored `_preserve`)
  whenever `by_time` is applied — even if `preserve` was concurrently True (the
  race).
- During `load_pending`: `render_directives` returns `(preserve, False, False)`
  and leaves `by_time`/`slide` intact (no consumption).
- `begin_completing_load` lowers `load_pending` and reports whether a switch was
  pending; the subsequent `render_directives` then applies the held intent.
- `snapshot`/`restore` round-trip.

## Non-goals
- The WINDOW math (ceil/floor bar rounding, `remap_window_by_time`, compare-slot
  xlim mirror, default-window sizing) stays in `core/viewport` +
  `ChartApp._compute_slot_window`; this module only decides WHICH mode is in
  effect and enforces its precedence/durability.
- Cache pinning / targeted refill (so a reload doesn't reset the view due to an
  eviction) is a DATA concern handled in `app.py` (`_view_pin_tickers`,
  `compare-toggle-targeted-first-load`), orthogonal to intent.
