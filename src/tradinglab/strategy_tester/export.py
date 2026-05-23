"""Strategy Tester report exporters — self-contained HTML + PDF.

PR 5 of the Strategy Tester rollout. Generates shareable artifacts
from an existing Run directory: the in-app Report view is great for
interactive analysis, but the user wants a single file to email a
collaborator or archive alongside the raw CSV.

Two output formats:

* :func:`export_html` — single-file HTML report with inline CSS,
  embedded headline metrics, per-symbol + per-year tables, and
  *relative* references to ``screenshots/`` PNGs (so the HTML stays
  small while the screenshot folder ships alongside). The HTML lives
  inside the Run directory so the relative paths just work.
* :func:`export_pdf` — multi-page PDF: cover page + statistics page +
  one page per trade screenshot. Built via matplotlib's
  :class:`~matplotlib.backends.backend_pdf.PdfPages` so the only
  dependency is the existing matplotlib pin.

Both functions are pure-Python aside from matplotlib (already required
for the live chart + headless screenshots) and accept an optional
in-memory :class:`RunAggregate` so the caller can avoid a redundant
disk read.

These exporters are intentionally read-only: they never mutate the
Run directory or the aggregate, and may be re-run any number of
times. Failures raise; the GUI catches and surfaces via messagebox.
"""

from __future__ import annotations

import html
import logging
import math
from pathlib import Path

import matplotlib.figure as _mpl_figure
from matplotlib.backends.backend_pdf import PdfPages

from . import report as _report

logger = logging.getLogger(__name__)

__all__ = [
    "HTML_FILENAME",
    "PDF_FILENAME",
    "export_html",
    "export_pdf",
]

HTML_FILENAME = "report.html"
PDF_FILENAME = "report.pdf"


# ---------------------------------------------------------------------------
# HTML export
# ---------------------------------------------------------------------------


def _fmt_money(v: float) -> str:
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
        return "$0.00"
    return f"${v:,.2f}"


def _fmt_pct(v: float) -> str:
    if v != v:
        return "0.0%"
    return f"{v * 100.0:.1f}%"


def _fmt_pf(v: float) -> str:
    if v >= 1e8:
        return "∞"
    return f"{v:.2f}"


