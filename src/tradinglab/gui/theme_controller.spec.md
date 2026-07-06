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
- `_apply_overlay_artists` walks the matplotlib overlays whose colours are baked into the artist at construction (and therefore wouldn't pick up a theme change automatically): hover annotation, crosshair lines, cursor-crosshair price labels, OHLCV readout (`_main_text` **and** every per-overlay legend row's name label — visible rows recolor `row["label_textarea"]` to `text`, hidden rows recolor every child `TextArea` to `muted`, so indicator names on the price pane follow a live theme swap instead of staying their build-time colour until the next `_render`), typing-preview text, per-pane inline value labels (`_pane_value_labels`, now `dict[axes, list[(config_id, Text)]]`), and the **live-price overlay** (`gui/live_price_overlay.LivePriceOverlay.apply_theme`). Adding a new always-on chart overlay typically requires a new branch here so theme toggles propagate immediately rather than waiting for the next `_render`.
- `_apply_menubar_theme` delegates to `gui.menu_theme.apply_menu_theme`, which sets every classic `tk.Menu` colour option plus `borderwidth=0` / `relief="flat"` on the menubar and recursively on cascade submenus. It also appends the always-on U+203A (`›`) label suffix to submenu cascade entries as the Windows workaround for native Win32 cascade arrows that ignore Tk foreground options. Audits `menu-disabled-fg`, `menu-cascade-arrow-dark`, `menu-cascade-chevron`.
- `_apply_ttk_style` patches the `Treeview.Heading` ttk layout to drop the built-in `Treeheading.cell` element. clam's `Treeheading.cell` paints its background using a hard-coded `#dcdad5` light grey at the C level — it carries zero configurable options, so neither `style.configure` nor `style.map` can recolour it. Dropping the element lets the next layer (`Treeheading.border`) paint the header using its configured `-background`, which is `theme["ax_bg"]` (dark `#2b2b2b` in dark mode, white `#ffffff` in light mode with a visible `bordercolor=spine` divider). The layout patch is idempotent; the call is wrapped in the same `_silent_tcl` block as the `style.theme_use("clam")` switch. Audit `treeview-heading-dark`. The visible payoff is every Treeview header row (Watchlists, Entries, Exits, Primary OHLC, Compare OHLC, Scanner) finally honouring dark mode instead of carrying a glaring light strip.
- `_apply_treeview_row_tags` covers the watchlist trees and (forward-looking, via `getattr(tab, "_tree", None)`) the Entries and Exits strategy trees. Today the latter pair don't `tag_configure("bull"/"bear")` their rows, but registering the palette now keeps those owned Treeviews reachable in one pass so a future bull/bear tinting feature lands without a controller change. The bull/bear row **backgrounds** route through `constants.bull_row_bg(theme)` / `bear_row_bg(theme)` and the directional **foregrounds** through `constants.sentiment_recolor(theme["bull_row_fg"|"bear_row_fg"], …)`, so when the Okabe-Ito color-blind palette is active the green/red row tints are recoloured to the orange/blue hue (preserving each theme's tuned tone). `ChartApp.set_use_colorblind_palette` calls `_apply_theme()` to re-run this pass on toggle. Audit `color-blind-palette-audit`.
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
