# gui/color_palette.py — Spec

## Purpose
Modal color picker with the Advanced HSV picker and Swatches honeycomb laid
out **side-by-side**. The advanced HSV picker (a saturation/value gradient
square + hue strip) sits on the left for precise color selection; the
Swatches honeycomb (1 center + 6 ring-1 hues + 12 ring-2 tint/shade pairs)
plus a 6-cell grayscale strip sits on the right. The live hex entry +
preview swatch row lives **under the swatches column**, grouping the "final
pick" affordances together. A `System…` button still falls back to
`tkinter.colorchooser.askcolor` for the OS chooser.

## Public API
- `pick_color(parent, initial="#888888", title="Pick a color") -> Optional[str]` — open the picker modally, block via `wait_window`, return the chosen `"#rrggbb"` (lower-case) or `None` on cancel / WM-close.
- `class HexColorPalette(BaseModalDialog)` — the popup itself; not normally constructed directly. Exposes `result: Optional[str]` after dismissal, and `_normalise(color)` (static) for hex canonicalisation.
- `hsv_to_hex(h, s, v) -> str` — module-level pure helper; HSV components in `[0, 1]` (clamped) → lower-case `#rrggbb`.
- `hex_to_hsv(hexstr) -> tuple[float, float, float]` — module-level pure helper; parses `#rgb` / `#rrggbb`, falls back to mid-gray on bad input.

## Dependencies
- External: `tkinter`, `tkinter.ttk`, `tkinter.colorchooser`, `colorsys`, `math`, `numpy`.
- Internal: `._modal_base.BaseModalDialog`, `._modal_base.protect_combobox_wheel`, `.native_theme`.

## Design Decisions
- **Side-by-side layout — no view toggle.** Both panes are permanently
  visible (audit `color-picker-side-by-side`): `_advanced_frame` is packed
  `side="left", fill="both", expand=True`, and `_swatches_frame` is packed
  `side="right", anchor="n", padx=(10, 0)`. There is no `_view_var`
  radiobutton, no `_show_view()` method, and no `_adv_btn`/`_sw_btn`
  toolbuttons — they were retired so the user never has to flip back and
  forth between "precise" and "graphical" pickers. The hex entry + preview
  swatch row is mounted inside `_build_swatches` under the honeycomb +
  grayscale strip so the touch-friendly swatch grid and the precise hex
  entry sit together on the right.
- **No PIL.** The SV gradient image is built with `tk.PhotoImage.put(...)` fed by a numpy-vectorised HSV→RGB conversion (`_sv_rgb_arrays`, `_put_data`). numpy is a core dependency; PIL is intentionally not bundled in the frozen `.exe`. The SV image is recomputed only when hue changes (not on every SV drag) to keep dragging responsive.
- **Wider, resizable window.** Geometry is `760x420`, `resizable=(True, True)`, with `minsize(720, 400)`. The wider default + minsize floor guarantees both panes plus the footer are visible side-by-side without horizontal clipping on the advanced HSV canvas.
- **Flat-top hexagonal grid via axial coordinates.** Swatch cells are positioned by the standard hex-grid formula `x = 1.5*size*q`, `y = √3*size*(r + q/2)` and drawn with `Canvas.create_polygon`. Ring-N coordinates come from a single CW walk in the six axial directions, `n` cells per direction.
- **Two visual layers, one click handler.** Honeycomb canvas + grayscale row are wired to the same `_on_pick(color)` callback (immediate commit). The advanced view commits via the footer OK button / Return; the hex entry commits on `<Return>` / `<FocusOut>` through `_on_hex_entry`.
- **System… as escape hatch.** Opens `tkinter.colorchooser.askcolor` for users who prefer the OS chooser.
- **Modal via `wait_window` + `grab_set`.** Picker callers (e.g. `IndicatorDialog`) get a synchronous return value, matching the ergonomics of `colorchooser.askcolor`.
- **Native Canvas theming**: the popup background, outer `tk.Frame`, the HSV `tk.Canvas` (`self._sv_canvas`), the hue strip (`self._hue_canvas`), and the swatch `tk.Canvas` (`self._canvas`) use the active theme's `win_bg` so dark mode does not show the OS-default light canvas behind any pane.
- **Hex normalisation.** All returned colors go through `_normalise`: `#RRGGBB` lower-cased; short-form `#RGB` expanded; empty input → `#888888`. Comparison sites (e.g. `_resolved_color_for`) use `.upper()` so case differences from external sources never produce false-positive overrides.

## Invariants
- Returns `None` iff the user cancelled (Esc / Cancel / WM close); never returns an empty string.
- The 19-color honeycomb table (`_HONEYCOMB_COLORS`) and the 6-color grayscale row (`_GRAYSCALE_COLORS`) are immutable module-level tuples; their lengths are part of the public contract (the smoke test asserts `len(_HONEYCOMB_COLORS) == 19`).
- `self._canvas` (the honeycomb canvas), `self._sv_canvas` (the HSV gradient), and `self._hue_canvas` (the hue strip) all exist after construction and are simultaneously visible (no view toggle).
- The picker grabs focus exclusively while open and releases the grab on dismissal.
- **Tk-main-thread-only** — all Tk widget construction and mutation occurs on the Tk thread.

## Testing
- `check_b42_indicator_color_palette` — covers hex normalisation, honeycomb table size, and the dialog integration round-trip.
- `tests/unit/gui/test_native_widget_dark_theme.py` asserts the swatch Canvas and popup background use `DARK_THEME["win_bg"]`.
- `tests/unit/gui/test_color_palette_advanced.py` — pins the HSV helper round-trips, the side-by-side layout (both panes simultaneously visible, no `_view_var`/`_adv_btn`/`_sw_btn` attrs), the wider resizable window (≥720 wide), OK/Cancel reachability, swatch immediate-commit, hex-entry commit, and the hex-entry-lives-under-swatches placement.

## Modal keys
`HexColorPalette.__init__` calls `BaseModalDialog._finalize_modal(primary=self._on_ok, cancel=self._on_cancel)`. Return/OK commits the current advanced selection (`self._current`); ESC cancels. Swatch clicks commit immediately via `_on_pick`.
