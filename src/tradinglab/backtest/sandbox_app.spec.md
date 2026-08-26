# backtest/sandbox_app.py — Spec

## Purpose
- `SandboxAppController` owns app-level sandbox state extracted from `app.py`.
- It is **not** the replay engine; `backtest/replay.py` still owns session advancement.
- Scope: panel mounting, compare/primary install wiring, toolbar locking, scanner refresh, and resume metadata helpers.

## State
- `_sandbox`, `_last_result`, `_last_screenshot_dir`
- `_panel`, `_panel_window`
- `_tag_store`
- `_universe`, `_universe_id`, `_strict_offline`
- `_feed` — the active [`SandboxFeedWarmer`](sandbox_feed.spec.md), or `None`

## Public surface
- Properties: `active`, `engine`, `last_result`, `last_screenshot_dir`, `panel`, `panel_window`, `tag_store`, `universe`, `universe_id`, `strict_offline`, `feed`
- Methods called from `ChartApp` delegation stubs:
  - `build_spec`, `current_result`, `current_screenshot_dir`
  - `show_panel`, `hide_panel`
  - `maybe_write_resume_metadata`, `maybe_prompt_resume`
  - `refresh_scanner_for_sandbox`, `reset_scanner_state`
  - `can_register`, `register_compare`, `sync_compare_to_var`, `register_and_focus`
  - `install_compare_series`, `restrict_toolbar_intervals`, `restore_toolbar_intervals`
  - `reset_compare_for_session_start`, `install_primary_series`
  - `start_feed`, `refresh_feed`, `stop_feed`

## Integration contract
- `ChartApp` keeps legacy method names (`_is_sandbox_active`, `_sandbox_register_compare`, etc.) as thin delegation stubs.
- **Mid-session fetches use the session's pinned source (audit `sandbox-data-source`).** `register_compare` and `register_and_focus` derive `src` via `_sandbox_preferred_src(app, interval)`, which returns the active session's `SandboxController.data_source` when one is pinned (the trader's choice in the Start Sandbox dialog). Only when nothing is pinned does it fall back to `data.quality.preferred_source(app.source_var, interval=interval)` = the global tier-aware priority in `data/source_ranking.py` (paid Alpaca / Schwab / Polygon / yfinance+Alpaca / yfinance / free Alpaca); the `interval` kwarg is accepted for back-compat but does not change the ranking. Preferring the session's pin is what makes a session single-tape: re-deriving the ranking mid-session was a latent vendor-mixing bug, because saving credentials (or a feed change) mid-replay reorders the ranking, so the next symbol loaded could come off a different tape than the timeline it is being replayed against. Falls back to the active source on any error.
- `ChartApp` also keeps legacy sandbox attribute names via property-backed aliases so existing callers and tests can continue reading/writing `app._sandbox`, `app._sandbox_panel`, and related fields.
- `can_register` enforces strict-offline membership with
  `data.index_aliases.canonical_symbol_key`, not literal strings. A universe
  prepared under yfinance may hold `^VIX`, while a Schwab replay offers `$VIX`
  and the Quant tab passes `VIX`; all three must compare as the same
  instrument.
- `can_register` admits a ratio iff every non-numeric leg is in the canonical
  universe. Ratios can never appear in a manifest because `disk_cache` does
  not persist them, but they recompute for free once their legs are cached.
  Rejecting the composite string would make every Quant ratio row unreachable
  in a strict-offline session. The status error names the missing legs.
- **The market feed is owned here, driven from `SandboxMenuMixin`.** `start_feed` on session start, `stop_feed` **before** `end_session` (so an in-flight batch can't register into a dead controller), and `refresh_feed` from `_kick_watchlist_preloads` when pinned watchlists change mid-session. See [sandbox_feed](sandbox_feed.spec.md).
- **`refresh_scanner_for_sandbox` is consumer-gated (`_scanner_has_consumer`).** Once the feed registers the prepared universe this is no longer a two-symbol scan: measured, `ScanRunner.run` over 500 symbols × 400 bars costs ~96 ms for one scan and ~423 ms for three, per tick, on the Tk thread. Two consumers with different tolerances:
  - The **Scanner tab when viewable** — same reasoning as the watchlist visibility guard in [`gui/watchlist_tab`](../gui/watchlist_tab.spec.md); a `ttk.Notebook` unmaps unselected tabs, so results computed while the user is on the Chart tab are invisible. Defaults to "visible" when Tk geometry can't be probed, so a headless harness never silently stops scanning.
  - An **armed `SCANNER_ALERT` entry strategy, always** — via `EntryEvaluator.has_armed_scanner_alert()`. Those fire from the runner's `new_rows` subscription, so a skipped scan is a swallowed entry, not a missed repaint. This keeps the cost on the tick even inside a batched `skip_to_next_day`, deliberately.   A missing / stubbed evaluator answers "yes" rather than risk swallowing a fire.
  - Complex UI work still flows through `ChartApp` callbacks/attributes (`_render`, `_set_data_state`, `_status`, `_toolbar`, Tk vars).
- `build_spec` carries the start-dialog's `decision_logging_enabled` opt-in into `SessionSpec`; missing payload keys default to `False`.

## Testing
- `tests/unit/test_quant_universe.py` covers canonical strict-offline
  membership and ratio leg gating.

## Non-goals
- No engine logic duplication.
- No change to `SandboxMenuMixin` lifecycle flow beyond using delegated `ChartApp` methods.
