# backtest/replay.py — Spec

> ⚠ **Tk-coupled module** — imports `tkinter`. The SOLE Tk-coupled
> module in `backtest/`; must NEVER be auto-imported from
> `backtest/__init__.py` or any kernel module.

## Purpose
`SandboxController` — Tk-coupled UI controller bridging
[`SandboxEngine`](engine.spec.md) to `ChartApp`. Owns session
lifecycle, the open-universe register-mid-session model, post-trade
review callback, screenshot capture, blind / auto-cycle orchestration,
and multi-timeframe daily-context. Plus `SandboxMemento` — the
pre-sandbox app-state snapshot restored on `end_session`.

## Public API
### `SandboxController`
- `start_session(*, spec, session_date, interval, reference_symbol, reference_candles, lookback_days=1, screenshot_dir=None, include_extended=False, auto_cycle=False, blind=False, eligible_dates=None, daily_lookback_bars=100, daily_reference_candles=None, display_intervals=None)` — open-universe start anchored on a single reference ticker.
- `register_ticker(symbol, candles) -> List[Any]` — add a symbol mid-session; returns the per-symbol *visible* candle list (stable identity for the session).
- `register_daily_for(symbol, daily_candles)` — lazy-attach a per-symbol raw daily series for 1d-context display.
- `next_bar() -> bool` — advance one tick; sync visibles, invalidate focused caches, redraw, drive post-trade callbacks + screenshots.
- `set_focus(symbol)` — swap primary chart; honours `display_interval`.
- `set_display_interval(interval) -> bool` — toggle between primary, any other entry in `display_intervals`, or `"1d"`. Other values rejected.
- `aggregated_visible_for(symbol, target_interval) -> List[Candle]` — re-aggregate the per-symbol primary visible list.
- `full_display_length_for(symbol) -> int` — total x-axis length the app should reserve for the current display interval, including not-yet-visible session bars.
- `submit_order(*, symbol, side, quantity, pre_trade_data) -> str` — mints `ord-NNNN`, files `PreTradeEntry`, queues the engine order, captures `<order_id>_pre.png`.
- `cycle_to_next() -> bool` — auto-flatten, archive engine, build fresh engine for the next eligible date with cash carried forward, re-register every previously-known ticker, tick once, fast-forward.
- `end_session() -> Optional[SessionResult]` — restore from memento, return merged result.
- `result() -> Optional[SessionResult]` — current SessionResult, merging archived (auto-cycle) + current cycle.
- `set_post_trade_callback(cb)` — register the post-trade review callback.
- `register_card_subscriber(callback) -> Callable[[], None]` — M5 ChartStack lockstep: register a zero-arg callback fired synchronously inside `next_bar` / `cycle_to_next` after the engine has advanced and per-symbol visibles extended. Returns an idempotent `release()` callable. Exceptions are swallowed per subscriber. Cleared on `end_session` AFTER one final fire — subscribers can observe `is_active() == False` and self-detach.
- `current_session_date() -> Optional[date]`.
- `current_day_note() -> str` / `set_day_note(text)` — get / set the trader's free-text watch note for the current replay session day. Keyed by the UTC `current_session_date()`; `set_day_note` strips trailing whitespace and removes the entry when the result is empty; both no-op when there is no clock.
- `current_day_ordinal() -> int` — blind-safe 1-based count of distinct session days visited so far (drives the panel's "Replay Day N" label without leaking the calendar date).
- `daily_visible_for(symbol) -> List[Any]` — daily slice strictly before `current_session_date()`, capped to `daily_lookback_bars`.
- Inspection helpers: `is_active`, `positions_snapshot`, `cash`, `clock_ts`, `tickers`.
- Public attributes: `engine`, `spec`, `interval`, `focus_symbol`, `visible_candles_by_symbol`, `tag_store`, `session_id`, `screenshot_dir`, `include_extended`, `auto_cycle`, `blind`, `display_interval`, `display_intervals`, `daily_lookback_bars`.

### `SandboxMemento`
- `capture(app) -> SandboxMemento` (classmethod) — snapshot ten fields: `_primary`, `_compare`, `candles` (lists); `ticker_var`, `compare_ticker_var`, `compare_var`, `interval_var` (Tk vars); `_drilldown_day`; `_confirmed_primary_ticker`, `_confirmed_compare_ticker`.
- `restore(app)` — re-assign; calls `app._render()` under a narrow `tk.TclError` guard for app-close races (via `_silent_tcl()`).

## Dependencies
- Internal: [`bars`](bars.spec.md), [`deck`](deck.spec.md), [`engine`](engine.spec.md), [`journal`](journal.spec.md), [`orders`](orders.spec.md), [`session`](session.spec.md), [`tags`](tags.spec.md).
- External: `tkinter`, `numpy` (only for `_fast_forward_to_session_open`'s `searchsorted`).

### File structure
- `replay.py` — `SandboxController` body + `SandboxMemento`.
- `replay_events.py` — `EventsControllerMixin` (first mixin in `SandboxController`'s bases). Owns interaction with `tradinglab.events`: `set_event_bundle`, `prefetch_events_for`, `_register_corporate_actions_from_bundle`, `events_visible_for`, `_compute_event_proximity`. The engine never imports from `events`; this mixin is the explicit boundary.

## Design Decisions
- **Open-universe**: master clock anchored on `reference_symbol` (typically SPY, sync-fetched). Tickers loaded mid-session via `register_ticker` join WITHOUT extending the timeline.
- **Master timeline frozen at `start_session`**: `SandboxEngine` constructed with `master_timeline=ref_bars.ts.copy()`. Subsequent `register_bars` only add per-symbol price sources. Extending mid-session would invalidate `clock.index`.
- **`register_ticker` is idempotent + immutable**: same-content fingerprint → return existing visible list; different-content → `ValueError`. Replacing a series mid-session would retroactively change prior fills' MAE/MFE.
- **Per-symbol visible list grown in place**: identity stable, so `app._series_cache` and the indicator cache (keyed by `id(visible)`) keep hits.
- **Catch-up replay on register**: a symbol joining at clock index 50 immediately gets bars `0..50` appended (driven by `BarSeries.index_for_ts(now_ts)`). Bars not in the master timeline are skipped.
- **`_fast_forward_to_session_open`**: bumps `clock.index` directly to the first bar of `session_date` (UTC midnight), clears warmup equity curve, re-syncs visibles. No fills during lookback.
- **`SandboxMemento` is explicit**: every pre-sandbox app state captured + restored in one call.
- **Bumps `app._fetch_token` at start**: stale background fetches bail. Also clears `_prefetched_raw` and `_drilldown_day`.
- **Blind mode (display-only)**: replay behaviour identical to non-blind; only display differs. Price axis anchored as if `now` were the right edge; date readout suppressed (only time-of-day shows). Time-of-day NOT hidden — session-relative position is inferable. Mirrored in [`session.spec.md`](session.spec.md).
- **Per-day watch notes**: `_day_notes` (dict keyed by UTC session date) buffers the trader's free-text pre-trade observations captured during replay via `set_day_note`; `_day_ordinal` counts distinct session days visited (incremented on each `next_bar` day-boundary cross, reset to 1 in `start_session`). `result()` folds `_day_notes` into `SessionResult.day_notes` on BOTH the single-cycle fast path and the auto-cycle merge path, so they persist through `save_session` and surface in the Performance View daily-journal pane. Engine-independent (like the post-trade review text) so reproducibility is unaffected.
- **Auto-cycle**: `next_bar` past end-of-data calls `cycle_to_next`, which auto-flattens (synthetic fills at last close), archives, draws the next eligible date (deterministic round-robin on seeded shuffle), rebuilds with cash carried forward, ticks + fast-forwards. Compare slot is force-cleared each cycle.
- **Multi-timeframe daily context**: `daily_full_by_symbol` stores raw daily candles per symbol; `daily_visible_for(symbol)` derives the slice live. Visibility rule: bar's session date **strictly less than** current — the in-progress day is omitted. Capped at `daily_lookback_bars`.
- **Daily-context refresh on day-boundary cross only**: `next_bar` tracks `_last_clock_session_date`; per-intraday-tick refreshes skipped while `display_interval == "1d"`. On a cross the daily slice is re-installed.
- **Full-session xlim pre-allocation**: mechanics live in `app.spec.md`; `replay.py` computes `full_display_length_for(symbol)` and passes it to `app._install_sandbox_primary_series(..., full_session_length=...)`.
- **Compare slot refreshed every tick**: when `compare_var` on, `next_bar` notifies the focused-symbol cache AND the compare-symbol cache (via `_notify_focused_panels_appended(compare_visible)`, falling back to `_invalidate_focused_panels` for older apps) and calls `_refresh_view_after_append("compare")` after primary. Append-aware notification preserves the indicator cache so `IndicatorCache.get_or_compute_incremental` can extend cached arrays in O(k).
- **App boundary is narrow**: controller never reads / writes `app._series_cache` or `app._indicator_manager.cache` directly — uses `_install_sandbox_primary_series`, `_notify_focused_panels_appended` (pure-append) / `_invalidate_focused_panels` (forming-bar / fallback), `_draw_slice`, `_render`, `_capture_chart_png`.
- **Per-tick app callbacks** (`next_bar`): after `panel.refresh()`, invokes optional Tk hooks via `getattr(self.app, name, None)`:
  - `_refresh_watchlist_for_sandbox()`
  - `_refresh_scanner_for_sandbox()` — runs every saved scan against `visible_candles_by_symbol`; see [`app.spec.md`](../app.spec.md) §"Scanner tab integration".
  - `_refresh_entries_for_sandbox()`
  - `_refresh_exits_for_sandbox()`

  All no-op in headless tests. Ordering: watchlist before scanner, then entries before exits. Scanner's `"watchlist"` row-action assumes the watchlist sub-tab is up-to-date; exits may rely on entry-side fills from the same tick. Any callback raising **must not** block the tick — wrapped in narrow try/except.
- **M5 ChartStack lockstep fan-out**: `_card_subscribers` is a flat `List[Callable[[], None]]`. `register_card_subscriber` appends + returns an idempotent removal closure. `_fire_card_subscribers` runs at the *end* of `next_bar` (after `_refresh_*_for_sandbox`, after all engine + visible-list mutation) and one final time at the start of `end_session` (before `memento.restore`, with `self.active = False` so subscribers can detach cleanly). Iterates a `list(...)` snapshot. Each call wrapped in swallowing try/except. Subscribers receive no arguments — they read `visible_candles_by_symbol` directly. List fully cleared after the `end_session` final fire.

### display_intervals contract
- User-selected list of viewable intraday timeframes (e.g. `("5m", "15m", "1h")`).
- Smallest entry MUST equal `interval` (the primary fetch interval).
- Every other entry MUST satisfy `aggregation.divides_evenly(interval, entry)`; else `start_session` raises `ValueError`.
- Defaults to `(interval,)`.
- `set_display_interval` accepts any value in `display_intervals` plus `"1d"`; all other values rejected.
- Daily-mode for a symbol with no registered daily series returns `False` rather than raising.

## Invariants
- After `start_session`, `engine.master_timeline` length never changes.
- `register_ticker(s, c)` twice with the same content returns the **same** visible-list object (`is`-equal); different content raises before any state mutation.
- `daily_visible_for(s)` never includes the in-progress day.
- `result()` for non-auto-cycle equals `engine.result()` (fills / pre / post / equity), with `day_notes` overwritten from the controller's watch-note buffer; auto-cycle prepends archived lists in cycle order.
- `end_session` calls `memento.restore` exactly once and clears `self.active` regardless of restore exceptions.

## See also
- [engine](engine.spec.md), [deck](deck.spec.md), [persistence](persistence.spec.md), [session](session.spec.md).
