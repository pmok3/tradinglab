# gui/config_menu.py — Spec

## Purpose

`ConfigMenuMixin` extracted from `ChartApp`. Owns the File-menu
plumbing for **Load/Save Config**, **Load/Save Watchlists**, and the
quit-time *"unsaved changes — save before exit?"* dialog. Every
method is a thin pass-through to the existing
:class:`gui.config_manager.ConfigManager` (no behaviour change —
purely organisational).

## Public API

### `ConfigMenuMixin` methods (bound on `ChartApp`)

- `_apply_loaded_config() -> None` — re-apply the config bag the
  manager has just loaded (theme, intervals, defaults, etc.).
- `_on_menu_load_config() -> None` — File → Load Config… handler.
- `_on_menu_save_config() -> None` — File → Save Config handler.
- `_on_menu_save_config_as() -> None` — File → Save Config As…
  handler.
- `_on_menu_load_watchlists() -> None` — File → Load Watchlists…
  handler.
- `_on_menu_save_watchlists() -> None` — File → Save Watchlists
  handler.
- `_on_menu_save_watchlists_as() -> None` — File → Save Watchlists
  As… handler.
- `_confirm_close_when_dirty() -> bool` — quit-time prompt. Returns
  True to continue closing, False to abort. Uses the live
  ``self._config_manager`` if available; falls back to the
  class-method ``ConfigManager.confirm_close_when_dirty_for(...)``
  for the unusual case where the manager wasn't fully initialised
  (early-boot crash path).

## Dependencies

- Internal: `.config_manager.ConfigManager` (only the fallback
  class-method invocation in `_confirm_close_when_dirty` needs the
  class itself; the seven dispatchers go through the instance).
- External: none.

## Design Decisions

- **No `__init__` on the mixin.** Relies on
  `ChartApp.__init__` having constructed
  ``self._config_manager`` and ``self._watchlists``.
- **Fallback path in `_confirm_close_when_dirty`.** If
  ``_config_manager`` is missing — e.g. construction failed before
  the manager was wired — the prompt still runs via
  ``ConfigManager.confirm_close_when_dirty_for(...)`` so the user
  isn't silently quit-without-prompt on a broken boot.

## Invariants

- Each `_on_menu_*` dispatcher is one line and never raises beyond
  what the underlying `ConfigManager` raises.
- `_confirm_close_when_dirty` always returns a bool (True / False
  — never None).
