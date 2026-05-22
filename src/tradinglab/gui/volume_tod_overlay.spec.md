# gui/volume_tod_overlay.py — Spec

## Overview

Chart artist layer for **time-of-day volume shading**. On top of the
1d volume bars from `tradinglab.rendering.draw_volume`, paints:

1. **Realized solid fill** — height = bar's full-day volume scaled
   by realized fraction at the reference time-of-day, using each
   day's 5m bars. Same hue as the native bar fill.
2. **Outline envelope** — hollow rectangle at full-day height,
   drawn in the bull/bear hue through
   `tradinglab.rendering.darker_shade` (same colour, darker frame).
3. **Neutral median tick** — at the rolling median full-day volume
   over the prior N RTH days.

Reference time-of-day = sandbox replay clock when active, else
wall-clock.

## Public symbols

- `VolumeTodPatch` — dataclass: `bar_index`, `full_day_volume`,
  `outline_height`, `filled_height`, `has_intraday`,
  `is_session_pre_open`, `base_color`, `median_height`. Per-bar
  geometry + metadata.
- `VolumeTodArtists` — dataclass: `artists` (matplotlib refs the
  caller must hold), `patches`.
- `compute_volume_tod_patches(candles, intraday, *, now_ms,
  slice_start, slice_end, rth_only=True, median_lookback_days=20,
  sandbox_active=False) -> List[VolumeTodPatch]` — pure-functional
  math layer; no Tk, no matplotlib, no app state.
- `draw_volume_tod_patches(ax_v, patches, *, offset, theme,
  dark_mode, show_median_tick=True) -> VolumeTodArtists` —
  projects patches onto the volume axes.
- `clear_volume_tod_artists(artists)` — iterates `.remove()`,
  swallows exceptions (axes may already be torn down).
- `patches_should_suppress_default_fill(patches) -> Dict[int, bool]`
  — bar indices whose default `draw_volume` fill must be hidden.

## Inputs (math layer)

- `candles` — full 1d series (median lookback reaches
  `median_lookback_days` bars LEFT of `slice_start`).
- `intraday` — 5m candles, ideally covering every visible day.
  Missing days degrade to `has_intraday=False`.
- `now_ms` — UTC epoch-ms reference (sandbox clock or wall-clock).
- `slice_start, slice_end` — `[slice_start, slice_end)` visible
  range; one patch per non-gap bar inside it.
- `rth_only: bool = True` — RTH-only intraday in v1.
- `median_lookback_days: int = 20`.
- `sandbox_active: bool = False` — distinguishes live-wall-clock
  pre-open (suppress) from sandbox-rewind pre-open (envelope, 0% fill).

## Inputs (draw layer)

- `ax_v` — slot's volume axis.
- `patches` — output of math layer.
- `offset` — slot's bar-index offset (always 0 in single-symbol layout).
- `theme` — neutral median-tick colour via `axis_text` / `spine` /
  `text` keys, fallback `#7d8794`.
- `dark_mode` — drives `darker_shade`'s clamp policy.
- `show_median_tick` — gate.

## Behavior

Per non-gap 1d bar in slice:

| Reference time-of-day | sandbox? | intraday? | outline | filled |
|---|---|---|---|---|
| Pre-9:30 ET (m < 570) | False | — | 0 | 0 |
| Pre-9:30 ET (m < 570) | True  | True  | full_day | 0 |
| 09:30 ≤ m < 16:00 ET  | —     | True  | full_day | full_day × realized_frac |
| 09:30 ≤ m < 16:00 ET  | —     | False | 0 | 0 |
| Post-16:00 ET (m ≥ 960) | — | True  | full_day | full_day (latch) |
| Post-16:00 ET (m ≥ 960) | — | False | 0 | 0 |

`realized_frac = realized_volume / full_day_intraday_total`,
clamped `[0, 1]`. Denominator is the day's intraday RTH total;
**height** comes from `candle.volume` so the overlay aligns
pixel-for-pixel with `draw_volume`.

Median tick drawn for every patch whose `median_height > 0`.
`_compute_median_tick_height` requires `>= lookback // 2` valid
(>0) entries; else returns 0.0 (soft floor on cold start).

