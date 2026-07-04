# `gui/native_theme.py` spec

## Purpose

Centralizes dark/light theming for classic Tk widgets that are not reached by the global `ttk.Style` sweep.

## Contract

- `current_theme(owner)` resolves the active theme dict for `owner`. **Walks the Tk widget `master` chain** looking for the first ancestor that exposes `_theme_ctrl` (typically ChartApp at the root), so a deeply-nested dialog (e.g. the color picker opened from inside an IndicatorDialog opened from ChartApp) still picks up the root ChartApp's active theme without every intermediate Toplevel re-exposing the controller. Fallback order if no `_theme_ctrl` is reachable: `owner.dark_var` → `owner._dark_mode` → `LIGHT_THEME`. Walk is hard-capped at 64 hops (paranoia) and uses `getattr` everywhere so non-Tk owners (test stubs, `SimpleNamespace`) still work.
- `apply_listbox_theme(widget, theme)` paints `tk.Listbox` with `tree_bg`, `tree_fg`, `spine`, removes the native border, and sets a one-pixel themed focus ring.
- `apply_text_theme(widget, theme)` paints `tk.Text` with `ax_bg`, `text`, `spine`, themed insertion/selection colors, no native border, and a one-pixel themed focus ring.
- `apply_canvas_theme(widget, theme)` paints `tk.Canvas` backgrounds with `win_bg`; canvas contents keep their own item colors.
- `apply_toplevel_theme(widget, theme)` paints a `tk.Toplevel` / `tk.Tk` `bg` with `win_bg` so any region a themed ttk frame does not cover (a form narrower/shorter than the window) matches the app in dark mode instead of showing the bright system default. Best-effort (`TclError` swallowed).

## Design Decisions

- **Master-chain walk-up** (audit `color-picker-theme-walks-master-chain`): the historical `current_theme` only inspected `owner._theme_ctrl` directly. Dialogs that are themselves Toplevels (e.g. `IndicatorDialog`) do NOT carry a `_theme_ctrl` attribute — only ChartApp does. Opening the color picker from such an intermediate dialog produced a stuck-light picker even when the app was in dark mode, because the picker's parent (the IndicatorDialog) had no `_theme_ctrl` and the lookup fell back to `LIGHT_THEME`. The walk-up fixes this without requiring every intermediate dialog to re-expose `_theme_ctrl`.
- **`winfo_toplevel` fallback** — when a node's `master` is `None` (it's a root) but the node IS NOT the ChartApp root (e.g. an embedded Toplevel), try `winfo_toplevel()` to jump straight to the real top. Some Tk hierarchies break the `master` chain at the Toplevel boundary; `winfo_toplevel()` handles them. Guarded with a `visited` set so cycles cannot loop forever.

## Tests

Pinned by `tests/unit/gui/test_native_widget_dark_theme.py`, which builds every audited native-widget dialog under `DARK_THEME` and asserts the classic Tk widget options are dark palette values rather than OS defaults. The themed `ThemedColorChooser` (audit `themed-color-chooser`) is covered separately by `tests/unit/gui/test_themed_color_chooser.py::test_dark_theme_chrome_uses_dark_bg` and `::test_dark_theme_labels_use_dark_palette` — those pin that the chooser's `tk.Canvas` chrome + classic `tk.Label`s adopt `DARK_THEME` palette values while leaving the rendered swatch / gradient pixels intact.

