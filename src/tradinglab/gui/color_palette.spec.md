# gui/color_palette.py — Spec

## Purpose
Thin wrapper around the native OS color chooser. Exposes a single
synchronous entry point :func:`pick_color` for selecting indicator
colors — the legend right-click "Change Color…" flow and the
per-output color swatches inside `IndicatorDialog` both route through
here.

Audit tag: ``color-picker-native-only``.

Previous revisions shipped a custom `HexColorPalette` modal (HSV
gradient + honeycomb swatches + a "System…" escape hatch). The user
reported the custom palette was too sparse and asked that the System
(native OS) chooser become the canonical *and only* color UI for
indicator color selection. The custom dialog, HSV helpers, honeycomb
tables, and the dedicated geometry-store key (`dlg.color_palette`)
were deleted in the same commit. There is no `HexColorPalette` class
anymore.

## Public API
- `pick_color(parent, initial="#888888", title="Pick a color") -> Optional[str]`
  — blocks on `tkinter.colorchooser.askcolor`, returns the normalised
  lower-case `"#rrggbb"` on OK, or `None` if the user cancels / closes
  the dialog / the underlying Tk call fails.
- `_normalise(color: str) -> str` — module-level hex canonicaliser.
  `#RRGGBB` lower-cased; `#RGB` expanded; empty input → `DEFAULT_COLOR`;
  X11 color names returned unchanged so Tk can still resolve them.
- `DEFAULT_COLOR = "#888888"` — mid-gray fallback, matches
  `tradinglab.indicators.base.LineStyle` default.

## Dependencies
- External: `tkinter`, `tkinter.colorchooser`.

## Design Decisions

### Native chooser is the only UI
The custom HSV/honeycomb palette was retired (user request:
"the System… popup is the only thing I want the user to see when
they select a colour for an indicator"). `pick_color` is now a
direct passthrough to `colorchooser.askcolor` plus hex normalisation
on input and output.

### OS theme, not app theme
The native chooser follows the OS theme — on Windows it uses the
system color picker, which does not honour the app's dark / light
mode. This is the explicit trade-off the user accepted by requesting
the System popup as the only surface. As a result there is no
`apply_*_theme` plumbing here and no entry in
`tests/unit/gui/test_native_widget_dark_theme.py`.

### Hex normalisation on both directions
`_normalise` runs on `initial` *before* the chooser opens (so a
caller passing a malformed / empty value still gets the chooser
opened with a sensible default rather than tripping Tk's input
validator and falling through to `None`) and again on the returned
`hex_color` so call sites consuming the result can always rely on
lower-case `#rrggbb`.

## Invariants
- Returns `None` iff the user cancelled / dismissed the chooser;
  never returns an empty string.
- **Tk-main-thread-only** — `askcolor` itself does this; documented
  here for symmetry with sibling dialogs.

## Testing
- `tests/unit/test_hex_case_constants.py::test_palette_normalise_returns_lowercase`
  — pins `_normalise` for `#RGB`, `#RRGGBB`, empty input.
- `tests/smoke/test_smoke_full.py::check_b42_indicator_color_palette`
  — covers `_normalise` plus the per-output `style_overrides` flow
  through `IndicatorDialog` (the dialog round-trip works because
  `pick_color` is monkey-patched / driven via the dialog's commit
  hooks, never opening the real native chooser inside the test).

## See also
- `gui.indicator_dialog` — primary caller via per-output color swatches.
- `app.ChartApp._legend_pick_color` — legend right-click "Change Color…" flow.
