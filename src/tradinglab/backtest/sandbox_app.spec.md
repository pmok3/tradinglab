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

## Public surface
- Properties: `active`, `engine`, `last_result`, `last_screenshot_dir`, `panel`, `panel_window`, `tag_store`, `universe`, `universe_id`, `strict_offline`
- Methods called from `ChartApp` delegation stubs:
  - `build_spec`, `current_result`, `current_screenshot_dir`
  - `show_panel`, `hide_panel`
  - `maybe_write_resume_metadata`, `maybe_prompt_resume`
  - `refresh_scanner_for_sandbox`, `reset_scanner_state`
  - `can_register`, `register_compare`, `sync_compare_to_var`, `register_and_focus`
  - `install_compare_series`, `restrict_toolbar_intervals`, `restore_toolbar_intervals`
  - `reset_compare_for_session_start`, `install_primary_series`

## Integration contract
- `ChartApp` keeps legacy method names (`_is_sandbox_active`, `_sandbox_register_compare`, etc.) as thin delegation stubs.
- **Mid-session fetches use the sandbox's preferred source (perf item #7).** `register_compare` and `register_and_focus` derive `src` via `_sandbox_preferred_src(app, interval)` = `data.quality.preferred_source(app.source_var, interval=interval)`, which now delegates to the global tier-aware priority in `data/source_ranking.py` (paid Alpaca / Schwab / Polygon / yfinance+Alpaca / yfinance / free Alpaca). The `interval` kwarg is accepted for back-compat but does not change the ranking. Compare/focus symbols added mid-session therefore follow the same global source policy instead of silently pulling from a different active-chart source. Falls back to the active source on any error.
- `ChartApp` also keeps legacy sandbox attribute names via property-backed aliases so existing callers and tests can continue reading/writing `app._sandbox`, `app._sandbox_panel`, and related fields.
- Complex UI work still flows through `ChartApp` callbacks/attributes (`_render`, `_set_data_state`, `_status`, `_toolbar`, Tk vars).

## Non-goals
- No engine logic duplication.
- No change to `SandboxMenuMixin` lifecycle flow beyond using delegated `ChartApp` methods.
