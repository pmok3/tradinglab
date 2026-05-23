# strategy_tester/export.py — spec

## Purpose

Generate shareable artifacts from a completed Strategy Tester Run
directory: a self-contained HTML report and a multi-page PDF report.
These exporters are read-only — they never mutate the Run directory
or the aggregate — and may be invoked any number of times on the
same Run.

## Public surface

* `HTML_FILENAME` (`"report.html"`) — canonical filename inside the Run dir.
* `PDF_FILENAME` (`"report.pdf"`) — canonical filename inside the Run dir.
* `export_html(run_dir, *, aggregate=None, out_path=None) -> Path`
* `export_pdf(run_dir, *, aggregate=None, out_path=None,
              include_screenshots=True, max_screenshots=200) -> Path`

Both functions:
* Accept an existing `RunAggregate` (avoids re-reading disk) and
  fall back to `report.load_aggregate(run_dir)` when omitted.
* Raise `FileNotFoundError` if no aggregate is available.
* Default `out_path` to `<run_dir>/report.html` or `<run_dir>/report.pdf`
  so that *relative* image references resolve next to the file.
* Return the absolute Path to the written file.

## HTML layout

`<run_dir>/report.html` is a single file with inline `<style>` and:

1. `<h1>` Run id + run-dir name.
2. Optional sample-size banner (yellow box) for `insufficient_sample`
   (N<30) or `low_sample` (N<100).
3. Headline metrics grid:
   * Win rate + 95% Wilson CI
   * Expectancy + 95% bootstrap CI
   * Profit factor + 95% bootstrap CI (rendered as `∞` when `>= 1e8`)
   * Gross / net P&L
   * Max drawdown ($ + %)
   * Sharpe / Sortino
   * Largest win / loss
   * Best/worst-month-removed P&L
4. Per-symbol table.
5. Per-year table.
6. Optional `<h2>Trade screenshots</h2>` grid with one `<figure>`
   per PNG under `<run_dir>/screenshots/`, using *relative* paths
   (`screenshots/<file>.png`). No base64 inlining — zipping the
   run dir gives a portable, lightweight bundle.

## PDF layout

Built via `matplotlib.backends.backend_pdf.PdfPages`. Pages in order:

1. **Cover page** — Letter portrait, two-column key/value layout
   of every headline metric. Sample-size banner at the top when
   triggered. No charts on this page.
2. **Breakouts page** — Letter portrait, two `ax.table` widgets:
   per-symbol on the top half, per-year on the bottom half.
3. **Equity curve page** — Letter landscape, single line plot of
   `agg.equity_curve` (`(ts_ms, equity)` tuples) with `autofmt_xdate`.
   Skipped if the curve is empty.
4. **Trade screenshots** — one landscape page per PNG, in
   `sorted(screenshots/*.png)` order. Capped at `max_screenshots`
   (default 200) so 500-trade reports don't balloon past ~50 MB.

## Determinism / threading

Both functions are pure-Python aside from matplotlib. They do not
spawn threads, do not touch Tk, and do not modify global state.
They are safe to invoke from background threads provided the
calling code does not concurrently mutate the Run directory.

## Failure modes

* Missing `aggregate.json` → `FileNotFoundError`.
* Missing `screenshots/` directory → HTML omits the `<figure>` block;
  PDF omits screenshot pages. Both are silent (not errors).
* Individual PNG read failures (corrupt files) → logged via `logger`
  and skipped; export continues.

## Out-of-scope (deferred)

* CSS theming / dark mode — uses fixed light palette to match
  email-client + browser defaults. Dark-mode HTML can land in a
  follow-up.
* JS / interactive charts — the HTML is intentionally static.
* Custom logo / branding header.
