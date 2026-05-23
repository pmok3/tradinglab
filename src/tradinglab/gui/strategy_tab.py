"""StrategyTab — right-side notebook tab for the Strategy Tester.

A self-contained ``ttk.Frame`` that owns the entire Configure → Running
→ Result UX loop:

1. **Configure** — entry/exit pickers, universe picker (Watchlists /
   Presets / Symbols list), date-range preset, advanced cost model.
2. **Running** — progress label + Stop button; the runner is driven
   on a worker thread (the kernel is Tk-free) so the UI stays responsive.
3. **Result** — headline metrics, banners, per-symbol + per-year
   breakouts and an inline trade list.

The kernel (``strategy_tester.run``) is invoked on a daemon
``threading.Thread`` and writes ``aggregate.json`` + ``trades.csv``
on its own via the runner integration shipped in PR 3. The tab polls
the worker via ``after()`` and reloads the aggregate from disk when
the worker is done.

This is a single-frame implementation; PR 5 will add the Recent Runs
sidebar + HTML/PDF export, and PR 6 the help integration.
"""

from __future__ import annotations

import logging
import threading
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from ..entries import storage as _entries_storage
from ..exits import storage as _exits_storage
from ..strategy_tester import (
    AcceptanceToken,
    CostModel,
    DatePreset,
    RunStatus,
    ScreenshotSpec,
    TestConfig,
    UniverseKind,
    UniverseSpec,
)
from ..strategy_tester import (
    run as run_strategy_test,
)
from ..strategy_tester.report import RunAggregate, load_aggregate
from ..strategy_tester.universe import list_presets
from ..watchlists import storage as _watchlists_storage

logger = logging.getLogger(__name__)


_DATE_PRESET_LABELS: dict[DatePreset, str] = {
    DatePreset.YTD: "Year to date",
    DatePreset.LAST_1Y: "Last 1 year",
    DatePreset.LAST_3Y: "Last 3 years",
    DatePreset.LAST_5Y: "Last 5 years",
    DatePreset.LAST_10Y: "Last 10 years",
    DatePreset.MAX: "Max history",
    DatePreset.CUSTOM: "Custom",
}


def _date_range_for_preset(preset: DatePreset) -> tuple[str, str]:
    """Return ``(start, end)`` ISO dates for a preset (UTC today as end)."""
    import datetime as _dt
    today = _dt.datetime.now(tz=_dt.timezone.utc).date()
    end = today.isoformat()
    if preset is DatePreset.YTD:
        start = today.replace(month=1, day=1).isoformat()
    elif preset is DatePreset.LAST_1Y:
        start = today.replace(year=today.year - 1).isoformat()
    elif preset is DatePreset.LAST_3Y:
        start = today.replace(year=today.year - 3).isoformat()
    elif preset is DatePreset.LAST_5Y:
        start = today.replace(year=today.year - 5).isoformat()
    elif preset is DatePreset.LAST_10Y:
        start = today.replace(year=today.year - 10).isoformat()
    elif preset is DatePreset.MAX:
        start = "2000-01-01"
    else:  # CUSTOM
        start = today.replace(year=today.year - 1).isoformat()
    return (start, end)


