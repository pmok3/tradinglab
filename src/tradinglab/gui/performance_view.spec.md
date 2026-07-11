# gui/performance_view.py — Spec

## Purpose
Phase 1d read-only Performance View Toplevel. Multi-pane window driven by a [`SessionResult`](../backtest/session.spec.md): a summary line, an equity-curve chart, a sortable trade table (one row per closed round-trip), a per-setup aggregates table, a per-proximity aggregates table, and a daily-journal pane (each replay day's watch note above that day's trades). Bottom button bar carries `Export CSV…`, `Copy to clipboard`, and `Close`. Used both for "View Performance…" on a live or just-ended session and for the post-Load review window.

## Public API
- `class PerformanceView(BaseModalDialog)` — `__init__(parent, result: SessionResult, *, title="Sandbox — Performance", screenshot_dir: Optional[Path] = None)`. `screenshot_dir` is the directory holding `<order_id>_pre.png` / `<ref_id>_post.png` files captured by [`SandboxController`](../backtest/replay.spec.md); when provided, `Export CSV…` mirrors them into a sibling bundle.
- `_fmt_ts(ts: int) -> str` — render epoch-seconds as `YYYY-MM-DD HH:MM` UTC.
- `_truncate(s, n=60) -> str` — single-line, ellipsis-clipped string for the thesis column.
- `_fmt_day(date_iso: str) -> str` — render a `YYYY-MM-DD` day key as `Mon DD, YYYY` for the daily-journal headers.

## Dependencies
- Internal: [`..backtest.performance`](../backtest/performance.spec.md) (`build_trade_rows`, `build_setup_aggregates`, `build_proximity_aggregates`, `TradeRow`, `SetupAggregate`, `ProximityAggregate`, `realized_pnl_curve`, `trade_rows_to_tsv`, `write_trade_rows_csv`), [`..backtest.session.SessionResult`](../backtest/session.spec.md), `gui._modal_base.BaseModalDialog` / `protect_combobox_wheel`.
- External: `tkinter`, `tkinter.ttk`, `tkinter.filedialog` (module-level import so smoke tests can patch `performance_view.filedialog.asksaveasfilename`); `matplotlib` (`Figure`, `FigureCanvasTkAgg`, `matplotlib.dates`) imported lazily inside `_build_equity_chart` to keep module import cheap.

## Design Decisions
- **Read-only**: no editing, no replay. Driven entirely by the `SessionResult` passed at construction. Re-opening for a finished session re-creates the window from the same result.
- **Equity chart shows two series**: MTM equity (blue, `ax.plot`) and realized P&L (red, `ax.step(where="post")`). Each has its own `Checkbutton` toggle backed by a `tk.BooleanVar`; toggling calls `Line2D.set_visible` + `canvas.draw_idle()` (no recompute, no flicker).
- **`ax.step` for realized**: closed-trade P&L is discrete — it should jump at `exit_ts` and stay flat between closes. A plain line plot would imply gradual change between closes and mislead a trader.
- **Chart pane hidden when `result.equity_curve` is empty** (engine never ticked, headless smoke). The trade table is still useful in that case.
- **Trade table is sortable, aggregates are not**: discretionary traders sort trades by P/L / setup / conviction freely; aggregates are intentionally pinned to the canonical `(-count, tag)` order from `build_setup_aggregates` so screenshots / cross-session comparisons are stable.
- **Per-proximity aggregates are not sortable**: market-event proximity rows come from `build_proximity_aggregates` and use the same count/win-rate/expectancy shape as setup aggregates; an empty proximity key renders as `(no-proximity)`.
- **Daily-journal pane**: a `ttk.Treeview` (`show="tree headings"`, columns `pl` / `detail`) with one expanded parent node per replay day from `build_day_groups` — the day's watch note as a header row, that day's trades nested beneath, and "flat" note-only days shown with no children — so the note reads *ahead of* the trades it preceded. A "Blind (hide dates)" `Checkbutton` re-renders day labels as "Replay Day N" via `_populate_journal`; default off, since the exercise is over and the post-hoc report reveals dates unless the user opts back into blind review.
- **Sort is stable across re-clicks**: clicking the same column toggles direction; the underlying sort uses Python's stable `sorted` so within-bucket ordering is preserved.
- **UTC everywhere** (`_fmt_ts` and the chart's `mdates.DateFormatter`): saved sessions render identically across timezones — the main chart's display-tz setting governs chart axes only, not the analytics window.
- **Thesis truncated to 60 chars in the table**; the full text is preserved in the CSV / clipboard exports.
- **Export CSV uses `filedialog.asksaveasfilename` and routes through `write_trade_rows_csv`**, which mirrors screenshots into `<csv_stem>_screenshots/` next to the chosen CSV path. The bundle is fully portable (no cross-drive `relpath` brittleness, no `..\..\..` paths). Cancel (`""` from filedialog) is a silent no-op.
- **Copy to clipboard uses `trade_rows_to_tsv` (header + body) via Tk's native `clipboard_clear` / `clipboard_append`** — the proven status-bar pattern. Screenshot columns are omitted; the clipboard is for paste-into-Excel, not portable bundling.
- **Export buttons disabled when `rows` is empty**, so an empty-session window can't write a header-only CSV by accident.
- **Status feedback via `parent._status`** (best-effort; falls back silently if the parent has no status log).

## Invariants
- The Toplevel never mutates `result`.
- Empty `result.post_trades` produces an empty trade table and an empty aggregates table — no error path. Both export buttons are `disabled` in that case.
- Empty `result.equity_curve` hides the chart pane entirely; the export-buttons row is unaffected.
- **Tk-main-thread-only** — all Tk widget construction and mutation occurs on the Tk thread. Cross-thread access via `self.after` queueing — but see `gui/watchlist_tab.spec.md` for the worker-inbox pattern that supersedes `after` for worker results.
- Toggling either Checkbutton mutates only the corresponding `Line2D.visible` + `canvas.draw_idle()`; data and axis limits are unchanged.

## Testing
- `check_b5_sandbox_save_load` exercises the underlying `build_trade_rows` / `build_setup_aggregates` pipeline.
- `check_d57_performance_view_equity_csv_export` covers the chart-toggle wiring, both export buttons (CSV with mirrored-screenshots bundle, TSV-to-clipboard), the cancel path, and the screenshot-filename fallback for unattributed closes.
- Daily-journal data (`build_day_groups`) is pinned in `tests/unit/test_day_notes_journal.py`; the pane is rendered by `_populate_journal` from `self._day_groups`.

## Modal keys
`__init__` calls `_finalize_modal(primary=None, cancel=self.destroy, grab=False)`; this is a read-only non-modal window so ESC closes, Return is intentionally a no-op, and the parent chart remains interactive while the view is open.