## Math contract

`_realized_at_tod` uses **strict-less-than** (`m < cutoff_minute`):
a 5m bar with start-minute `m` counts toward `realized` iff `m`
is strictly less than the reference minute. At exactly 10:00 ET
the 10:00–10:05 bar hasn't accumulated yet.

Pre-open clamp: `cutoff = 0`. Post-close clamp: `cutoff = 960`
(all 78 RTH bars satisfy m < 960; last bar at start-minute 955).

RTH window: 09:30 → 16:00 ET = minutes 570 → 960 = 78 of 5m bars.

## Date-key convention

`_candle_date_key(c)` uses **UTC date** (not ET date), matching
`events.render._bar_index_for_ts`. A 1d bar at midnight ET (05:00
UTC) and an intraday 09:30 ET bar (14:30 UTC) on the same trading
day share the same UTC date. The narrow window where they
disagree (00:00–05:00 UTC) is post-market and filtered by
`rth_only`.

## Color derivation

`_bar_base_color(c)` replicates `tradinglab.rendering.vol_geometry`
exactly:
`to_rgba(BULL_COLOR if c.is_bull else BEAR_COLOR, 0.7 * extended_alpha)`
where `extended_alpha = 0.45 if c.is_extended else 1.0`.
Duplicated rather than imported (private helper). Pinned by
smoke-gate pixel-equality probes.

## Z-order layering

```
crosshair / hover annotation     zorder=5
events glyphs                    zorder=4
indicators                       zorder=3
median tick                      zorder=2.8
outline envelope                 zorder=2.6
solid realized fill              zorder=2.5
volume bars (draw_volume)        zorder=2
candles                          zorder=2
session shading                  zorder=1
watermark                        zorder=0
```

## Integration points (ChartApp)

- `set_volume_tod_enabled(enabled)` — public toggle. Writes
  `settings.json`, calls `defaults.reload()`, kicks intraday
  prefetch, schedules redraw.
- `_now_ms_for_slot(slot)` — returns `int(self._sandbox.clock_ts())`
  when sandbox active, else `int(time.time() * 1000)`.
- `panel_state[slot]['vol_tod_artists']` + `['vol_tod_patches']` —
  slot-scoped refs, cleared by `_reset_slot_artists` via
  `clear_volume_tod_artists`.
- `_draw_slice` calls `_render_volume_tod_for_slot(slot)` after
  events overlay.
- `_render_volume_tod_for_slot` orchestrates: read tunable,
  fetch intraday, compute, draw, suppress default fill.
- `_get_intraday_for_volume_tod(symbol)` — reads
  `self._full_cache[(source, symbol, "5m")]`; on miss returns
  empty + kicks async prefetch.
- `_suppress_default_volume_fill(slot, suppress_indices)` —
  mutates `vol_bars._sc_colors` to `(0, 0, 0, 0)` for affected
  indices; preserves collection identity (H1 stream-tick
  fastpath cache stays valid).

## Determinism

Visual-only overlay. Nothing it computes lands in
`SessionResult`, journal, engine, or any persisted state.
Flipping `volume_tod_enabled` mid-session leaves engine output
byte-identical (locked by `check_b68`).

## Invariants

- Pure-functional surface; identical inputs → identical outputs.
- `compute_volume_tod_patches` never raises; on failure returns `[]`.
- `_realized_at_tod` strict-less-than: at cutoff 10:00 ET
  (m=600), bars 570..595 count (6 bars); 10:00 bar (start=600) does not.
- `realized / intraday_total` clamped `[0, 1]`.
- `ax.add_collection` failures swallowed (partial overlay
  rather than aborted frame).
- `clear_volume_tod_artists` idempotent.

## Side effects

- `draw_volume_tod_patches` calls `ax_v.add_collection(...)` up
  to three times. No other side effects.

## Tunables (see `defaults.spec.md`)

- `volume_tod_enabled: bool = False`.
- `volume_tod_median_lookback_days: int = 20`.
- `volume_tod_rth_only: bool = True` — internal.
- `volume_tod_intraday_interval: str = "5m"` — internal.