class StrategyTab(ttk.Frame):
    """Notebook tab implementing the full Strategy Tester UX loop."""

    POLL_INTERVAL_MS = 250

    def __init__(
        self,
        master: tk.Misc,
        *,
        entries_storage: Any = None,
        exits_storage: Any = None,
        watchlists_storage: Any = None,
        # Optional overrides exposed for smoke testing.
        run_fn: Callable[..., Any] | None = None,
        candles_fetcher: Callable[[str, str], Any] | None = None,
    ) -> None:
        super().__init__(master)
        self._entries_storage = entries_storage or _entries_storage
        self._exits_storage = exits_storage or _exits_storage
        self._watchlists_storage = watchlists_storage or _watchlists_storage
        self._run_fn = run_fn or run_strategy_test
        self._candles_fetcher = candles_fetcher

        # Strategy library caches (refreshed each refresh()).
        self._entries: list[Any] = []
        self._exits: list[Any] = []
        self._watchlist_names: list[str] = []

        # Active Run state.
        self._token: AcceptanceToken | None = None
        self._worker: threading.Thread | None = None
        self._worker_result: dict[str, Any] = {}
        self._poll_after_id: str | None = None
        self._current_run_dir: Path | None = None
        self._current_aggregate: RunAggregate | None = None

        # Tk Variables for the Configure pane.
        self._var_entry_id = tk.StringVar(value="")
        self._var_exit_id = tk.StringVar(value="")
        self._var_universe_kind = tk.StringVar(value=UniverseKind.SYMBOLS.value)
        self._var_universe_symbols = tk.StringVar(value="AAPL, MSFT, NVDA")
        self._var_universe_watchlist = tk.StringVar(value="")
        self._var_universe_preset = tk.StringVar(value="")
        self._var_date_preset = tk.StringVar(value=DatePreset.LAST_3Y.value)
        self._var_start_date = tk.StringVar(value="2023-01-01")
        self._var_end_date = tk.StringVar(value="2026-01-01")
        self._var_interval = tk.StringVar(value="1d")
        self._var_starting_cash = tk.StringVar(value="100000")
        self._var_slip_bps = tk.StringVar(value="5")
        self._var_comm_trade = tk.StringVar(value="0")
        self._var_comm_share = tk.StringVar(value="0")
        self._var_user_label = tk.StringVar(value="")
        self._var_screenshots = tk.BooleanVar(value=True)
        self._var_advanced_open = tk.BooleanVar(value=False)
        self._var_status = tk.StringVar(value="Ready.")

        self._build_layout()
        self.refresh()

        self.bind("<Destroy>", self._on_destroy, add="+")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload entry / exit / watchlist libraries from storage."""
        try:
            entries, _broken = self._entries_storage.load_all()
        except Exception:  # noqa: BLE001
            logger.exception("StrategyTab: entries load_all failed")
            entries = []
        try:
            exits, _broken = self._exits_storage.load_all()
        except Exception:  # noqa: BLE001
            logger.exception("StrategyTab: exits load_all failed")
            exits = []
        try:
            wls, _pinned = self._watchlists_storage.load_all()
            self._watchlist_names = [w.name for w in wls]
        except Exception:  # noqa: BLE001
            logger.exception("StrategyTab: watchlists load_all failed")
            self._watchlist_names = []

        self._entries = sorted(entries, key=lambda e: e.name.lower())
        self._exits = sorted(exits, key=lambda x: x.name.lower())

        self._populate_pickers()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        # Top-level horizontal paned: Configure | Report
        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        self._cfg_frame = ttk.Frame(paned, padding=6)
        self._report_frame = ttk.Frame(paned, padding=6)
        paned.add(self._cfg_frame, weight=1)
        paned.add(self._report_frame, weight=2)

        self._build_configure(self._cfg_frame)
        self._build_report(self._report_frame)

    # ----- Configure pane ---------------------------------------------

    def _build_configure(self, parent: ttk.Frame) -> None:
        row = 0
        ttk.Label(parent, text="Entry strategy", font=("", 9, "bold")).grid(
            row=row, column=0, sticky="w", pady=(0, 2)
        )
        row += 1
        self._cb_entry = ttk.Combobox(
            parent, textvariable=self._var_entry_id,
            state="readonly", width=40,
        )
        self._cb_entry.grid(row=row, column=0, columnspan=2, sticky="we")
        row += 1

        ttk.Label(parent, text="Exit strategy", font=("", 9, "bold")).grid(
            row=row, column=0, sticky="w", pady=(8, 2)
        )
        row += 1
        self._cb_exit = ttk.Combobox(
            parent, textvariable=self._var_exit_id,
            state="readonly", width=40,
        )
        self._cb_exit.grid(row=row, column=0, columnspan=2, sticky="we")
        row += 1

        # Universe picker - three modes
        ttk.Separator(parent, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="we", pady=8
        )
        row += 1
        ttk.Label(parent, text="Universe", font=("", 9, "bold")).grid(
            row=row, column=0, sticky="w", pady=(0, 2)
        )
        row += 1

        uframe = ttk.Frame(parent)
        uframe.grid(row=row, column=0, columnspan=2, sticky="we")
        row += 1
        for kind, label in (
            (UniverseKind.SYMBOLS, "Symbols list"),
            (UniverseKind.WATCHLIST, "Watchlist"),
            (UniverseKind.PRESET, "Preset"),
        ):
            ttk.Radiobutton(
                uframe, text=label, value=kind.value,
                variable=self._var_universe_kind,
                command=self._on_universe_kind_change,
            ).pack(side="left", padx=(0, 8))

        # Symbols entry
        self._frame_symbols = ttk.Frame(parent)
        self._frame_symbols.grid(row=row, column=0, columnspan=2, sticky="we", pady=(4, 0))
        row += 1
        ttk.Label(self._frame_symbols, text="Symbols (comma-separated):").pack(
            side="left", padx=(0, 4)
        )
        ttk.Entry(
            self._frame_symbols, textvariable=self._var_universe_symbols, width=30,
        ).pack(side="left", fill="x", expand=True)

        # Watchlist picker
        self._frame_watchlist = ttk.Frame(parent)
        ttk.Label(self._frame_watchlist, text="Watchlist:").pack(
            side="left", padx=(0, 4)
        )
        self._cb_watchlist = ttk.Combobox(
            self._frame_watchlist, textvariable=self._var_universe_watchlist,
            state="readonly", width=28,
        )
        self._cb_watchlist.pack(side="left", fill="x", expand=True)

        # Preset picker
        self._frame_preset = ttk.Frame(parent)
        ttk.Label(self._frame_preset, text="Preset:").pack(side="left", padx=(0, 4))
        self._cb_preset = ttk.Combobox(
            self._frame_preset, textvariable=self._var_universe_preset,
            state="readonly", width=28,
        )
        self._cb_preset.pack(side="left", fill="x", expand=True)

        # Survivorship banner — only visible when PRESET mode.
        self._banner_survivorship = ttk.Label(
            parent,
            text=(
                "⚠ Survivorship bias: current preset memberships are used "
                "for the entire date range, including periods when symbols "
                "may not have been members."
            ),
            foreground="#a06000",
            wraplength=380,
        )

        # Date range
        ttk.Separator(parent, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="we", pady=8
        )
        row += 1
        ttk.Label(parent, text="Date range", font=("", 9, "bold")).grid(
            row=row, column=0, sticky="w", pady=(0, 2)
        )
        row += 1
        dframe = ttk.Frame(parent)
        dframe.grid(row=row, column=0, columnspan=2, sticky="we")
        row += 1
        ttk.Label(dframe, text="Preset:").pack(side="left", padx=(0, 4))
        self._cb_date_preset = ttk.Combobox(
            dframe, textvariable=self._var_date_preset,
            state="readonly", width=18,
            values=[p.value for p in DatePreset],
        )
        self._cb_date_preset.pack(side="left", padx=(0, 8))
        self._cb_date_preset.bind(
            "<<ComboboxSelected>>", self._on_date_preset_change
        )
        ttk.Label(dframe, text="Interval:").pack(side="left", padx=(0, 4))
        ttk.Combobox(
            dframe, textvariable=self._var_interval, width=6,
            state="readonly",
            values=("1d", "5m", "1m"),
        ).pack(side="left")

        # Custom date inputs (shown only when CUSTOM preset is selected).
        self._frame_custom_dates = ttk.Frame(parent)
        ttk.Label(self._frame_custom_dates, text="Start (YYYY-MM-DD):").pack(
            side="left", padx=(0, 4)
        )
        ttk.Entry(
            self._frame_custom_dates, textvariable=self._var_start_date, width=12,
        ).pack(side="left", padx=(0, 8))
        ttk.Label(self._frame_custom_dates, text="End:").pack(side="left", padx=(0, 4))
        ttk.Entry(
            self._frame_custom_dates, textvariable=self._var_end_date, width=12,
        ).pack(side="left")

        # Capital + advanced cost model
        ttk.Separator(parent, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="we", pady=8
        )
        row += 1
        cframe = ttk.Frame(parent)
        cframe.grid(row=row, column=0, columnspan=2, sticky="we")
        row += 1
        ttk.Label(cframe, text="Starting cash (per symbol): $").pack(
            side="left", padx=(0, 4)
        )
        ttk.Entry(cframe, textvariable=self._var_starting_cash, width=12).pack(
            side="left"
        )

        self._btn_advanced = ttk.Checkbutton(
            parent, text="Advanced (slippage / commission)",
            variable=self._var_advanced_open,
            command=self._on_advanced_toggle,
        )
        self._btn_advanced.grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))
        row += 1
        self._frame_advanced = ttk.Frame(parent)
        ttk.Label(self._frame_advanced, text="Slippage (bps):").grid(
            row=0, column=0, sticky="w", padx=(0, 4)
        )
        ttk.Entry(self._frame_advanced, textvariable=self._var_slip_bps, width=8).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(self._frame_advanced, text="Commission/trade ($):").grid(
            row=1, column=0, sticky="w", padx=(0, 4)
        )
        ttk.Entry(self._frame_advanced, textvariable=self._var_comm_trade, width=8).grid(
            row=1, column=1, sticky="w"
        )
        ttk.Label(self._frame_advanced, text="Commission/share ($):").grid(
            row=2, column=0, sticky="w", padx=(0, 4)
        )
        ttk.Entry(self._frame_advanced, textvariable=self._var_comm_share, width=8).grid(
            row=2, column=1, sticky="w"
        )

        # Screenshot opt-in
        ttk.Separator(parent, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="we", pady=8
        )
        row += 1
        ttk.Checkbutton(
            parent, text="Generate per-trade screenshots (PNG)",
            variable=self._var_screenshots,
        ).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        # Label + Run / Stop buttons
        ttk.Separator(parent, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="we", pady=8
        )
        row += 1
        ttk.Label(parent, text="Run label (optional):").grid(
            row=row, column=0, sticky="w"
        )
        ttk.Entry(parent, textvariable=self._var_user_label, width=30).grid(
            row=row, column=1, sticky="we", padx=(4, 0)
        )
        row += 1

        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=row, column=0, columnspan=2, sticky="we", pady=(8, 0))
        row += 1
        self._btn_run = ttk.Button(btn_frame, text="Run", command=self._on_run_clicked)
        self._btn_run.pack(side="left", padx=(0, 6))
        self._btn_stop = ttk.Button(
            btn_frame, text="Stop", command=self._on_stop_clicked, state="disabled",
        )
        self._btn_stop.pack(side="left")

        # Status line
        ttk.Label(parent, textvariable=self._var_status, foreground="#404040").grid(
            row=row, column=0, columnspan=2, sticky="we", pady=(8, 0)
        )

        parent.grid_columnconfigure(1, weight=1)

        # Initial conditional-visibility state
        self._on_universe_kind_change()
        self._on_date_preset_change()
        self._on_advanced_toggle()

    # ----- Report pane ------------------------------------------------

    def _build_report(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Run Report", font=("", 12, "bold")).pack(
            anchor="w"
        )
        self._lbl_run_id = ttk.Label(parent, text="(no run yet)")
        self._lbl_run_id.pack(anchor="w", pady=(2, 6))

        self._banner_sample = ttk.Label(
            parent, text="", foreground="#a06000", wraplength=420,
        )
        # Headline metrics grid
        hg = ttk.LabelFrame(parent, text="Headline", padding=6)
        hg.pack(fill="x", pady=(0, 6))
        self._lbl_trades = ttk.Label(hg, text="Trades: 0")
        self._lbl_trades.grid(row=0, column=0, sticky="w", padx=(0, 12))
        self._lbl_winrate = ttk.Label(hg, text="Win rate: 0.0%")
        self._lbl_winrate.grid(row=0, column=1, sticky="w", padx=(0, 12))
        self._lbl_expectancy = ttk.Label(hg, text="Expectancy: $0.00")
        self._lbl_expectancy.grid(row=0, column=2, sticky="w")
        self._lbl_pf = ttk.Label(hg, text="Profit factor: 0.00")
        self._lbl_pf.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(2, 0))
        self._lbl_pnl_gross = ttk.Label(hg, text="P&L gross: $0.00")
        self._lbl_pnl_gross.grid(row=1, column=1, sticky="w", padx=(0, 12), pady=(2, 0))
        self._lbl_pnl_net = ttk.Label(hg, text="P&L net: $0.00")
        self._lbl_pnl_net.grid(row=1, column=2, sticky="w", pady=(2, 0))
        self._lbl_dd = ttk.Label(hg, text="Max DD: $0.00 (0.0%)")
        self._lbl_dd.grid(row=2, column=0, sticky="w", padx=(0, 12), pady=(2, 0))
        self._lbl_sharpe = ttk.Label(hg, text="Sharpe: 0.00")
        self._lbl_sharpe.grid(row=2, column=1, sticky="w", padx=(0, 12), pady=(2, 0))
        self._lbl_sortino = ttk.Label(hg, text="Sortino: 0.00")
        self._lbl_sortino.grid(row=2, column=2, sticky="w", pady=(2, 0))

        # Notebook of per-symbol / per-year breakouts
        nb = ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)

        # Per-symbol Treeview
        sym_frame = ttk.Frame(nb, padding=4)
        nb.add(sym_frame, text="Per-symbol")
        sym_cols = ("symbol", "trades", "wins", "losses", "win_rate", "pnl", "pf", "dd")
        self._tree_symbol = ttk.Treeview(
            sym_frame, columns=sym_cols, show="headings", height=10,
        )
        for col, hdr, w in (
            ("symbol", "Symbol", 80),
            ("trades", "Trades", 60),
            ("wins", "Wins", 50),
            ("losses", "Losses", 60),
            ("win_rate", "Win %", 60),
            ("pnl", "P&L net", 90),
            ("pf", "PF", 60),
            ("dd", "Max DD", 90),
        ):
            self._tree_symbol.heading(col, text=hdr)
            self._tree_symbol.column(col, width=w, anchor="w")
        self._tree_symbol.pack(fill="both", expand=True)

        # Per-year Treeview
        yr_frame = ttk.Frame(nb, padding=4)
        nb.add(yr_frame, text="Per-year")
        yr_cols = ("year", "trades", "wins", "losses", "win_rate", "pnl", "expectancy", "pf")
        self._tree_year = ttk.Treeview(
            yr_frame, columns=yr_cols, show="headings", height=10,
        )
        for col, hdr, w in (
            ("year", "Year", 60),
            ("trades", "Trades", 60),
            ("wins", "Wins", 50),
            ("losses", "Losses", 60),
            ("win_rate", "Win %", 60),
            ("pnl", "P&L net", 90),
            ("expectancy", "Expectancy", 90),
            ("pf", "PF", 60),
        ):
            self._tree_year.heading(col, text=hdr)
            self._tree_year.column(col, width=w, anchor="w")
        self._tree_year.pack(fill="both", expand=True)

        # Action row: open run folder, copy CSV path
        action_row = ttk.Frame(parent)
        action_row.pack(fill="x", pady=(6, 0))
        self._btn_open_folder = ttk.Button(
            action_row, text="Open run folder", command=self._on_open_folder,
            state="disabled",
        )
        self._btn_open_folder.pack(side="left", padx=(0, 6))
        self._btn_export_csv = ttk.Button(
            action_row, text="Export CSV…", command=self._on_export_csv,
            state="disabled",
        )
        self._btn_export_csv.pack(side="left")

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def _populate_pickers(self) -> None:
        entry_values = [f"{e.name} · {e.id[:8]}" for e in self._entries]
        exit_values = [f"{x.name} · {x.id[:8]}" for x in self._exits]
        self._cb_entry["values"] = entry_values
        self._cb_exit["values"] = exit_values
        if entry_values and not self._var_entry_id.get():
            self._var_entry_id.set(entry_values[0])
        if exit_values and not self._var_exit_id.get():
            self._var_exit_id.set(exit_values[0])

        self._cb_watchlist["values"] = list(self._watchlist_names)
        if self._watchlist_names and not self._var_universe_watchlist.get():
            self._var_universe_watchlist.set(self._watchlist_names[0])

        # Presets: store the preset id but show the human label.
        presets = list_presets()
        self._preset_map = {label: pid for pid, label in presets}
        self._cb_preset["values"] = [label for _pid, label in presets]
        if presets and not self._var_universe_preset.get():
            self._var_universe_preset.set(presets[0][1])

    # ------------------------------------------------------------------
    # Conditional-visibility callbacks
    # ------------------------------------------------------------------

    def _on_universe_kind_change(self, *_args) -> None:
        kind = UniverseKind(self._var_universe_kind.get())
        self._frame_symbols.grid_remove()
        self._frame_watchlist.grid_remove()
        self._frame_preset.grid_remove()
        self._banner_survivorship.grid_remove()
        if kind is UniverseKind.SYMBOLS:
            self._frame_symbols.grid(
                row=self._frame_symbols.grid_info().get("row", 0),
                column=0, columnspan=2, sticky="we", pady=(4, 0),
            )
            # Re-establish geometry if grid_info was empty (first time).
            self._frame_symbols.grid_configure(sticky="we")
        elif kind is UniverseKind.WATCHLIST:
            self._frame_watchlist.grid(
                row=14, column=0, columnspan=2, sticky="we", pady=(4, 0),
            )
        elif kind is UniverseKind.PRESET:
            self._frame_preset.grid(
                row=14, column=0, columnspan=2, sticky="we", pady=(4, 0),
            )
            self._banner_survivorship.grid(
                row=15, column=0, columnspan=2, sticky="we", pady=(4, 0),
            )

    def _on_date_preset_change(self, *_args) -> None:
        try:
            preset = DatePreset(self._var_date_preset.get())
        except ValueError:
            return
        if preset is DatePreset.CUSTOM:
            self._frame_custom_dates.grid(
                row=22, column=0, columnspan=2, sticky="we", pady=(4, 0),
            )
        else:
            self._frame_custom_dates.grid_remove()
            start, end = _date_range_for_preset(preset)
            self._var_start_date.set(start)
            self._var_end_date.set(end)

    def _on_advanced_toggle(self, *_args) -> None:
        if self._var_advanced_open.get():
            self._frame_advanced.grid(
                row=27, column=0, columnspan=2, sticky="we", pady=(4, 0),
            )
        else:
            self._frame_advanced.grid_remove()

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def _build_config_from_ui(self) -> TestConfig | None:
        # Resolve entry / exit
        entry = self._selected_entry()
        exit_strat = self._selected_exit()
        if entry is None:
            messagebox.showwarning(
                "Strategy Tester", "Pick an entry strategy first.")
            return None
        if exit_strat is None:
            messagebox.showwarning(
                "Strategy Tester", "Pick an exit strategy first.")
            return None

        # Universe
        try:
            universe = self._build_universe_spec()
        except ValueError as exc:
            messagebox.showwarning("Strategy Tester", str(exc))
            return None

        # Date range
        try:
            preset = DatePreset(self._var_date_preset.get())
        except ValueError:
            preset = DatePreset.LAST_3Y
        if preset is DatePreset.CUSTOM:
            start = self._var_start_date.get().strip()
            end = self._var_end_date.get().strip()
        else:
            start, end = _date_range_for_preset(preset)

        # Cost model
        try:
            cost = CostModel(
                slippage_bps=float(self._var_slip_bps.get() or 0.0),
                commission_per_trade=float(self._var_comm_trade.get() or 0.0),
                commission_per_share=float(self._var_comm_share.get() or 0.0),
            )
        except ValueError:
            messagebox.showwarning(
                "Strategy Tester",
                "Slippage / commission fields must be numeric."
            )
            return None
        try:
            starting_cash = float(self._var_starting_cash.get() or 100_000.0)
        except ValueError:
            starting_cash = 100_000.0

        return TestConfig(
            entry_strategy_id=entry.id,
            exit_strategy_id=exit_strat.id,
            universe=universe,
            start_date=start,
            end_date=end,
            interval=self._var_interval.get() or "1d",
            starting_cash=starting_cash,
            cost_model=cost,
            date_preset=preset,
            user_label=self._var_user_label.get().strip(),
        )

    def _selected_entry(self) -> Any | None:
        label = self._var_entry_id.get()
        for e in self._entries:
            if label.startswith(e.name):
                return e
        return None

    def _selected_exit(self) -> Any | None:
        label = self._var_exit_id.get()
        for x in self._exits:
            if label.startswith(x.name):
                return x
        return None

    def _build_universe_spec(self) -> UniverseSpec:
        kind = UniverseKind(self._var_universe_kind.get())
        if kind is UniverseKind.SYMBOLS:
            raw = self._var_universe_symbols.get()
            syms = tuple(
                s.strip().upper()
                for s in raw.replace(";", ",").split(",")
                if s.strip()
            )
            if not syms:
                raise ValueError("Symbols list is empty.")
            return UniverseSpec(kind=kind, symbols=syms)
        if kind is UniverseKind.WATCHLIST:
            name = self._var_universe_watchlist.get().strip()
            if not name:
                raise ValueError("Pick a watchlist.")
            return UniverseSpec(kind=kind, watchlist_name=name)
        # PRESET
        label = self._var_universe_preset.get().strip()
        if not label:
            raise ValueError("Pick a preset.")
        preset_id = self._preset_map.get(label, "")
        if not preset_id:
            raise ValueError(f"Unknown preset label: {label!r}")
        return UniverseSpec(kind=kind, preset_id=preset_id)

    def _on_run_clicked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            messagebox.showinfo("Strategy Tester", "A Run is already in progress.")
            return
        cfg = self._build_config_from_ui()
        if cfg is None:
            return

        # Resolve loaders against the freshly-loaded library.
        entry = self._selected_entry()
        exit_strat = self._selected_exit()
        assert entry is not None and exit_strat is not None
        entries_by_id = {entry.id: entry}
        exits_by_id = {exit_strat.id: exit_strat}

        # Screenshot opt-in
        screenshot_spec = ScreenshotSpec() if self._var_screenshots.get() else None

        self._token = AcceptanceToken()
        self._worker_result = {}
        self._set_running_ui(True)
        self._var_status.set("Run starting…")

        def _on_progress(test_run: Any) -> None:
            try:
                done = getattr(test_run, "symbol_count_done", 0)
                total = getattr(test_run, "symbol_count_total", 0)
                self._var_status.set(
                    f"Running… {done}/{total} symbols done"
                )
            except Exception:  # noqa: BLE001
                pass

        def _worker_main() -> None:
            try:
                result = self._run_fn(
                    cfg,
                    cancel_token=self._token,
                    candles_fetcher=self._candles_fetcher,
                    entry_loader=lambda sid: entries_by_id[sid],
                    exit_loader=lambda sid: exits_by_id[sid],
                    progress=_on_progress,
                    screenshot_spec=screenshot_spec,
                )
                self._worker_result["result"] = result
            except Exception as exc:  # noqa: BLE001
                logger.exception("StrategyTab: worker crashed")
                self._worker_result["error"] = str(exc)

        self._worker = threading.Thread(
            target=_worker_main, daemon=True, name="StrategyTabRunner",
        )
        self._worker.start()
        self._schedule_poll()

    def _on_stop_clicked(self) -> None:
        if self._token is not None:
            self._token.cancel()
        self._var_status.set("Stopping… (waiting for in-flight symbols)")

    def _schedule_poll(self) -> None:
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:  # noqa: BLE001
                pass
        try:
            self._poll_after_id = self.after(
                self.POLL_INTERVAL_MS, self._on_poll
            )
        except Exception:  # noqa: BLE001
            self._poll_after_id = None

    def _on_poll(self) -> None:
        self._poll_after_id = None
        if self._worker is None:
            return
        if self._worker.is_alive():
            self._schedule_poll()
            return
        # Worker finished
        err = self._worker_result.get("error")
        result = self._worker_result.get("result")
        self._set_running_ui(False)
        self._worker = None
        if err:
            self._var_status.set(f"Run failed: {err}")
            messagebox.showerror("Strategy Tester", f"Run failed:\n{err}")
            return
        if result is None:
            self._var_status.set("Run produced no result.")
            return
        run_dir = Path(result.run_dir)
        self._current_run_dir = run_dir
        agg = load_aggregate(run_dir)
        if agg is None:
            self._var_status.set(
                "Run finished but aggregate.json is missing."
            )
            return
        self._current_aggregate = agg
        status = result.test_run.status
        if status is RunStatus.CANCELLED:
            self._var_status.set(
                f"Stopped. Partial results: "
                f"{result.test_run.symbol_count_done}"
                f"/{result.test_run.symbol_count_total} symbols."
            )
        elif status is RunStatus.DONE:
            self._var_status.set(
                f"Done. {result.test_run.symbol_count_done} symbols, "
                f"{agg.trade_count} trades."
            )
        else:
            self._var_status.set(f"Run finished with status: {status.value}")
        self._render_aggregate(agg, run_dir)

    def _set_running_ui(self, running: bool) -> None:
        if running:
            self._btn_run.configure(state="disabled")
            self._btn_stop.configure(state="normal")
        else:
            self._btn_run.configure(state="normal")
            self._btn_stop.configure(state="disabled")

    # ------------------------------------------------------------------
    # Report rendering
    # ------------------------------------------------------------------

    def _render_aggregate(self, agg: RunAggregate, run_dir: Path) -> None:
        self._lbl_run_id.configure(
            text=f"Run {agg.run_id} · {run_dir.name}"
        )
        self._lbl_trades.configure(
            text=f"Trades: {agg.trade_count}  "
            f"(W {agg.win_count} / L {agg.loss_count})"
        )
        wr_pct = agg.win_rate * 100.0
        wr_lo = agg.win_rate_ci_95.lo * 100.0
        wr_hi = agg.win_rate_ci_95.hi * 100.0
        self._lbl_winrate.configure(
            text=f"Win rate: {wr_pct:.1f}% [{wr_lo:.1f}–{wr_hi:.1f}]"
        )
        exp_ci = agg.expectancy_ci_95
        self._lbl_expectancy.configure(
            text=f"Expectancy: ${agg.expectancy:,.2f} "
            f"[${exp_ci.lo:,.2f}–${exp_ci.hi:,.2f}]"
        )
        pf_disp = "∞" if agg.profit_factor >= 1e8 else f"{agg.profit_factor:.2f}"
        self._lbl_pf.configure(text=f"Profit factor: {pf_disp}")
        self._lbl_pnl_gross.configure(
            text=f"P&L gross: ${agg.total_pnl_gross:,.2f}"
        )
        self._lbl_pnl_net.configure(
            text=f"P&L net: ${agg.total_pnl_net:,.2f}"
        )
        self._lbl_dd.configure(
            text=f"Max DD: ${agg.max_drawdown:,.2f} "
            f"({agg.max_drawdown_pct * 100.0:.1f}%)"
        )
        self._lbl_sharpe.configure(text=f"Sharpe: {agg.sharpe_ratio:.2f}")
        self._lbl_sortino.configure(text=f"Sortino: {agg.sortino_ratio:.2f}")

        # Banners
        if agg.insufficient_sample:
            self._banner_sample.configure(
                text=(
                    f"⚠ Insufficient sample (N={agg.trade_count} < 30). "
                    f"Confidence intervals are wide — treat headline "
                    f"numbers as illustrative."
                ),
            )
            self._banner_sample.pack(anchor="w", fill="x", pady=(0, 4))
        elif agg.low_sample:
            self._banner_sample.configure(
                text=(
                    f"⚠ Low sample (N={agg.trade_count} < 100). "
                    f"Confidence intervals may be wider than you'd like."
                ),
            )
            self._banner_sample.pack(anchor="w", fill="x", pady=(0, 4))
        else:
            try:
                self._banner_sample.pack_forget()
            except Exception:  # noqa: BLE001
                pass

        # Per-symbol Treeview
        for iid in self._tree_symbol.get_children():
            self._tree_symbol.delete(iid)
        for s in agg.per_symbol:
            pf_s = "∞" if s.profit_factor >= 1e8 else f"{s.profit_factor:.2f}"
            self._tree_symbol.insert(
                "", "end",
                values=(
                    s.symbol, s.trade_count, s.wins, s.losses,
                    f"{s.win_rate * 100.0:.1f}",
                    f"${s.total_pnl_net:,.2f}",
                    pf_s,
                    f"${s.max_drawdown:,.2f}",
                ),
            )

        # Per-year Treeview
        for iid in self._tree_year.get_children():
            self._tree_year.delete(iid)
        for y in agg.per_year:
            pf_y = "∞" if y.profit_factor >= 1e8 else f"{y.profit_factor:.2f}"
            self._tree_year.insert(
                "", "end",
                values=(
                    y.year, y.trade_count, y.wins, y.losses,
                    f"{y.win_rate * 100.0:.1f}",
                    f"${y.total_pnl_net:,.2f}",
                    f"${y.expectancy:,.2f}",
                    pf_y,
                ),
            )

        self._btn_open_folder.configure(state="normal")
        self._btn_export_csv.configure(state="normal")

    def _on_open_folder(self) -> None:
        if self._current_run_dir is None:
            return
        path = self._current_run_dir
        try:
            import os
            import subprocess
            import sys
            if sys.platform == "win32":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])  # noqa: S607,S603
            else:
                subprocess.Popen(["xdg-open", str(path)])  # noqa: S607,S603
        except Exception:  # noqa: BLE001
            logger.exception("StrategyTab: failed to open run folder")

    def _on_export_csv(self) -> None:
        if self._current_run_dir is None:
            return
        src = self._current_run_dir / "trades.csv"
        if not src.exists():
            messagebox.showinfo(
                "Strategy Tester", "trades.csv not yet written."
            )
            return
        dst = filedialog.asksaveasfilename(
            title="Export Run CSV",
            defaultextension=".csv",
            initialfile=f"strategy_run_{int(time.time())}.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not dst:
            return
        try:
            import shutil
            shutil.copyfile(src, dst)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Strategy Tester", f"Export failed: {exc}")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _on_destroy(self, _evt) -> None:
        try:
            if self._token is not None:
                self._token.cancel()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._poll_after_id is not None:
                self.after_cancel(self._poll_after_id)
        except Exception:  # noqa: BLE001
            pass
