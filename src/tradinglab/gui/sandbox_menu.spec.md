# gui/sandbox_menu

## Purpose

Menu-callback handlers for the **Sandbox** cascade — Start, End,
Performance, Market Heatmap, Save, Load, Tags, and Prepare Universe
Data. Extracted
from `app.py` so menu wiring is decoupled from sandbox lifecycle
helpers (those remain on ChartApp because they're also called from
non-menu paths: ticker entry, watchlist, drilldown).

## Public Surface

`class SandboxMenuMixin` (all methods private):

- `_on_menu_sandbox_start()` — open `SandboxStartDialog`,
  resolve the reference symbol from `sandbox_reference_symbol`
  (default `SPY`), sync-fetch it at chosen interval if not cached, build
  `SessionSpec` via `_build_sandbox_spec`, construct
  `SandboxController`, call `start_session`, restrict toolbar
  intervals, mount SandboxPanel, install strict-offline universe
  seal, auto-register the user's pre-sandbox primary ticker.
- `_on_menu_sandbox_end()` — call `end_session`, cache result +
  screenshot dir for post-end Save/Performance, drop controller
  ref, hide panel, reset scanner/watchlist state, refresh
  indicator dialog's interval set.
- `_on_menu_sandbox_perf()` — open `PerformanceView` on the
  current/last `SessionResult`.
- `_on_menu_sandbox_heatmap()` — open the Sandbox Market Heatmap
  pop-out for the active session. Warns when no session is active.
- `_on_menu_sandbox_save()` — write current/last result to JSON
  via `backtest.persistence.save_session`.
- `_on_menu_sandbox_load()` — read saved `SessionResult` + open
  `PerformanceView` (doesn't clobber an active session).
- `_on_menu_sandbox_tags()` — open `TagsEditorDialog` for the
  setup-tag taxonomy.
- `_on_menu_sandbox_prepare_universe()` — open
  `UniversePrepareDialog`. Refuses while sandbox active (would
  mutate `_full_cache` mid-replay).

## Mixin Rules

- No `__init__`.
- No cooperative `super()` — plain MRO.
- No name collisions with other mixins / `ChartApp`.

## Required Instance State (provided by ChartApp)

- `self._sandbox` — current `SandboxController` (or None).
- `self._sandbox_tag_store` — `SetupTagStore`.
- `self._sandbox_universe`, `self._sandbox_universe_id`,
  `self._sandbox_strict_offline` — universe-seal state.
- `self._last_sandbox_result`, `self._last_sandbox_screenshot_dir`
  — post-end cache.
- `self._sandbox_full_session_xlim`,
  `self._preserve_xlim_on_render` — cleared by
  `_on_menu_sandbox_end` so sandbox-only chart state doesn't leak.
- `self._full_cache` — populated with SPY entries on start when
  dialog selects an uncached interval.
- `self._confirmed_primary_ticker` — read at session start.
- `self._watchlist_snapshot`, `self._indicator_dialog`,
  `self._status`, `self.source_var`, `self.interval_var`,
  `self.prepost_var`.

## Helpers Delegated to ChartApp

`_is_sandbox_active`, `_sandbox_screenshot_dir`,
`_build_sandbox_spec`, `_show_sandbox_panel`,
`_hide_sandbox_panel`, `_sandbox_register_and_focus`,
`_restrict_toolbar_intervals_for_sandbox`,
`_restore_toolbar_intervals_from_sandbox`, `_reset_scanner_state`,
`_refresh_watchlist_for_sandbox`, `_current_sandbox_result`,
`_current_sandbox_screenshot_dir`, `_preload_watchlist`,
`_preload_watchlist_daily`, `_populate_watchlist_tab`.

## Locked Design Decisions

- **Reference symbol**: `sandbox_reference_symbol` (default `SPY`)
  anchors the master clock. If it can't be fetched, fail fast with a
  status message rather than synthesizing a fallback timeline.
- **Data source: longest + highest-quality available (perf item #7).**
  The reference (and daily-context) fetch uses `_sandbox_src(itv)` =
  `data.quality.preferred_source(source_var, interval=itv)` — the
  best-ranked registered/user-visible source for the interval, NOT
  merely the active chart source. Rationale: a sandbox needs deep
  replayable history; yfinance's ~60-day intraday cap barely covers two
  months of eligible days, while Alpaca/Schwab reach years. Respects an
  explicit synthetic/stub choice (returns it unchanged), so the default
  headless env (yfinance-only) is a no-op. When the chosen source differs
  from the active one, a `_status.info` line names it; when it has partial
  (IEX) volume, `_status.warn(partial_volume_warning(...))` fires (perf
  item #1). All three source-selection sites (`_eligible_dates_at`,
  `_fetch_reference_at`, the start-flow fetch) share `_sandbox_src` so the
  `_full_cache` keys stay consistent.
- **Sandbox intervals**: `["1m", "2m", "5m", "15m", "30m", "1h"]`
  only (master clock is intraday).
- **Daily reference fetch** (`daily_lookback_bars > 0`): degrades
  gracefully — failure leaves the 1d toggle unavailable but
  sandbox still starts.
- **Auto-cycle without eligible dates**: falls back to single-day
  mode and warns rather than refusing.
- **Strict-offline universe**: captured **after** `self._sandbox`
  is constructed so a session-build failure doesn't leak universe
  state. Reference symbol implicitly added to allow-set.
- **ChartStack lockstep (M5)**: after `_show_sandbox_panel()`,
  `_on_menu_sandbox_start` calls
  `self._chartstack.attach_sandbox(self._sandbox)` (when panel is
  wired) so each card advances one bar per
  `SandboxController.next_bar`. Detach is implicit: `end_session`
  fires panel's subscriber one final time with
  `is_active()==False`; panel self-detaches via
  `_on_sandbox_tick` → `detach_sandbox`. Wrapped in swallowing
  try/except — chartstack failure must never block sandbox start.
- **Prepare Universe Data placement**: `_on_menu_sandbox_prepare_universe`
  is wired under the Sandbox cascade because it prepares the offline
  universe that strict-offline sessions replay.
