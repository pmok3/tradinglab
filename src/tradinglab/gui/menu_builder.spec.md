# `gui/menu_builder.py` — menubar extraction

## Purpose
- Move the large menubar-construction block out of `app.py` while preserving TradingLab's existing menubar surface across these top-level cascades, **in display order**:
  1. **File** — Load / Save / Save As Configuration, Recent Configurations, Theme…, Exit.
  2. **Watchlists** — Open Watchlists Manager (Ctrl+L), Load / Save / Save As Watchlists, Recent Watchlists.
  3. **Indicators** — Manage Indicators, presets.
  4. **Sandbox** — Start / End Session, Performance, Save / Load Session, Tags.
  5. **Entries** — sits left of **Exits** (entries logically precede exits in the trade lifecycle).
  6. **Exits** — Edit Strategies.
  7. **Strategy** — Open Strategy Tester.
  8. **View** — Heikin-Ashi (cascade: Show Heikin-Ashi Candles + Highlight Flat Bars), Highlight Key Bars, Volume time-of-day shading, ChartStack (cascade: Show ChartStack + Settings…), Heatmap.
  9. **Tools** — Credentials, Connect to Schwab, Local Data, Download Replay Data, Export CSV, Status History, Reveal Data Folder, Restore Templates.
  10. **Help** — built by `HelpMenuMixin._build_help_menu`.
- Keep menu commands routed back into `ChartApp` through a narrow callback protocol so the builder owns widget construction, not app business logic.

## Public API
- `MenuBuilder(root, callbacks)`
  - `root`: the live `tk.Tk` instance that owns the menu widgets.
  - `callbacks`: protocol-typed command surface implemented naturally by `ChartApp`.
- `build() -> tk.Menu`
  - Constructs the full menubar and returns the top-level `tk.Menu`.
- Properties:
  - `menubar`
  - `view_menu`
  - `ha_menu`
  - `chartstack_menu`
  - `submenus`
  - `recent_config_menu`
  - `recent_watchlist_menu`

## Design
- The builder creates only widgets and menu wiring.
- Dynamic cascades stay dynamic:
  - recent configurations/watchlists rebuild via `_refresh_recent_menu(..., on_pick=...)`
  - indicator preset cascades rebuild via `_populate_indicator_preset_menu(...)`
- `HelpMenuMixin` stays the owner of Help-menu entry wiring; `MenuBuilder.build()` calls `_build_help_menu(menubar)` and appends the returned submenu to `submenus` for theme repaint compatibility.
- `ChartApp._build_menubar()` remains the compatibility seam; it instantiates `MenuBuilder`, installs the returned menu on the root, and mirrors legacy attributes (`_menubar`, `_view_menu`, `_ha_menu`, `_menubar_submenus`, `_recent_config_menu`, `_recent_watchlist_menu`).

