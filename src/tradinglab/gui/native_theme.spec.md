# `gui/native_theme.py` spec

## Purpose

Centralizes dark/light theming for classic Tk widgets that are not reached by the global `ttk.Style` sweep.

## Contract

- `current_theme(owner)` returns `owner._theme_ctrl.theme` when present, otherwise resolves dark mode from `owner.dark_var` / `owner._dark_mode`, and finally falls back to `LIGHT_THEME`.
- `apply_listbox_theme(widget, theme)` paints `tk.Listbox` with `tree_bg`, `tree_fg`, `spine`, removes the native border, and sets a one-pixel themed focus ring.
- `apply_text_theme(widget, theme)` paints `tk.Text` with `ax_bg`, `text`, `spine`, themed insertion/selection colors, no native border, and a one-pixel themed focus ring.
- `apply_canvas_theme(widget, theme)` paints `tk.Canvas` backgrounds with `win_bg`; canvas contents keep their own item colors.

## Tests

Pinned by `tests/unit/gui/test_native_widget_dark_theme.py`, which builds every audited native-widget dialog under `DARK_THEME` and asserts the classic Tk widget options are dark palette values rather than OS defaults.
