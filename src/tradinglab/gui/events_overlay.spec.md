# gui/events_overlay.py — Spec

## Overview

Chart artist layer for the historical earnings & dividends feature.
Renders :class:`tradinglab.events.render.EventGlyph` descriptors as
matplotlib text glyphs anchored at the bottom edge of each price pane,
following plan.md decision 13b (mixed transform: bar-index X /
axes-fraction Y).

## Public symbols

- `EventGlyphArtists` — dataclass: `artists` (list of matplotlib artists), `hit_meta` (list of `(x_data, glyph_kind, tooltip)` for hover), `forward_badge_tooltip` (str, empty when no right-edge badge).
- `draw_event_glyphs(ax, glyphs, *, offset, theme=None, show_earnings=True, show_dividends=True, show_upcoming=True) -> EventGlyphArtists` — pure side-effecting; returns the artist refs the caller must hold for teardown.
- `clear_event_glyph_artists(artists)` — iterates `.remove()`, swallowing exceptions (axes may already be torn down).

## Inputs

- `ax: Axes` — the slot's price axis (volume axes are never the glyph host).
- `glyphs: Sequence[EventGlyph]` — output of `events.render.build_event_glyphs`.
- `offset: int` — slot's bar-index offset (always 0 in current single-symbol layout, but accepted for parity with `_panel_state`).
- `theme` — optional theme dict (looks up `tooltip_fg` / `text` for letters and `tooltip_bg` / `ax_bg` plus `spine` for the rounded backing box); falls back to readable light-theme defaults.
- Three `show_*` flags — user-tunable filters mapping to the same-named entries in `defaults.py`.

## Behavior

- For each glyph with `bar_index >= 0`: draw a single bold `Text` artist at `(bar_index + offset, 0.025)` in a blended `(transData, transAxes)` transform.
- Text letters come from `EventGlyph.marker_glyph` when present: `A` for earnings AMC, `B` for earnings BMO, `D` for dividend ex-dates (including special/spinoff cash events), and `S` for splits. Missing glyph text falls back to a per-kind marker table.
- Every in-pane letter has `ha="center"`, `va="center"`, and a small rounded theme-aware bbox to keep it readable against candle bodies/wicks.
- For each glyph with `bar_index < 0` (right-edge forward badge): draw a small italic `Text` at `(0.985, 0.04)` in axes-fraction with the descriptor's tooltip; descriptor builders normally collapse this to the nearest forward event, and the returned tooltip reflects the last badge drawn.
- Visibility gating: a glyph is skipped when its kind's user flag is False.
- Z-order 4 → above indicator lines (3), below crosshair (5).

## Side effects

- Calls `ax.text(...)` only; the legacy square/circle/diamond/triangle `ax.plot(...)` markers are not drawn.
- Returns artist refs so the caller can clean them up before the next render. This module does **not** hold any state.

## Invariants

- Pure-functional surface; calling `draw_event_glyphs` twice with the same inputs produces visually identical output (modulo matplotlib's intra-frame zorder ties, which are stable for `ax.text` insertion order).
- Y coordinate is always axes-fraction `_GLYPH_Y = 0.025` — glyphs stay in the same bottom band as the legacy markers.
- Failures in `ax.text` are swallowed; partial glyph sets render rather than aborting the frame.

## Z-order layering

```
crosshair / hover annotation    zorder=5
events glyphs (this module)     zorder=4
indicators (lines / fills)      zorder=3
candles / volume                zorder=2
session shading                 zorder=1
watermark                       zorder=0
```