## Design decisions
- **Watchlists is a top-level cascade.** Previously the load / save / recent items were nested under File. They were promoted to a dedicated top-level menu so the most-used watchlist actions (and the manager dialog) sit one click away rather than two — matching the toolbar's `Watchlists (Ctrl+L)` button affordance.
- **Entries appears left of Exits.** Entries logically precede Exits in the trade lifecycle, so the menubar mirrors that order rather than alphabetical.
- **Theme lives under File, not View.** Theme selection is a one-time/per-session preference (similar to "Load Configuration") rather than a transient view toggle like Heikin-Ashi or ChartStack — the placement matches that mental model. The accelerator on the View menu is removed; users open the theme editor via File → Theme… or via Settings → Open Theme Editor….
- **Heikin-Ashi is a cascade, not three top-level entries.** Audit `ha-menu-cascade` (2026) grouped the "Show Heikin-Ashi Candles" toggle and the "Highlight Flat Bars" overlay into a single `Heikin-Ashi` cascade inside View. The flat-bar entry is always enabled/clickable even while HA is off; its BooleanVar persists independently, and rendering is gated downstream by HA mode AND the flat-highlight toggle. Top-level "Highlight Key Bars" stays a sibling because it's not HA-specific. The cascade submenu is registered in `submenus` so `ThemeController._apply_menubar_theme` repaints it on theme toggle.
- **Volume TOD shading is in View and Settings.** The overlay remains default-off and still appears in Settings, but the View menu also exposes a checkbutton so chart-only users can discover and flip the visual layer without opening the full settings dialog. Both surfaces drive `ChartApp.set_volume_tod_enabled`, keeping persistence, prefetch warmup, and redraw behavior identical.
- **Ratio charts (A/B) submenu** (audit `ratio-render-modes`). `View → Ratio charts (A/B)` is a cascade (`_ratio_menu`, registered in `_submenus` for dark-theme + cleanup) with one checkbutton — *Rebase to 100* (`_ratio_rebase_var` → `_on_menu_toggle_ratio_rebase`). Only affects ratio symbols (AMD/NVDA, ...), which always render as candlesticks with the volume pane hidden; see `app.spec.md` → "Ratio render mode". A ratio is charted by typing `NUM/DEN` directly in the ticker box — there is no Tools-menu dialog. Persisted + restored like the other View toggles.
- **Heatmap is a direct browser launch, not a dialog.** Audit `view-heatmap-launcher` (2026). The `View → Heatmap` entry hands off to `webbrowser.open("https://finviz.com/map.ashx?t=sec", new=2, autoraise=True)` — the Finviz S&P 500 sector treemap (1D performance). No intermediate popup; per `tests/unit/gui/test_ellipsis_semantics.py` the label has no ellipsis since it doesn't open a dialog. Fallback when the OS browser hand-off fails is a `messagebox.showinfo` containing the URL so the user can copy-paste it manually. Callback lives on `ChartApp._on_view_heatmap` and is declared on the `MenuBuilderCallbacks` protocol next to `_on_view_toggle_chartstack`.
- **ChartStack Settings popup opens from the View menu.** Audit `chartstack-fixed-preset` (2026). The `View → ChartStack → Settings…` entry (with ellipsis since it opens a dialog) opens `gui.chartstack_settings_dialog.ChartStackSettingsDialog`, a small modal with one `ttk.Entry` per ChartStack card slot. Saving writes the entries' upper-cased contents to `chartstack.fixed_preset_symbols`, flips `chartstack.binding.mode` to `"FIXED_PRESET"`, and (if the live `_chartstack` panel is mounted on the parent) calls `panel.refresh()` so the cards re-bind immediately. Callback `ChartApp._on_view_chartstack_settings` is declared on the `MenuBuilderCallbacks` protocol next to `_on_view_heatmap`.
- **ChartStack is a cascade, not two flat entries.** Audit `chartstack-menu-cascade` (2026) grouped the show/hide toggle and the per-slot Settings popup into a single `ChartStack` cascade inside View — mirroring the Heikin-Ashi cascade. The cascade child `Show ChartStack` is the checkbutton (keeps the `Ctrl+`` accelerator, binds `_chartstack_visible_var` / `_on_view_toggle_chartstack`); `Settings…` is the command opening the dialog. The submenu is built as `cs_menu`, exposed via the `chartstack_menu` property + mirrored onto `ChartApp._chartstack_menu`, and registered in `submenus` so `ThemeController._apply_menubar_theme` repaints it on theme toggle. The previous flat layout (top-level `ChartStack` checkbutton + top-level `ChartStack Settings…` command) is retired.
- **Strategy Tester is its own top-level cascade.** The single
  `Open Strategy Tester…` command sits between Exits and View so
  strategy-testing workflow is discoverable without overloading the
  Sandbox menu.

## Notes
- `MenuBuilder` intentionally preserves the existing submenu list shape used by `ThemeController._apply_menubar_theme`.
- The Indicators → `Manage Indicators…` entry still opens `gui.indicator_dialog.open_indicator_dialog(self)`; only the widget assembly moved.
- The Indicators → `Custom Indicator Builder…` entry (added directly under `Manage Indicators…`) dispatches via `self._cb._on_custom_indicator_builder()` to `IndicatorMenuMixin._on_custom_indicator_builder`, which opens `gui.custom_indicator_dialog.open_custom_indicator_dialog(self)`. See `gui/custom_indicator_dialog.spec.md`.
