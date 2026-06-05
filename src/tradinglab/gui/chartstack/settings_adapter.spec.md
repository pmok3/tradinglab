# `chartstack/settings_adapter.py` — Defaults + parsing for `chartstack.*` keys

## Purpose
One canonical defaults table for the ChartStack settings keys, plus
helpers (`is_enabled`, `card_count`, `binding_mode`) that apply the
clamp / parse rules in exactly one place.

## Public API
- `DEFAULTS: dict[str, Any]` — the table; mirrors §7 of the spec.
  Now includes `chartstack.fixed_preset_symbols: ["SPY", "QQQ", "VXX"]`
  (audit `chartstack-fixed-preset`).
- `get(key)` — `_settings.get(key, DEFAULTS[key])` with a graceful
  pass-through for non-DEFAULTS keys.
- `is_enabled() -> bool` — convenience for the panel constructor.
- `card_count() -> int` — clamped to `[min, max]`.
- `binding_mode() -> BindingMode` — accepts string, enum, or junk.
  **Default is now `BindingMode.FIXED_PRESET`** (was `HYBRID`).
- `fixed_preset_symbols() -> list[str]` — the per-slot preset
  symbols, normalised (upper-cased + stripped) and length-aligned
  to `card_count()`. Blank entries stay blank (they map to `None`
  bindings downstream); garbage stored under the key falls back to
  the default trio. Audit `chartstack-fixed-preset`.

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
- **Default `binding.mode` = `FIXED_PRESET`** (audit
  `chartstack-fixed-preset`). Previously `HYBRID`. The change means
  that out-of-the-box the cards show the broad-market reference trio
  (SPY top, QQQ middle, VXX bottom) rather than whatever HYBRID
  derives from the user's watchlist + open positions. Users can flip
  back to `HYBRID` by hand-editing settings.json or by exposing a
  binding-mode dropdown in the ChartStack Settings popup later.
