# `gui/strategy_tab.py` — Spec

## Purpose
PR 4 of the Strategy Tester rollout. A self-contained Tk widget
(``ttk.Frame`` subclass) embedded in a Toplevel popup launched from
the **Strategy** menubar entry (between **Exits** and **View**). Owns
the entire **Configure → Running → Result** UX loop for the strategy
tester.

The widget itself remains parent-agnostic — it is constructed against
whatever ``master`` is passed in (a Toplevel in production, the Tk
root in smoke tests). The popup wrapper + menubar wiring live in
``ChartApp._on_open_strategy_dialog``; the widget is stashed at
``app._strategy_tab`` only while the popup is open.

## Public surface
- `StrategyTab(master, *, entries_storage=None, exits_storage=None,
  watchlists_storage=None, run_fn=None, candles_fetcher=None)` —
  Toplevel-mountable widget. Optional kwargs are dependency-injection
  hooks used by smoke tests; production wiring passes nothing.
- `refresh()` — reloads the entry / exit / watchlist library snapshots
  used to populate the pickers.

## UX
### Configure pane (left)
- **Entry strategy** — readonly combobox listing
  ``"<name> · <id_short>"`` for every saved entry strategy.
- **Exit strategy** — same shape for saved exit strategies.
- **Universe picker** — three radio modes:

  - `Symbols list` — comma / semicolon-separated tickers, case-folded
    to upper.
  - `Watchlist` — readonly combobox over saved watchlist names.
  - `Preset` — readonly combobox over ``universe.list_presets()``;
    surfaces a yellow **survivorship-bias** banner.
- **Date range** — preset dropdown
  (``YTD / Last 1Y / 3Y / 5Y / 10Y / Max / Custom``). Custom reveals
  two YYYY-MM-DD entries. The preset start/end is computed by
  ``_date_range_for_preset`` using UTC `today` as the end.
- **Interval** — readonly combobox over ``("1d", "5m", "1m")``.
- **Starting cash (per symbol)** — float entry, default 100 000.
- **Advanced** — collapsible group with slippage (bps),
  commission/trade ($), commission/share ($). Default values match
  the ``CostModel`` defaults (5 bps / $0 / $0).
- **Per-trade screenshots** — opt-in checkbox; on, the runner is
  passed ``ScreenshotSpec()`` (default 1600×900 @ 110 dpi).
- **Run label (optional)** — free-text seeding ``TestConfig.user_label``.
- **Run** / **Stop** buttons + a status label + a **progress bar**.
  - The ``ttk.Progressbar`` (``mode='determinate'``) sits between the
    status label and the Recent Runs separator. It is **hidden** when
    no Run is in progress and **shown** once the user clicks **Run**.
  - ``maximum`` is set to the total symbol count on the first progress
    callback; ``value`` increments after every symbol completion.
  - One second after the Run finishes (Done / Cancelled / Failed) the
    bar hides itself so the finished state is visible momentarily.
  - **Paint-forcing invariant** — ``_apply_progress`` MUST call
    ``self._pbar.update_idletasks()`` after every ``configure`` so the
    bar visibly advances between rapid sequential updates. Without
    this, when symbols complete sub-second (cached data + simple
    strategies), the runner's ``progress(test_run)`` fires 12 times in
    <100ms which queues 12 ``after(0, ...)`` callbacks; Tk processes
    them all in a single batch BEFORE yielding to redraw, so the bar
    visually jumps from 0 to N/N at the END of the run instead of
    advancing one symbol at a time.


### Report pane (right)
- **Header** — Run id + on-disk run-directory name.
- **Sample-size banner** — yellow inline label populated when
  ``RunAggregate.insufficient_sample`` (N<30) or ``low_sample`` (N<100)
  fires.
- **Headline metrics** — Trades / Win rate (point + 95% Wilson CI) /
  Expectancy ($ + 95% bootstrap CI) / Profit Factor / P&L gross + net /
  Max DD ($ + %) / Sharpe / Sortino.
- **Notebook** with two tabs:
  - **Per-symbol** — Treeview rows from ``RunAggregate.per_symbol``.
  - **Per-year** — Treeview rows from ``RunAggregate.per_year``.
- **Action row** — ``Open run folder`` + ``Export CSV…`` + ``Export HTML…`` + ``Export PDF…`` buttons, enabled after a successful Run.

### Recent Runs sidebar (PR 5, bottom of Configure pane)
- ``ttk.Treeview`` with ``selectmode="extended"`` listing the newest 50
  runs from ``storage.list_runs_with_paths()`` with columns:
  ``Started`` / ``Status`` / ``Label`` / ``Trades``.
