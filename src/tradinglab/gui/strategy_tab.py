"""StrategyTab — embedded widget for the Strategy Tester popup.

A self-contained ``ttk.Frame`` that owns the entire Configure → Running
→ Result UX loop. Mounted inside a Toplevel that ``ChartApp`` opens from
the **Strategy** menubar entry (between **Exits** and **View**):

1. **Configure** — entry/exit pickers, universe picker (Watchlists /
   Presets / Symbols list), date-range preset, advanced cost model.
2. **Running** — progress label + Stop button; the runner is driven
   on a worker thread (the kernel is Tk-free) so the UI stays responsive.
3. **Result** — headline metrics, banners, per-symbol + per-year
   breakouts and an inline trade list.

The kernel (``strategy_tester.run``) is invoked on a daemon
``threading.Thread`` and writes ``aggregate.json`` + ``trades.csv``
on its own via the runner integration shipped in PR 3. The widget polls
the worker via ``after()`` and reloads the aggregate from disk when
the worker is done.
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
from ..strategy_tester.interval_compat import incompatible_indicators_for_interval
from ..strategy_tester.report import RunAggregate, load_aggregate
from ..strategy_tester.universe import list_presets
from ..watchlists import storage as _watchlists_storage
from .colors import MUTED_GREY

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
        app: Any = None,
        entries_storage: Any = None,
        exits_storage: Any = None,
        watchlists_storage: Any = None,
        # Optional overrides exposed for smoke testing.
        run_fn: Callable[..., Any] | None = None,
        candles_fetcher: Callable[[str, str], Any] | None = None,
    ) -> None:
        super().__init__(master)
        self._app = app  # ChartApp reference; supplies _worker_count
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
        self._pbar_hide_after_id: str | None = None
        self._current_run_dir: Path | None = None
        self._current_aggregate: RunAggregate | None = None

        # Background-export state (PDF/HTML/CSV — see _on_export_pdf etc.).
        # ``_export_kind`` is one of {"PDF", "HTML", "CSV"} while a job is
        # in flight, otherwise None. The button that started the job has
        # its text swapped to "Cancel"; the other two are disabled.
        self._export_in_flight: bool = False
        self._export_kind: str | None = None
        self._export_dst: str | None = None
        self._export_cancel_token: AcceptanceToken | None = None
        self._export_thread: threading.Thread | None = None
        self._export_result: dict[str, Any] = {}
        self._export_latest_progress: tuple[int, int, str] | None = None
        self._export_poll_after_id: str | None = None
        self._export_btn_original_text: dict[str, str] = {}

        # Tk Variables for the Configure pane.
        self._var_entry_id = tk.StringVar(value="")
        self._var_exit_id = tk.StringVar(value="")
        # Mine | Templates | All filter governing BOTH strategy dropdowns;
        # defaults to "Mine" each time the tab is built (session-only, NOT
        # persisted) so the pickers aren't buried under the ~21/22 bundled
        # starter templates (id prefix ``tmpl-``). Audit ``template-filter``.
        self._strategy_filter_var = tk.StringVar(value="mine")
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
        self._var_include_extended_hours = tk.BooleanVar(value=False)
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
        # Mine | Templates | All filter governing BOTH strategy dropdowns
        # below. Defaults to "Mine" (session-only) so the pickers open
        # decluttered instead of buried under the bundled starter
        # templates (id prefix ``tmpl-``). Audit ``template-filter``.
        filt = ttk.Frame(parent)
        filt.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 2))
        ttk.Label(filt, text="Show:").pack(side="left")
        self._strategy_filter_buttons: dict[str, ttk.Radiobutton] = {}
        for _value, _label in (("mine", "Mine"), ("templates", "Templates"),
                               ("all", "All")):
            rb = ttk.Radiobutton(
                filt, text=_label, value=_value,
                variable=self._strategy_filter_var,
                command=self._on_strategy_filter_change,
            )
            rb.pack(side="left", padx=(4, 0))
            self._strategy_filter_buttons[_value] = rb
        row += 1
        self._strategy_filter_hint = ttk.Label(
            parent, text="", foreground=MUTED_GREY, wraplength=320,
            justify="left")
        self._strategy_filter_hint.grid(row=row, column=0, columnspan=2,
                                        sticky="w")
        row += 1

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

        # Universe body: a single grid row hosting one of three
        # mutually-exclusive sub-frames + a banner. Using a sub-frame
        # with internal pack() avoids the bug where re-gridding the
        # WATCHLIST / PRESET frames at hard-coded outer rows overlapped
        # the "Generate per-trade screenshots" separator + checkbox
        # below (visible as a half-rendered horizontal line cutting
        # off mid-row when Watchlist / Preset was selected).
        self._frame_universe_body = ttk.Frame(parent)
        self._frame_universe_body.grid(
            row=row, column=0, columnspan=2, sticky="we", pady=(4, 0),
        )
        row += 1

        # Symbols entry
        self._frame_symbols = ttk.Frame(self._frame_universe_body)
        ttk.Label(self._frame_symbols, text="Symbols (comma-separated):").pack(
            side="left", padx=(0, 4)
        )
        ttk.Entry(
            self._frame_symbols, textvariable=self._var_universe_symbols, width=30,
        ).pack(side="left", fill="x", expand=True)

        # Watchlist picker
        self._frame_watchlist = ttk.Frame(self._frame_universe_body)
        ttk.Label(self._frame_watchlist, text="Watchlist:").pack(
            side="left", padx=(0, 4)
        )
        self._cb_watchlist = ttk.Combobox(
            self._frame_watchlist, textvariable=self._var_universe_watchlist,
            state="readonly", width=28,
        )
        self._cb_watchlist.pack(side="left", fill="x", expand=True)

        # Preset picker
        self._frame_preset = ttk.Frame(self._frame_universe_body)
        ttk.Label(self._frame_preset, text="Preset:").pack(side="left", padx=(0, 4))
        self._cb_preset = ttk.Combobox(
            self._frame_preset, textvariable=self._var_universe_preset,
            state="readonly", width=28,
        )
        self._cb_preset.pack(side="left", fill="x", expand=True)

        # Survivorship banner — only visible when PRESET mode.
        self._banner_survivorship = ttk.Label(
            self._frame_universe_body,
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

        # Extended-hours opt-in (default OFF = RTH-only). Premarket /
        # postmarket bars otherwise leak into indicator math and skew
        # EMA / SMA / RSI / VWAP values at the open.
        ttk.Checkbutton(
            parent, text="Include pre/post-market data",
            variable=self._var_include_extended_hours,
            command=self._on_extended_hours_toggle,
        ).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        self._lbl_extended_hours_warning = ttk.Label(
            parent,
            text=(
                "\u26a0 Warning: indicators (EMA, SMA, RSI, VWAP, etc.) "
                "will be skewed by extended-hours data."
            ),
            foreground="#cc6600",
            wraplength=400,
        )
        self._lbl_extended_hours_warning.grid(
            row=row, column=0, columnspan=2, sticky="we", padx=(20, 0)
        )
        self._lbl_extended_hours_warning.grid_remove()
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
        row += 1

        # Progress bar — visible only while a run is in progress.
        self._pbar = ttk.Progressbar(parent, mode="determinate", maximum=1)
        self._pbar.grid(row=row, column=0, columnspan=2, sticky="we", pady=(4, 0))
        self._pbar.grid_remove()  # hidden until _set_running_ui(True)
        row += 1

        # Recent Runs sidebar (browse + load prior runs from disk).
        ttk.Separator(parent, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="we", pady=8
        )
        row += 1
        recent_frame = ttk.LabelFrame(parent, text="Recent runs", padding=4)
        recent_frame.grid(
            row=row, column=0, columnspan=2, sticky="nswe", pady=(0, 0),
        )
        row += 1
        parent.grid_rowconfigure(row - 1, weight=1)
        recent_cols = ("started", "status", "label", "trades")
        self._tree_recent = ttk.Treeview(
            recent_frame, columns=recent_cols, show="headings", height=6,
            selectmode="extended",  # Ctrl/Shift+click for bulk-delete
        )
        for col, hdr, w in (
            ("started", "Started", 130),
            ("status", "Status", 70),
            ("label", "Label", 140),
            ("trades", "Trades", 50),
        ):
            self._tree_recent.heading(col, text=hdr)
            self._tree_recent.column(col, width=w, anchor="w")
        self._tree_recent.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(
            recent_frame, orient="vertical",
            command=self._tree_recent.yview,
        )
        scrollbar.pack(side="left", fill="y")
        self._tree_recent.configure(yscrollcommand=scrollbar.set)
        self._tree_recent.bind(
            "<<TreeviewSelect>>", self._on_recent_run_select,
        )

        recent_btns = ttk.Frame(parent)
        recent_btns.grid(
            row=row, column=0, columnspan=2, sticky="we", pady=(4, 0),
        )
        row += 1
        self._btn_load_run = ttk.Button(
            recent_btns, text="Load",
            command=self._on_load_recent_run, state="disabled",
        )
        self._btn_load_run.pack(side="left", padx=(0, 6))
        self._btn_refresh_recent = ttk.Button(
            recent_btns, text="Refresh",
            command=self._refresh_recent_runs,
        )
        self._btn_refresh_recent.pack(side="left", padx=(0, 6))
        self._btn_delete_run = ttk.Button(
            recent_btns, text="Delete…",
            command=self._on_delete_recent_run, state="disabled",
        )
        self._btn_delete_run.pack(side="left")

        # Cache of (run_dir, TestRun) keyed by Treeview iid.
        self._recent_run_index: dict[str, tuple[Path, Any]] = {}

        parent.grid_columnconfigure(1, weight=1)

        # Initial conditional-visibility state
        self._on_universe_kind_change()
        self._on_date_preset_change()
        self._on_advanced_toggle()
        self._refresh_recent_runs()

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
        self._banner_interval = ttk.Label(
            parent, text="", foreground="#1f3a73", wraplength=420,
            font=("", 9),
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

        # Action row: open run folder, copy CSV path, export HTML/PDF
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
        self._btn_export_csv.pack(side="left", padx=(0, 6))
        self._btn_export_html = ttk.Button(
            action_row, text="Export HTML…", command=self._on_export_html,
            state="disabled",
        )
        self._btn_export_html.pack(side="left", padx=(0, 6))
        self._btn_export_pdf = ttk.Button(
            action_row, text="Export PDF…", command=self._on_export_pdf,
            state="disabled",
        )
        self._btn_export_pdf.pack(side="left")

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def _populate_pickers(self) -> None:
        # Mine | Templates | All filter for the strategy dropdowns (audit
        # ``template-filter``). Display-only: it never changes a selection
        # already made, and ``_selected_entry`` / ``_selected_exit`` still
        # resolve against the FULL library.
        flt = (self._strategy_filter_var.get()
               if hasattr(self, "_strategy_filter_var") else "all")
        entries = [e for e in self._entries if self._filter_ok(e, flt)]
        exits = [x for x in self._exits if self._filter_ok(x, flt)]
        entry_values = [f"{e.name} · {e.id[:8]}" for e in entries]
        exit_values = [f"{x.name} · {x.id[:8]}" for x in exits]
        self._cb_entry["values"] = entry_values
        self._cb_exit["values"] = exit_values
        if entry_values and not self._var_entry_id.get():
            self._var_entry_id.set(entry_values[0])
        if exit_values and not self._var_exit_id.get():
            self._var_exit_id.set(exit_values[0])
        self._update_strategy_filter_hint(entry_values, exit_values)

        self._cb_watchlist["values"] = list(self._watchlist_names)
        if self._watchlist_names and not self._var_universe_watchlist.get():
            self._var_universe_watchlist.set(self._watchlist_names[0])

        # Presets: store the preset id but show the human label.
        presets = list_presets()
        self._preset_map = {label: pid for pid, label in presets}
        self._cb_preset["values"] = [label for _pid, label in presets]
        if presets and not self._var_universe_preset.get():
            self._var_universe_preset.set(presets[0][1])

    @staticmethod
    def _is_template(strategy: Any) -> bool:
        """True for a bundled starter template (seeded on first run),
        identified by the ``tmpl-`` id prefix — NOT
        ``created_with.template`` (a loaded/duplicated copy keeps a UUID
        id and belongs under "Mine"). Audit ``template-filter``.
        """
        return str(getattr(strategy, "id", "")).startswith("tmpl-")

    @classmethod
    def _filter_ok(cls, strategy: Any, flt: str) -> bool:
        is_tmpl = cls._is_template(strategy)
        if flt == "mine":
            return not is_tmpl
        if flt == "templates":
            return is_tmpl
        return True

    def _on_strategy_filter_change(self) -> None:
        """Re-populate both dropdowns for the newly selected Mine/Templates/
        All view (display-only; existing selections are preserved)."""
        self._populate_pickers()

    def _update_strategy_filter_hint(
        self, entry_values: list, exit_values: list,
    ) -> None:
        if entry_values and exit_values:
            msg = ""
        else:
            missing = []
            if not entry_values:
                missing.append("entry")
            if not exit_values:
                missing.append("exit")
            msg = (f"No {' or '.join(missing)} strategies in this view — "
                   "switch to Templates or All.")
        try:
            self._strategy_filter_hint.configure(text=msg)
        except (tk.TclError, AttributeError):
            pass

    # ------------------------------------------------------------------
    # Conditional-visibility callbacks
    # ------------------------------------------------------------------

    def _on_universe_kind_change(self, *_args) -> None:
        kind = UniverseKind(self._var_universe_kind.get())
        # All four sub-widgets live inside ``_frame_universe_body`` and
        # are toggled with pack/pack_forget, so no outer grid row
        # numbers are involved (the previous hardcoded ``row=14``/
        # ``row=15`` regrids overlapped the screenshot separator below).
        self._frame_symbols.pack_forget()
        self._frame_watchlist.pack_forget()
        self._frame_preset.pack_forget()
        self._banner_survivorship.pack_forget()
        if kind is UniverseKind.SYMBOLS:
            self._frame_symbols.pack(fill="x", expand=False)
        elif kind is UniverseKind.WATCHLIST:
            self._frame_watchlist.pack(fill="x", expand=False)
        elif kind is UniverseKind.PRESET:
            self._frame_preset.pack(fill="x", expand=False)
            self._banner_survivorship.pack(
                fill="x", expand=False, pady=(4, 0),
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

    def _on_extended_hours_toggle(self, *_args) -> None:
        if self._var_include_extended_hours.get():
            self._lbl_extended_hours_warning.grid()
        else:
            self._lbl_extended_hours_warning.grid_remove()

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
            include_extended_hours=bool(self._var_include_extended_hours.get()),
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

        # Block Runs that reference an intraday-only indicator (VWAP, RVOL
        # cumulative/time-of-day, RRVOL, Prior Day H/L) on a non-intraday
        # interval — they resolve to NaN every bar, so the strategy would
        # silently never trigger and the Run would report zero trades.
        # Audit ``intraday-interval-guard``.
        incompatible = incompatible_indicators_for_interval(
            entry, exit_strat, cfg.interval,
        )
        if incompatible:
            names = "\n".join(
                f"  \u2022 {name} \u2014 {reason}" for name, reason in incompatible
            )
            messagebox.showerror(
                "Strategy Tester \u2014 incompatible interval",
                f"This strategy can't run on the \"{cfg.interval}\" interval.\n\n"
                "It references indicator(s) that only work on intraday "
                "intervals (e.g. 1m, 5m, 15m, 1h):\n\n"
                f"{names}\n\n"
                f"On \"{cfg.interval}\" bars those indicators have no value, so "
                "the strategy would never trigger and the Run would produce "
                "zero trades. Choose an intraday interval, or edit the "
                "strategy to remove the intraday-only indicator(s).",
            )
            return

        # Screenshot opt-in
        screenshot_spec = ScreenshotSpec() if self._var_screenshots.get() else None

        self._token = AcceptanceToken()
        self._worker_result = {}
        self._set_running_ui(True)
        self._var_status.set("Run starting…")

        def _worker_main() -> None:
            try:
                max_workers = getattr(self._app, "_worker_count", None)
                result = self._run_fn(
                    cfg,
                    cancel_token=self._token,
                    candles_fetcher=self._candles_fetcher,
                    entry_loader=lambda sid: entries_by_id[sid],
                    exit_loader=lambda sid: exits_by_id[sid],
                    progress=self._on_progress,
                    screenshot_spec=screenshot_spec,
                    max_workers=max_workers,
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
        status = result.test_run.status
        # FAILED runs do NOT write aggregate.json (runner.py:507 gates
        # the aggregate write on status DONE/CANCELLED). Surface the
        # actual error message the runner captured so the user can
        # diagnose, instead of the misleading "aggregate is missing"
        # text we used to show.
        if status is RunStatus.FAILED:
            err_detail = result.test_run.error or "(no further details captured)"
            self._var_status.set(f"Run failed: {err_detail}")
            messagebox.showerror(
                "Strategy Tester",
                f"Run failed.\n\n{err_detail}\n\n"
                f"Per-symbol artifacts (manifest.json + per_symbol/*.json) "
                f"are in:\n{run_dir}",
            )
            try:
                self._refresh_recent_runs()
            except Exception:  # noqa: BLE001
                logger.exception("StrategyTab: _refresh_recent_runs raised")
            return
        agg = load_aggregate(run_dir)
        if agg is None:
            # Status was DONE/CANCELLED but the aggregate write itself
            # failed (runner caught + logged the exception). Tell the
            # user where to look.
            self._var_status.set(
                "Run finished but aggregate.json is missing — "
                "check the application log for the write error."
            )
            messagebox.showwarning(
                "Strategy Tester",
                "The Run completed but writing aggregate.json failed. "
                "Per-symbol artifacts are still on disk; see the "
                f"application log and inspect:\n{run_dir}",
            )
            try:
                self._refresh_recent_runs()
            except Exception:  # noqa: BLE001
                logger.exception("StrategyTab: _refresh_recent_runs raised")
            return
        self._current_aggregate = agg
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
        # Refresh the Recent runs sidebar so the new run shows up.
        try:
            self._refresh_recent_runs()
        except Exception:  # noqa: BLE001
            logger.exception("StrategyTab: refresh_recent_runs failed")

    def _set_running_ui(self, running: bool) -> None:
        # Cancel any pending bar-hide timer so a rapid re-run doesn't
        # hide the bar one second after it was re-shown.
        if self._pbar_hide_after_id is not None:
            try:
                self.after_cancel(self._pbar_hide_after_id)
            except Exception:  # noqa: BLE001
                pass
            self._pbar_hide_after_id = None

        if running:
            self._btn_run.configure(state="disabled")
            self._btn_stop.configure(state="normal")
            self._pbar.configure(value=0, maximum=1)
            self._pbar.grid()
        else:
            self._btn_run.configure(state="normal")
            self._btn_stop.configure(state="disabled")
            # Keep the bar visible for 1 s so the user sees the "full" state.
            self._pbar_hide_after_id = self.after(1000, self._hide_progress_bar)

    def _hide_progress_bar(self) -> None:
        """Hide the progress bar (scheduled 1 s after run completion)."""
        self._pbar_hide_after_id = None
        try:
            self._pbar.grid_remove()
        except Exception:  # noqa: BLE001
            pass

    def _on_progress(self, test_run: Any) -> None:
        """Progress callback; invoked from the runner's worker thread.

        Marshals the update onto the Tk main thread via ``after(0, ...)``.
        The bar shows completed / total symbols; the status label is updated
        with the same counts.
        """
        try:
            done = getattr(test_run, "symbol_count_done", 0)
            total = getattr(test_run, "symbol_count_total", 0)
            self.after(0, lambda d=done, t=total: self._apply_progress(d, t))
        except Exception:  # noqa: BLE001
            pass

    def _apply_progress(self, done: int, total: int) -> None:
        """Apply a progress update on the Tk main thread.

        ``update_idletasks()`` at the end is mandatory: when symbols complete
        sub-second (e.g. cached data + simple strategies), the runner fires
        ``progress(test_run)`` 12 times in <100ms, which queues 12
        ``after(0, ...)`` callbacks. Tk processes them all in a single
        batch BEFORE yielding to redraw, so without forcing idle-task
        processing here the bar visually jumps straight from 0 to N/N at
        the end of the run instead of advancing one symbol at a time.
        ``update_idletasks()`` flushes pending paint requests synchronously
        without re-entering the event loop, which is exactly what we want.
        """
        try:
            if total > 0:
                self._pbar.configure(maximum=total, value=done)
            self._var_status.set(f"Running… {done}/{total} symbols")
            try:
                self._pbar.update_idletasks()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass

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

        # Interval-override banner (independent of sample-size banner;
        # both can show simultaneously). Single-interval mode rewrites
        # every authored interval to TestConfig.interval — surface that
        # to the user so they're not confused why a "1m EMA cross"
        # template tested at 5m fires on 5m bars instead of 1m.
        if agg.interval_overrides:
            lines = "\n".join(f"  • {s}" for s in agg.interval_overrides)
            self._banner_interval.configure(
                text=(
                    "ℹ Interval overrides (strategy_tester runs in "
                    "single-interval mode):\n" + lines
                ),
            )
            self._banner_interval.pack(anchor="w", fill="x", pady=(0, 4))
        else:
            try:
                self._banner_interval.pack_forget()
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
        self._btn_export_html.configure(state="normal")
        self._btn_export_pdf.configure(state="normal")

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
        """Copy the in-run trades.csv to a user-chosen destination.

        CSV export is fast (~0.5s) but still backgrounded so the UI
        stays consistent with HTML/PDF.
        """
        if self._export_in_flight:
            self._cancel_in_flight_export()
            return
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

        self._begin_export("CSV", dst)

        def _bg() -> None:
            try:
                import shutil
                shutil.copyfile(src, dst)
                self._export_result["dst"] = dst
            except Exception as exc:  # noqa: BLE001
                self._export_result["error"] = str(exc)

        self._export_thread = threading.Thread(
            target=_bg, daemon=True, name="StrategyTabExportCSV",
        )
        self._export_thread.start()
        self._schedule_export_poll()

    def _on_export_html(self) -> None:
        """Render report.html on a background thread and copy it to dst."""
        if self._export_in_flight:
            self._cancel_in_flight_export()
            return
        if self._current_run_dir is None:
            return
        dst = filedialog.asksaveasfilename(
            title="Export HTML report",
            defaultextension=".html",
            initialfile=f"strategy_report_{int(time.time())}.html",
            filetypes=[("HTML files", "*.html"), ("All files", "*.*")],
        )
        if not dst:
            return

        self._begin_export("HTML", dst)
        run_dir = self._current_run_dir
        agg = self._current_aggregate
        token = self._export_cancel_token

        def _bg() -> None:
            try:
                from ..strategy_tester import export as _exp
                in_run_html = _exp.export_html(
                    run_dir,
                    aggregate=agg,
                    progress_callback=self._on_export_progress,
                    cancel_token=token,
                )
                import shutil
                shutil.copyfile(in_run_html, dst)
                self._export_result["dst"] = dst
            except Exception as exc:  # noqa: BLE001
                from ..strategy_tester import export as _exp
                if isinstance(exc, _exp.Cancelled):
                    self._export_result["cancelled"] = True
                else:
                    self._export_result["error"] = str(exc)

        self._export_thread = threading.Thread(
            target=_bg, daemon=True, name="StrategyTabExportHTML",
        )
        self._export_thread.start()
        self._schedule_export_poll()

    def _on_export_pdf(self) -> None:
        """Render report.pdf on a background thread and copy it to dst.

        PDF export is the slow one (20-60 s for a 200-screenshot report).
        Runs on a daemon thread, polls a cancel token between pages, and
        marshals progress + completion back to the Tk main thread via
        the same ``after(POLL_INTERVAL_MS, ...)`` pattern the runner
        uses. A second click on the Export PDF button (whose label is
        now "Cancel PDF…") cancels the in-flight job.
        """
        if self._export_in_flight:
            self._cancel_in_flight_export()
            return
        if self._current_run_dir is None:
            return
        dst = filedialog.asksaveasfilename(
            title="Export PDF report",
            defaultextension=".pdf",
            initialfile=f"strategy_report_{int(time.time())}.pdf",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not dst:
            return

        self._begin_export("PDF", dst)
        run_dir = self._current_run_dir
        agg = self._current_aggregate
        token = self._export_cancel_token

        def _bg() -> None:
            try:
                from ..strategy_tester import export as _exp
                in_run_pdf = _exp.export_pdf(
                    run_dir,
                    aggregate=agg,
                    progress_callback=self._on_export_progress,
                    cancel_token=token,
                )
                import shutil
                shutil.copyfile(in_run_pdf, dst)
                self._export_result["dst"] = dst
            except Exception as exc:  # noqa: BLE001
                from ..strategy_tester import export as _exp
                if isinstance(exc, _exp.Cancelled):
                    self._export_result["cancelled"] = True
                else:
                    self._export_result["error"] = str(exc)

        self._export_thread = threading.Thread(
            target=_bg, daemon=True, name="StrategyTabExportPDF",
        )
        self._export_thread.start()
        self._schedule_export_poll()

    # ------------------------------------------------------------------
    # Background-export plumbing (shared by CSV / HTML / PDF)
    # ------------------------------------------------------------------

    EXPORT_POLL_INTERVAL_MS = 100

    def _begin_export(self, kind: str, dst: str) -> None:
        """Initialise per-export state and switch the UI to export mode.

        Called on the Tk main thread before the background thread is
        spawned. Sets the cancel token + result dict, flips the
        in-flight flag, and calls :meth:`_set_export_ui` to swap button
        labels + show the progress bar.
        """
        self._export_in_flight = True
        self._export_kind = kind
        self._export_dst = dst
        self._export_cancel_token = AcceptanceToken()
        self._export_result = {}
        # latest_progress is updated from the background thread (atomic
        # tuple assignment is safe under the GIL) and read by the
        # Tk-main-thread poller. We intentionally do NOT call
        # ``self.after`` from the worker because tkinter's ``after`` is
        # only thread-safe when CPython's Tcl was built with threads,
        # which is not the case on the default Windows install — the
        # ``RuntimeError("main thread is not in main loop")`` surfaces
        # in pytest as PytestUnhandledThreadExceptionWarning.
        self._export_latest_progress: tuple[int, int, str] | None = None
        self._set_export_ui(True, kind)

    def _cancel_in_flight_export(self) -> None:
        """Signal the in-flight export to cancel.

        The background thread polls the token between pages and raises
        :class:`export.Cancelled`, which routes through
        :meth:`_on_export_done` with ``error="cancelled"``.
        """
        if self._export_cancel_token is not None:
            self._export_cancel_token.cancel()
        if self._export_kind:
            self._var_status.set(f"{self._export_kind} export cancelling…")

    def _on_export_progress(self, current: int, total: int, label: str) -> None:
        """Progress callback invoked from the export thread.

        Writes the latest progress tuple into ``self._export_latest_progress``
        for the Tk-main-thread poller to pick up. We rely on CPython's
        GIL making the single attribute assignment atomic; no lock
        needed because lost intermediate ticks are acceptable (the
        latest one always wins).
        """
        self._export_latest_progress = (current, total, label)

    def _schedule_export_poll(self) -> None:
        """Schedule the next export-poll tick on the Tk main thread."""
        if self._export_poll_after_id is not None:
            try:
                self.after_cancel(self._export_poll_after_id)
            except Exception:  # noqa: BLE001
                pass
        try:
            self._export_poll_after_id = self.after(
                self.EXPORT_POLL_INTERVAL_MS, self._on_export_poll,
            )
        except Exception:  # noqa: BLE001
            self._export_poll_after_id = None

    def _on_export_poll(self) -> None:
        """Tk-main-thread poll: paint progress, check for completion."""
        self._export_poll_after_id = None
        # Pick up any progress tick the worker dropped since last poll.
        progress = self._export_latest_progress
        if progress is not None:
            self._apply_export_progress(*progress)
            self._export_latest_progress = None
        thread = self._export_thread
        if thread is not None and thread.is_alive():
            self._schedule_export_poll()
            return
        # Worker finished — drain the result dict.
        result = self._export_result
        kind = self._export_kind or "Export"
        if result.get("cancelled"):
            self._on_export_done(kind, None, "cancelled")
        elif result.get("error"):
            self._on_export_done(kind, None, result["error"])
        else:
            self._on_export_done(kind, result.get("dst"), None)

    def _apply_export_progress(self, current: int, total: int, label: str) -> None:
        """Apply an export progress tick on the Tk main thread.

        See ``strategy_tab.spec.md`` and ``_apply_progress`` for the
        ``update_idletasks()`` paint-forcing rationale: rapid sub-second
        progress callbacks queue up and Tk batches their paints,
        producing a stuck-at-zero bar without the forced flush.
        """
        kind = self._export_kind or "Export"
        try:
            if total > 0:
                try:
                    self._pbar.configure(mode="determinate", maximum=total, value=current)
                except Exception:  # noqa: BLE001
                    pass
            self._var_status.set(
                f"Exporting {kind}… ({current}/{total}: {label})"
            )
            try:
                self._pbar.update_idletasks()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass

    def _on_export_done(self, kind: str, dst: str | None, error: str | None) -> None:
        """Export-thread completion callback (Tk main thread).

        Restores the UI, shows the appropriate dialog, and on success
        offers an "Open now?" prompt that launches the file in the OS
        default viewer.
        """
        self._export_in_flight = False
        self._export_kind = None
        self._export_dst = None
        self._export_cancel_token = None
        self._export_thread = None
        self._export_result = {}
        self._export_latest_progress = None
        self._set_export_ui(False, kind)
        if error == "cancelled":
            self._var_status.set(f"{kind} export cancelled.")
        elif error:
            self._var_status.set(f"{kind} export failed.")
            messagebox.showerror(
                "Strategy Tester", f"{kind} export failed: {error}"
            )
        else:
            self._var_status.set(f"{kind} exported to {dst}.")
            if dst and messagebox.askyesno(
                "Strategy Tester",
                f"{kind} exported to:\n{dst}\n\nOpen now?",
            ):
                self._open_in_os_default(dst)

    def _set_export_ui(self, active: bool, kind: str) -> None:
        """Toggle UI between idle and export-in-flight modes.

        Reuses ``self._pbar`` (Option A in the task brief). Saves the
        original button text on first activation so cancellation can
        restore it. While active, the kind's button text becomes
        ``"Cancel <kind>…"`` and the other two export buttons are
        disabled to prevent concurrent file writes into the in-run-dir
        ``report.pdf`` / ``report.html``.

        Run / Stop are intentionally *not* affected — exports are
        considered a separate concern from the runner.
        """
        all_btns = {
            "CSV": self._btn_export_csv,
            "HTML": self._btn_export_html,
            "PDF": self._btn_export_pdf,
        }
        if active:
            # Cancel any pending hide-bar timer so a quick export doesn't
            # have its bar yanked away one second in.
            if self._pbar_hide_after_id is not None:
                try:
                    self.after_cancel(self._pbar_hide_after_id)
                except Exception:  # noqa: BLE001
                    pass
                self._pbar_hide_after_id = None
            for k, btn in all_btns.items():
                # Stash original label exactly once per button.
                if k not in self._export_btn_original_text:
                    try:
                        self._export_btn_original_text[k] = btn.cget("text")
                    except Exception:  # noqa: BLE001
                        self._export_btn_original_text[k] = f"Export {k}…"
                if k == kind:
                    try:
                        btn.configure(text=f"Cancel {k}…", state="normal")
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    try:
                        btn.configure(state="disabled")
                    except Exception:  # noqa: BLE001
                        pass
            # Indeterminate by default; flips to determinate on first
            # progress tick.
            try:
                self._pbar.configure(mode="indeterminate", maximum=1, value=0)
                self._pbar.grid()
                self._pbar.start(50)
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                self._pbar.stop()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._pbar.configure(mode="determinate")
            except Exception:  # noqa: BLE001
                pass
            for k, btn in all_btns.items():
                original = self._export_btn_original_text.get(k, f"Export {k}…")
                try:
                    btn.configure(text=original, state="normal")
                except Exception:  # noqa: BLE001
                    pass
            # Hide the bar after 1 s, mirroring _set_running_ui's
            # "leave full state visible briefly" behaviour. The Run path
            # also uses self._pbar_hide_after_id, so the most-recent
            # caller wins — fine because exports and Runs don't overlap
            # at the pbar level (Run uses determinate; exports return
            # the bar to determinate on exit).
            self._pbar_hide_after_id = self.after(1000, self._hide_progress_bar)

    def _open_in_os_default(self, path: str) -> None:
        """Open ``path`` in the OS-default application (Acrobat / browser)."""
        try:
            import os as _os
            import subprocess as _sub
            import sys
            if sys.platform == "win32":
                _os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                _sub.Popen(["open", path])  # noqa: S607,S603
            else:
                _sub.Popen(["xdg-open", path])  # noqa: S607,S603
        except Exception:  # noqa: BLE001
            logger.exception("StrategyTab: open default failed for %s", path)

    # ------------------------------------------------------------------
    # Recent Runs sidebar
    # ------------------------------------------------------------------

    def _refresh_recent_runs(self) -> None:
        """Reload the Recent Runs Treeview from disk."""
        from ..strategy_tester import storage as _st_storage
        try:
            pairs = _st_storage.list_runs_with_paths()
        except Exception:  # noqa: BLE001
            logger.exception("StrategyTab: list_runs_with_paths failed")
            pairs = []

        # Clear existing tree.
        for iid in self._tree_recent.get_children():
            self._tree_recent.delete(iid)
        self._recent_run_index.clear()

        # Cap to newest 50 — anything older still browsable via "Open
        # storage folder" in PR 6.
        for path, run in pairs[:50]:
            label = run.config.user_label or "(no label)"
            started = run.started_at or "?"
            # Trim ISO microseconds and "T" -> space for compactness.
            try:
                started_display = started.replace("T", " ")[:19]
            except Exception:  # noqa: BLE001
                started_display = started
            iid = self._tree_recent.insert(
                "", "end",
                values=(
                    started_display,
                    run.status.value if hasattr(run.status, "value") else str(run.status),
                    label,
                    run.trade_count,
                ),
            )
            self._recent_run_index[iid] = (path, run)

    def _on_recent_run_select(self, _evt=None) -> None:
        """Enable/disable Load + Delete buttons on selection change.

        Load operates on exactly one run (loading multiple aggregates
        makes no sense in this UI); Delete supports multi-select
        (Ctrl/Shift+click) for bulk-deletes from the Recent runs list.
        """
        sel = self._tree_recent.selection()
        if len(sel) == 1:
            self._btn_load_run.configure(state="normal")
            self._btn_delete_run.configure(state="normal")
        elif len(sel) > 1:
            self._btn_load_run.configure(state="disabled")
            self._btn_delete_run.configure(state="normal")
        else:
            self._btn_load_run.configure(state="disabled")
            self._btn_delete_run.configure(state="disabled")

    def _on_load_recent_run(self) -> None:
        sel = self._tree_recent.selection()
        if not sel:
            return
        iid = sel[0]
        if iid not in self._recent_run_index:
            return
        run_dir, _run = self._recent_run_index[iid]
        try:
            agg = load_aggregate(run_dir)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Strategy Tester",
                f"Could not load run aggregate: {exc}",
            )
            return
        if agg is None:
            # Non-modal status update (was a messagebox.showinfo modal that
            # hung headless CI tests forever). The Recent Runs flow is
            # forgiving — a missing aggregate.json just means the run is
            # in flight or failed before reporting; users can see the
            # status string + try a refresh.
            self._var_status.set(
                "This run has no aggregate.json — it may still be running "
                "or have failed before reaching the report stage."
            )
            return
        self._current_run_dir = run_dir
        self._current_aggregate = agg
        self._render_aggregate(agg, run_dir)
        self._var_status.set(f"Loaded prior run from {run_dir.name}")

    def _on_delete_recent_run(self) -> None:
        """Delete the selected Recent Runs row(s).

        Supports multi-select (Ctrl/Shift+click) — the underlying
        ttk.Treeview uses ``selectmode="extended"`` by default. When
        more than one row is selected, a single confirm dialog with
        the count + first-five IDs gates the bulk delete; the report
        pane is cleared if the *currently-viewed* run is among the
        targets.
        """
        sel = self._tree_recent.selection()
        if not sel:
            return
        # Resolve all selected iids to (run_dir, TestRun) pairs.
        targets: list[tuple[Path, Any]] = []
        for iid in sel:
            if iid in self._recent_run_index:
                targets.append(self._recent_run_index[iid])
        if not targets:
            return

        # Build the confirm dialog text. Show up to 5 run IDs to keep
        # the dialog small; report the total count beyond that.
        n = len(targets)
        if n == 1:
            run_dir, run = targets[0]
            prompt = f"Delete run {run.run_id}?\n\nThis removes:\n{run_dir}"
        else:
            id_lines = "\n".join(
                f"  • {run.run_id}" for _, run in targets[:5]
            )
            if n > 5:
                id_lines += f"\n  • … and {n - 5} more"
            prompt = (
                f"Delete {n} runs?\n\nThis removes the following run "
                f"directories on disk:\n{id_lines}"
            )
        if not messagebox.askyesno("Strategy Tester", prompt):
            return

        from ..strategy_tester import storage as _st_storage

        failures: list[Path] = []
        cleared_current = False
        for run_dir, _run in targets:
            ok = _st_storage.delete_run(run_dir)
            if not ok:
                failures.append(run_dir)
                continue
            if self._current_run_dir == run_dir:
                cleared_current = True

        if cleared_current:
            self._current_run_dir = None
            self._current_aggregate = None

        if failures:
            lines = "\n".join(str(p) for p in failures[:5])
            extra = (
                f"\n… and {len(failures) - 5} more" if len(failures) > 5 else ""
            )
            messagebox.showerror(
                "Strategy Tester",
                f"Failed to delete {len(failures)} of {n} run(s). Close any "
                f"open Explorer windows pointing at these directories and "
                f"try again:\n{lines}{extra}",
            )

        deleted = n - len(failures)
        if deleted > 0:
            if cleared_current:
                self._var_status.set(
                    f"Deleted {deleted} run(s); current report cleared."
                )
            else:
                self._var_status.set(f"Deleted {deleted} run(s).")

        self._refresh_recent_runs()

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
        try:
            if self._pbar_hide_after_id is not None:
                self.after_cancel(self._pbar_hide_after_id)
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._export_cancel_token is not None:
                self._export_cancel_token.cancel()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._export_poll_after_id is not None:
                self.after_cancel(self._export_poll_after_id)
        except Exception:  # noqa: BLE001
            pass
        # Audit Tier 1.7 — best-effort join on the export daemon thread
        # so deterministic-teardown tests don't trip on a thread that
        # outlives the dialog. 2-second ceiling — the export polls
        # ``cancel_token.is_cancelled()`` between pages (PDF) or before
        # render/write (HTML), so cancellation observation latency is
        # bounded by ``_CANCEL_POLL_INTERVAL`` ~256 bars (microseconds)
        # for the strategy_tester evaluator path, and one matplotlib
        # render cycle for export (<1s). The 2s ceiling is intentionally
        # loose so a stuck-but-uncancelled thread can't hang dialog
        # destroy indefinitely; daemon=True ensures the process can
        # still exit cleanly if the join times out.
        try:
            thread = self._export_thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass
