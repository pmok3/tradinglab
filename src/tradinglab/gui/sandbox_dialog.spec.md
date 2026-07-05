# gui/sandbox_dialog.py — Spec

## Purpose
Modal `BaseModalDialog` start dialog for the sandbox subsystem:
`SandboxStartDialog` configures an open-universe Phase 1c-redux
sandbox session.

## File structure
- `gui/sandbox_dialog.py` owns `SandboxStartDialog`.
- `gui/pre_trade_dialog.py` owns `PreTradeFormDialog`; sandbox order
  journaling imports that module directly.

## Public API
- `class SandboxStartDialog(app, *, reference_symbol, intervals, eligible_dates_provider, fetch_provider=None, default_interval=None, default_selected_intervals=None, manifest_provider=None)` — modal, blocks via `wait_window`. `self.result` on close is either `None` (cancel) or a payload dict:
  ```
  {session_date, interval, display_intervals, lookback_days, daily_lookback_bars,
   starting_cash, slippage_bps, commission, deck_seed,
   blind, auto_cycle, eligible_dates,
   universe_id, universe_symbols, strict_offline}
  ```
  `auto_cycle == blind` (Phase 1d UX coupling — blind random implies auto-cycle). `interval` is the *primary* tick interval (= smallest checked entry in `display_intervals`); `display_intervals` is the sorted-ascending list of all checked intraday timeframes (e.g. `["5m", "15m", "1h"]`).

  `universe_id` is the manifest id (e.g. `"sp500"`, `"qqq"`, `"watchlist:Mega Caps"`) when the user picked a prepared universe, else `""`. `universe_symbols` is a sorted tuple of upper-case tickers (the strict-offline allow-list); empty tuple when no universe is chosen. `strict_offline` is `True` only when both a universe is chosen *and* the locked-checked checkbox is set (i.e. always `True` with a universe, always `False` without one).

## Dependencies
- Internal: `..backtest.deck.draw_one_date` (for the "Random eligible date" button + blind-mode self-draw), `._modal_base.BaseModalDialog`, `._modal_base.protect_combobox_wheel`.
- External: `tkinter`, `tkinter.ttk`.

## Design Decisions
- **`fetch_provider` parameter** — optional callable `(interval) -> bool` that sync-fetches the reference symbol at the given interval and stores it in the host's cache. Invoked when `eligible_dates_provider` returns empty (interval change, Random click, or Blind+Start) so Random / Blind aren't stranded by an empty cache. The dialog disables Start / Random / interval controls and shows a `"Fetching {symbol} {itv}…"` status during the call, then restores the prior status on failure. When `None`, the caller is expected to fetch elsewhere; missing-cache simply surfaces the existing error path.
- **No ticker selection in `SandboxStartDialog`**: Phase 1c-redux is open-universe — tickers are loaded mid-session via the regular ticker entry / watchlist. The dialog only configures the master clock + economics. The `reference_symbol` shown is display-only.
- **`eligible_dates_provider` is callable, not a list**: invoked on dialog open and on every interval change so the eligible-count status line + Random button stay in sync without the caller managing change notifications.
- **Multi-interval checkbox group replaces a single combobox**: the user picks any subset of `{1m, 2m, 5m, 15m, 30m, 1h}`; the smallest checked is the primary tick interval and every other entry must satisfy `aggregation.divides_evenly(primary, target)` (validated via `_validate_intervals` before Start). Default-checked: `{5m, 15m, 1h}` ∩ available. `_primary_interval()` is the canonical accessor — invalid combos block Start with an inline error rather than silently picking a primary.
- **`_filtered_eligible_dates` applies the intraday lookback live**: the user typing a new value sees the trimmed count immediately. Both the manual Random button and blind-mode self-draw use the filtered list (otherwise a draw could land on a date with no prior context).
- **Blind mode collapses date controls**: when checked, the date entry shows `(hidden)` and is disabled; on Start the dialog draws the date itself (via `draw_one_date`) and sets `auto_cycle=True`. Eligibility is mandatory in blind mode — without a list there's nothing to randomise over.
- **Blind-mode time-based seed override** (`_on_start`): when `blind=True` and the user-entered seed is the default `0`, the dialog overrides it with `time.time_ns() & 0x7FFFFFFF` so successive blind sessions land on different dates. A non-zero seed is treated as a user-pinned reproducible draw and is honoured as-is. Non-blind sessions never override (the seed entry only feeds the manual Random button + auto-cycle deck), so `seed=0` stays `0` there.
- **Validation in `_on_start`** writes to `_error_var` (a red label in the dialog) rather than message boxes — keeps the modal compact and the user's input visible.
- **Universe / strict-offline group** (sandbox-preload feature): the dialog renders a "Universe (optional)" `LabelFrame` with a readonly combobox, a coverage label, and a strict-offline checkbox. Combobox values: `"(none — legacy unrestricted)"` plus one entry per `manifest_provider()` result, formatted `"<name>  (<id>, <count> symbols)"`. Selecting a real universe **force-checks and disables** the strict-offline checkbox (locked-checked = visually clear that the universe choice implies the seal; can't be turned off from this dialog). Selecting `(none)` un-checks and re-enables it (and it stays un-checked, since strict-offline without a universe has no allow-list to compare against). Coverage label refreshes on universe change *and* on every keystroke in the date entry (`StringVar.trace_add("write")`); calls `manifest.coverage_for_date(...)` against the disk_cache and shows `"<covered> / <total> symbols cover <date> at <interval>"`. When `manifest_provider is None` (legacy callers / tests) only `(none)` is shown and back-compat is preserved end-to-end (result has empty universe_id / universe_symbols / strict_offline=False).

## Invariants
- `self.result is None` on cancel / Esc / window-close.
- The dialog finalizes with the base modal grab enabled and releases it on close — no other window receives input while open.
- Start dialog payload `auto_cycle == blind` (Phase 1d coupling).

## Testing
- Exercised through `check_g0_sandbox_replay_integration` and `check_g2_sandbox_open_universe` indirectly (controllers driven with synthetic payloads); dialog instantiation itself is not unit-tested headlessly because Tk modals can't be event-driven offscreen.

## Modal keys and wheel guard
`SandboxStartDialog.__init__` calls `protect_combobox_wheel(self)` and
then `BaseModalDialog._finalize_modal(primary=self._on_start,
cancel=self._on_cancel)`. ESC cancels, Return starts the sandbox
session, and interval / universe comboboxes are guarded against
wheel-driven value changes.
