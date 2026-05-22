# `gui/menu_builder.py` — menubar extraction

## Purpose
- Move the large menubar-construction block out of `app.py` while preserving TradingLab's existing menubar surface across these top-level cascades, **in display order**:
  1. **File** — Load / Save / Save As Configuration, Recent Configurations, Theme…, Exit.
  2. **Watchlists** — Open Watchlists Manager (Ctrl+L), Load / Save / Save As Watchlists, Recent Watchlists.
  3. **Indicators** — Manage Indicators, presets.
  4. **Sandbox** — Start / End Session, Performance, Save / Load Session, Tags.
  5. **Entries** — sits left of **Exits** (entries logically precede exits in the trade lifecycle).
  6. **Exits** — Edit Strategies.
  7. **View** — Heikin-Ashi (cascade: Show Heikin-Ashi Candles + Highlight Flat Bars), Highlight Key Bars, ChartStack.
  8. **Tools** — Credentials, Local Data, Download Replay Data, Export CSV, Status History, Reveal Data Folder, Restore Templates.
  9. **Help** — built by `HelpMenuMixin._build_help_menu`.
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
- **Heikin-Ashi is a cascade, not three top-level entries.** Audit `ha-menu-cascade` (2026) grouped the "Show Heikin-Ashi Candles" toggle and the "Highlight Flat Bars" overlay into a single `Heikin-Ashi` cascade inside View. The previous flat layout used a disabled-greyed top-level entry to communicate that the flat-bar overlay only matters when HA is on — clearer hierarchy beats clever state styling. The flat-bar entry is still gated on HA mode (via `app._sync_highlight_ha_flat_menu_state`, which walks `_ha_menu` now instead of `_view_menu`); disabling it inside the cascade preserves the persisted preference across HA-off intervals. Top-level "Highlight Key Bars" stays a sibling because it's not HA-specific. The cascade submenu is registered in `submenus` so `ThemeController._apply_menubar_theme` repaints it on theme toggle.

## Notes
- `MenuBuilder` intentionally preserves the existing submenu list shape used by `ThemeController._apply_menubar_theme`.
- The Indicators → `Manage Indicators…` entry still opens `gui.indicator_dialog.open_indicator_dialog(self)`; only the widget assembly moved.