- **Load** button reads ``aggregate.json`` from the selected run via
  ``report.load_aggregate`` and re-renders the Report pane against it.
  Enabled **only when exactly one** row is selected (multi-row Load
  doesn't make sense).
- **Refresh** rescans disk (useful after the user manually copies in
  an external run dir, or trims runs from Explorer).
- **Delete…** supports **multi-select** (Ctrl/Shift+click). A single
  ``messagebox.askyesno`` confirm shows the count and the first 5
  ``run_id``s; on confirm, ``storage.delete_run`` is invoked for each
  selected run. The Delete button is enabled whenever ≥1 row is
  selected. Failures are aggregated into a single error dialog; the
  status bar reports the success count. If the currently-rendered run
  was among the deleted, the Report pane is cleared.
- The sidebar auto-refreshes after every successful Run completion
  via a ``_refresh_recent_runs()`` call at the end of ``_on_poll``.

### Export buttons (PR 5)
- **Export HTML…** writes ``<run_dir>/report.html`` via
  ``strategy_tester.export.export_html`` then prompts a
  ``filedialog.asksaveasfilename`` to copy the file out.
- **Export PDF…** mirrors the HTML flow via ``export_pdf`` (one cover
  page + one breakouts page + one equity-curve page + one landscape
  page per trade screenshot, capped at 200 pages).
- Both buttons reuse the in-memory ``RunAggregate`` (``self._current_aggregate``)
  so no extra disk read is required.

## Run lifecycle
- Click **Run** → `_build_config_from_ui` validates the form and
  produces a ``TestConfig``. Validation errors surface via a
  ``messagebox.showwarning`` and abort the Run.
- A new ``AcceptanceToken`` is created and stashed; the kernel is
  invoked on a daemon ``threading.Thread`` so the UI stays responsive
  during long Runs.
- The thread invokes ``strategy_tester.run(cfg, cancel_token=token,
  candles_fetcher=..., entry_loader=..., exit_loader=...,
  progress=..., screenshot_spec=...)``. The runner already auto-writes
  ``aggregate.json`` + ``trades.csv`` after the symbol loop
  (PR 3 integration).
- A 250 ms ``after()`` poll loop watches `self._worker.is_alive()`
  and, on completion, loads the aggregate via
  ``report.load_aggregate(run_dir)`` and re-renders the Report pane.
- Status transitions: ``Ready`` → ``Run starting…`` → ``Running… N/M
  symbols`` → ``Done. N symbols, K trades.`` (or ``Stopped.
  Partial: …`` on cancel).
- **Stop** → ``self._token.cancel()``; the worker finishes the
  in-flight symbol and writes a CANCELLED manifest. The aggregate /
  CSV are still generated (PR 3 integration), so the partial Report
  renders.

## Dependency-injection hooks (for tests)
- ``entries_storage`` / ``exits_storage`` / ``watchlists_storage`` —
  any object exposing ``load_all() -> (list, list)``. Used in the
  ``check_st3_strategy_tab_end_to_end`` smoke check to feed in-memory
  test strategies without round-tripping through the entries
  validator (which rejects synthetic ``STRAT_X`` ticker names).
- ``candles_fetcher`` — passed straight through to
  ``strategy_tester.run``; tests use the deterministic
  ``_fake_candles`` helper.
- ``run_fn`` — override the kernel entry point (currently only used
  to confirm the indirection works; production passes
  ``strategy_tester.run``).

## Cleanup
- ``<Destroy>`` binding cancels any in-flight Run (``token.cancel()``)
  and disposes the pending ``after()`` callback. The worker thread is
  a daemon so it dies with the app.

## Known gaps (deferred to PR 5+)
- No Recent Runs sidebar (browse / delete / open prior runs).
- No HTML / PDF export buttons (`Export CSV…` only).
- No equity-curve chart in the Report pane (line + DD shading).
- No screenshot gallery viewer.
- Custom date entries are unvalidated free text; bad dates surface
  as an error from the runner rather than the UI.

## See also
- [`strategy_tester/runner.spec.md`](../strategy_tester/runner.spec.md)
  — the orchestration kernel the worker thread invokes.
- [`strategy_tester/report.spec.md`](../strategy_tester/report.spec.md)
  — the Run-aggregate JSON / CSV the Report pane renders.
- [`gui/entries_tab.spec.md`](entries_tab.spec.md) /
  [`gui/exits_tab.spec.md`](exits_tab.spec.md) — sibling notebook tabs
  whose ``load_all()``-based refresh pattern this widget mirrors.
