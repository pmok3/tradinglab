"""Modal dialogs for the sandbox subsystem.

* :class:`SandboxStartDialog` — Phase 1c-redux: open-universe start
  dialog. The user picks a session date + interval (no ticker
  selection — the universe is fully open during the session). A
  "Random eligible date" button stamps the date entry with a date
  drawn from the reference-ticker's eligible-day pool.
* :class:`PreTradeFormDialog` — mandatory journal form on every order.
  Refuses Submit if thesis or size is missing/invalid.

Both are ``tk.Toplevel`` modals using ``transient`` + ``grab_set`` so
the rest of the UI is locked while open. ``wait_window`` blocks the
caller; when it returns, ``self.result`` is either a payload dict or
``None`` (cancelled).
"""

from __future__ import annotations

import datetime as _dt
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from ._modal_keys import bind_modal_keys


class SandboxStartDialog(tk.Toplevel):
    """Modal: configure and start an open-universe sandbox session.

    The dialog DOES NOT pick tickers. The reference symbol (master
    clock anchor) is supplied by the caller; tradeable tickers are
    loaded mid-session via the regular ticker entry / watchlist.

    Args:
        app: parent ``ChartApp`` (used for transience + grab).
        reference_symbol: display-only label (e.g. "SPY") so the user
            knows what anchors the timeline.
        intervals: ordered list of valid interval strings (e.g.
            ``["1m", "5m", "15m"]``); first is the default.
        eligible_dates_provider: callable
            ``(interval) -> sorted_eligible_dates`` that returns the
            eligibility list at a given interval. Invoked on dialog
            open and on every interval change. An empty list means
            the reference symbol isn't cached at this interval; the
            dialog will trigger ``fetch_provider`` (if supplied) to
            populate the cache lazily.
        fetch_provider: optional callable ``(interval) -> bool`` that
            sync-fetches the reference symbol at the given interval
            and stores it in the host's cache. Returns True on
            success. Invoked when ``eligible_dates_provider`` comes
            back empty (on interval change, Random click, or
            Blind+Start) so Random / Blind aren't stranded by an
            empty cache. The dialog disables controls + shows a
            "Fetching..." status during the call. If absent, the
            caller is responsible for fetching elsewhere.
        default_interval: pre-select this interval in the combobox
            instead of ``intervals[0]``. Useful so the dialog opens
            on the same interval the user is already charting (the
            cache is most likely populated for that one).
    """

    def __init__(
        self,
        app: Any,
        *,
        reference_symbol: str,
        intervals: list[str],
        eligible_dates_provider: Callable[[str], list[_dt.date]],
        fetch_provider: Callable[[str], bool] | None = None,
        default_interval: str | None = None,
        default_selected_intervals: list[str] | None = None,
        manifest_provider: Callable[[], list[Any]] | None = None,
    ):
        super().__init__(app)
        self.app = app
        self.reference_symbol = reference_symbol
        self._intervals = list(intervals) or ["5m"]
        self._eligible_provider = eligible_dates_provider
        self._fetch_provider = fetch_provider
        # Phase: sandbox universe / strict-offline. ``manifest_provider``
        # returns the prepared :class:`UniverseManifest` list (most
        # recent first). Sentinel ``None`` is permitted for callers
        # that haven't been wired yet (e.g. legacy tests) — the dialog
        # then offers only the "(none)" option.
        self._manifest_provider = manifest_provider
        self._manifests: list[Any] = []
        self._refresh_manifests()
        # Phase 1d-multitf-2: multi-select intervals. The smallest
        # checked interval becomes the primary tick interval; larger
        # checked intervals are upscaled (aggregated) from primary
        # bars on the fly during the session. Default-checked set
        # may be overridden by ``default_selected_intervals`` (the
        # host can prefer the chart's current interval to keep the
        # opening fetch cheap).
        if default_selected_intervals:
            initial_checked = [
                i for i in default_selected_intervals if i in self._intervals]
        else:
            initial_checked = [
                i for i in ("5m", "15m", "1h") if i in self._intervals]
        if not initial_checked:
            # Fall back to the legacy default_interval (or first) so the
            # dialog is never opened with zero intervals selected.
            single = (default_interval if (default_interval
                       and default_interval in self._intervals)
                      else self._intervals[0])
            initial_checked = [single]
        self._initial_checked = initial_checked
        # Legacy attribute kept for back-compat with any caller still
        # poking at ``self._initial_interval``; equals smallest checked.
        self._initial_interval = sorted(
            initial_checked, key=self._minutes_for)[0]
        self.result: dict[str, Any] | None = None

        self.title("Start Sandbox Session")
        self.transient(app)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        # Geometry persistence (position only; size is fixed).
        try:
            from .geometry_store import attach_persistent_geometry
            attach_persistent_geometry(self, "dlg.sandbox_start", "480x520")
        except tk.TclError:
            pass

        self._build()
        self._refresh_eligible_count()
        bind_modal_keys(self, cancel=self._on_cancel, primary=self._on_start)
        # If the primary interval has no cache, kick off a lazy fetch
        # right after the dialog renders so Random / Blind paths work
        # without forcing the user to toggle checkboxes first.
        try:
            self.after_idle(
                lambda: self._ensure_cached_for_interval(
                    self._primary_interval()))
        except tk.TclError:
            pass

        try:
            self.update_idletasks()
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_set()

    @staticmethod
    def _minutes_for(itv: str) -> int:
        """Sortable minute count for an interval string (1m=1 .. 1h=60)."""
        from ..backtest.aggregation import INTERVAL_MINUTES
        return INTERVAL_MINUTES.get(itv, 0)

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        frame = ttk.Frame(self)
        frame.grid(row=0, column=0, sticky="nsew", **pad)

        ttk.Label(
            frame,
            text=(f"Open-universe replay anchored on "
                  f"{self.reference_symbol}.\n"
                  "Tickers are loaded during the session — none here."),
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(frame, text="Intervals:").grid(
            row=1, column=0, sticky="ne", **pad)
        # Phase 1d-multitf-2: multi-select interval checkboxes. The
        # smallest checked interval is implicitly the primary tick
        # interval. Larger checked intervals are upscaled from primary
        # at session time (e.g. {5m, 15m, 1h}: tick is 5m, 15m and 1h
        # are aggregated views — the trailing higher-TF bar grows in
        # place as new 5m bars arrive). Only intervals where every
        # other checked entry is an integer multiple of the primary
        # are valid combos (e.g. {2m, 5m} is rejected — 5m can't be
        # reconstructed from 2m bars).
        intervals_frame = ttk.Frame(frame)
        intervals_frame.grid(row=1, column=1, columnspan=2,
                             sticky="w", **pad)
        self._interval_vars: dict[str, tk.BooleanVar] = {}
        self._interval_cbs: dict[str, ttk.Checkbutton] = {}
        for col, itv in enumerate(self._intervals):
            var = tk.BooleanVar(value=(itv in self._initial_checked))
            cb = ttk.Checkbutton(
                intervals_frame,
                text=itv,
                variable=var,
                command=self._on_interval_change,
            )
            cb.grid(row=0, column=col, sticky="w", padx=2)
            self._interval_vars[itv] = var
            self._interval_cbs[itv] = cb
        # Legacy alias so paths that still reference ``self._interval_cb``
        # (e.g. the busy-state toggle) don't crash. Points at the
        # primary's checkbox so disabling it during a fetch is visible.
        primary_itv = self._primary_interval()
        self._interval_cb = self._interval_cbs.get(
            primary_itv, list(self._interval_cbs.values())[0])
        # Hint line under the checkboxes — small + grey, mirrors the
        # daily-context hint pattern below.
        ttk.Label(
            frame,
            text=("Smallest checked = primary tick interval; larger "
                  "ones are upscaled in real time."),
            foreground="grey",
        ).grid(row=1, column=1, columnspan=2, sticky="sw",
               padx=8, pady=(28, 0))

        ttk.Label(frame, text="Session date (YYYY-MM-DD):").grid(
            row=2, column=0, sticky="e", **pad)
        self._date_var = tk.StringVar(value="")
        self._date_entry = ttk.Entry(
            frame, textvariable=self._date_var, width=14)
        self._date_entry.grid(row=2, column=1, sticky="w", **pad)
        self._random_btn = ttk.Button(
            frame, text="Random eligible date",
            command=self._on_random_date)
        self._random_btn.grid(row=2, column=2, sticky="w", **pad)

        self._eligible_count_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._eligible_count_var,
                  foreground="grey").grid(
            row=3, column=0, columnspan=3, sticky="w", **pad)

        # Phase 1d: blind random + auto-cycle. When checked, the date
        # field is hidden and disabled; on Start the dialog draws an
        # eligible date itself (without revealing it) and the
        # controller cycles through additional random dates each time
        # the master clock exhausts. The sandbox panel's clock readout
        # also drops the date portion in this mode.
        self._blind_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Blind random (date hidden, auto-cycle until End)",
            variable=self._blind_var,
            command=self._on_blind_toggle,
        ).grid(row=4, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(frame, text="Intraday lookback (days):").grid(
            row=5, column=0, sticky="e", **pad)
        self._lookback_var = tk.StringVar(value="1")
        ttk.Entry(frame, textvariable=self._lookback_var, width=6).grid(
            row=5, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Daily context (bars):").grid(
            row=6, column=0, sticky="e", **pad)
        self._daily_bars_var = tk.StringVar(value="100")
        ttk.Entry(frame, textvariable=self._daily_bars_var, width=6).grid(
            row=6, column=1, sticky="w", **pad)
        ttk.Label(
            frame,
            text=("Daily view shows completed sessions only "
                  "(current day omitted)."),
            foreground="grey",
        ).grid(row=6, column=2, sticky="w", **pad)

        ttk.Label(frame, text="Starting cash ($):").grid(
            row=7, column=0, sticky="e", **pad)
        self._cash_var = tk.StringVar(value="100000")
        ttk.Entry(frame, textvariable=self._cash_var, width=12).grid(
            row=7, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Slippage (bps):").grid(
            row=8, column=0, sticky="e", **pad)
        self._slip_var = tk.StringVar(value="5")
        ttk.Entry(frame, textvariable=self._slip_var, width=12).grid(
            row=8, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Commission ($/trade):").grid(
            row=9, column=0, sticky="e", **pad)
        self._comm_var = tk.StringVar(value="0")
        ttk.Entry(frame, textvariable=self._comm_var, width=12).grid(
            row=9, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Deck seed (int):").grid(
            row=10, column=0, sticky="e", **pad)
        self._seed_var = tk.StringVar(value="0")
        ttk.Entry(frame, textvariable=self._seed_var, width=12).grid(
            row=10, column=1, sticky="w", **pad)

        self._error_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._error_var,
                  foreground="red").grid(
            row=11, column=0, columnspan=3, sticky="w", **pad)

        # ---- Universe / strict-offline group ------------------------
        # Decoupled preload: the user runs Sandbox \u2192 Prepare Universe
        # Data… ahead of time; this combobox just lists the resulting
        # manifests. "(none)" preserves the legacy unrestricted flow.
        uni_box = ttk.LabelFrame(frame, text="Universe (optional)",
                                 padding=6)
        uni_box.grid(row=12, column=0, columnspan=3, sticky="ew", **pad)
        uni_box.columnconfigure(1, weight=1)

        ttk.Label(uni_box, text="Prepared:").grid(
            row=0, column=0, sticky="w")
        self._universe_var = tk.StringVar(value=self._UNIVERSE_NONE_LABEL)
        self._universe_combo = ttk.Combobox(
            uni_box, textvariable=self._universe_var,
            state="readonly", values=self._universe_combo_values(),
            width=42,
        )
        self._universe_combo.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._universe_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._on_universe_change(),
        )

        self._coverage_var = tk.StringVar(value="")
        ttk.Label(uni_box, textvariable=self._coverage_var,
                  foreground="grey").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self._strict_offline_var = tk.BooleanVar(value=False)
        self._strict_cb = ttk.Checkbutton(
            uni_box,
            text=("Strict offline: reject tickers outside the "
                  "universe (no live fetch)"),
            variable=self._strict_offline_var,
        )
        self._strict_cb.grid(row=2, column=0, columnspan=2, sticky="w",
                             pady=(2, 0))
        # Refresh coverage when the date entry changes.
        self._date_var.trace_add("write", lambda *_a: self._refresh_coverage())
        self._refresh_strict_state()
        self._refresh_coverage()

        btns = ttk.Frame(frame)
        btns.grid(row=13, column=0, columnspan=3, sticky="ew", **pad)
        # Windows dialog convention (audit ``button-order-windows``):
        # visual order ``[Start] [Cancel]`` with the dismiss action
        # rightmost. ``side=tk.RIGHT`` reverses pack order, so pack
        # Cancel first so it lands rightmost.
        ttk.Button(btns, text="Cancel", command=self._on_cancel) \
            .pack(side=tk.RIGHT, padx=4)
        self._start_btn = ttk.Button(
            btns, text="Start", command=self._on_start)
        self._start_btn.pack(side=tk.RIGHT, padx=4)

    def _selected_intervals(self) -> list[str]:
        """Return checked intervals sorted ascending by minute count.

        Returns ``[]`` when nothing is checked. Order is deterministic
        (smallest first), so ``_selected_intervals()[0]`` is always the
        primary tick interval when the list is non-empty.
        """
        chosen = [itv for itv, var in self._interval_vars.items()
                  if var.get()]
        chosen.sort(key=self._minutes_for)
        return chosen

    def _primary_interval(self) -> str:
        """Smallest checked interval (the primary tick interval).

        Falls back to ``self._intervals[0]`` if no checkbox is checked
        — the dialog's :meth:`_validate_intervals` surfaces an error
        in that case but other handlers (eligibility fetch, busy
        toggle) still need a sensible interval string.
        """
        sel = self._selected_intervals()
        return sel[0] if sel else self._intervals[0]

    def _validate_intervals(self) -> str | None:
        """Return an error message describing an invalid checkbox combo, or None.

        Validation rules:

        1. At least one interval must be checked.
        2. Every checked interval (other than the primary) must be an
           integer multiple of the primary's minute count — otherwise
           that timeframe cannot be reconstructed by aggregating
           primary bars (e.g. ``{2m, 5m}`` is invalid since ``5 % 2 != 0``).
        """
        from ..backtest.aggregation import divides_evenly
        sel = self._selected_intervals()
        if not sel:
            return "Select at least one interval."
        primary = sel[0]
        bad = [itv for itv in sel[1:]
               if not divides_evenly(primary, itv)]
        if bad:
            return (f"Invalid combo: primary {primary} cannot upscale to "
                    f"{', '.join(bad)} (not an integer multiple).")
        return None

    # ---- Universe selector helpers -----------------------------------

    _UNIVERSE_NONE_LABEL = "(none — legacy unrestricted)"

    def _refresh_manifests(self) -> None:
        """Pull the latest manifest list from the provider (best-effort)."""
        if self._manifest_provider is None:
            self._manifests = []
            return
        try:
            self._manifests = list(self._manifest_provider() or [])
        except Exception:  # noqa: BLE001
            self._manifests = []

    def _universe_combo_values(self) -> list[str]:
        labels = [self._UNIVERSE_NONE_LABEL]
        for m in self._manifests:
            try:
                sym_count = len(m.symbols)
                labels.append(f"{m.name}  ({m.id}, {sym_count} symbols)")
            except Exception:  # noqa: BLE001
                continue
        return labels

    def _selected_manifest(self) -> Any | None:
        """Manifest currently chosen in the combobox, or None.

        We resolve by parsing the trailing ``(<id>, ...)`` slug rather
        than by index so combobox edits stay robust if labels gain
        formatting later.
        """
        label = self._universe_var.get()
        if not label or label == self._UNIVERSE_NONE_LABEL:
            return None
        for m in self._manifests:
            try:
                slug = f"({m.id},"
                if slug in label:
                    return m
            except Exception:  # noqa: BLE001
                continue
        return None

    def _on_universe_change(self) -> None:
        self._refresh_strict_state()
        self._refresh_coverage()

    def _refresh_strict_state(self) -> None:
        """When a universe is picked, force-check + lock strict offline."""
        man = self._selected_manifest()
        if man is None:
            self._strict_offline_var.set(False)
            try:
                self._strict_cb.configure(state="normal")
            except tk.TclError:
                pass
        else:
            self._strict_offline_var.set(True)
            # Locked-checked: visible but not user-editable.
            try:
                self._strict_cb.configure(state="disabled")
            except tk.TclError:
                pass

    def _refresh_coverage(self) -> None:
        """Recompute the X / Y coverage label for the chosen date."""
        man = self._selected_manifest()
        if man is None:
            self._coverage_var.set("")
            return
        date_str = (self._date_var.get() or "").strip()
        if not date_str or date_str == "(hidden)":
            self._coverage_var.set(
                f"{len(man.symbols)} symbols in universe. Pick a date "
                f"to see coverage.")
            return
        try:
            target = _dt.date.fromisoformat(date_str)
        except ValueError:
            self._coverage_var.set(
                f"{len(man.symbols)} symbols in universe. "
                f"(Date not yet ISO-valid.)")
            return
        primary = self._primary_interval()
        try:
            from ..preload import manifest as _mod_manifest
            report = _mod_manifest.coverage_for_date(man, target, primary)
            self._coverage_var.set(
                f"{report.covered_count} / {report.total_count} symbols cover "
                f"{target.isoformat()} at {primary}.")
        except Exception as exc:  # noqa: BLE001
            self._coverage_var.set(
                f"Coverage check failed: {exc}")

    def _on_interval_change(self) -> None:
        """Handle interval-checkbox change.

        Refreshes the eligibility readout against the (possibly new)
        primary. If nothing's cached *and* a ``fetch_provider`` is
        wired, kicks off a sync-fetch so Random / Blind paths work
        without a separate Start round-trip. Also surfaces invalid-
        combo errors via the eligibility status line so the user
        sees the problem before clicking Start.
        """
        # Update legacy alias so _set_busy disables the right checkbox.
        primary_itv = self._primary_interval()
        self._interval_cb = self._interval_cbs.get(
            primary_itv, self._interval_cb)
        err = self._validate_intervals()
        if err:
            self._eligible_count_var.set(err)
            self._error_var.set("")
            return
        self._error_var.set("")
        self._refresh_eligible_count()
        if self._fetch_provider is None:
            return
        try:
            dates = list(self._eligible_provider(primary_itv))
        except Exception:  # noqa: BLE001
            dates = []
        if dates:
            return
        self._ensure_cached_for_interval(primary_itv)

    def _ensure_cached_for_interval(self, itv: str) -> bool:
        """Sync-fetch the reference symbol at ``itv`` if not cached.

        Returns ``True`` when, after the call, the eligibility provider
        produces a non-empty list at ``itv``. Safe to call when
        ``fetch_provider`` is ``None`` (returns the current
        cached-or-not state without fetching).

        Disables the Start / Random / interval controls during the
        fetch and surfaces a "Fetching SPY 5m…" status so the
        user understands the brief UI freeze.
        """
        try:
            dates = list(self._eligible_provider(itv))
        except Exception:  # noqa: BLE001
            dates = []
        if dates or self._fetch_provider is None:
            return bool(dates)
        prev_count = self._eligible_count_var.get()
        self._eligible_count_var.set(
            f"Fetching {self.reference_symbol} {itv}…")
        self._set_busy(True)
        try:
            self.update_idletasks()
        except tk.TclError:
            pass
        ok = False
        try:
            ok = bool(self._fetch_provider(itv))
        except Exception as exc:  # noqa: BLE001
            self._error_var.set(
                f"Fetch of {self.reference_symbol} {itv} failed: {exc}")
        finally:
            self._set_busy(False)
        if not ok:
            # Restore prior status; explicit error already surfaced.
            self._eligible_count_var.set(prev_count)
            return False
        # Cache now warm \u2014 recompute the readout against fresh data.
        self._refresh_eligible_count()
        try:
            return bool(list(self._eligible_provider(itv)))
        except Exception:  # noqa: BLE001
            return False

    def _set_busy(self, busy: bool) -> None:
        """Enable / disable input controls during a sync-fetch."""
        state = "disabled" if busy else "normal"
        # Disable every interval checkbox during the fetch so the user
        # can't change the primary mid-fetch.
        for cb in self._interval_cbs.values():
            try:
                cb.configure(state=state)
            except tk.TclError:
                pass
        for widget in (self._random_btn, self._start_btn):
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

    def _on_blind_toggle(self) -> None:
        """Lock / unlock the date controls based on the blind checkbox."""
        if self._blind_var.get():
            self._date_var.set("(hidden)")
            try:
                self._date_entry.configure(state="disabled")
                self._random_btn.configure(state="disabled")
            except tk.TclError:
                pass
        else:
            try:
                self._date_entry.configure(state="normal")
                self._random_btn.configure(state="normal")
            except tk.TclError:
                pass
            if self._date_var.get() == "(hidden)":
                self._date_var.set("")

    def _filtered_eligible_dates(self) -> list[_dt.date]:
        """Eligible dates with the *intraday lookback* trimmed off the front.

        Random draws (manual button + blind mode) and the eligible-list
        passed to the controller all need to honour the lookback so a
        randomised session_date always has the requested number of
        prior trading days available as context. Reads ``lookback_var``
        live (so the user typing a new value before clicking Random
        sees the effect).
        """
        try:
            dates = list(self._eligible_provider(self._primary_interval()))
        except Exception:  # noqa: BLE001
            return []
        try:
            lookback = max(0, int(self._lookback_var.get()))
        except (ValueError, tk.TclError):
            lookback = 0
        return dates[lookback:] if lookback else dates

    def _refresh_eligible_count(self) -> None:
        """Update the 'eligible dates available' status line.

        Called on dialog open and on every interval change. Catches
        provider errors as a status-line message rather than a crash
        — the user can still type a date manually if eligibility
        introspection fails. Reports the count *after* the lookback
        trim so the user sees what's actually drawable.
        """
        try:
            raw_dates = list(self._eligible_provider(self._primary_interval()))
        except Exception as exc:  # noqa: BLE001
            self._eligible_count_var.set(
                f"(eligibility unavailable: {exc})")
            return
        if not raw_dates:
            self._eligible_count_var.set(
                f"No cached {self.reference_symbol} data at this "
                f"interval — Start will sync-fetch.")
            return
        try:
            lookback = max(0, int(self._lookback_var.get()))
        except (ValueError, tk.TclError):
            lookback = 0
        filt = raw_dates[lookback:] if lookback else raw_dates
        if filt:
            self._eligible_count_var.set(
                f"{len(filt)} eligible session dates "
                f"({filt[0]} … {filt[-1]}) "
                f"after {lookback}d lookback trim.")
        else:
            self._eligible_count_var.set(
                f"All {len(raw_dates)} cached dates trimmed by "
                f"{lookback}d lookback — increase cache or lower "
                f"lookback.")

    def _on_random_date(self) -> None:
        from ..backtest.deck import draw_one_date
        # Lazy fetch if cache is empty (fetch_provider may be None;
        # in that case ensure() returns False and we fall through to
        # the existing error path).
        self._ensure_cached_for_interval(self._primary_interval())
        dates = self._filtered_eligible_dates()
        if not dates:
            self._error_var.set(
                "No eligible dates cached for this interval (after "
                "lookback trim). Start a session to sync-fetch first, "
                "increase lookback below the available history, or "
                "type a date manually.")
            return
        try:
            seed = int(self._seed_var.get() or "0")
        except ValueError:
            seed = 0
        chosen = draw_one_date(dates, seed)
        self._date_var.set(chosen.isoformat())
        self._error_var.set("")

    def _on_start(self) -> None:
        try:
            cash = float(self._cash_var.get())
            slip = float(self._slip_var.get())
            comm = float(self._comm_var.get())
            seed = int(self._seed_var.get())
            lookback = int(self._lookback_var.get())
            daily_bars = int(self._daily_bars_var.get())
        except ValueError:
            self._error_var.set(
                "Cash / slippage / commission / seed / lookback / "
                "daily-bars must be numeric.")
            return
        if cash <= 0:
            self._error_var.set("Starting cash must be positive.")
            return
        if lookback < 0:
            self._error_var.set("Lookback must be non-negative.")
            return
        if daily_bars < 0:
            self._error_var.set("Daily context bars must be non-negative.")
            return

        blind = bool(self._blind_var.get())
        # Blind mode: if the user left the seed at its default ``0``, derive
        # a fresh seed from the wall clock so successive blind sessions land
        # on different dates. A non-zero seed is treated as a user-pinned
        # reproducible draw and is honored as-is. (For non-blind sessions,
        # the seed entry only feeds the manual Random button + auto-cycle
        # deck, so we leave it alone.)
        if blind and seed == 0:
            import time as _time
            seed = _time.time_ns() & 0x7FFFFFFF
        eligible_dates: list[_dt.date] = []
        if blind:
            # Blind mode: dialog picks the date itself; the user never
            # sees it. Eligibility is mandatory here \u2014 without a list
            # there's nothing to randomize over. Fetch lazily if the
            # cache is empty; only error out when the fetch hook is
            # absent or the fetch itself failed.
            self._ensure_cached_for_interval(self._primary_interval())
            eligible_dates = self._filtered_eligible_dates()
            if not eligible_dates:
                self._error_var.set(
                    "Blind mode needs cached reference data (after "
                    "lookback trim). Switch to manual + Start to "
                    "sync-fetch first, lower the lookback, or pick "
                    "an interval that's already cached.")
                return
            from ..backtest.deck import draw_one_date
            session_date = draw_one_date(eligible_dates, seed)
        else:
            date_str = self._date_var.get().strip()
            if not date_str or date_str == "(hidden)":
                self._error_var.set("Pick a session date (or use Random).")
                return
            try:
                session_date = _dt.date.fromisoformat(date_str)
            except ValueError:
                self._error_var.set("Date must be ISO format YYYY-MM-DD.")
                return

        # Validate the multi-interval combo before committing.
        combo_err = self._validate_intervals()
        if combo_err:
            self._error_var.set(combo_err)
            return
        selected_intervals = self._selected_intervals()
        primary = selected_intervals[0]

        # Universe / strict-offline result fields. These are non-empty
        # only when the user selected a prepared universe; otherwise
        # the legacy unrestricted flow is preserved.
        chosen_man = self._selected_manifest()
        if chosen_man is not None:
            try:
                universe_id = chosen_man.id
                universe_symbols = tuple(sorted(chosen_man.symbol_set()))
            except Exception:  # noqa: BLE001
                universe_id = ""
                universe_symbols = ()
        else:
            universe_id = ""
            universe_symbols = ()
        strict_offline = bool(self._strict_offline_var.get()) and bool(universe_id)

        self.result = {
            "session_date": session_date,
            "interval": primary,
            "display_intervals": list(selected_intervals),
            "lookback_days": lookback,
            "daily_lookback_bars": daily_bars,
            "starting_cash": cash,
            "slippage_bps": slip,
            "commission": comm,
            "deck_seed": seed,
            "blind": blind,
            "auto_cycle": blind,
            "eligible_dates": eligible_dates,
            "universe_id": universe_id,
            "universe_symbols": universe_symbols,
            "strict_offline": strict_offline,
        }
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


# PreTradeFormDialog was extracted to a focused module; re-exported here
# so existing imports (``from .sandbox_dialog import PreTradeFormDialog``)
# keep working.
from .pre_trade_dialog import PreTradeFormDialog  # noqa: E402,F401

__all__ = ("SandboxStartDialog", "PreTradeFormDialog")
