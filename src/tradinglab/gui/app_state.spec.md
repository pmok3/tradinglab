# gui/app_state.py — Spec

## Purpose
Owns the Tk variable registry for `ChartApp`. This extracts `StringVar` / `BooleanVar` construction out of `app.py` while preserving the historic `self.ticker_var`, `self.compare_var`, `self.status`, and related attribute names through aliases created by `ChartApp.__init__`.

## Public API
- `class AppState`
  - `AppState(master, startup_defaults)` — creates every Tk variable used by `ChartApp` state wiring and parents each one to the live Tk root passed as `master`.
  - `_sync_compare_label(*_args)` — trace callback that mirrors the stripped, upper-cased `compare_ticker` into `compare_label` only while compare mode is enabled.

## State
Constructs and owns these Tk variables:
- `ticker`, `compare_ticker`, `compare`, `compare_enabled`, `compare_label`
- `source`, `interval`, `prepost`, `days`
- `dark`, `log_price`, `watchlist`
- `status`, `status_display`
- `ha_display`, `highlight_key_bars`, `highlight_ha_flat`, `volume_tod`, `chartstack_visible`

## Dependencies
- Internal: `tradinglab.defaults`, `tradinglab.settings`, `tradinglab.data.DATA_SOURCES`, `tradinglab.data.is_internal_source`, `tradinglab.data.user_visible_sources`, `tradinglab.watchlists.DEFAULT_WATCHLIST_NAME`, `tradinglab.gui.chartstack.settings_adapter` (late import for initial ChartStack visibility).
- External: `tkinter`.

## Design Decisions
- **Master-owned vars**: every Tk variable is created with `master=master` so it stays anchored to the app root for the full session.
- **No `ChartApp` import**: the module must stay independent of `tradinglab.app` to avoid circular imports during startup.
- **Compare label stays local**: the compare-label trace lives here because it only depends on Tk variables, while `interval` traces remain in `app.py` because they call back into `ChartApp` behavior.
- **`highlight_ha_flat` defaults to OFF** — first-launch users see plain HA candles without the cross-hatched overlay. Previously the default was ON, which surprised users who didn't opt in. Users who want the highlight enable it explicitly via View → Heikin-Ashi → "Highlight Flat Bars"; the setting is persisted under `highlight_ha_flat` even when HA mode is off. Audit `ha-flat-default-off`.

## Invariants
- `compare_enabled` is an alias of `compare`.
- `compare_label` is blank when compare mode is off.
- Invalid, empty, unregistered, or internal startup `source` values fall back to the first user-visible data source, then to literal `"yfinance"`.

## Testing
- Covered indirectly by the `ChartApp` initialization and smoke tests that read/write the aliased vars.
