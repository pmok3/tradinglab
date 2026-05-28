# gui/recent_menus.py — Spec

## Purpose

`RecentMenusMixin` extracted from `ChartApp`. Thin pass-through to
`self._config_manager` for the File → Recent Configurations / Recent
Watchlists cascades. The extraction is purely organisational — keeps
the recent-menu plumbing out of the ChartApp god-class so it can
grow (recent strategies, recent sandbox sessions, etc.) without
adding lines to `app.py`.

## Public API

### `RecentMenusMixin` methods (bound on `ChartApp`)

- `_push_recent(kind, path)` — record `path` in the recent list
  for `kind` (e.g. `"config"`, `"watchlist"`).
- `_refresh_recent_menu(menu, kind, *, on_pick)` — rebuild a Tk
  cascade `menu` from the persisted recent list for `kind`, using
  `on_pick` as the per-entry command. Always appends a "Clear
  List" terminal entry.
- `_clear_recent_kind(kind)` — wipe the recent list for `kind`.
- `_on_recent_config_pick(path)` — open the config file at `path`
  (load → apply → refresh menus).
- `_on_recent_watchlist_pick(path)` — open the watchlist file at
  `path`.

## Dependencies

- `self._config_manager` (a `ConfigManager` instance built in
  `ChartApp.__init__`).
- `tkinter` (type-only — `tk.Menu` parameter annotation).

## Design Decisions

- **No `__init__` on the mixin.** Relies on
  `ChartApp.__init__` having instantiated `self._config_manager`
  before any menu handler can fire.
- **Pure delegation, no policy.** Every method body is a
  one-liner forwarding to `ConfigManager`. The mixin is the
  organisational seam; the policy lives in `ConfigManager`.
- **`clear_label="Clear List"` is the only hardcoded literal.**
  Kept here because it's the menu-level label, not config policy.

## Invariants

- All methods are safe to call before any menu is actually shown
  (the `ConfigManager` defends its own state).
- No method raises out of the mixin under normal operation —
  `ConfigManager` swallows / surfaces errors through the status
  bar.
