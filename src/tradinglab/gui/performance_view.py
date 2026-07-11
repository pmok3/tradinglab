"""Phase 1d Performance View — read-only review of a finished session.

Three-pane Toplevel:

* **Top pane** (new): equity-curve chart — plots
  ``SessionResult.equity_curve`` (mark-to-market: cash + Σ qty·px,
  pushed per bar by :meth:`Portfolio.mark_to_market`) alongside the
  derived realized P&L curve from
  :func:`~tradinglab.backtest.performance.realized_pnl_curve`.
  Two checkboxes toggle each series. Hidden when the session has no
  equity samples (e.g. engine never ticked).
* **Middle pane**: sortable trade table — one row per closed
  round-trip, columns: entry_ts (UTC iso-min), exit_ts, ticker, side,
  qty, P/L ($), P/L %, setup, conviction, MAE %, MFE %, target,
  thesis (truncated to 60 chars). Click a column header to sort by
  that column; click again to reverse. Sort is stable across
  re-clicks.
* **Bottom pane**: per-setup aggregates — count, win-rate, avg P/L,
  total P/L, expectancy. Sorted by descending count (the canonical
  ordering from :func:`build_setup_aggregates`); not user-resortable
  in MVP.

Bottom button bar: ``Export CSV…`` writes a portable trade-journal
bundle (CSV + sibling ``<stem>_screenshots/`` mirror — see
:func:`write_trade_rows_csv`); ``Copy to clipboard`` copies the
TSV body for quick paste into Excel; ``Close`` dismisses the
window.

Read-only: no editing, no replay. Driven entirely by a
:class:`SessionResult` passed in at construction. ``screenshot_dir``
is optional — when provided (live controller dir or
``LoadedSession.screenshot_dir``), the CSV export bundles linked
PNGs alongside.
"""

from __future__ import annotations

import datetime as _dt
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any

from ..backtest.performance import (
    DayGroup,
    ProximityAggregate,
    SetupAggregate,
    TradeRow,
    build_day_groups,
    build_proximity_aggregates,
    build_setup_aggregates,
    build_trade_rows,
    realized_pnl_curve,
    trade_rows_to_tsv,
    write_trade_rows_csv,
)
from ..backtest.session import SessionResult
from ._modal_base import BaseModalDialog, protect_combobox_wheel

_TRADE_COLUMNS = (
    ("entry_ts", "Entry", 130, "center"),
    ("exit_ts", "Exit", 130, "center"),
    ("symbol", "Ticker", 70, "center"),
    ("side", "Side", 50, "center"),
    ("quantity", "Qty", 60, "center"),
    ("pnl", "P/L ($)", 90, "center"),
    ("pnl_pct", "P/L %", 70, "center"),
    ("setup_tag", "Setup", 100, "center"),
    ("conviction", "Conv.", 50, "center"),
    ("mae_pct", "MAE %", 70, "center"),
    ("mfe_pct", "MFE %", 70, "center"),
    ("target", "Target", 70, "center"),
    ("thesis", "Thesis", 240, "w"),
)


_AGG_COLUMNS = (
    ("setup_tag", "Setup", 120, "center"),
    ("count", "Count", 60, "center"),
    ("wins", "Wins", 60, "center"),
    ("losses", "Losses", 60, "center"),
    ("win_rate", "Win %", 70, "center"),
    ("avg_pnl", "Avg P/L", 90, "center"),
    ("total_pnl", "Total P/L", 100, "center"),
    ("expectancy", "Expectancy", 100, "center"),
)


# Proximity-aggregate table (plan.md decision 14). Same shape as
# :data:`_AGG_COLUMNS` but keyed by ``proximity_tag`` so the two tables
# read identically — only the leading column header changes.
_PROX_COLUMNS = (
    ("proximity_tag", "Proximity", 160, "center"),
    ("count", "Count", 60, "center"),
    ("wins", "Wins", 60, "center"),
    ("losses", "Losses", 60, "center"),
    ("win_rate", "Win %", 70, "center"),
    ("avg_pnl", "Avg P/L", 90, "center"),
    ("total_pnl", "Total P/L", 100, "center"),
    ("expectancy", "Expectancy", 100, "center"),
)


