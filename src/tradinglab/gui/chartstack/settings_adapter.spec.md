# `chartstack/settings_adapter.py` — Defaults + parsing for `chartstack.*` keys

## Purpose
One canonical defaults table for the ChartStack settings keys, plus
helpers (`is_enabled`, `card_count`, `binding_mode`) that apply the
clamp / parse rules in exactly one place.

## Public API
- `DEFAULTS: dict[str, Any]` — the table; mirrors §7 of the spec.
- `get(key)` — `_settings.get(key, DEFAULTS[key])` with a graceful
  pass-through for non-DEFAULTS keys.
- `is_enabled() -> bool` — convenience for the panel constructor.
- `card_count() -> int` — clamped to `[min, max]`.
- `binding_mode() -> BindingMode` — accepts string, enum, or junk.

## Dependencies
- `tradinglab.settings` (lazy import inside `get()` to avoid an
  import-time cycle while the chartstack package is constructed).
- `.binding.BindingMode` (lazy import inside `binding_mode()`).

## Design decisions
- **Read-only.** This module never writes settings. Mutations go
  through the settings dialog, which calls `_settings.set(...)`
  directly.
- **Clamp at read time.** A user who hand-edits their config to
  `cards.count=12` should still get a working stack at the configured max
  (default 6), not a crash.
- **Default = `chartstack.enabled=False`.** ChartStack remains opt-in even
  though later milestone code paths are present.
