# events/render.py — Spec

## Purpose
Pure renderer-helper that projects an `EventsView` onto a visible-candle window, producing `EventGlyph` descriptors that the GUI overlay layer turns into matplotlib artists. Keeping descriptor build pure makes placement testable without X and lets Compare panes, the primary chart, and any future thumbnail share one code path.

## Public API
- Glyph-kind constants: `GLYPH_EARNINGS_PAST`, `GLYPH_EARNINGS_FORWARD`, `GLYPH_DIVIDEND`, `GLYPH_SPECIAL_DIVIDEND`, `GLYPH_SPLIT`.
- `@dataclass(frozen=True) class EventGlyph(bar_index, glyph_kind, tooltip, ts_ms)`.
- `build_event_glyphs(view, candles, *, blind=False) -> List[EventGlyph]`.

## Dependencies
Internal: `.base` (record types — duck-typed `getattr` lookups for shape tolerance). External: `math`, `dataclasses`, `typing`.

## Design Decisions
- **Descriptor list, not artists.** Matplotlib artists built by `gui/sandbox_*` overlay modules; this stays headless.
- **Bar-index, not x-coord.** Visible-candle lists are append-only with stable identity; integer index more robust than `transData` x (zoom/pan warps data coords).
- **`bar_index = -1` means right-edge badge.** Forward events outside the visible window become a single right-edge "next earnings in T-N days" descriptor. In-pane forwards take precedence: any forward inside the window suppresses the right-edge badge (in-pane glyph carries strictly more info).
- **Tooltip pre-built here.** Blind-mode redaction stays inside the events module — GUI never sees an absolute forward date when blind.

## Invariants
- Order: past dividends, past earnings, forward earnings, then right-edge badge. Stable.
- A glyph with `bar_index >= 0` satisfies `0 <= bar_index < len(candles)`.
- A blind-mode call never produces a tooltip containing the absolute year of a forward event.

## Algorithm
1. For each past event, compute matching `bar_index` via linear scan over candle dates (visible windows are O(100s); bisect setup not worth it).
2. Build tooltip per record (`_earnings_tooltip` / `_dividend_tooltip`).
3. Forward earnings: emit in-pane glyph if date inside window; else nothing (right-edge badge handles).
4. If forward badges and no in-pane forward glyph, emit one right-edge badge using the nearest badge.

## Known limitations
- One glyph per bar maximum per kind. Stacked glyphs (earnings AND ex-div same day) would overlap; future: y-stagger via a per-bar slot counter.
- Tooltip formatting fixed (no user customisation).
