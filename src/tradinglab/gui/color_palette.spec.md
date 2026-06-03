# gui/color_palette.py — Spec

## Purpose
Themed clone of the Windows Win32 ChooseColor dialog. Implemented
as :class:`ThemedColorChooser`, a modal :class:`BaseModalDialog`
that mirrors the OS chooser layout (Basic colours grid, Custom
colours grid with persistence, H×S pad, L slider, H/S/L + R/G/B
numeric fields, hex entry, "Colour|Solid" split preview, OK / Cancel
+ Add to Custom Colours) but follows the app's light/dark theme via
the existing `gui/native_theme.py` helpers.

Audit tag: ``themed-color-chooser``.

## Why this exists
The native Win32 ChooseColor (`COMMDLG`) opened by
`tkinter.colorchooser.askcolor` has a hardcoded light-grey
background and does NOT honour Windows 10 / 11 dark-mode setting.
The user reported the native chooser was "too sparse" in dark mode
and asked for "a near-identical clone that follows the colour
themes — the only difference should be the background colour." This
module is the answer.

History: the codebase previously shipped a custom `HexColorPalette`
(HSV gradient + honeycomb swatches) → replaced briefly with a
`colorchooser.askcolor` passthrough → re-replaced with this themed
clone. The earlier honeycomb layout was retired; this picker
mirrors the Win32 ChooseColor layout for muscle-memory continuity.

## Public API
- `pick_color(parent, initial="#888888", title="Pick a colour") -> Optional[str]`
  — blocks on `wait_window`, returns the normalised lower-case
  `"#rrggbb"` on OK, or `None` if the user cancels / closes the
  dialog / the underlying Tk call fails.
- `class ThemedColorChooser(BaseModalDialog)` — the dialog itself.
  Exposes `result: Optional[str]` after dismissal. Not normally
  constructed directly; callers go through `pick_color`.
- `_normalise(color: str) -> str` — module-level hex canonicaliser.
  `#RRGGBB` lower-cased; `#RGB` expanded; empty input →
  :data:`DEFAULT_COLOR`; X11 colour names returned unchanged (Tk can
  still resolve them — pinned by
  `tests/unit/test_hex_case_constants.py`).
- `DEFAULT_COLOR = "#888888"` — mid-grey fallback, matches
  `tradinglab.indicators.base.LineStyle` default.

## Dependencies
- External: `tkinter`, `tkinter.ttk`, `colorsys`, `numpy`,
  `pathlib`.
- Internal: `core.io_helpers.atomic_write_json` / `read_json` for
  custom-colours persistence; `paths.app_data_dir` for the file
  location; `_modal_base.BaseModalDialog` /
  `_modal_base.protect_combobox_wheel`; `native_theme.current_theme`
  / `apply_canvas_theme`.

## Design Decisions

### HSL throughout (matches Win32 ChooseColor field labels)
Internal state is `(h, s, l)` in degrees (0–359) + percent (0–100).
RGB and HSL conversions go through `colorsys.hls_to_rgb` /
`rgb_to_hls`. The 2D pad is rendered at fixed L=0.5 so the user's
chosen H+S is unambiguous; the L slider then varies brightness from
0 (black at bottom) to 1 (white at top) for that H+S point. Spinbox
ranges are H 0–359 / S 0–100 / L 0–100 / R/G/B 0–255 — more
intuitive than Win32's internal 0–240 scale.

### Theme wiring
- Dialog `bg`, all `tk.Frame` children, and the four `tk.Canvas`
  objects (basic-grid, custom-grid, pad, slider) use the active
  theme's `win_bg` (resolved via `current_theme(parent)` which
  walks the master-chain for `_theme_ctrl`).
- Classic `tk.Label` chrome uses `win_bg` + `text`.
- The rendered *content* of the canvases (swatch fills, pad
  gradient pixels, slider gradient pixels) deliberately keeps its
  rendered colours — those ARE the colours being chosen, they
  must not theme.
- Numeric fields are `ttk.Spinbox` (themed via the ttk style
  sheet) and `ttk.Entry` (same).

### Custom-colours persistence
- 16-slot list of hex strings persisted as JSON at
  `app_data_dir() / "custom_colors.json"`.
