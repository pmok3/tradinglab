"""Modal dialog: preload a basket / watchlist into the disk cache.

This is the GUI wrapper around :func:`tradinglab.preload.service.preload_universe`.
It owns the threading / Tk-marshalling concerns the service deliberately
sidesteps:

* A worker ``threading.Thread`` runs the pure-logic preload service.
* A ``queue.Queue`` carries :class:`ProgressEvent` objects from worker
  to GUI.
* The dialog drains the queue on the Tk thread via ``after(50)``. All
  mutations of the parent app's ``_full_cache`` (in-process L1) happen
  on the Tk thread so we don't race the chart's read paths.
* A ``threading.Event`` lets the Cancel button interrupt mid-loop;
  combined with the service's ``cancellable_sleep`` the worst-case
  cancel latency is one in-flight HTTP request.

On success the dialog writes a :class:`UniverseManifest` sidecar so a
later sandbox session can reference the universe without re-fetching.
"""

from __future__ import annotations

import datetime as _dt
import queue as _queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import baskets as _baskets
from .. import disk_cache as _disk_cache
from ..models import Candle
from ..preload import manifest as _manifest
from ..preload import service as _service
from ..preload.fundamental_filter import (
    FundamentalFilter,
    is_filter_active,
    passes_fundamental_filter,
)
from ._modal_keys import bind_modal_keys
from .colors import MUTED_GREY

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_INTRADAY_INTERVAL = "5m"
_DEFAULT_INTRADAY_CHOICES: Tuple[str, ...] = ("1m", "2m", "5m", "15m", "30m", "60m")
_DAILY_INTERVAL = "1d"

# ---------------------------------------------------------------------------
# Estimate constants
# ---------------------------------------------------------------------------
# These are deliberately rough — the dialog labels them "≈" / "estimated"
# so a 2x miss on either dimension is acceptable. Calibration source:
# yfinance 5m fetches over SP500 average ~1.2 s / call with a 0.3 s
# inter-call sleep at the default rate limit; 1d fetches average ~0.6 s.
# Disk size is empirical: a 5m bar averages ~120 bytes pickled, a 1d bar
# averages ~110 bytes; intraday history is 60 days × 78 bars = ~4,680
# bars × 120 B = ~560 KB / symbol; daily history is 5 years × 252 bars =
# ~1,260 bars × 110 B = ~140 KB / symbol.
_EST_SECS_PER_INTRADAY_OP = 1.5
_EST_SECS_PER_DAILY_OP = 0.9
_EST_BYTES_PER_INTRADAY_OP = 560_000
_EST_BYTES_PER_DAILY_OP = 140_000

# Per-radio metadata cached at dialog-load time so resolving NYSE /
# NASDAQ size doesn't pay the CSV-parse cost on every keystroke.
_BASKET_SIZE_CACHE: Dict[str, int] = {}


def _basket_size(kind: str) -> int:
    """Return the cardinality of a built-in basket, cached per-process.

    Returns 0 if the basket is unknown or fails to resolve (e.g. CSV
    missing on a stripped-down install). Caller is responsible for
    treating 0 as "size unknown" in the UI.
    """
    if kind in _BASKET_SIZE_CACHE:
        return _BASKET_SIZE_CACHE[kind]
    loader = _baskets.BUILTIN_BASKETS.get(kind)
    if loader is None:
        return 0
    try:
        n = len(loader())
    except Exception:  # noqa: BLE001
        n = 0
    _BASKET_SIZE_CACHE[kind] = n
    return n


def compute_run_estimate(
    *,
    symbol_count: int,
    intervals: Tuple[str, ...],
    daily_interval: str = _DAILY_INTERVAL,
) -> Dict[str, Any]:
    """Pure-function ETA + size estimator for the dialog footer.

    Splits intervals into "daily" (cheap, ~0.6 s/op) and "intraday"
    (more expensive, ~1.2 s/op + rate-limit sleep). Returns a dict
    with ``ops``, ``seconds``, ``bytes``, plus a ready-to-render
    ``label`` string. ``label`` is empty when ``symbol_count == 0``
    (no universe selected yet) so the dialog can blank the line.

    Kept entirely pure so it can be unit-tested without Tk.
    """
    if symbol_count <= 0 or not intervals:
        return {"ops": 0, "seconds": 0.0, "bytes": 0, "label": ""}

    daily_count = sum(1 for i in intervals if i == daily_interval)
    intraday_count = len(intervals) - daily_count

    daily_ops = symbol_count * daily_count
    intraday_ops = symbol_count * intraday_count
    ops = daily_ops + intraday_ops

    seconds = (
        daily_ops * _EST_SECS_PER_DAILY_OP
        + intraday_ops * _EST_SECS_PER_INTRADAY_OP
    )
    bytes_ = (
        daily_ops * _EST_BYTES_PER_DAILY_OP
        + intraday_ops * _EST_BYTES_PER_INTRADAY_OP
    )

    def _fmt_time(s: float) -> str:
        if s < 60:
            return f"{int(s)} s"
        if s < 3600:
            return f"≈{int(round(s / 60))} min"
        h, m = divmod(int(round(s / 60)), 60)
        return f"≈{h} h {m:02d} min"

    def _fmt_size(b: int) -> str:
        if b < 1_000_000:
            return f"{b / 1_000:.0f} KB"
        if b < 1_000_000_000:
            return f"{b / 1_000_000:.0f} MB"
        return f"{b / 1_000_000_000:.1f} GB"

    interval_summary = ", ".join(intervals) if intervals else "—"
    label = (
        f"Estimated: ~{symbol_count} symbols · "
        f"{interval_summary} · {_fmt_time(seconds)} · {_fmt_size(bytes_)}"
    )
    return {
        "ops": ops,
        "seconds": seconds,
        "bytes": bytes_,
        "label": label,
    }


