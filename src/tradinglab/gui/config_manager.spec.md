# `gui/config_manager.py`

## Purpose
Owns configuration-file I/O, startup-default resolution, recent-files dropdown maintenance, the window title computation, and the "unsaved changes" confirm-on-quit prompt. Extracted from `ChartApp` as a controller to keep `app.py` from owning every File / Settings / Recent-list operation.

## Public surface
- `ConfigManager(root: tk.Tk, intervals: tuple[str, ...], sources: list[str])` — bound to a Tk root + the current interval / data-source vocabularies (needed for startup-defaults validation).
- `startup_defaults: dict[str, str]` (property) — resolved defaults (ticker / compare / interval / source / theme), validated against the active vocabularies.
- `apply_loaded_config(parent_widget)` — re-applies a freshly-loaded settings file to live app state: timezone, scroll-zoom direction, theme overrides, startup defaults, indicator manager state, then triggers a render + table refresh + title update.
- `load_config(parent_widget)` / `save_config(parent_widget)` / `save_config_as(parent_widget)` — File menu handlers; uses `filedialog` + `messagebox` so the GUI thread owns the prompt.
- `load_watchlists(parent_widget)` / `save_watchlists(parent_widget)` / `save_watchlists_as(parent_widget)` — same pattern for the Watchlists menu.
- `load_startup_defaults(intervals=None, sources=None) -> dict[str, str]` / `save_startup_defaults()` / `set_startup_default(key, value)` / `clear_startup_defaults()` / `replace_startup_defaults(defaults)` — startup-default lifecycle.
- `push_recent(kind, path)` / `refresh_recent_menu(menu, kind, callback, *, clear_label=...)` / `clear_recent_kind(kind)` — Recent-files dropdown maintenance.
- `on_recent_config_pick(parent_widget, path)` / `on_recent_watchlist_pick(parent_widget, path)` — invoked when the user clicks a Recent menu entry; auto-removes the entry from the dropdown if the file is gone.
- `refresh_title(...)` / `refresh_title_for(...)` (static) — recomputes the window title from ticker / interval / loaded-config / loaded-watchlists / dirty state and applies via `title_setter`.
- `confirm_close_when_dirty(...)` / `confirm_close_when_dirty_for(...)` (static) — Yes/No/Cancel prompt; honors `PYTEST_CURRENT_TEST` + `TRADINGLAB_NO_QUIT_PROMPT=1` to never block CI.

## Design notes
- **Defensive `except` everywhere** — every disk / settings / Tk call is wrapped because the manager is invoked from menu callbacks where an unhandled exception kills the menu reload loop. Pattern: log nothing here, return / continue silently; user-facing errors go through `messagebox.showerror`.
- **`refresh_title_for` is `staticmethod`** so the bound version can be tested without a `Tk` root by passing in mock callables.
- **Confirm-on-quit is bypassed in tests** by checking `PYTEST_CURRENT_TEST` and `TRADINGLAB_NO_QUIT_PROMPT=1`; otherwise smoke tests hang on the modal.
- **Startup-default validation** routes through `constants.resolve_startup_defaults` which is the single source of truth for "what makes a valid `interval`/`source`/`theme` choice".

## Dependencies
- Internal: `..defaults`, `..recent_files`, `..settings`, `..constants` (BUILTIN_STARTUP_DEFAULTS, resolve_startup_defaults). Late: `.._version` for the title-bar version segment.
- Stdlib only beyond that (`tkinter`, `pathlib`, `os`).

## Consumers
`ChartApp.__init__` instantiates one `ConfigManager`; menu-builder handlers route File / Settings / Recent commands through it.

## Tests
Indirectly exercised by every smoke test that opens settings or loads a config. No dedicated unit suite — the manager is mostly orchestration over `settings` / `recent_files` modules which have their own coverage.
