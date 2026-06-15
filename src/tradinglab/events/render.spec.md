# events/render.py — Spec

## Purpose
Pure renderer-helper that projects an `EventsView` onto a visible-candle window, producing `EventGlyph` descriptors that the GUI overlay layer turns into matplotlib artists. Keeping descriptor build pure makes placement testable without X and lets Compare panes, the primary chart, and any future thumbnail share one code path.

## Public API
- Glyph-kind constants: `GLYPH_EARNINGS_PAST`, `GLYPH_EARNINGS_FORWARD`, `GLYPH_DIVIDEND`, `GLYPH_SPECIAL_DIVIDEND`, `GLYPH_SPLIT`.
- `EVENT_MARKER_GLYPH = {"earnings_amc": "A", "earnings_bmo": "B", "dividend": "D"}`.
- `@dataclass(frozen=True) class EventGlyph(bar_index, glyph_kind, tooltip, ts_ms, marker_glyph="")`.
- `build_event_glyphs(view, candles, *, blind=False) -> List[EventGlyph]`.

## Dependencies
Internal: none (record/view inputs are duck-typed with `getattr` lookups for shape tolerance). External: `math`, `dataclasses`, `typing`, `collections.abc`.

## Design Decisions
- **Descriptor list, not artists.** Matplotlib artists built by `gui/sandbox_*` overlay modules; this stays headless.
- **Bar-index, not x-coord.** Visible-candle lists are append-only with stable identity; integer index more robust than `transData` x (zoom/pan warps data coords).
- **`bar_index = -1` means right-edge badge.** In blind mode, forward badges become a single right-edge "next earnings in T-N days" descriptor. Non-blind forward records suppress the right-edge badge even if their date is outside the visible window.
- **Tooltip pre-built here.** Blind-mode redaction stays inside the events module — GUI never sees an absolute forward date when blind.
- **Marker letters are centralized.** `EVENT_MARKER_GLYPH` maps AMC earnings to `A`, BMO earnings to `B`, and dividend ex-dates to `D`; unsupported earnings slots fall back to `E` while splits remain `S`.

## Invariants
- Order: past dividends, past earnings, forward earnings, then right-edge badge. Stable.
- A glyph with `bar_index >= 0` satisfies `0 <= bar_index < len(candles)`.
- Earnings AMC/BMO in-pane glyphs carry marker letters `A` / `B`; dividend and special-dividend in-pane glyphs carry `D`.
- A blind-mode call never produces a tooltip containing the absolute year of a forward event.

## Algorithm
1. Precompute a `day → first-bar-index` map ONCE per call (`_build_day_index_map`, one `date.timestamp()` per candle), then resolve each event's `bar_index` via an O(1) lookup keyed by `ts_ms // MS_PER_DAY`. Projection is **O(bars + events)** — previously each event ran an independent O(bars) linear scan (O(events × bars)), which dominated the per-render events cost on symbols with many dividends/earnings in view (measured ~3 ms/render on a 140-bar/68-event window, more as events grow). `_bar_index_for_ts` (the single-shot linear scan) is retained for back-compat callers but is no longer on the hot path. First-index-wins on day collisions matches the old scan.
2. Build tooltip and marker letter per record (`_earnings_tooltip` / `_dividend_tooltip`, `EVENT_MARKER_GLYPH`).
3. Forward earnings: emit in-pane glyph if date inside window; outside-window records emit nothing.
4. If forward badges exist and `forward_earnings` is empty, emit one right-edge badge using the nearest badge.

## Known limitations
- One glyph per bar maximum per kind. Stacked glyphs (earnings AND ex-div same day) would overlap; future: y-stagger via a per-bar slot counter.
- Tooltip formatting fixed (no user customisation).
