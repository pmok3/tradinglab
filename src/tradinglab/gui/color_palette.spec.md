# gui/color_palette.py — Spec

## Purpose
Modal hexagonal honeycomb color picker. Replaces / supplements the standard OS color chooser with a touch-friendly graphical palette of 19 swatches (1 center + 6 ring-1 hues + 12 ring-2 tint/shade pairs) plus a 6-cell grayscale strip and a `Custom…` button that falls back to `tkinter.colorchooser.askcolor` for free hex entry.

## Public API
- `pick_color(parent, initial="#888888", title="Pick a color") -> Optional[str]` — open the picker modally, block via `wait_window`, return the chosen `"#rrggbb"` (lower-case) or `None` on cancel / WM-close.
- `class HexColorPalette(tk.Toplevel)` — the popup itself; not normally constructed directly. Exposes `result: Optional[str]` after dismissal, and `_normalise(color)` (static) for hex canonicalisation.

## Dependencies
- External: `tkinter`, `tkinter.ttk`, `tkinter.colorchooser`, `math`.
- Internal: none (pure Tk widget).

## Design Decisions
- **Flat-top hexagonal grid via axial coordinates.** Cells are positioned by the standard hex-grid formula `x = 1.5*size*q`, `y = √3*size*(r + q/2)` and drawn with `Canvas.create_polygon` using six vertices at `60°·k` from each cell's center. Ring-N coordinates come from a single CW walk in the six axial directions, `n` cells per direction.
- **Two visual layers, one click handler.** Honeycomb canvas + grayscale rectangle row are wired to the same `_on_pick(color)` callback, so adding more swatch styles later (e.g. recently-used row) doesn't fragment the dispatch.
- **Custom… as escape hatch.** The 19-cell palette is intentionally small; `Custom…` opens `tkinter.colorchooser.askcolor` so a user who wants `#1f77b4` exactly is never blocked.
- **Modal via `wait_window` + `grab_set`.** Picker callers (e.g. `IndicatorDialog`) get a synchronous return value, matching the ergonomics of `colorchooser.askcolor`.
- **Hex normalisation.** All returned colors go through `_normalise`: `#RRGGBB` upper-cased; short-form `#RGB` expanded; empty input → `#888888`. Comparison sites (e.g. `_resolved_color_for`) use `.upper()` so case differences from external sources never produce false-positive overrides.

## Invariants
- Returns `None` iff the user cancelled (Esc / Cancel / WM close); never returns an empty string.
- The 19-color honeycomb table (`_HONEYCOMB_COLORS`) and the 6-color grayscale row (`_GRAYSCALE_COLORS`) are immutable module-level tuples; their lengths are part of the public contract (the smoke test asserts `len(_HONEYCOMB_COLORS) == 19`).
- The picker grabs focus exclusively while open and releases the grab on dismissal.
- **Tk-main-thread-only** — all Tk widget construction and mutation occurs on the Tk thread. Cross-thread access via `self.after` queueing — but see `gui/watchlist_tab.spec.md` for the worker-inbox pattern that supersedes `after` for worker results.

## Testing
- `check_b42_indicator_color_palette` — covers hex normalisation, honeycomb table size, and the dialog integration round-trip.

## Modal keys
`HexColorPalette.__init__` calls `bind_modal_keys(self, cancel=self._on_cancel, primary=None)` after `protocol("WM_DELETE_WINDOW")`. Provides ESC-to-cancel parity with every other modal dialog. `primary=None` is intentional: the palette's primary action is per-swatch click (`_on_pick`), not an Enter-key submit.
