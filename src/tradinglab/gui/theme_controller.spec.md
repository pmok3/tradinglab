# `gui/theme_controller.py` — Spec

## Purpose
Owns TradingLab's resolved theme palette and persisted per-mode overrides.
Moves the theme application pipeline out of `app.py` while keeping `ChartApp._theme`, `ChartApp._theme_overrides`, and the existing theming entrypoints available for backward compatibility.

## Public API
- `class ThemeController`
  - `ThemeController(root, *, figure=None, canvas=None)` — bind the Tk owner plus optional matplotlib handles.
  - `theme -> Dict[str, str]` — mutable resolved palette dict.
  - `overrides -> Dict[str, Dict[str, str]]` — mutable persisted override dict.
  - `bind_plot(*, figure=None, canvas=None)` — attach the live figure/canvas after UI construction.
  - `on_change(callback)` — register a post-apply callback that receives the resolved palette.
  - `apply(dark: bool)` — resolve the active mode and repaint Tk/matplotlib surfaces.
  - `_load_theme_overrides()`, `_save_theme_overrides()`, `set_theme_override(...)`, `clear_theme_overrides(...)`, `replace_theme_overrides(...)` — override persistence helpers preserved from `ChartApp`.

## Responsibilities
- Resolve `light` / `dark` palettes via `constants.resolve_theme`.
- Apply theme colors to the root window, figure, axes, ttk styles, treeview row tags, hardcoded overlay artists, and classic Tk menus.
- `_apply_overlay_artists` walks the matplotlib overlays whose colours are baked into the artist at construction (and therefore wouldn't pick up a theme change automatically): hover annotation, crosshair lines, cursor-crosshair price labels, OHLCV readout, typing-preview text, and the **live-price overlay** (`gui/live_price_overlay.LivePriceOverlay.apply_theme`). Adding a new always-on chart overlay typically requires a new branch here so theme toggles propagate immediately rather than waiting for the next `_render`.
- `_apply_menubar_theme` sets `disabledforeground=theme["text_disabled"]` on the menubar and every cascade submenu (in addition to the bg/fg/active triple). Without this the OS-default Win32 disabled-text style (etched/embossed) renders the greyed "Highlight Flat HA Candles" entry as blurry/illegible on dark backgrounds. Audit `menu-disabled-fg`.
- Persist sparse `theme_overrides` payloads to `settings.json`.
- Notify `ChartApp` callbacks after each apply so dialogs, overlay legends, notebook tabs, ChartStack, and other owner-managed surfaces can repaint too.

## Design Decisions
- `ChartApp` still exposes the old theme methods as thin delegation stubs; callers do not need to know the controller exists.
- `theme` and `overrides` are mutated in place so the `ChartApp` aliases remain live references.
- The module does not import `tradinglab.app`; owner-specific access happens through the passed `root` object.
- Menubar theming stays here even though menus are built before the plot exists; `bind_plot()` back-fills the matplotlib handles later.

## Invariants
- `overrides` always has top-level `light` and `dark` keys.
- Unknown persisted override keys are filtered out on load.
- Theme application is best-effort: torn-down Tk widgets, matplotlib artists, and callbacks must not raise through the controller.
- All Tk mutation remains main-thread-only.

## Testing
- Covered by the app/unit smoke suite that exercises `_apply_theme`, theme override persistence, dialog live preview, and ChartStack theme propagation.
- Ruff checks target both `app.py` and this module for extraction regressions.