def export_html(
    run_dir: Path,
    *,
    aggregate: _report.RunAggregate | None = None,
    out_path: Path | None = None,
) -> Path:
    """Render an HTML report next to ``aggregate.json``.

    The report is a single ``report.html`` file with inline CSS and
    *relative* references to ``screenshots/<file>.png`` — so simply
    zipping the Run directory gives a portable report.

    Parameters
    ----------
    run_dir : Path
        Strategy Tester run directory (the parent of
        ``aggregate.json`` / ``screenshots/``).
    aggregate : RunAggregate | None
        In-memory aggregate. If None, loaded from disk via
        :func:`report.load_aggregate`. Raises ``FileNotFoundError``
        if neither path nor aggregate produce a valid object.
    out_path : Path | None
        Where to write the HTML. Defaults to
        ``run_dir / report.html`` so the relative screenshot links
        work without modification.
    """
    run_dir = Path(run_dir)
    if aggregate is None:
        aggregate = _report.load_aggregate(run_dir)
        if aggregate is None:
            raise FileNotFoundError(
                f"aggregate.json missing under {run_dir}; run "
                f"strategy_tester.report.aggregate_run first."
            )
    if out_path is None:
        out_path = run_dir / HTML_FILENAME

    # Build HTML body.
    head = aggregate
    banner_html = ""
    if head.insufficient_sample:
        banner_html = (
            f"<div class='banner banner-warn'>⚠ Insufficient sample "
            f"(N={head.trade_count} &lt; 30). Confidence intervals are "
            f"wide — treat headline numbers as illustrative.</div>"
        )
    elif head.low_sample:
        banner_html = (
            f"<div class='banner banner-warn'>⚠ Low sample "
            f"(N={head.trade_count} &lt; 100). Confidence intervals may "
            f"be wider than you'd like.</div>"
        )

    pf_disp = _fmt_pf(head.profit_factor)
    pf_ci_lo = _fmt_pf(head.profit_factor_ci_95.lo)
    pf_ci_hi = _fmt_pf(head.profit_factor_ci_95.hi)
    exp_ci = head.expectancy_ci_95
    wr_ci = head.win_rate_ci_95

    rows_sym = "\n".join(
        f"        <tr><td>{html.escape(s.symbol)}</td>"
        f"<td>{s.trade_count}</td><td>{s.wins}</td><td>{s.losses}</td>"
        f"<td>{_fmt_pct(s.win_rate)}</td>"
        f"<td>{_fmt_money(s.total_pnl_net)}</td>"
        f"<td>{_fmt_pf(s.profit_factor)}</td>"
        f"<td>{_fmt_money(s.max_drawdown)}</td></tr>"
        for s in head.per_symbol
    )
    rows_year = "\n".join(
        f"        <tr><td>{y.year}</td><td>{y.trade_count}</td>"
        f"<td>{y.wins}</td><td>{y.losses}</td>"
        f"<td>{_fmt_pct(y.win_rate)}</td>"
        f"<td>{_fmt_money(y.total_pnl_net)}</td>"
        f"<td>{_fmt_money(y.expectancy)}</td>"
        f"<td>{_fmt_pf(y.profit_factor)}</td></tr>"
        for y in head.per_year
    )

    # Gather screenshots from the screenshots/ dir.
    shots_dir = run_dir / "screenshots"
    shot_imgs = ""
    if shots_dir.is_dir():
        png_files = sorted(shots_dir.glob("*.png"))
        if png_files:
            shot_imgs = "<h2>Trade screenshots</h2>\n<div class='screenshots'>\n"
            for f in png_files:
                rel = f"screenshots/{f.name}"
                shot_imgs += (
                    f"  <figure><img src='{html.escape(rel)}' alt='trade'>"
                    f"<figcaption>{html.escape(f.name)}</figcaption>"
                    f"</figure>\n"
                )
            shot_imgs += "</div>\n"

    css = """\
body { font-family: -apple-system, Segoe UI, sans-serif;
       max-width: 1200px; margin: 24px auto; padding: 0 16px; color: #222; }
h1 { font-size: 1.4rem; margin: 0 0 8px; }
h2 { font-size: 1.1rem; margin: 16px 0 6px; }
.meta { color: #666; margin-bottom: 12px; }
.banner { padding: 8px 12px; border-radius: 6px; margin: 8px 0;
          font-size: 0.95rem; }
.banner-warn { background: #fff7d6; color: #6b5200;
               border: 1px solid #d9c269; }
.headline { display: grid; grid-template-columns: repeat(3, 1fr);
            gap: 8px 16px; margin: 12px 0; }
.headline .label { color: #666; font-size: 0.85rem; }
.headline .value { font-weight: 600; font-size: 1.05rem; }
.headline .ci    { color: #888; font-size: 0.85rem; }
table { border-collapse: collapse; width: 100%;
        font-size: 0.9rem; margin: 8px 0 16px; }
th, td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; }
th { background: #f4f4f4; }
tr:nth-child(even) td { background: #fafafa; }
.screenshots { display: grid; grid-template-columns: repeat(2, 1fr);
               gap: 12px; }
.screenshots figure { margin: 0; }
.screenshots img { width: 100%; height: auto; border: 1px solid #ddd; }
.screenshots figcaption { font-size: 0.8rem; color: #666;
                          text-align: center; }
"""

    body = f"""\
<h1>Strategy Tester Run · {html.escape(head.run_id)}</h1>
<div class='meta'>Trades {head.trade_count}
({head.win_count} wins, {head.loss_count} losses)
&nbsp;·&nbsp; {len(head.per_symbol)} symbols
&nbsp;·&nbsp; {len(head.per_year)} years</div>
{banner_html}

<h2>Headline metrics</h2>
<div class='headline'>
  <div>
    <div class='label'>Win rate</div>
    <div class='value'>{_fmt_pct(head.win_rate)}</div>
    <div class='ci'>95% CI [{_fmt_pct(wr_ci.lo)} – {_fmt_pct(wr_ci.hi)}]</div>
  </div>
  <div>
    <div class='label'>Expectancy</div>
    <div class='value'>{_fmt_money(head.expectancy)}</div>
    <div class='ci'>95% CI [{_fmt_money(exp_ci.lo)} – {_fmt_money(exp_ci.hi)}]</div>
  </div>
  <div>
    <div class='label'>Profit factor</div>
    <div class='value'>{pf_disp}</div>
    <div class='ci'>95% CI [{pf_ci_lo} – {pf_ci_hi}]</div>
  </div>
  <div>
    <div class='label'>P&amp;L (gross / net)</div>
    <div class='value'>{_fmt_money(head.total_pnl_gross)} / {_fmt_money(head.total_pnl_net)}</div>
  </div>
  <div>
    <div class='label'>Max drawdown</div>
    <div class='value'>{_fmt_money(head.max_drawdown)} ({_fmt_pct(head.max_drawdown_pct)})</div>
  </div>
  <div>
    <div class='label'>Sharpe / Sortino</div>
    <div class='value'>{head.sharpe_ratio:.2f} / {head.sortino_ratio:.2f}</div>
  </div>
  <div>
    <div class='label'>Largest win / loss</div>
    <div class='value'>{_fmt_money(head.largest_win)} / {_fmt_money(head.largest_loss)}</div>
  </div>
  <div>
    <div class='label'>Best-month-removed P&amp;L</div>
    <div class='value'>{_fmt_money(head.best_month_removed_total_pnl)}</div>
  </div>
  <div>
    <div class='label'>Worst-month-removed P&amp;L</div>
    <div class='value'>{_fmt_money(head.worst_month_removed_total_pnl)}</div>
  </div>
</div>

<h2>Per-symbol</h2>
<table>
  <thead><tr>
    <th>Symbol</th><th>Trades</th><th>Wins</th><th>Losses</th>
    <th>Win rate</th><th>P&amp;L net</th><th>PF</th><th>Max DD</th>
  </tr></thead>
  <tbody>
{rows_sym}
  </tbody>
</table>

<h2>Per-year</h2>
<table>
  <thead><tr>
    <th>Year</th><th>Trades</th><th>Wins</th><th>Losses</th>
    <th>Win rate</th><th>P&amp;L net</th><th>Expectancy</th><th>PF</th>
  </tr></thead>
  <tbody>
{rows_year}
  </tbody>
</table>

{shot_imgs}
"""

    doc = (
        "<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
        "<meta charset='utf-8'>\n"
        f"<title>Strategy Tester · {html.escape(head.run_id)}</title>\n"
        f"<style>\n{css}</style>\n"
        "</head>\n<body>\n"
        f"{body}"
        "</body>\n</html>\n"
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------


def _draw_cover_page(pdf: PdfPages, agg: _report.RunAggregate) -> None:
    """Append a one-page cover sheet to ``pdf`` with headline metrics."""
    fig = _mpl_figure.Figure(figsize=(8.5, 11.0))
    fig.subplots_adjust(left=0.07, right=0.93, top=0.92, bottom=0.07)
    ax = fig.add_subplot(111)
    ax.axis("off")
    ax.set_title(
        f"Strategy Tester Run\n{agg.run_id}",
        fontsize=14, fontweight="bold", loc="left",
    )

    wr_ci = agg.win_rate_ci_95
    exp_ci = agg.expectancy_ci_95
    pf_disp = _fmt_pf(agg.profit_factor)

    lines = [
        ("Trades", f"{agg.trade_count}  "
                   f"({agg.win_count} W / {agg.loss_count} L)"),
        ("Win rate",
         f"{_fmt_pct(agg.win_rate)}  "
         f"95% CI [{_fmt_pct(wr_ci.lo)} – {_fmt_pct(wr_ci.hi)}]"),
        ("Expectancy",
         f"{_fmt_money(agg.expectancy)}  "
         f"95% CI [{_fmt_money(exp_ci.lo)} – {_fmt_money(exp_ci.hi)}]"),
        ("Profit factor", pf_disp),
        ("P&L gross", _fmt_money(agg.total_pnl_gross)),
        ("P&L net", _fmt_money(agg.total_pnl_net)),
        ("Max drawdown",
         f"{_fmt_money(agg.max_drawdown)}  ({_fmt_pct(agg.max_drawdown_pct)})"),
        ("Sharpe (daily, annualised)", f"{agg.sharpe_ratio:.2f}"),
        ("Sortino (daily, annualised)", f"{agg.sortino_ratio:.2f}"),
        ("Largest win", _fmt_money(agg.largest_win)),
        ("Largest loss", _fmt_money(agg.largest_loss)),
        ("Best-month-removed P&L",
         _fmt_money(agg.best_month_removed_total_pnl)),
        ("Worst-month-removed P&L",
         _fmt_money(agg.worst_month_removed_total_pnl)),
        ("Symbols tested", str(len(agg.per_symbol))),
        ("Years covered", str(len(agg.per_year))),
    ]

    banner = ""
    if agg.insufficient_sample:
        banner = (
            f"Insufficient sample (N={agg.trade_count} < 30). "
            f"Confidence intervals are wide — treat headline numbers "
            f"as illustrative."
        )
    elif agg.low_sample:
        banner = (
            f"Low sample (N={agg.trade_count} < 100). "
            f"Confidence intervals may be wider than you'd like."
        )
    if banner:
        ax.text(
            0.0, 0.94, banner,
            transform=ax.transAxes,
            fontsize=9, color="#8a6500",
            wrap=True,
        )

    # 2-column key/value layout
    n = len(lines)
    half = math.ceil(n / 2)
    y0 = 0.86
    dy = 0.045
    for col, batch in enumerate((lines[:half], lines[half:])):
        x_label = 0.02 + 0.50 * col
        x_value = 0.18 + 0.50 * col
        for i, (label, value) in enumerate(batch):
            y = y0 - i * dy
            ax.text(x_label, y, f"{label}:",
                    transform=ax.transAxes,
                    fontsize=9.5, color="#444")
            ax.text(x_value, y, value,
                    transform=ax.transAxes,
                    fontsize=10, fontweight="bold", color="#222")

    pdf.savefig(fig)


def _draw_breakouts_page(pdf: PdfPages, agg: _report.RunAggregate) -> None:
    """Per-symbol + per-year tables on a single page."""
    fig = _mpl_figure.Figure(figsize=(8.5, 11.0))
    fig.subplots_adjust(left=0.05, right=0.95, top=0.92, bottom=0.05,
                        hspace=0.4)

    ax1 = fig.add_subplot(211)
    ax1.axis("off")
    ax1.set_title("Per-symbol", loc="left", fontsize=12)
    if agg.per_symbol:
        cols = ("Symbol", "Trades", "Wins", "Losses",
                "Win rate", "P&L net", "PF", "Max DD")
        rows = [
            (s.symbol, s.trade_count, s.wins, s.losses,
             _fmt_pct(s.win_rate), _fmt_money(s.total_pnl_net),
             _fmt_pf(s.profit_factor), _fmt_money(s.max_drawdown))
            for s in agg.per_symbol
        ]
        t = ax1.table(cellText=rows, colLabels=cols, loc="upper left")
        t.auto_set_font_size(False)
        t.set_fontsize(8)
        t.scale(1.0, 1.2)
    else:
        ax1.text(0.0, 0.9, "(no symbols)", transform=ax1.transAxes)

    ax2 = fig.add_subplot(212)
    ax2.axis("off")
    ax2.set_title("Per-year", loc="left", fontsize=12)
    if agg.per_year:
        cols = ("Year", "Trades", "Wins", "Losses",
                "Win rate", "P&L net", "Expectancy", "PF")
        rows = [
            (y.year, y.trade_count, y.wins, y.losses,
             _fmt_pct(y.win_rate), _fmt_money(y.total_pnl_net),
             _fmt_money(y.expectancy), _fmt_pf(y.profit_factor))
            for y in agg.per_year
        ]
        t2 = ax2.table(cellText=rows, colLabels=cols, loc="upper left")
        t2.auto_set_font_size(False)
        t2.set_fontsize(8)
        t2.scale(1.0, 1.2)
    else:
        ax2.text(0.0, 0.9, "(no per-year data)", transform=ax2.transAxes)

    pdf.savefig(fig)


def _draw_equity_curve_page(
    pdf: PdfPages, agg: _report.RunAggregate,
) -> None:
    """Equity curve line plot, one page."""
    if not agg.equity_curve:
        return
    import datetime as _dt
    fig = _mpl_figure.Figure(figsize=(11.0, 8.5))  # landscape
    fig.subplots_adjust(left=0.08, right=0.96, top=0.92, bottom=0.10)
    ax = fig.add_subplot(111)
    xs = [
        _dt.datetime.fromtimestamp(ts_ms / 1000.0, tz=_dt.timezone.utc)
        for ts_ms, _ in agg.equity_curve
    ]
    ys = [eq for _, eq in agg.equity_curve]
    ax.plot(xs, ys, color="#2360c8", linewidth=1.2)
    ax.set_title("Equity curve", fontsize=12, loc="left")
    ax.set_xlabel("Date (UTC)")
    ax.set_ylabel("Equity ($)")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    pdf.savefig(fig)


def _draw_screenshot_page(pdf: PdfPages, png_path: Path) -> None:
    """One full-page screenshot."""
    try:
        import matplotlib.image as _mpl_img
        img = _mpl_img.imread(str(png_path))
    except Exception:  # noqa: BLE001
        logger.warning("Could not read screenshot %s", png_path)
        return
    fig = _mpl_figure.Figure(figsize=(11.0, 8.5))  # landscape
    fig.subplots_adjust(left=0.04, right=0.96, top=0.94, bottom=0.04)
    ax = fig.add_subplot(111)
    ax.imshow(img)
    ax.axis("off")
    ax.set_title(png_path.name, fontsize=10, loc="left")
    pdf.savefig(fig)


def export_pdf(
    run_dir: Path,
    *,
    aggregate: _report.RunAggregate | None = None,
    out_path: Path | None = None,
    include_screenshots: bool = True,
    max_screenshots: int = 200,
) -> Path:
    """Render a multi-page PDF report.

    Pages (in order):
    1. Cover with headline metrics + sample-size banner.
    2. Per-symbol + per-year breakouts as matplotlib tables.
    3. Equity-curve line plot (skipped if curve is empty).
    4-N. One landscape page per trade screenshot, in filename order.
       Capped at ``max_screenshots`` to keep file size reasonable.

    Parameters
    ----------
    run_dir, aggregate, out_path
        Same shape as :func:`export_html`.
    include_screenshots : bool
        If False, skip pages 4-N entirely (cover + breakouts + equity
        only).
    max_screenshots : int
        Hard cap on screenshot pages. PRs over 200 trades produce
        report files >>10 MB; default 200 keeps file size sensible.
    """
    run_dir = Path(run_dir)
    if aggregate is None:
        aggregate = _report.load_aggregate(run_dir)
        if aggregate is None:
            raise FileNotFoundError(
                f"aggregate.json missing under {run_dir}; run "
                f"strategy_tester.report.aggregate_run first."
            )
    if out_path is None:
        out_path = run_dir / PDF_FILENAME
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with PdfPages(str(out_path)) as pdf:
        _draw_cover_page(pdf, aggregate)
        _draw_breakouts_page(pdf, aggregate)
        _draw_equity_curve_page(pdf, aggregate)
        if include_screenshots:
            shots_dir = run_dir / "screenshots"
            if shots_dir.is_dir():
                png_files = sorted(shots_dir.glob("*.png"))
                for f in png_files[:max_screenshots]:
                    _draw_screenshot_page(pdf, f)
    return out_path