class UniversePrepareDialog(tk.Toplevel):
    """Modal preload dialog. Blocks the caller until closed.

    Args:
        app: parent ``ChartApp``. Used as Toplevel parent and for
            access to ``_full_cache`` and ``_watchlists``. The dialog
            never reaches deeper than these two attributes.
        source_name: the data-source key used by the rest of the app
            (e.g. ``"yfinance"``). Threaded through to the disk cache
            and to the manifest so the cache keys match what a
            session-time read will look up.
        fetcher: ``(ticker, interval) -> Optional[List[Candle]]``
            callable from ``DATA_SOURCES``. Synchronous; injected so
            tests can supply a fake.
        on_finished: optional callback invoked on the Tk thread once
            the worker thread has fully exited and a manifest has
            been written. Receives the saved
            :class:`UniverseManifest` (or ``None`` if the run was
            cancelled / had zero loaded symbols).
    """

    def __init__(
        self,
        app: Any,
        *,
        source_name: str,
        fetcher: Callable[[str, str], Optional[List[Candle]]],
        on_finished: Optional[Callable[[Optional[_manifest.UniverseManifest]], None]] = None,
    ) -> None:
        super().__init__(app)
        self.app = app
        self._source_name = source_name
        self._fetcher = fetcher
        self._on_finished = on_finished

        self.title("Prepare Universe Data")
        self.transient(app)
        # Width is fixed (the form fields are sized in characters), but
        # vertical growth is allowed so users on low-DPI / scaled
        # Windows displays can drag the dialog taller when the Start
        # and Close buttons would otherwise be clipped by the title bar
        # plus the now-larger universe selector (3 LabelFrames + amber
        # survivorship banner + reactive ETA line).
        self.resizable(False, True)
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)
        # Geometry persistence. Default bumped 560x620 -> 560x780 to fit
        # the post-NYSE/NASDAQ universe selector (3 LabelFrames + banner
        # + reactive ETA line) plus the progress + status rows + the
        # Start/Close button row. The persistence key is suffixed with
        # ``_v2`` so users who already have the old 560x620 cached do
        # NOT inherit the too-short window after upgrading.
        try:
            from .geometry_store import attach_persistent_geometry
            attach_persistent_geometry(self, "dlg.universe_prepare_v2", "560x780")
        except tk.TclError:
            pass
        # Hard floor so users can never shrink to the point where the
        # bottom button row falls below the screen.
        try:
            self.minsize(540, 720)
        except tk.TclError:
            pass

        # Worker / queue state. ``_worker`` is None except while a run
        # is in progress.
        self._cancel_event = threading.Event()
        self._event_queue: "_queue.Queue[_service.ProgressEvent]" = _queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._poll_after_id: Optional[str] = None
        self._saved_manifest: Optional[_manifest.UniverseManifest] = None

        # The plan (symbols + intervals + manifest meta) for the run
        # in progress. Captured at Start so a Watchlist rename
        # mid-preload doesn't desync the manifest.
        self._run_plan: Optional[Dict[str, Any]] = None
        # When a fundamental filter pre-pass runs, this tracks the
        # number of symbols that matched. ``-1`` means "no filter
        # was applied this run". Used by ``_on_worker_finished`` to
        # surface "filter matched 0" with the right phrasing.
        self._filter_matched_count: int = -1

        # ---- Tk variables ----
        self._kind_var = tk.StringVar(value="sp500")
        self._wl_var = tk.StringVar(value="")
        self._intraday_var = tk.StringVar(value=_DEFAULT_INTRADAY_INTERVAL)
        self._include_daily_var = tk.BooleanVar(value=True)
        self._status_var = tk.StringVar(value="Pick a universe and press Start.")

        # Fundamental-filter form vars. All Entry-backed StringVars so
        # an empty string means "no constraint on this dimension".
        # ``_resolve_plan`` parses these into an immutable
        # :class:`FundamentalFilter` snapshot; the worker pre-pass uses
        # the snapshot, never the live vars (so a user edit during a
        # run can't corrupt in-flight filter logic).
        self._flt_min_vol_var = tk.StringVar(value="")
        self._flt_min_close_var = tk.StringVar(value="")
        self._flt_max_close_var = tk.StringVar(value="")
        self._flt_lookback_var = tk.StringVar(value="20")

        self._build_ui()
        self._refresh_kind_specific_state()
        self._center_on_parent()

        # Modal grab AFTER the window is realised so geometry is sane.
        self.update_idletasks()
        self.grab_set()
        bind_modal_keys(
            self,
            cancel=self._on_close_request,
            primary=self._on_start,
        )

    # ---- result accessor ----

    @property
    def result(self) -> Optional[_manifest.UniverseManifest]:
        """Manifest written on success, or None if cancelled / failed."""
        return self._saved_manifest

    # -----------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")

        # --- Universe selector ----------------------------------------
        # 3 grouped LabelFrames per UX agent guidance:
        #   * Index constituents (S&P 500, QQQ)
        #   * Full exchange listings (NYSE, NASDAQ) — show amber
        #     survivorship banner when one of these is selected
        #   * Custom (Watchlist)
        # Each radio carries a "refreshed YYYY-MM-DD" suffix sourced
        # from baskets.BUILTIN_BASKET_REFRESHED_DATES so users can see
        # how stale their snapshot is at a glance.
        uni_outer = ttk.Frame(outer)
        uni_outer.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 8))

        # Index group
        idx_frame = ttk.LabelFrame(uni_outer, text="Index constituents", padding=8)
        idx_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 4))

        ttk.Radiobutton(
            idx_frame,
            text=f"S&P 500 — ~{_basket_size('sp500')} symbols · curated CSV",
            variable=self._kind_var, value="sp500",
            command=self._refresh_kind_specific_state,
        ).grid(row=0, column=0, sticky="w")

        qqq_refreshed = _baskets.BUILTIN_BASKET_REFRESHED_DATES.get("qqq", "")
        ttk.Radiobutton(
            idx_frame,
            text=(
                f"Nasdaq-100 (QQQ) — ~{_basket_size('qqq')} symbols · "
                f"refreshed {qqq_refreshed}"
            ),
            variable=self._kind_var, value="qqq",
            command=self._refresh_kind_specific_state,
        ).grid(row=1, column=0, sticky="w")

        # Full exchange group
        ex_frame = ttk.LabelFrame(uni_outer, text="Full exchange listings", padding=8)
        ex_frame.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 4))

        nyse_refreshed = _baskets.BUILTIN_BASKET_REFRESHED_DATES.get("nyse", "")
        ttk.Radiobutton(
            ex_frame,
            text=(
                f"NYSE — all common stocks (~{_basket_size('nyse')} symbols) · "
                f"refreshed {nyse_refreshed}"
            ),
            variable=self._kind_var, value="nyse",
            command=self._refresh_kind_specific_state,
        ).grid(row=0, column=0, sticky="w")

        nasdaq_refreshed = _baskets.BUILTIN_BASKET_REFRESHED_DATES.get("nasdaq", "")
        ttk.Radiobutton(
            ex_frame,
            text=(
                f"NASDAQ — all common stocks (~{_basket_size('nasdaq')} symbols) · "
                f"refreshed {nasdaq_refreshed}"
            ),
            variable=self._kind_var, value="nasdaq",
            command=self._refresh_kind_specific_state,
        ).grid(row=1, column=0, sticky="w")

        # Amber survivorship banner, only visible when a full-exchange
        # basket is selected. Lives inside ex_frame so it visually
        # belongs to the offending radios.
        self._survivorship_banner = tk.Label(
            ex_frame,
            text=(
                "⚠ Survivorship caveat: this snapshot is point-in-time. "
                "Replays anchored on past dates will miss companies that "
                "have since delisted, merged, or been acquired (and will "
                "include any that listed since the snapshot date)."
            ),
            fg="#a86b00",  # amber
            justify="left",
            wraplength=440,
            font=("TkDefaultFont", 9, "italic"),
        )
        # Don't grid yet — _refresh_kind_specific_state controls visibility.

        # Custom group
        cust_frame = ttk.LabelFrame(uni_outer, text="Custom", padding=8)
        cust_frame.grid(row=2, column=0, sticky="ew")

        ttk.Radiobutton(
            cust_frame, text="Watchlist:",
            variable=self._kind_var, value="watchlist",
            command=self._refresh_kind_specific_state,
        ).grid(row=0, column=0, sticky="w")
        self._wl_combo = ttk.Combobox(
            cust_frame, textvariable=self._wl_var,
            state="readonly", width=28, values=self._available_watchlists(),
        )
        self._wl_combo.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._wl_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._refresh_estimate_label(),
        )

        # --- Fundamental filter (optional) ---------------------------
        # Leave every field blank to disable. When any field is set,
        # the worker fetches 1d bars for every superset symbol and
        # drops those that don't meet the criteria BEFORE the regular
        # intraday preload runs. See
        # :func:`tradinglab.preload.fundamental_filter.passes_fundamental_filter`.
        flt_frame = ttk.LabelFrame(
            outer, text="Optional fundamental filter (leave blank to skip)",
            padding=8,
        )
        flt_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(flt_frame, text="Min avg volume (millions):").grid(
            row=0, column=0, sticky="w",
        )
        ttk.Entry(
            flt_frame, textvariable=self._flt_min_vol_var, width=10,
        ).grid(row=0, column=1, sticky="w", padx=(8, 16))

        ttk.Label(flt_frame, text="Lookback (trading days):").grid(
            row=0, column=2, sticky="w",
        )
        ttk.Spinbox(
            flt_frame, textvariable=self._flt_lookback_var,
            from_=1, to=252, width=6, increment=1,
        ).grid(row=0, column=3, sticky="w", padx=(8, 0))

        ttk.Label(flt_frame, text="Min close ($):").grid(
            row=1, column=0, sticky="w", pady=(6, 0),
        )
        ttk.Entry(
            flt_frame, textvariable=self._flt_min_close_var, width=10,
        ).grid(row=1, column=1, sticky="w", padx=(8, 16), pady=(6, 0))

        ttk.Label(flt_frame, text="Max close ($):").grid(
            row=1, column=2, sticky="w", pady=(6, 0),
        )
        ttk.Entry(
            flt_frame, textvariable=self._flt_max_close_var, width=10,
        ).grid(row=1, column=3, sticky="w", padx=(8, 0), pady=(6, 0))

        flt_hint = (
            "Example: 10 / 80 / — / 20 → keep symbols whose 20-day "
            "average daily volume is ≥ 10M shares AND whose latest "
            "close is ≥ $80. Filter uses cached daily bars when "
            "available; otherwise fetches them in parallel."
        )
        ttk.Label(
            flt_frame, text=flt_hint, foreground=MUTED_GREY,
            wraplength=510, justify="left",
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        # --- Interval selector ---------------------------------------
        itv_frame = ttk.LabelFrame(outer, text="Intervals", padding=8)
        itv_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        ttk.Label(itv_frame, text="Primary intraday:").grid(row=0, column=0, sticky="w")
        intraday_combo = ttk.Combobox(
            itv_frame, textvariable=self._intraday_var,
            state="readonly", width=8,
            values=list(_DEFAULT_INTRADAY_CHOICES),
        )
        intraday_combo.grid(row=0, column=1, sticky="w", padx=(8, 0))
        # Reactive recompute on any combobox/radio/checkbutton change.
        intraday_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._refresh_estimate_label(),
        )

        ttk.Checkbutton(
            itv_frame, text="Also preload 1d (daily) bars",
            variable=self._include_daily_var,
            command=self._refresh_estimate_label,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # Reactive ETA / size label, sits just below intervals. Lives
        # in its own slim frame so its grid row is independent of the
        # intervals layout.
        self._estimate_var = tk.StringVar(value="")
        ttk.Label(
            outer, textvariable=self._estimate_var,
            foreground="#444", wraplength=510, justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(0, 8))

        # --- Progress + status ---------------------------------------
        prog_frame = ttk.Frame(outer)
        prog_frame.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        prog_frame.columnconfigure(0, weight=1)

        self._progress = ttk.Progressbar(
            prog_frame, mode="determinate", maximum=1, value=0,
        )
        self._progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            prog_frame, textvariable=self._status_var, foreground="#444",
            wraplength=440, justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(4, 0))

        # --- Buttons --------------------------------------------------
        btn_frame = ttk.Frame(outer)
        btn_frame.grid(row=5, column=0, sticky="e")
        self._start_btn = ttk.Button(btn_frame, text="Start", command=self._on_start)
        self._start_btn.grid(row=0, column=0, padx=(0, 6))
        self._cancel_btn = ttk.Button(btn_frame, text="Close", command=self._on_close_request)
        self._cancel_btn.grid(row=0, column=1)

    def _available_watchlists(self) -> List[str]:
        wm = getattr(self.app, "_watchlists", None)
        if wm is None:
            return []
        try:
            return [n for n in wm.list_names() if (wm.get(n) and wm.get(n).tickers)]
        except Exception:  # noqa: BLE001
            return []

    def _refresh_kind_specific_state(self) -> None:
        kind = self._kind_var.get()
        is_watchlist = kind == "watchlist"
        self._wl_combo.configure(state=("readonly" if is_watchlist else "disabled"))
        if is_watchlist and not self._wl_var.get():
            opts = self._available_watchlists()
            if opts:
                self._wl_var.set(opts[0])
        # Survivorship banner is only shown for full-exchange baskets.
        try:
            in_full_exchange = kind in _baskets.FULL_EXCHANGE_BASKETS
        except AttributeError:  # legacy baskets module
            in_full_exchange = False
        if in_full_exchange:
            self._survivorship_banner.grid(
                row=2, column=0, sticky="w", pady=(6, 0),
            )
        else:
            self._survivorship_banner.grid_forget()
        self._refresh_estimate_label()

    def _refresh_estimate_label(self) -> None:
        """Recompute the ETA/size line based on current selections.

        Called whenever the universe radio, watchlist combobox,
        intraday combobox, or daily checkbox changes. Pure work
        happens in :func:`compute_run_estimate`; this wrapper only
        does the Tk-StringVar update.
        """
        try:
            kind = self._kind_var.get()
            if kind == "watchlist":
                wl_name = (self._wl_var.get() or "").strip()
                wm = getattr(self.app, "_watchlists", None)
                wl = wm.get(wl_name) if (wm is not None and wl_name) else None
                count = len(wl.tickers) if wl and wl.tickers else 0
            else:
                count = _basket_size(kind)
            intraday = (self._intraday_var.get() or "").strip()
            intervals: List[str] = []
            if intraday:
                intervals.append(intraday)
            if self._include_daily_var.get() and _DAILY_INTERVAL not in intervals:
                intervals.append(_DAILY_INTERVAL)
            est = compute_run_estimate(
                symbol_count=count, intervals=tuple(intervals),
            )
            self._estimate_var.set(est["label"])
        except Exception:  # noqa: BLE001
            # Estimate is purely informational; never let a labelling
            # error break the dialog.
            self._estimate_var.set("")

    def _center_on_parent(self) -> None:
        try:
            self.update_idletasks()
            px = self.app.winfo_rootx()
            py = self.app.winfo_rooty()
            pw = self.app.winfo_width()
            ph = self.app.winfo_height()
            ww = self.winfo_width() or 480
            wh = self.winfo_height() or 360
            x = px + max(0, (pw - ww) // 2)
            y = py + max(0, (ph - wh) // 2)
            self.geometry(f"+{x}+{y}")
        except Exception:  # noqa: BLE001
            pass

    # -----------------------------------------------------------------
    # Resolve plan
    # -----------------------------------------------------------------

    def _resolve_plan(self) -> Optional[Dict[str, Any]]:
        """Translate the form into a runnable plan.

        Returns a dict ``{uid, name, kind, source, intervals, symbols,
        filter}`` or ``None`` (with status set) if the form is
        invalid. ``filter`` is a :class:`FundamentalFilter` (or
        ``None`` when every form field is blank).
        """
        kind = self._kind_var.get()
        intraday = (self._intraday_var.get() or "").strip()
        if not intraday:
            self._status_var.set("Pick a primary intraday interval.")
            return None

        # Parse the fundamental-filter form. Blank = no constraint.
        # Negative / non-numeric input -> per-field complaint via
        # status; the rest of the form survives so the user can fix
        # one field without losing the others.
        flt_spec = self._parse_filter_form()
        if flt_spec is None:
            return None  # _parse_filter_form already set the status

        intervals: List[str] = [intraday]
        if self._include_daily_var.get() and _DAILY_INTERVAL not in intervals:
            intervals.append(_DAILY_INTERVAL)
        # When the fundamental filter is active we must fetch 1d bars
        # for the pre-pass anyway. Forcing-include daily here means
        # those fetched bars get persisted into the manifest for
        # free (no extra network spend, big sandbox usability win).
        if is_filter_active(flt_spec) and _DAILY_INTERVAL not in intervals:
            intervals.append(_DAILY_INTERVAL)

        if kind == "watchlist":
            wl_name = (self._wl_var.get() or "").strip()
            wm = getattr(self.app, "_watchlists", None)
            wl = wm.get(wl_name) if (wm is not None and wl_name) else None
            if not wl or not wl.tickers:
                self._status_var.set("Pick a non-empty watchlist.")
                return None
            uid = f"watchlist:{wl_name}"
            display = f"Watchlist: {wl_name}"
            symbols = list(wl.tickers)
        elif kind in _baskets.BUILTIN_BASKETS:
            try:
                symbols = _baskets.BUILTIN_BASKETS[kind]()
            except Exception as exc:  # noqa: BLE001
                self._status_var.set(f"Failed to resolve basket: {exc}")
                return None
            if not symbols:
                self._status_var.set(f"Basket '{kind}' resolved to empty list.")
                return None
            uid = kind
            display = _baskets.BUILTIN_BASKET_LABELS.get(kind, kind)
        else:
            self._status_var.set(f"Unknown universe kind: {kind!r}.")
            return None

        # De-dupe while preserving order.
        seen: set = set()
        deduped: List[str] = []
        for s in symbols:
            s2 = (s or "").strip().upper()
            if s2 and s2 not in seen:
                seen.add(s2)
                deduped.append(s2)

        return {
            "uid": uid,
            "name": display,
            "kind": ("basket" if kind != "watchlist" else "watchlist"),
            "source": self._source_name,
            "intervals": tuple(intervals),
            "symbols": tuple(deduped),
            "filter": flt_spec if is_filter_active(flt_spec) else None,
        }

    def _parse_filter_form(self) -> Optional[FundamentalFilter]:
        """Build a :class:`FundamentalFilter` from the four StringVars.

        Returns ``None`` (with status set) on parse failure.
        Otherwise returns a (possibly all-None) spec — caller uses
        :func:`is_filter_active` to know whether to run the pre-pass.
        """
        def _opt_float(raw: str, label: str) -> Tuple[bool, Optional[float]]:
            s = (raw or "").strip()
            if not s:
                return (True, None)
            try:
                v = float(s)
            except ValueError:
                self._status_var.set(f"{label} must be a number (or blank).")
                return (False, None)
            if v < 0:
                self._status_var.set(f"{label} must be ≥ 0.")
                return (False, None)
            return (True, v)

        ok, min_vol = _opt_float(self._flt_min_vol_var.get(), "Min avg volume")
        if not ok:
            return None
        ok, min_close = _opt_float(self._flt_min_close_var.get(), "Min close")
        if not ok:
            return None
        ok, max_close = _opt_float(self._flt_max_close_var.get(), "Max close")
        if not ok:
            return None

        if (min_close is not None and max_close is not None
                and min_close > max_close):
            self._status_var.set("Min close must be ≤ Max close.")
            return None

        lookback_raw = (self._flt_lookback_var.get() or "20").strip()
        try:
            lookback = int(float(lookback_raw))
        except ValueError:
            self._status_var.set("Lookback (trading days) must be an integer.")
            return None
        if lookback < 1:
            lookback = 1

        return FundamentalFilter(
            min_avg_volume_millions=min_vol,
            min_close=min_close,
            max_close=max_close,
            lookback_days=lookback,
        )

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------

    def _on_start(self) -> None:
        if self._worker is not None:
            return
        plan = self._resolve_plan()
        if plan is None:
            return
        self._run_plan = plan
        self._cancel_event = threading.Event()
        # Drain any stale events from a prior aborted attempt.
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except _queue.Empty:
                break

        # Lock the form, swap Close -> Stop (safe to resume).
        # The "safe to resume" framing is critical for full-exchange
        # universes: the user MUST know that stopping at, say,
        # symbol 800/2400 is non-destructive — disk_cache writes
        # already happened, the manifest is unioned with any prior
        # run, and a fresh Start picks up exactly where Stop left off
        # via the disk-cache short-circuit (`l1_hit`/`disk_hit`).
        self._set_form_enabled(False)
        self._cancel_btn.configure(
            text="Stop (safe to resume)", command=self._on_cancel,
        )

        # Initial status / progress reflects the *unfiltered* total
        # for the regular preload phase. If a filter pre-pass runs,
        # _on_filter_phase_done resets these once the matched set is
        # known (and per-filter-bar progress runs against a separate
        # max emitted via _FilterPhaseStart).
        flt = plan.get("filter")
        total = len(plan["symbols"]) * len(plan["intervals"])
        self._filter_matched_count = -1  # reset for this run
        if flt is not None:
            # Initial bar reflects the filter pre-pass total; the
            # preload phase resets it on _FilterPhaseDone.
            self._progress.configure(maximum=max(1, len(plan["symbols"])), value=0)
            self._status_var.set(
                f"Filtering {len(plan['symbols'])} symbols by fundamentals "
                f"(min_vol={flt.min_avg_volume_millions}M, "
                f"min_close={flt.min_close}, max_close={flt.max_close}, "
                f"lookback={flt.lookback_days})…"
            )
        else:
            self._progress.configure(maximum=max(1, total), value=0)
            self._status_var.set(
                f"Preloading {len(plan['symbols'])} symbols × "
                f"{len(plan['intervals'])} intervals "
                f"({total} ops) from {plan['source']}…"
            )
        self._saved_manifest = None

        self._worker = threading.Thread(
            target=self._worker_main,
            args=(plan,),
            daemon=True,
            name=f"preload-{plan['uid']}",
        )
        self._worker.start()
        self._poll_after_id = self.after(50, self._drain_events)

    def _worker_main(self, plan: Dict[str, Any]) -> None:
        """Runs on the worker thread. Pure I/O + queue.put — no Tk."""
        def _emit(ev: _service.ProgressEvent) -> None:
            self._event_queue.put(ev)

        # ---- Phase 1: optional fundamental filter pre-pass ---------
        flt: Optional[FundamentalFilter] = plan.get("filter")
        symbols: Tuple[str, ...] = plan["symbols"]
        if flt is not None and is_filter_active(flt):
            matched = self._run_filter_prepass(symbols, plan["source"], flt)
            if self._cancel_event.is_set():
                # User-cancelled mid-filter; piggy-back on the
                # service's PreloadResult shape by emitting an empty
                # _PreloadDone. The Tk side will set "cancelled"
                # status via the normal worker-finished path.
                self._event_queue.put(_PreloadDone(result=_service.PreloadResult(
                    intervals=plan["intervals"],
                    per_symbol={s: [] for s in symbols},
                    cancelled=True,
                )))
                return
            self._event_queue.put(_FilterPhaseDone(
                matched_symbols=matched,
                total=len(symbols),
            ))
            symbols = tuple(matched)
            if not symbols:
                # Zero matched: short-circuit the preload, surface
                # an empty result so _on_worker_finished can emit a
                # clean "0 symbols matched the filter" status.
                self._event_queue.put(_PreloadDone(result=_service.PreloadResult(
                    intervals=plan["intervals"],
                    per_symbol={},
                    cancelled=False,
                )))
                return

        # ---- Phase 2: regular preload ------------------------------
        try:
            result = _service.preload_universe(
                list(symbols),
                list(plan["intervals"]),
                source_name=plan["source"],
                fetcher=self._fetcher,
                cache_load=_disk_cache.load,
                cache_save=_disk_cache.save,
                merge=_disk_cache.merge_candles,
                cancel_event=self._cancel_event,
                progress_cb=_emit,
                # NOTE: l1_check is intentionally None. The L1 cache
                # belongs to the Tk thread; reading it from the worker
                # would race with chart fetches. We accept one extra
                # disk_cache.load() per symbol — it's pickle-fast.
                l1_check=None,
            )
        except Exception as exc:  # noqa: BLE001
            # Surface as a synthetic finish event the poller can handle.
            self._event_queue.put(_service.ProgressEvent(
                kind="finish", error=f"worker crashed: {exc!r}",
            ))
            return
        # Stash the full result so the poller can build the manifest.
        # We piggy-back on the queue with a sentinel kind so ordering
        # against the in-flight 'finish' event from preload_universe
        # itself stays clean.
        self._event_queue.put(_PreloadDone(result=result))

    def _run_filter_prepass(
        self,
        symbols: Tuple[str, ...],
        source: str,
        spec: FundamentalFilter,
    ) -> List[str]:
        """Parallel-fetch 1d bars per symbol and apply the filter.

        Cache-first: disk_cache.load(...) hits avoid the network. On a
        warm cache the pre-pass for SP500 is sub-second; on a cold
        cache it pays the cost of 500 daily-bar fetches against the
        live fetcher with up to 4 concurrent workers (mirrors
        ``data/parallel.fetch_chunks_parallel`` semantics).

        Cancellation is checked between every symbol fetch; the
        in-flight HTTP request (if any) cannot be interrupted but
        the loop exits on the next iteration.

        Returns the list of symbols that pass the filter, in input
        order.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        matched: List[str] = []
        pass_map: Dict[str, bool] = {}
        total = len(symbols)
        max_workers = max(1, min(4, total))

        def _check_one(sym: str) -> Tuple[str, bool]:
            if self._cancel_event.is_set():
                return (sym, False)
            bars: Optional[List[Candle]] = None
            try:
                bars = _disk_cache.load(source, sym, _DAILY_INTERVAL)
            except Exception:  # noqa: BLE001
                bars = None
            if not bars:
                # Cold cache: fall back to the live fetcher.
                try:
                    bars = self._fetcher(sym, _DAILY_INTERVAL)
                except Exception:  # noqa: BLE001
                    bars = None
                if bars:
                    # Persist for the regular preload phase (and
                    # future runs) — daily bars are cheap to store.
                    try:
                        _disk_cache.save(source, sym, _DAILY_INTERVAL, bars)
                    except Exception:  # noqa: BLE001
                        pass
            if not bars:
                return (sym, False)
            return (sym, passes_fundamental_filter(bars, spec))

        # Phase-start sentinel lets the Tk side reset the progress
        # bar's maximum to the pre-pass count BEFORE the first
        # per-symbol event arrives.
        self._event_queue.put(_FilterPhaseStart(total=total))

        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="prepass",
        ) as pool:
            futures = [pool.submit(_check_one, s) for s in symbols]
            completed = 0
            for fut in as_completed(futures):
                if self._cancel_event.is_set():
                    # Best-effort: any unstarted futures will short-
                    # circuit at the top of _check_one. We don't
                    # cancel running futures (we'd lose disk_cache.save
                    # side effects for in-flight responses).
                    pass
                try:
                    sym, ok = fut.result()
                except Exception:  # noqa: BLE001
                    continue
                pass_map[sym] = ok
                completed += 1
                self._event_queue.put(_FilterPhaseProgress(
                    index=completed - 1,
                    total=total,
                    symbol=sym,
                    passed=ok,
                ))

        # Rebuild matched in input order so the manifest's symbol
        # listing stays deterministic.
        for sym in symbols:
            if pass_map.get(sym, False):
                matched.append(sym)
        return matched

    def _drain_events(self) -> None:
        """Drain ``_event_queue`` on the Tk thread and update widgets."""
        self._poll_after_id = None
        try:
            for _ in range(200):  # cap per tick so the UI stays responsive
                try:
                    ev = self._event_queue.get_nowait()
                except _queue.Empty:
                    break
                self._handle_event(ev)
        finally:
            if self._worker is not None and self._worker.is_alive():
                self._poll_after_id = self.after(50, self._drain_events)
            elif not self._event_queue.empty():
                # Worker exited but we haven't drained final events yet.
                self._poll_after_id = self.after(20, self._drain_events)

    def _handle_event(self, ev: Any) -> None:
        if isinstance(ev, _PreloadDone):
            self._on_worker_finished(ev.result)
            return
        if isinstance(ev, _FilterPhaseStart):
            # Reset the progress bar to track filter pre-pass per-
            # symbol completions (1 unit per symbol).
            self._progress.configure(maximum=max(1, ev.total), value=0)
            return
        if isinstance(ev, _FilterPhaseProgress):
            self._progress.configure(value=ev.index + 1)
            tag = "kept" if ev.passed else "dropped"
            pct = int(100 * (ev.index + 1) / max(1, ev.total))
            self._status_var.set(
                f"[filter {pct}%] {ev.symbol} → {tag}"
            )
            return
        if isinstance(ev, _FilterPhaseDone):
            plan = self._run_plan
            self._filter_matched_count = len(ev.matched_symbols)
            ops = len(ev.matched_symbols) * (len(plan["intervals"]) if plan else 1)
            self._progress.configure(maximum=max(1, ops), value=0)
            source = plan["source"] if plan else "?"
            self._status_var.set(
                f"Filter passed {len(ev.matched_symbols)} / {ev.total}. "
                f"Preloading {len(ev.matched_symbols)} symbols × "
                f"{len(plan['intervals']) if plan else 0} intervals "
                f"({ops} ops) from {source}…"
            )
            return
        if not isinstance(ev, _service.ProgressEvent):
            return
        if ev.kind == "start":
            return  # already set up at _on_start
        if ev.kind == "symbol":
            self._progress.configure(value=ev.index + 1)
            self._update_l1_for(ev)
            self._status_var.set(self._format_symbol_line(ev))
            return
        if ev.kind == "finish":
            # The service's own finish event arrives slightly before
            # our _PreloadDone sentinel. We wait for the sentinel to
            # finalize state.
            if ev.error:
                self._status_var.set(f"Worker error: {ev.error}")
            return

    def _format_symbol_line(self, ev: _service.ProgressEvent) -> str:
        total = max(1, ev.total or self._progress["maximum"])
        pct = int(100 * (ev.index + 1) / total)
        tag = ev.status
        suffix = ""
        if ev.status == "failed" and ev.error:
            suffix = f"  [{ev.error[:80]}]"
        return f"[{pct}%] {ev.symbol}/{ev.interval} → {tag} ({ev.bars} bars){suffix}"

    def _update_l1_for(self, ev: _service.ProgressEvent) -> None:
        """Mirror just-loaded bars into ``app._full_cache`` (Tk thread).

        We touch L1 only on disk_hit / fetched outcomes (l1_hit means
        the bars were already there). Reading from disk_cache here is
        cheap and avoids any cross-thread mutation of ``_full_cache``.
        """
        if ev.status not in ("disk_hit", "fetched"):
            return
        if ev.bars <= 0:
            return
        plan = self._run_plan
        if plan is None:
            return
        full_cache = getattr(self.app, "_full_cache", None)
        if full_cache is None:
            return
        try:
            bars = _disk_cache.load(plan["source"], ev.symbol, ev.interval)
        except Exception:  # noqa: BLE001
            return
        if not bars:
            return
        key = (plan["source"], ev.symbol, ev.interval)
        try:
            full_cache[key] = bars
        except Exception:  # noqa: BLE001
            pass
        # Best-effort LRU pressure relief if the host exposes it.
        trim = getattr(self.app, "_trim_full_cache", None)
        if callable(trim):
            try:
                trim()
            except Exception:  # noqa: BLE001
                pass

    def _on_worker_finished(self, result: _service.PreloadResult) -> None:
        """Final state transition on the Tk thread after worker exit."""
        self._worker = None
        plan = self._run_plan
        self._run_plan = None

        # Re-enable form, swap Cancel back to Close.
        self._set_form_enabled(True)
        self._cancel_btn.configure(text="Close", command=self._on_close_request)

        if plan is None:
            self._status_var.set("Preload finished (no plan).")
            self._notify_finished(None)
            return

        per_loaded = result.loaded_per_symbol()
        non_empty = {sym: itvs for sym, itvs in per_loaded.items() if itvs}

        if not non_empty:
            self._progress.configure(value=0)
            if result.cancelled:
                self._status_var.set("Cancelled before any symbols persisted. No manifest written.")
            elif plan.get("filter") is not None and self._filter_matched_count == 0:
                # The filter pre-pass dropped every symbol. Be explicit
                # so the user doesn't think the preload itself failed.
                self._status_var.set(
                    "Fundamental filter matched 0 symbols. "
                    "Loosen the criteria and try again. No manifest written."
                )
            else:
                self._status_var.set("Preload finished but zero symbols persisted. No manifest written.")
            self._notify_finished(None)
            return

        # Build + persist manifest. Union with any prior manifest of
        # the same UID so re-running with a smaller interval set does
        # not silently drop previously-loaded bars (the disk_cache
        # pickles still exist; the manifest must continue to claim
        # coverage for them).
        try:
            previous = _manifest.load(plan["uid"])
            man = _manifest.build_from_loaded(
                uid=plan["uid"],
                name=plan["name"],
                kind=plan["kind"],
                source=plan["source"],
                intervals=plan["intervals"],
                per_symbol=per_loaded,
                previous=previous,
            )
            _manifest.save(man)
            self._saved_manifest = man
        except Exception as exc:  # noqa: BLE001
            self._status_var.set(f"Loaded {len(non_empty)} symbols but manifest save failed: {exc}")
            self._notify_finished(None)
            return

        failed = result.failed()
        head = "Canceled" if result.cancelled else "Done"
        self._status_var.set(
            f"{head}. {len(non_empty)} / {len(plan['symbols'])} symbols persisted. "
            f"{len(failed)} interval failures. Manifest saved to '{plan['uid']}'."
        )
        self._progress.configure(value=self._progress["maximum"])
        self._notify_finished(self._saved_manifest)

    def _notify_finished(self, man: Optional[_manifest.UniverseManifest]) -> None:
        if self._on_finished is None:
            return
        try:
            self._on_finished(man)
        except Exception:  # noqa: BLE001
            pass

    def _set_form_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        ro = "readonly" if enabled else "disabled"
        self._start_btn.configure(state=state)
        # Walk children of the two LabelFrames and disable them. Keep
        # the Cancel button always enabled (it's our cancel trigger).
        for child in self.winfo_children():
            self._set_state_recursive(child, enabled, ro)
        # Cancel button is always usable.
        self._cancel_btn.configure(state="normal")

    def _set_state_recursive(self, widget: tk.Misc, enabled: bool, ro_state: str) -> None:
        kids = widget.winfo_children()
        for kid in kids:
            self._set_state_recursive(kid, enabled, ro_state)
        if widget is self._cancel_btn:
            return
        try:
            if isinstance(widget, ttk.Combobox):
                if not enabled:
                    widget.configure(state="disabled")
                else:
                    # Watchlist combobox should only re-enable when
                    # Watchlist radio is selected.
                    if widget is self._wl_combo and self._kind_var.get() != "watchlist":
                        widget.configure(state="disabled")
                    else:
                        widget.configure(state=ro_state)
            elif isinstance(widget, (ttk.Radiobutton, ttk.Checkbutton, ttk.Button, ttk.Entry, ttk.Spinbox)):
                widget.configure(state=("normal" if enabled else "disabled"))
        except tk.TclError:
            pass

    def _on_cancel(self) -> None:
        if self._worker is None:
            return
        self._cancel_event.set()
        # Match the "Stop (safe to resume)" button framing — the user
        # needs to know the in-flight HTTP request will finish, the
        # bars already on disk are intact, and a fresh Start will
        # resume from the disk_cache short-circuit at the next symbol.
        self._status_var.set(
            "Stopping after current symbol — bars already on disk are "
            "safe; press Start again to resume from where this stopped."
        )
        self._cancel_btn.configure(state="disabled")

    def _on_close_request(self) -> None:
        if self._worker is not None:
            # In progress: treat close as cancel. User must press Close
            # again after the worker exits.
            self._on_cancel()
            return
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:  # noqa: BLE001
                pass
            self._poll_after_id = None
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


# ---------------------------------------------------------------------------
# Internal sentinel: lets us deliver the final PreloadResult through the
# same queue that carries ProgressEvents, preserving event ordering.
# ---------------------------------------------------------------------------


class _PreloadDone:
    __slots__ = ("result",)

    def __init__(self, *, result: _service.PreloadResult) -> None:
        self.result = result


class _FilterPhaseStart:
    """Tk-side cue to reset the progress-bar max for the filter pre-pass."""

    __slots__ = ("total",)

    def __init__(self, *, total: int) -> None:
        self.total = int(total)


class _FilterPhaseProgress:
    """One symbol's filter outcome reported back to the Tk thread."""

    __slots__ = ("index", "total", "symbol", "passed")

    def __init__(self, *, index: int, total: int, symbol: str, passed: bool) -> None:
        self.index = int(index)
        self.total = int(total)
        self.symbol = str(symbol)
        self.passed = bool(passed)


class _FilterPhaseDone:
    """Filter pre-pass is over; Tk should reset progress-bar max for the
    regular preload phase and surface a "X of Y matched" status."""

    __slots__ = ("matched_symbols", "total")

    def __init__(self, *, matched_symbols: List[str], total: int) -> None:
        self.matched_symbols = list(matched_symbols)
        self.total = int(total)
