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
* `class Cancelled(Exception)` — raised mid-export when the caller's
  `cancel_token.is_cancelled()` returns True between pages.
* `ProgressCallback` — `Callable[[int, int, str], None]` alias for
  `(current, total, label) -> None` page-tick callbacks.
* `export_html(run_dir, *, aggregate=None, out_path=None,
              progress_callback=None, cancel_token=None) -> Path`
* `export_pdf(run_dir, *, aggregate=None, out_path=None,
              include_screenshots=True, max_screenshots=200,
              progress_callback=None, cancel_token=None) -> Path`

Both functions:
* Accept an existing `RunAggregate` (avoids re-reading disk) and
  fall back to `report.load_aggregate(run_dir)` when omitted.
* Raise `FileNotFoundError` if no aggregate is available.
* Default `out_path` to `<run_dir>/report.html` or `<run_dir>/report.pdf`
  so that *relative* image references resolve next to the file.
* Return the absolute Path to the written file.

## Progress callback contract

`progress_callback(current, total, label)` is invoked synchronously
on the export thread after each major page/step completes:

* PDF: 3 fixed-page ticks (`"Cover"`, `"Breakouts"`, `"Equity curve"`)
  followed by one tick per screenshot, with `label` = the PNG filename.
  `current` is 1-based and monotonically increases; `total` is fixed
  for the whole export and equals
  `3 + min(len(png_files), max_screenshots)`.
* HTML: exactly 3 ticks (`"Loaded aggregate"` / `"Rendered body"` /
  `"Wrote file"`).

The callback may raise; the exception is logged at DEBUG and the
export continues. This protects against buggy GUI marshalers from
breaking long batch exports.

## Cancel token contract

`cancel_token` is any object exposing `is_cancelled() -> bool`. It is
polled before each page is drawn (PDF) or before render/write (HTML).
When the token returns True, the `with PdfPages` context exits cleanly
— leaving a valid (truncated) PDF on disk — and `Cancelled` is raised.
HTML cancellation prior to the disk write leaves no partial file.

Exceptions raised by `is_cancelled()` itself are swallowed; the export
keeps running ("safe-default keep going" over "abort on probe
failure"). Callers that wish to discard a partial PDF must `unlink`
the out_path themselves in their `except Cancelled` branch.

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
   run dir gives a portable, lightweight bundle. Screenshot markup is
   assembled from a list of fragments and joined once so large
   screenshot directories do not repeatedly copy an ever-growing HTML
   string.

## PDF layout

Built via `matplotlib.backends.backend_pdf.PdfPages`. Pages in order:

1. **Cover page** — Letter portrait, two-column key/value layout
   of every headline metric. Sample-size banner at the top when
   triggered. No charts on this page.
   * Labels are **right-aligned** at `x_label = 0.34 + 0.50·col`
     (axes fraction) so long labels like "Sharpe (daily, annualised):"
     grow leftward and can never overlap the value column.
   * Win-rate and Expectancy 95% CI ranges are rendered as a smaller
     sub-text (fontsize 7.5) immediately below each value rather than
     inline in the value string, preventing column-overflow onto col 1.
2. **Breakouts page** — Letter portrait, two `ax.table` widgets:
   per-symbol on the top half, per-year on the bottom half.
3. **Equity curve page** — Letter landscape, single line plot of
   `agg.equity_curve` (`(epoch_seconds, equity)` tuples) with `autofmt_xdate`.
   Skipped if the curve is empty. Dense curves are plotted from
   matplotlib numeric day values (`epoch_seconds / 86400`) rather than
   allocating one `datetime` object per equity point.
4. **Trade screenshots** — one landscape page per PNG, in
   `sorted(screenshots/*.png)` order. Capped at `max_screenshots`
   (default 200) so 500-trade reports don't balloon past ~50 MB. When
   capped, PDF export uses bounded `heapq.nsmallest` selection instead
   of sorting every PNG in the directory; this preserves the first
   `max_screenshots` filenames in sorted order while reducing work for
   large screenshot folders.

## Determinism / threading

Both functions are pure-Python aside from matplotlib. They do not
spawn threads, do not touch Tk, and do not modify global state.
They are **designed to run on a background thread** — the GUI
``StrategyTab`` invokes them from a daemon thread and polls the
``progress_callback``/``cancel_token`` plumbing to keep the Tk main
loop responsive. The callbacks are invoked synchronously on the
calling thread; GUI callers must marshal back to the Tk main thread
themselves (see ``gui/strategy_tab.spec.md`` § "Background-export
plumbing").

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