def _fmt_ts(ts: int) -> str:
    """Render an epoch-second int as ``YYYY-MM-DD HH:MM`` UTC.

    Sandbox engine stores timestamps as UTC epoch seconds. The
    Performance View deliberately uses UTC (not local) so saved/
    loaded sessions render identically across timezones.
    """
    try:
        dt = _dt.datetime.fromtimestamp(int(ts), tz=_dt.timezone.utc)
    except (OverflowError, ValueError, OSError):
        return str(ts)
    return dt.strftime("%Y-%m-%d %H:%M")


def _truncate(s: str, n: int = 60) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt_day(date_iso: str) -> str:
    """``2025-04-29`` -> ``Apr 29, 2025``; passthrough on parse failure."""
    try:
        return _dt.date.fromisoformat(date_iso).strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return date_iso or "(unknown day)"


class PerformanceView(BaseModalDialog):
    """Read-only Toplevel showing trades + per-setup aggregates."""

    def __init__(self, parent: Any, result: SessionResult,
                 *, title: str = "Sandbox — Performance",
                 screenshot_dir: Path | None = None):
        super().__init__(
            parent,
            title=title,
            geometry_key="dlg.performance_view",
            default_geometry="1100x780",
        )

        self._result = result
        self._screenshot_dir: Path | None = (
            Path(screenshot_dir) if screenshot_dir is not None else None)
        self._rows: list[TradeRow] = build_trade_rows(result)
        self._aggs: list[SetupAggregate] = build_setup_aggregates(self._rows)
        # Per-proximity-tag rollup (plan.md decision 14). One row per
        # non-empty ``earnings_proximity_tag`` / ``dividend_proximity_tag``
        # value; trades with neither carry an empty-string key
        # rendered as "(no-proximity)". Sorted by descending count to
        # mirror the setup table's ordering convention.
        self._proxs: list[ProximityAggregate] = (
            build_proximity_aggregates(self._rows))
        # Daily journal: per-day watch note joined with that day's
        # trades (blind-safe day labels). See build_day_groups.
        self._day_groups: list[DayGroup] = build_day_groups(result)
        self._journal_blind_var: tk.BooleanVar | None = None
        # Per-column sort state for the trade table: (column_key,
        # ascending). Toggled on every header click.
        self._trade_sort: tuple = ("exit_ts", True)
        # Equity-chart bookkeeping (set lazily by _build_equity_chart;
        # left as None when the session has no equity samples).
        self._equity_canvas = None
        self._equity_lines: dict = {}
        self._show_mtm_var: tk.BooleanVar | None = None
        self._show_realized_var: tk.BooleanVar | None = None
        # Status hook from parent app (best-effort; falls back to None).
        self._status = getattr(parent, "_status", None)

        self._build()
        self._populate_trades()
        self._populate_aggregates()
        self._populate_proximity()
        self._populate_summary()
        self._populate_journal()
        protect_combobox_wheel(self)
        # Read-only view: no primary action. ESC / WM_DELETE destroy.
        # Non-modal (no grab) — original Toplevel did not grab_set; the
        # user must be able to interact with the parent chart while
        # reviewing performance.
        self._finalize_modal(primary=None, cancel=self.destroy, grab=False)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        outer = ttk.Frame(self, padding=6)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        # Row layout: 0 summary, 1 equity chart (optional), 2 "Trades:"
        # label, 3 trades treeview, 4 "Per-setup aggregates:" label,
        # 5 aggregates treeview, 6 "Per-proximity aggregates:" label,
        # 7 proximity treeview, 8 "Daily journal:" header,
        # 9 journal treeview, 10 button bar.
        outer.rowconfigure(3, weight=3)
        outer.rowconfigure(5, weight=2)
        outer.rowconfigure(7, weight=2)
        outer.rowconfigure(9, weight=2)

        # Summary line.
        self._summary_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self._summary_var,
                  font=("TkDefaultFont", 10, "bold"))\
            .grid(row=0, column=0, sticky="w", pady=(0, 4))

        # ----- Equity chart pane (hidden when no data).
        self._equity_frame = ttk.Frame(outer)
        if self._result.equity_curve:
            self._equity_frame.grid(row=1, column=0, sticky="nsew",
                                    pady=(0, 6))
            self._build_equity_chart(self._equity_frame)

        # ----- Trades pane.
        ttk.Label(outer, text="Trades:").grid(row=2, column=0,
                                              sticky="w", pady=(2, 0))
        trades_frame = ttk.Frame(outer)
        trades_frame.grid(row=3, column=0, sticky="nsew", pady=(2, 4))
        trades_frame.columnconfigure(0, weight=1)
        trades_frame.rowconfigure(0, weight=1)

        cols = [k for k, _l, _w, _a in _TRADE_COLUMNS]
        self._trades = ttk.Treeview(
            trades_frame, columns=cols, show="headings", height=14)
        for key, label, width, anchor in _TRADE_COLUMNS:
            self._trades.heading(
                key, text=label,
                command=lambda c=key: self._sort_trades_by(c))
            self._trades.column(key, width=width, anchor=anchor,
                                stretch=False)
        self._trades.grid(row=0, column=0, sticky="nsew")
        scroll_y = ttk.Scrollbar(trades_frame, orient="vertical",
                                 command=self._trades.yview)
        scroll_y.grid(row=0, column=1, sticky="ns")
        self._trades.configure(yscrollcommand=scroll_y.set)

        # ----- Aggregates pane.
        ttk.Label(outer, text="Per-setup aggregates:").grid(
            row=4, column=0, sticky="w", pady=(8, 0))
        agg_frame = ttk.Frame(outer)
        agg_frame.grid(row=5, column=0, sticky="nsew")
        agg_frame.columnconfigure(0, weight=1)
        agg_frame.rowconfigure(0, weight=1)

        agg_cols = [k for k, _l, _w, _a in _AGG_COLUMNS]
        self._aggs_tree = ttk.Treeview(
            agg_frame, columns=agg_cols, show="headings", height=8)
        for key, label, width, anchor in _AGG_COLUMNS:
            self._aggs_tree.heading(key, text=label)
            self._aggs_tree.column(key, width=width, anchor=anchor,
                                   stretch=False)
        self._aggs_tree.grid(row=0, column=0, sticky="nsew")

        # ----- Proximity-aggregates pane (plan.md decision 14).
        # Sits directly below the setup-aggregates table so the user
        # can compare "by my setup tag" against "by market-event
        # proximity" side-by-side. Same column shape so the visual
        # alignment is preserved.
        ttk.Label(outer, text="Per-proximity aggregates:").grid(
            row=6, column=0, sticky="w", pady=(8, 0))
        prox_frame = ttk.Frame(outer)
        prox_frame.grid(row=7, column=0, sticky="nsew")
        prox_frame.columnconfigure(0, weight=1)
        prox_frame.rowconfigure(0, weight=1)

        prox_cols = [k for k, _l, _w, _a in _PROX_COLUMNS]
        self._prox_tree = ttk.Treeview(
            prox_frame, columns=prox_cols, show="headings", height=6)
        for key, label, width, anchor in _PROX_COLUMNS:
            self._prox_tree.heading(key, text=label)
            self._prox_tree.column(key, width=width, anchor=anchor,
                                   stretch=False)
        self._prox_tree.grid(row=0, column=0, sticky="nsew")

        # ----- Daily-journal pane: each replay day's watch note as a
        # header row, with that day's trades nested beneath it. Flat
        # days (a note but no trade) show as a header with no children.
        journal_header = ttk.Frame(outer)
        journal_header.grid(row=8, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(journal_header, text="Daily journal:").pack(side=tk.LEFT)
        self._journal_blind_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            journal_header, text="Blind (hide dates)",
            variable=self._journal_blind_var,
            command=self._populate_journal).pack(side=tk.LEFT, padx=(10, 0))
        journal_frame = ttk.Frame(outer)
        journal_frame.grid(row=9, column=0, sticky="nsew")
        journal_frame.columnconfigure(0, weight=1)
        journal_frame.rowconfigure(0, weight=1)
        self._journal_tree = ttk.Treeview(
            journal_frame, columns=("pl", "detail"),
            show="tree headings", height=8)
        self._journal_tree.heading("#0", text="Day / Trade")
        self._journal_tree.heading("pl", text="P/L")
        self._journal_tree.heading("detail", text="Note / Setup")
        self._journal_tree.column("#0", width=210, anchor="w", stretch=False)
        self._journal_tree.column("pl", width=90, anchor="e", stretch=False)
        self._journal_tree.column("detail", width=560, anchor="w", stretch=True)
        self._journal_tree.grid(row=0, column=0, sticky="nsew")
        jscroll = ttk.Scrollbar(journal_frame, orient="vertical",
                                command=self._journal_tree.yview)
        jscroll.grid(row=0, column=1, sticky="ns")
        self._journal_tree.configure(yscrollcommand=jscroll.set)

        # ----- Button bar.
        bar = ttk.Frame(outer)
        bar.grid(row=10, column=0, sticky="ew", pady=(8, 0))
        bar.columnconfigure(0, weight=1)
        export_state = "normal" if self._rows else "disabled"
        self._export_btn = ttk.Button(
            bar, text="Export CSV…",
            command=self._on_export_csv, state=export_state)
        self._export_btn.grid(row=0, column=1, padx=(0, 6))
        self._copy_btn = ttk.Button(
            bar, text="Copy to clipboard",
            command=self._on_copy_clipboard, state=export_state)
        self._copy_btn.grid(row=0, column=2, padx=(0, 6))
        ttk.Button(bar, text="Close",
                   command=self.destroy).grid(row=0, column=3)

    # ----------------------------------------------------------- equity chart
    def _build_equity_chart(self, parent: ttk.Frame) -> None:
        """Embed a small matplotlib subplot showing MTM + realized lines."""
        # Local imports keep module-level import cheap (Performance
        # View is opened lazily; many tests never construct it).
        import matplotlib.dates as mdates
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        fig = Figure(figsize=(10, 2.0), dpi=100)
        fig.subplots_adjust(left=0.07, right=0.99, top=0.92, bottom=0.22)
        ax = fig.add_subplot(111)
        ax.grid(True, alpha=0.25)
        ax.set_title("Account equity", fontsize=9)

        eq = self._result.equity_curve
        ts = [int(t) for t, _v in eq]
        mtm = [float(v) for _t, v in eq]
        times = [_dt.datetime.fromtimestamp(t, tz=_dt.timezone.utc)
                 for t in ts]
        (mtm_line,) = ax.plot(
            times, mtm, color="#1f77b4", linewidth=1.4,
            label="MTM equity")
        realized = realized_pnl_curve(self._result)
        rvals = [v for _t, v in realized]
        # `step` matches the discrete nature of closed-trade P&L:
        # value applies from each bar timestamp forward.
        (realized_line,) = ax.step(
            times, rvals, where="post", color="#d62728", linewidth=1.2,
            label="Realized P&L (closed trades, gross)")
        self._equity_lines = {"mtm": mtm_line, "realized": realized_line}
        ax.xaxis.set_major_formatter(
            mdates.DateFormatter("%H:%M", tz=_dt.timezone.utc))
        ax.legend(loc="upper left", fontsize=8)
        for label in ax.get_xticklabels():
            label.set_fontsize(8)
        for label in ax.get_yticklabels():
            label.set_fontsize(8)

        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH,
                                    expand=True)
        canvas.draw_idle()
        self._equity_canvas = canvas

        # Toggle row.
        toggles = ttk.Frame(parent)
        toggles.pack(side=tk.TOP, fill=tk.X)
        self._show_mtm_var = tk.BooleanVar(value=True)
        self._show_realized_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            toggles, text="MTM equity",
            variable=self._show_mtm_var,
            command=self._on_toggle_equity_lines)\
            .pack(side=tk.LEFT, padx=(4, 12))
        ttk.Checkbutton(
            toggles, text="Realized P&L (closed trades)",
            variable=self._show_realized_var,
            command=self._on_toggle_equity_lines)\
            .pack(side=tk.LEFT)

    def _on_toggle_equity_lines(self) -> None:
        if not self._equity_canvas or not self._equity_lines:
            return
        try:
            self._equity_lines["mtm"].set_visible(
                bool(self._show_mtm_var and self._show_mtm_var.get()))
            self._equity_lines["realized"].set_visible(
                bool(self._show_realized_var
                     and self._show_realized_var.get()))
            self._equity_canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    # -------------------------------------------------------------- exports
    def _on_export_csv(self) -> None:
        if not self._rows:
            return
        path_str = filedialog.asksaveasfilename(
            parent=self,
            title="Export sandbox session as CSV",
            defaultextension=".csv",
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not path_str:
            return
        try:
            written = write_trade_rows_csv(
                self._rows,
                csv_path=Path(path_str),
                screenshot_dir=self._screenshot_dir,
            )
        except Exception as exc:  # noqa: BLE001
            self._status_warn(f"Export CSV failed: {exc}")
            return
        bundle_note = ""
        if self._screenshot_dir is not None:
            bundle_note = (f"; screenshots mirrored to "
                           f"{written.stem}_screenshots/")
        self._status_info(
            f"Exported {len(self._rows)} trade(s) to {written.name}"
            f"{bundle_note}")

    def _on_copy_clipboard(self) -> None:
        if not self._rows:
            return
        text = trade_rows_to_tsv(self._rows)
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except tk.TclError as exc:
            self._status_warn(f"Copy to clipboard failed: {exc}")
            return
        self._status_info(
            f"Copied {len(self._rows)} trade(s) to clipboard (TSV).")

    def _status_info(self, msg: str) -> None:
        if self._status is not None:
            try:
                self._status.info(msg)
                return
            except Exception:  # noqa: BLE001
                pass

    def _status_warn(self, msg: str) -> None:
        if self._status is not None:
            try:
                self._status.warn(msg)
                return
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ data
    def _row_values(self, r: TradeRow) -> tuple:
        post = r.post
        target_str = ("" if r.target is None
                      else f"{float(r.target):,.2f}")
        return (
            _fmt_ts(post.entry_ts),
            _fmt_ts(post.exit_ts),
            str(post.symbol),
            str(post.side),
            f"{float(post.quantity):.0f}",
            f"{float(post.pnl):+,.2f}",
            f"{float(post.pnl_pct) * 100:+.2f}%",
            r.setup_tag or "(unattributed)",
            (str(r.conviction) if r.pre is not None else ""),
            f"{float(post.mae_pct) * 100:+.2f}%",
            f"{float(post.mfe_pct) * 100:+.2f}%",
            target_str,
            _truncate(r.thesis, 60),
        )

    def _agg_values(self, a: SetupAggregate) -> tuple:
        return (
            a.setup_tag or "(unattributed)",
            str(a.count),
            str(a.wins),
            str(a.losses),
            f"{a.win_rate * 100:.1f}%",
            f"{a.avg_pnl:+,.2f}",
            f"{a.total_pnl:+,.2f}",
            f"{a.expectancy:+,.2f}",
        )

    def _prox_values(self, a: ProximityAggregate) -> tuple:
        # Empty-string tag → "(no-proximity)" so the row is still
        # visible / explicable. Mirrors the "(unattributed)" treatment
        # in the setup-aggregates table for consistency.
        return (
            a.proximity_tag or "(no-proximity)",
            str(a.count),
            str(a.wins),
            str(a.losses),
            f"{a.win_rate * 100:.1f}%",
            f"{a.avg_pnl:+,.2f}",
            f"{a.total_pnl:+,.2f}",
            f"{a.expectancy:+,.2f}",
        )

    def _populate_trades(self) -> None:
        for child in self._trades.get_children():
            self._trades.delete(child)
        # Apply current sort.
        key, ascending = self._trade_sort
        sorted_rows = self._sorted_rows(key, ascending)
        for i, r in enumerate(sorted_rows):
            self._trades.insert("", "end", iid=str(i),
                                values=self._row_values(r))

    def _populate_aggregates(self) -> None:
        for child in self._aggs_tree.get_children():
            self._aggs_tree.delete(child)
        for i, a in enumerate(self._aggs):
            self._aggs_tree.insert("", "end", iid=str(i),
                                   values=self._agg_values(a))

    def _populate_proximity(self) -> None:
        # Pre-sorted descending-by-count by build_proximity_aggregates.
        tree = getattr(self, "_prox_tree", None)
        if tree is None:
            return
        for child in tree.get_children():
            tree.delete(child)
        for i, a in enumerate(self._proxs):
            tree.insert("", "end", iid=str(i),
                        values=self._prox_values(a))

    def _populate_journal(self) -> None:
        """Render each replay day's watch note with its trades nested.

        Day nodes are expanded by default so the report reads top-to-
        bottom: the day's note (header) sits *ahead of* the trades taken
        that day. The Blind checkbox swaps calendar dates for
        "Replay Day N" so a hindsight-safe review is possible.
        """
        tree = getattr(self, "_journal_tree", None)
        if tree is None:
            return
        for child in tree.get_children():
            tree.delete(child)
        blind = bool(self._journal_blind_var.get()) if self._journal_blind_var else False
        for g in self._day_groups:
            if blind:
                day_label = f"Replay Day {g.ordinal}"
            else:
                day_label = f"{_fmt_day(g.date_iso)}  ·  Day {g.ordinal}"
            note_line = (_truncate(g.note.replace("\r\n", " ").replace("\n", " "), 90)
                         if g.note else "(no note)")
            pl_str = f"{g.total_pnl:+,.2f}" if g.rows else ""
            parent = tree.insert(
                "", "end", text=day_label, open=True,
                values=(pl_str, note_line))
            for r in g.rows:
                setup = r.setup_tag or "(unattributed)"
                detail = f"{r.post.side} · {setup}"
                if r.thesis:
                    detail += f" · {_truncate(r.thesis, 50)}"
                tree.insert(
                    parent, "end", text=f"    {r.post.symbol}",
                    values=(f"{float(r.post.pnl):+,.2f}", detail))

    def _populate_summary(self) -> None:
        n = len(self._rows)
        if n == 0:
            self._summary_var.set("No closed trades in this session.")
            return
        wins = sum(1 for r in self._rows if r.is_win)
        total = sum(float(r.post.pnl) for r in self._rows)
        win_rate = wins / n if n else 0.0
        self._summary_var.set(
            f"{n} trade(s) — {wins} win(s), "
            f"win rate {win_rate * 100:.1f}%, "
            f"total P/L ${total:+,.2f}"
        )

    # ----------------------------------------------------------------- sort
    def _sort_key_fn(self, key: str):
        """Return a stable sort key callable for trade column ``key``."""
        if key in ("entry_ts", "exit_ts"):
            return lambda r: int(getattr(r.post, key))
        if key == "symbol":
            return lambda r: str(r.post.symbol)
        if key == "side":
            return lambda r: str(r.post.side)
        if key in ("quantity", "pnl", "pnl_pct", "mae_pct", "mfe_pct"):
            return lambda r: float(getattr(r.post, key))
        if key == "setup_tag":
            return lambda r: r.setup_tag
        if key == "conviction":
            return lambda r: int(r.conviction)
        if key == "target":
            # None sorts last in ascending; use +inf sentinel.
            return lambda r: (float("inf") if r.target is None
                              else float(r.target))
        if key == "thesis":
            return lambda r: r.thesis
        # Fallback: stringify whatever attribute we asked for.
        return lambda r: str(getattr(r.post, key, ""))

    def _sorted_rows(self, key: str, ascending: bool) -> list[TradeRow]:
        keyfn = self._sort_key_fn(key)
        return sorted(self._rows, key=keyfn, reverse=not ascending)

    def _sort_trades_by(self, key: str) -> None:
        prev_key, prev_asc = self._trade_sort
        ascending = (not prev_asc) if key == prev_key else True
        self._trade_sort = (key, ascending)
        self._populate_trades()


__all__ = ("PerformanceView",)