- Loaded once at dialog construction via `read_json(...,
  default=None)` which tolerates missing file + corrupt JSON →
  fall back to 16 white slots.
- Saved on every "Add to Custom Colours" click via
  `atomic_write_json`. OSError during save is logged (WARNING)
  but never raised — the colour pick still works.
- Add-to-custom fill order: first replaces any default-white slot
  from left to right; once all 16 are user-defined, shifts left
  (drops oldest) and appends to slot 15.

### `PhotoImage` retention
The pad + slider `tk.PhotoImage` handles are stored on `self._pad_img`
/ `self._slider_img` (NOT locals) so Tk doesn't garbage-collect
them and blank out the canvases — a perennial Tk gotcha.

### Pad gradient vectorisation
`_render_pad_pixels(W, H)` is pure-numpy HSL→RGB; no PIL. Builds
the `put`-data string for `tk.PhotoImage.put` via a vectorised
hex-formatting trick (~50× faster than per-pixel `%x` Python loop).
Runs once at construction since the pad is fixed at L=0.5.

### Slider gradient
Re-renders whenever H or S change (i.e. user clicks pad / edits
H/S spinbox / picks a swatch / edits hex/RGB). Uses
`colorsys.hls_to_rgb` per-row Python loop — the slider is only
22 × 200 px so the loop overhead is negligible.

### Re-entrancy guard
`self._updating: bool` blocks recursion when programmatic
`Spinbox.set(...)` calls fire the spinbox's own `<FocusOut>` /
command callback. Every `_refresh_all_widgets()` sets the flag
on entry and clears it in `finally`; every edit handler bails
early if the flag is set.

### Initial colour resolution
`_resolve_to_hex(parent, initial)` walks: `_normalise` (lowercases
or expands hex) → if not hex, ask Tk via `parent.winfo_rgb(name)`
(handles X11 names like `"red"`, `"MidnightBlue"`) →
`DEFAULT_COLOR` on `TclError`. This preserves the pre-rewrite
contract that `pick_color(parent, initial="red")` is valid.

### Wheel-over-Spinbox protection (§7.11)
`protect_combobox_wheel(self)` is invoked at the end of `__init__`,
covering all 6 numeric `ttk.Spinbox` widgets in one sweep.

### Geometry persistence
`geometry_key="dlg.color_palette"` re-uses the historical key so
the dialog remembers its position across launches.

## Invariants
- Returns `None` iff the user cancelled / dismissed the dialog;
  never returns an empty string.
- Custom-colours file is always 16 entries on disk (under-length
  read is padded; over-length read is truncated).
- All chrome `tk.Frame` / `tk.Canvas` / `tk.Label` widgets use the
  active theme's `win_bg` / `text` palette values; rendered
  swatch / gradient pixels are unaffected.
- **Tk-main-thread-only** — all widget construction + mutation
  occurs on the Tk thread; persistence I/O is synchronous on the
  same thread (acceptable: `custom_colors.json` is < 1 KB).

## Testing
- `tests/unit/gui/test_themed_color_chooser.py` — pins the public
  surface: helper round-trips (`_normalise` / `_load_*` / `_save_*`),
  dialog widget attributes, initial-colour seeding (hex + X11 name),
  field bi-directional round-trips (hex ↔ RGB ↔ HSL), OK / Cancel
  result wiring, Add-to-Custom persistence, dark-theme chrome colour
  assertions, wheel-over-spinbox guard.
- `tests/unit/test_hex_case_constants.py::test_palette_normalise_returns_lowercase`
  pins `_normalise` lowercase contract.
- `tests/smoke/test_smoke_full.py::check_b42_indicator_color_palette`
  exercises the `IndicatorDialog` per-output `style_overrides` flow
  through `pick_color` (the dialog is constructed but the user
  never visually drives it in the test — commit hooks are driven
  programmatically).
- `tests/unit/gui/test_toplevel_geometry_wiring.py` re-pins
  `("...color_palette.py", "dlg.color_palette")` registry entry.

## See also
- `gui.indicator_dialog` — primary caller via per-output colour swatches.
- `app.ChartApp._legend_pick_color` — legend right-click "Change Colour…" flow.
