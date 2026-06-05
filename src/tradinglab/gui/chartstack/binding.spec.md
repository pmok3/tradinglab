# `chartstack/binding.py` — Mode-by-mode card binding resolution

## Purpose
Pure-data layer answering "what symbol does slot N show right
now?". No Tk, no matplotlib, no app state — snapshot inputs only.
Lets `ChartStackPanel.refresh()` re-resolve bindings on every
redraw without worrying about side effects, and lets the unit test
matrix cover the four modes exhaustively.

## Public API
- `BindingMode` enum: `PINNED_WATCHLIST`, `SCANNER_TOP_N`,
  `OPEN_POSITIONS`, `HYBRID`, `FIXED_PRESET`.
- `CardBinding` frozen dataclass: `(symbol: str, source_label: str)`.
- `resolve_bindings(mode, *, watchlist, scanner_results,
  open_positions, manual_pins, fixed_preset, card_count) -> list[CardBinding | None]`.

## Input tolerance
Each sequence may contain strings, dicts with `symbol`/`ticker`
keys, or dataclass-like objects with matching attributes.
Normalisation upper-cases and trims; empty strings → `None` →
filtered.

## Hybrid ordering (§2.3 of synthesis)
1. Open positions
2. Manual pins
3. Active watchlist tickers
4. Scanner edges not already covered

Deduplicated first-seen across all sources, capped at `card_count`.
The result is **always** length `card_count`, padded with `None`.

## FIXED_PRESET ordering (audit `chartstack-fixed-preset`)
Per-slot positional binding — slot `i` shows `fixed_preset[i]`
verbatim. No first-seen dedup, no source fall-through: this mode
deliberately does NOT consult `watchlist` / `open_positions` /
`scanner_results` / `manual_pins`, since the user picked these
symbols explicitly via the ChartStack Settings popup.

Slot rules:
- Out-of-range slots (`i >= len(fixed_preset)`) → `None` binding
  (empty card).
- Blank / whitespace entries (`""` / `"   "`) → `None` binding,
  so the user can intentionally hold a slot empty.
- Symbols are upper-cased on the way in via `_normalise_symbol`.
- Source label is `"preset"`.

Default `fixed_preset` (when no override): `["SPY", "QQQ", "VXX"]`
— sourced from `chartstack.settings_adapter.DEFAULTS` and surfaced
by the `fixed_preset_symbols()` helper.

## Design decisions
- **First-seen dedup** keeps the slot order stable across refreshes.
  A symbol that appears in both positions and the watchlist shows
  exactly once, in the position slot.
- **Pad with `None`, not with placeholder symbols**, so the panel
  can render a "(empty)" message rather than a fake ticker.
- **Source label is plain string**, not enum, because the only
  consumer is the card's status row label.
- M1: PINNED_WATCHLIST + HYBRID are exercised by the panel;
  SCANNER_TOP_N + OPEN_POSITIONS are implemented (pure logic) but
  no UI consumer until M6.
