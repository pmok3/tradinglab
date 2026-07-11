# gui/sandbox_panel.py — Spec

## Purpose
Sidebar widget shown only while a sandbox session is active. Mounts to the right of the chart and surfaces the live state of the [`SandboxController`](../backtest/replay.spec.md): clock, cash, positions, focus list, Buy / Sell, Next-bar, per-day watch-notes box, End-session.

## Public API
- `class SandboxPanel(ttk.Frame)`.
  - `__init__(app, controller, **kwargs)` — registers `_open_post_trade_modal` as the controller's post-trade callback.
  - `refresh()` — pull fresh state from the controller and repopulate every widget. Called by the controller after every state-changing event.

## Dependencies
- Internal: `..backtest.replay.SandboxController` (duck-typed via `controller`).
- External: `tkinter`, `tkinter.ttk`.

## Design Decisions
- **Dumb panel, smart controller**: the panel never derives state — every `refresh()` re-reads `controller.clock_ts()`, `cash()`, `positions_snapshot()`, `tickers()`. Eliminates a class of "panel and engine disagree" bugs at the cost of a few extra dict lookups per tick.
- **Right-arrow keystroke binding lives on `app`, not the panel**: pressing → anywhere in the app advances the bar (suppressed when an Entry/Combobox/Text widget has focus, so the user can edit form fields without ticking the clock). The panel's "Next bar (→)" button is a redundant entry point. The keybind was historically `<KeyPress-n>` and was migrated to `<KeyPress-Right>` after the N keybind interfered with typing tickers in the toolbar entry. See `check_b21` for the suppression contract.
- **Buy / Sell open `PreTradeFormDialog`** for the currently focused ticker. Cancelling the modal silently no-ops; submitting routes through `controller.submit_order`. When `sandbox_skip_detailed_journal` is enabled, the panel bypasses the modal and submits a quantity-1 quickfire order with a sentinel `"(skipped)"` thesis.
- **Event-proximity notice**: the pre-trade dialog receives a passive earnings / dividend proximity notice plus suggested setup tags when the corresponding default is enabled and the controller can compute proximity at the current clock.
- **Post-trade modal driven via callback**: the controller invokes `_open_post_trade_modal(post_trade)` synchronously inside its `next_bar` loop. The callback opens [`PostTradeReviewDialog`](sandbox_review_dialog.spec.md), waits, and returns the user's text. With `sandbox_skip_detailed_journal` enabled, it returns an empty review string instead of opening the modal.
- **All widget access wrapped in `tk.TclError` guards**: app-close races during `refresh()` have torn down the underlying Tk widgets; we want a silent no-op, not a stack trace.
- **Display timezone mirrors the chart**: the clock uses `ChartApp._display_tz` when present, treats an empty value as ET-native, and falls back to UTC on bad zones. Blind mode suppresses the calendar date and shows only time plus blind-cycle number.
- **Focus sync is defensive**: `refresh()` keeps the toolbar ticker variable aligned with `controller.focus_symbol` when the active controller exposes its host app.
- **Positions Treeview diffing**: `refresh()` skips delete/reinsert work when the position snapshot signature is unchanged.
- **Native Listbox theming**: the Focus ticker `tk.Listbox` is painted from the active theme (`tree_bg`, `tree_fg`, `spine`) at construction so dark-mode sessions do not show the OS-default white listbox.
- **Per-day watch-notes box**: a `tk.Text` in a `LabelFrame` whose label is blind-safe — "Replay Day N" (`controller.current_day_ordinal()`) while `controller.blind`, else the session date. Captures the trader's pre-trade observations for the current replay day. `refresh()` reloads the box only when the session day changes (`_refresh_day_notes` compares `current_session_date()` against a cached key) so in-progress typing on the current day is never clobbered. The text is committed to `controller.set_day_note` on `<FocusOut>`, before every `next_bar` advance (`_on_next_bar` / Right-arrow), and before End-session. Themed via `apply_text_theme` for dark mode. Surfaces in the Performance View's daily-journal pane.

## Invariants
- The panel is created and `pack`'d only while `controller.is_active()`. `end_session` triggers `app._hide_sandbox_panel()`.
- `refresh()` after `end_session` is a no-op (controller returns `None` / `0.0` / `[]` from inspection helpers).

## Testing
- `check_g0_sandbox_replay_integration` and `check_g1_sandbox_phase1c` drive the controller's post-trade callback path end-to-end (smoke-mode panel-less; the dialog is short-circuited).
- `tests/unit/gui/test_native_widget_dark_theme.py` asserts the Focus ticker `Listbox` uses dark palette colors under `DARK_THEME`.
- Watch-notes capture wiring is pinned in `tests/unit/backtest/test_replay_state_machine.py::TestDayNotes` (`set_day_note` / `current_day_note` / `result()` injection); the pure day-grouping in `tests/unit/test_day_notes_journal.py`.

## Recent history
- Added quickfire journal-skip handling, event-proximity pre-trade notices, chart-timezone clock rendering, focus sync, and position Treeview diffing.
