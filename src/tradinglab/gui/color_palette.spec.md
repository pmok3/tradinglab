# gui/color_palette.py — Spec

## Purpose
Modal color picker with two views. The **default** view is an **advanced HSV picker** (a saturation/value gradient square + hue strip + live hex entry + preview swatch) for precise color selection. A secondary **Swatches** view supplies the touch-friendly graphical palette of 19 honeycomb swatches (1 center + 6 ring-1 hues + 12 ring-2 tint/shade pairs) plus a 6-cell grayscale strip. A `System…` button still falls back to `tkinter.colorchooser.askcolor` for the OS chooser.

## Public API
- `pick_color(parent, initial="#888888", title="Pick a color") -> Optional[str]` — open the picker modally, block via `wait_window`, return the chosen `"#rrggbb"` (lower-case) or `None` on cancel / WM-close.
- `class HexColorPalette(BaseModalDialog)` — the popup itself; not normally constructed directly. Exposes `result: Optional[str]` after dismissal, and `_normalise(color)` (static) for hex canonicalisation.
- `hsv_to_hex(h, s, v) -> str` — module-level pure helper; HSV components in `[0, 1]` (clamped) → lower-case `#rrggbb`.
- `hex_to_hsv(hexstr) -> tuple[float, float, float]` — module-level pure helper; parses `#rgb` / `#rrggbb`, falls back to mid-gray on bad input.

## Dependencies
- External: `tkinter`, `tkinter.ttk`, `tkinter.colorchooser`, `colorsys`, `math`, `numpy`.
- Internal: `._modal_base.BaseModalDialog`, `._modal_base.protect_combobox_wheel`, `.native_theme`.

## Design Decisions
- **Advanced HSV picker is the default view.** Opening the dialog shows the embedded SV gradient + hue strip + hex entry, so a user who wants `#1f77b4` exactly is never forced through a tiny swatch grid. The honeycomb is demoted to an opt-in "Swatches" view toggled via a `ttk.Radiobutton` group bound to `self._view_var` (`"advanced"` / `"swatches"`). `_show_view(name)` packs one body frame and `pack_forget()`s the other.
- **No PIL.** The SV gradient image is built with `tk.PhotoImage.put(...)` fed by a numpy-vectorised HSV→RGB conversion (`_sv_rgb_arrays`, `_put_data`). numpy is a core dependency; PIL is intentionally not bundled in the frozen `.exe`. The SV image is recomputed only when hue changes (not on every SV drag) to keep dragging responsive.
- **Larger, resizable window.** Geometry is `440x420`, `resizable=(True, True)`, with `minsize(400, 380)`. The minsize floor guarantees the OK/Cancel/System footer stays visible even if a stale small geometry was persisted by `geometry_store` for returning users — this fixes the previous `260x260` fixed window that clipped the action buttons off-screen.
- **Flat-top hexagonal grid via axial coordinates.** Swatch cells are positioned by the standard hex-grid formula `x = 1.5*size*q`, `y = √3*size*(r + q/2)` and drawn with `Canvas.create_polygon`. Ring-N coordinates come from a single CW walk in the six axial directions, `n` cells per direction.
- **Two visual layers, one click handler.** Honeycomb canvas + grayscale row are wired to the same `_on_pick(color)` callback (immediate commit). The advanced view commits via the footer OK button / Return.
- **System… as escape hatch.** Opens `tkinter.colorchooser.askcolor` for users who prefer the OS chooser.
- **Modal via `wait_window` + `grab_set`.** Picker callers (e.g. `IndicatorDialog`) get a synchronous return value, matching the ergonomics of `colorchooser.askcolor`.
- **Native Canvas theming**: the popup background, outer `tk.Frame`, and swatch `tk.Canvas` (`self._canvas`, built in the swatches frame at init even while hidden) use the active theme's `win_bg` so dark mode does not show the OS-default light canvas behind the color cells.
- **Hex normalisation.** All returned colors go through `_normalise`: `#RRGGBB` lower-cased; short-form `#RGB` expanded; empty input → `#888888`. Comparison sites (e.g. `_resolved_color_for`) use `.upper()` so case differences from external sources never produce false-positive overrides.

## Invariants
- Returns `None` iff the user cancelled (Esc / Cancel / WM close); never returns an empty string.
- The 19-color honeycomb table (`_HONEYCOMB_COLORS`) and the 6-color grayscale row (`_GRAYSCALE_COLORS`) are immutable module-level tuples; their lengths are part of the public contract (the smoke test asserts `len(_HONEYCOMB_COLORS) == 19`).
- `self._canvas` (the honeycomb canvas) always exists after construction even when the advanced view is shown, so the dark-theme test can assert its theming.
- The picker grabs focus exclusively while open and releases the grab on dismissal.
- **Tk-main-thread-only** — all Tk widget construction and mutation occurs on the Tk thread.

## Testing
- `check_b42_indicator_color_palette` — covers hex normalisation, honeycomb table size, and the dialog integration round-trip.
- `tests/unit/gui/test_native_widget_dark_theme.py` asserts the swatch Canvas and popup background use `DARK_THEME["win_bg"]`.
- `tests/unit/gui/test_color_palette_advanced.py` — pins the HSV helper round-trips, the advanced-default view, the larger resizable window, OK/Cancel reachability, swatches-view toggle, and hex-entry commit.

## Modal keys
`HexColorPalette.__init__` calls `BaseModalDialog._finalize_modal(primary=self._on_ok, cancel=self._on_cancel)`. Return/OK commits the current advanced selection (`self._current`); ESC cancels. Swatch clicks in the Swatches view commit immediately via `_on_pick`.
