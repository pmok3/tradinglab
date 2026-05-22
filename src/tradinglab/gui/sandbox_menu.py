"""Sandbox-menu mixin for :class:`tradinglab.app.ChartApp`.

Hosts the menu-callback handlers wired up by ``_build_menubar`` for the
``Sandbox`` cascade — Start, End, Performance, Save, Load, Tags,
Prepare Universe Data.

Mixin rules (see decomposition plan):
* No ``__init__``.
* No cooperative ``super()`` — method resolution relies on plain MRO.
* No name collisions with other mixins or ``ChartApp``.

The mixin is intentionally thin: it owns the menu-callback control
flow and delegates lifecycle work (``_show_sandbox_panel``,
``_hide_sandbox_panel``, ``_build_sandbox_spec``, toolbar interval
restriction, scanner-state reset, watchlist re-preload) to methods
that remain on ChartApp. Instance state read/written here
(``_sandbox``, ``_full_cache``, ``_sandbox_universe`` etc.) is
initialised by ``ChartApp.__init__``.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Any, List, Optional

from ..data import DATA_SOURCES
from ..models import Candle


class SandboxMenuMixin:
    """Sandbox-menu callbacks (Start / End / Perf / Save / Load / Tags / Prepare)."""

    def _on_menu_sandbox_start(self) -> None:
        """Open the SandboxStartDialog and, on OK, begin a replay session.

        Phase 1c-redux open-universe model: anchor the session on a
        single reference ticker (SPY by convention, overridable via
        the ``sandbox_reference_symbol`` setting — audit
        ``sandbox-ref-symbol``). Tradeable tickers are loaded
        mid-session via the regular ticker entry / watchlist — they
        do NOT need to be picked up front.

        The reference symbol is sync-fetched at the chosen interval
        if not already in ``_full_cache``. Per the locked design
        (decision A): if the reference cannot be fetched, fail fast
        with a status-bar message rather than synthesising a
        fallback timeline (silent divergence is worse than explicit
        failure).
        """
        if self._is_sandbox_active():
            try:
                self._status.info(
                    "Sandbox already active; end the current session first")
            except Exception:  # noqa: BLE001
                pass
            return

        # Master-clock anchor ticker. Defaults to SPY but the user
        # can pin a different liquid benchmark via
        # ``sandbox_reference_symbol`` in settings.json (audit
        # ``sandbox-ref-symbol``). Empty / unknown values fall back
        # to the SPY default rather than failing fast — the engine
        # already handles "no bars at this interval" downstream.
        from .. import defaults as _defaults_mod
        try:
            _ref_raw = _defaults_mod.get("sandbox_reference_symbol")
        except Exception:  # noqa: BLE001
            _ref_raw = "SPY"
        reference_symbol = (str(_ref_raw or "").strip().upper() or "SPY")
        # Restrict dialog to intervals the engine + chart cope with
        # cleanly. Daily-and-above are excluded — the master clock is
        # an *intraday* concept and a daily-bar replay degenerates
        # to one tick per day, which the rest of the UX isn't built
        # for. (Could be lifted in Phase 2.)
        sandbox_intervals = ["1m", "2m", "5m", "15m", "30m", "1h"]

        def _eligible_dates_at(itv: str) -> List[Any]:
            """Eligibility provider for the dialog. Cache-only — no fetch.

            Returns empty list if SPY isn't cached at this interval; the
            dialog will display a "Start will sync-fetch" hint and the
            user can either Random-pick (after caching by switching
            interval back) or type a date manually.

            ``regular_only`` is keyed off the live pre/post toggle: if
            the user has pre/post OFF in their regular session, days
            that *only* qualify because of pre/post bars must not slip
            into the deck (otherwise the random date lands on a day
            with no regular-hours data and replay starts with an
            empty chart).
            """
            from ..backtest.deck import build_eligible_dates
            src = self.source_var.get()
            cached = self._full_cache.get((src, reference_symbol, itv))
            if not cached:
                return []
            include_ext = bool(self.prepost_var.get()) \
                if hasattr(self, "prepost_var") else False
            return build_eligible_dates(
                cached, regular_only=not include_ext)

        def _fetch_reference_at(itv: str) -> bool:
            """Sync-fetch ``reference_symbol`` at ``itv`` into ``_full_cache``.

            Returns True on success (and the next call to
            ``_eligible_dates_at(itv)`` will report a populated list).
            Used by the start dialog to lazily warm the cache when the
            user lands on, or switches to, an interval SPY hasn't been
            loaded at yet.
            """
            fetcher = DATA_SOURCES.get(self.source_var.get())
            if fetcher is None:
                return False
            try:
                candles = fetcher(reference_symbol, itv) or []
            except Exception:  # noqa: BLE001
                return False
            if not candles:
                return False
            self._full_cache[
                (self.source_var.get(), reference_symbol, itv)] = candles
            return True

        # Default the dialog to the chart's current interval if it's
        # one we support in sandbox \u2014 most likely already cached, so
        # the dialog opens without any fetch round-trip.
        try:
            current_itv = self.interval_var.get()
        except Exception:  # noqa: BLE001
            current_itv = ""
        default_itv = current_itv if current_itv in sandbox_intervals \
            else sandbox_intervals[0]

        from ..gui.sandbox_dialog import SandboxStartDialog
        # Phase: pass a manifest provider so the dialog can offer
        # prepared universes. Lazy import keeps app.py's eager import
        # graph cheap; the failure mode is swallowed here so a bad
        # manifest dir never blocks Start.
        def _manifest_provider() -> List[Any]:
            try:
                from ..preload import manifest as _man
                return _man.load_all()
            except Exception:  # noqa: BLE001
                return []

        dlg = SandboxStartDialog(
            self,
            reference_symbol=reference_symbol,
            intervals=sandbox_intervals,
            eligible_dates_provider=_eligible_dates_at,
            fetch_provider=_fetch_reference_at,
            default_interval=default_itv,
            manifest_provider=_manifest_provider,
        )
        self.wait_window(dlg)
        if dlg.result is None:
            return

        chosen_itv = dlg.result["interval"]
        display_intervals = list(
            dlg.result.get("display_intervals") or [chosen_itv])
        session_date = dlg.result["session_date"]
        lookback_days = dlg.result["lookback_days"]
        daily_lookback_bars = int(dlg.result.get("daily_lookback_bars", 100))

        # Sync-fetch SPY if not cached at the chosen interval.
        src = self.source_var.get()
        ref_key = (src, reference_symbol, chosen_itv)
        ref_candles = self._full_cache.get(ref_key)
        if not ref_candles:
            fetcher = DATA_SOURCES.get(src)
            if fetcher is None:
                try:
                    self._status.error(
                        f"Sandbox: no fetcher configured for the "
                        f"selected data source; cannot anchor on "
                        f"{reference_symbol}")
                except Exception:  # noqa: BLE001
                    pass
                return
            try:
                self._status.info(
                    f"Sandbox: sync-fetching {reference_symbol} "
                    f"{chosen_itv}…")
            except Exception:  # noqa: BLE001
                pass
            try:
                ref_candles = fetcher(reference_symbol, chosen_itv) or []
            except Exception as exc:  # noqa: BLE001
                try:
                    self._status.error(
                        f"Sandbox: failed to fetch {reference_symbol} "
                        f"{chosen_itv}: {exc}")
                except Exception:  # noqa: BLE001
                    pass
                return
            if ref_candles:
                self._full_cache[ref_key] = list(ref_candles)
        if not ref_candles:
            try:
                self._status.error(
                    f"Sandbox: no {reference_symbol} bars available at "
                    f"{chosen_itv} — cannot anchor session timeline")
            except Exception:  # noqa: BLE001
                pass
            return

        # Phase 1d-multitf: also fetch the reference symbol at "1d" so
        # the user can toggle the chart to daily and see prior-context
        # bars during the session. Failure here degrades gracefully —
        # the sandbox still starts, just without 1d-toggle data
        # (rubber-duck blocker — daily fetch must not block intraday).
        daily_ref_candles: List[Candle] = []
        if daily_lookback_bars > 0:
            daily_key = (src, reference_symbol, "1d")
            cached_daily = self._full_cache.get(daily_key)
            if cached_daily:
                daily_ref_candles = list(cached_daily)
            else:
                fetcher = DATA_SOURCES.get(src)
                if fetcher is not None:
                    try:
                        self._status.info(
                            f"Sandbox: sync-fetching {reference_symbol} "
                            f"1d for daily context…")
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        daily_ref_candles = list(
                            fetcher(reference_symbol, "1d") or [])
                    except Exception as exc:  # noqa: BLE001
                        try:
                            self._status.warn(
                                f"Sandbox: daily fetch for "
                                f"{reference_symbol} failed: {exc} — "
                                f"1d toggle will be unavailable")
                        except Exception:  # noqa: BLE001
                            pass
                        daily_ref_candles = []
                    if daily_ref_candles:
                        self._full_cache[daily_key] = list(daily_ref_candles)

        spec = self._build_sandbox_spec(dlg.result)

        # Phase 1d: blind / auto-cycle wiring + extended-hours
        # default. Sandbox replay uses *regular-hours-only* bars by
        # default to avoid the regular-session UX leaking pre/post
        # candles the user didn't ask for. The blind / auto-cycle
        # checkbox in the dialog is a single boolean: when set, both
        # behaviours are enabled (date hidden + auto-roll on EOD).
        blind = bool(dlg.result.get("blind", False))
        auto_cycle = bool(dlg.result.get("auto_cycle", False))
        eligible_dates = list(dlg.result.get("eligible_dates") or [])
        # Auto-cycle needs a deck; if the dialog didn't materialise
        # one (e.g. cache was cold), fall back to single-day mode and
        # warn rather than refusing to start.
        if auto_cycle and not eligible_dates:
            auto_cycle = False
            blind = False
            try:
                self._status.warn(
                    "Sandbox: blind auto-cycle requested but no eligible "
                    "dates cached for the chosen interval; falling back "
                    "to single-day session")
            except Exception:  # noqa: BLE001
                pass

        from ..backtest.replay import SandboxController
        self._sandbox = SandboxController(
            app=self, tag_store=self._sandbox_tag_store)
        try:
            self._sandbox.start_session(
                spec=spec,
                session_date=session_date,
                interval=chosen_itv,
                reference_symbol=reference_symbol,
                reference_candles=list(ref_candles),
                lookback_days=lookback_days,
                include_extended=False,
                auto_cycle=auto_cycle,
                blind=blind,
                eligible_dates=eligible_dates,
                daily_lookback_bars=daily_lookback_bars,
                daily_reference_candles=list(daily_ref_candles),
                display_intervals=display_intervals,
            )
            sid = self._sandbox.session_id
            if sid:
                self._sandbox.screenshot_dir = self._sandbox_screenshot_dir(sid)
            # Restrict toolbar interval combobox to the user-selected
            # sandbox intervals (+ "1d" if daily context is registered)
            # so non-eligible intervals can't be picked while a session
            # is active. Saved values are restored on _on_menu_sandbox_end.
            try:
                self._restrict_toolbar_intervals_for_sandbox(
                    display_intervals=display_intervals,
                    daily_available=bool(daily_ref_candles),
                )
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            self._sandbox = None
            try:
                self._status.error(f"Sandbox failed to start: {exc}")
            except Exception:  # noqa: BLE001
                pass
            return
        self._show_sandbox_panel()

        # M5 ChartStack lockstep: hand the running sandbox to the
        # ChartStack panel so each card advances in step with the
        # master clock. ``end_session`` self-detaches via the
        # ``active=False`` final-tick contract; no explicit detach
        # call is needed in ``_on_menu_sandbox_end``.
        cs = getattr(self, "_chartstack", None)
        if cs is not None:
            try:
                cs.attach_sandbox(self._sandbox)
            except Exception:  # noqa: BLE001 - never let chartstack break sandbox start
                pass

        # Strict-offline universe seal (sandbox-preload feature).
        # Captured AFTER ``self._sandbox`` is constructed so a failure
        # during session build doesn't leak universe state. Reference
        # symbol is implicitly added so SPY is never rejected.
        try:
            uni_syms = dlg.result.get("universe_symbols") or ()
            uni_id = (dlg.result.get("universe_id") or "").strip()
            strict = bool(dlg.result.get("strict_offline"))
        except Exception:  # noqa: BLE001
            uni_syms, uni_id, strict = (), "", False
        if uni_id and uni_syms:
            allow = {str(s).strip().upper() for s in uni_syms if s}
            allow.add(reference_symbol)  # master-clock anchor must be allowed
            self._sandbox_universe = frozenset(allow)
            self._sandbox_universe_id = uni_id
            self._sandbox_strict_offline = strict
        else:
            self._sandbox_universe = frozenset()
            self._sandbox_universe_id = ""
            self._sandbox_strict_offline = False

        # Auto-register the user's pre-sandbox primary ticker so it's
        # available for trading without a manual load step. SPY is the
        # master clock anchor, but the chart should focus on whatever
        # the user was looking at (e.g. AMD) so they can place trades
        # immediately. Skipped when the pre-sandbox ticker is SPY itself
        # (already registered as the reference) or empty.
        try:
            pre_primary = (self._confirmed_primary_ticker or "").strip().upper()
        except Exception:  # noqa: BLE001
            pre_primary = ""
        if pre_primary and pre_primary != reference_symbol:
            try:
                self._sandbox_register_and_focus(pre_primary)
            except Exception as exc:  # noqa: BLE001
                try:
                    self._status.warn(
                        f"Sandbox: could not auto-load {pre_primary} "
                        f"({exc}); chart stays on {reference_symbol}.")
                except Exception:  # noqa: BLE001
                    pass
        try:
            if blind:
                msg = (
                    f"Sandbox started on {reference_symbol} @ {chosen_itv} "
                    f"(blind auto-cycle, date hidden), "
                    f"cash ${spec.starting_cash:,.2f}. "
                    f"Load tickers via the regular entry / watchlist."
                )
            else:
                msg = (
                    f"Sandbox started on {reference_symbol} @ {chosen_itv}, "
                    f"date {session_date.isoformat()}, "
                    f"cash ${spec.starting_cash:,.2f}. Load tickers via "
                    f"the regular entry / watchlist."
                )
            self._status.info(msg)
        except Exception:  # noqa: BLE001
            pass
        # Refresh watchlist Last/Change to reflect the sandbox replay
        # clock (instead of today's live values).
        try:
            self._refresh_watchlist_for_sandbox()
        except Exception:  # noqa: BLE001
            pass
        # If the Manage Indicators dialog is open, refresh its
        # per-row interval checkbox set to reflect the sandbox's
        # display_intervals (b41).
        try:
            dlg = getattr(self, "_indicator_dialog", None)
            if dlg is not None:
                dlg.refresh_available_intervals()
        except Exception:  # noqa: BLE001
            pass

    def _on_menu_sandbox_end(self) -> None:
        """End the active sandbox session and restore prior chart state.

        Tear-down runs in three stages, each independently guarded so
        a failure in one stage doesn't strand the next: (1) call
        ``end_session`` on the controller, (2) cache the result for
        post-end Save / Performance, (3) drop the controller
        reference + hide the panel.  Errors in any stage surface to
        the status bar so an issue doesn't silently leave the UI in
        a half-sandbox state.
        """
        if not self._is_sandbox_active():
            return
        ended = None
        try:
            ended = self._sandbox.end_session()
        except Exception as exc:  # noqa: BLE001
            try:
                self._status.error(f"Sandbox end raised: {exc}")
            except Exception:  # noqa: BLE001
                pass
        # Cache last result + screenshot dir for post-end Save / Performance.
        if ended is not None:
            self._last_sandbox_result = ended
            try:
                self._last_sandbox_screenshot_dir = (
                    self._sandbox.screenshot_dir
                    if hasattr(self._sandbox, "screenshot_dir") else None)
            except Exception:  # noqa: BLE001
                self._last_sandbox_screenshot_dir = None
        self._sandbox = None
        # Clear strict-offline universe state — the next session
        # picks its own (or none).
        self._sandbox_universe = frozenset()
        self._sandbox_universe_id = ""
        self._sandbox_strict_offline = False
        # Drop sandbox-only chart state so post-session normal-mode
        # interactions (load data, pan, scroll-zoom) don't keep
        # snapping back to the sandbox's pre-allocated xlim.
        self._sandbox_full_session_xlim = None
        self._preserve_xlim_on_render = False
        try:
            self._restore_toolbar_intervals_from_sandbox()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._reset_scanner_state()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._hide_sandbox_panel()
        except Exception as exc:  # noqa: BLE001
            try:
                self._status.error(f"Sandbox panel hide failed: {exc}")
            except Exception:  # noqa: BLE001
                pass
        try:
            self._status.info("Sandbox: session ended")
        except Exception:  # noqa: BLE001
            pass
        # Restore real-world watchlist Last/Change now that the
        # sandbox clock no longer applies. Clear the sandbox-influenced
        # snapshot fields and re-run the live preloads.
        try:
            snap_map = getattr(self, "_watchlist_snapshot", None)
            if isinstance(snap_map, dict):
                for snap in snap_map.values():
                    if not isinstance(snap, dict):
                        continue
                    for k in ("last", "change_1d", "pct_1d", "chg", "pct"):
                        snap.pop(k, None)
            self._preload_watchlist()
            self._preload_watchlist_daily()
            self._populate_watchlist_tab()
        except Exception:  # noqa: BLE001
            pass
        # Restore Manage Indicators dialog's interval checkbox set
        # to the full toolbar list now that sandbox is over (b41).
        try:
            dlg = getattr(self, "_indicator_dialog", None)
            if dlg is not None:
                dlg.refresh_available_intervals()
        except Exception:  # noqa: BLE001
            pass

    def _on_menu_sandbox_perf(self) -> None:
        """Open the Performance View on the current/last SessionResult."""
        result = self._current_sandbox_result()
        if result is None:
            try:
                self._status.warn(
                    "Sandbox: no session result to show. "
                    "Start or load a session first")
            except Exception:  # noqa: BLE001
                pass
            return
        from ..gui.performance_view import PerformanceView
        title = ("Sandbox \u2014 Performance (live)"
                 if self._is_sandbox_active()
                 else "Sandbox \u2014 Performance")
        win = PerformanceView(
            self, result, title=title,
            screenshot_dir=self._current_sandbox_screenshot_dir())
        try:
            win.lift()
        except tk.TclError:
            pass

    def _on_menu_sandbox_save(self) -> None:
        """Save the current/last SessionResult to a JSON file."""
        from tkinter import filedialog

        from ..backtest.persistence import save_session
        result = self._current_sandbox_result()
        if result is None:
            try:
                self._status.warn(
                    "Sandbox: nothing to save. "
                    "Start a session first")
            except Exception:  # noqa: BLE001
                pass
            return
        path_str = filedialog.asksaveasfilename(
            parent=self,
            title="Save Sandbox Session",
            defaultextension=".json",
            filetypes=[("Sandbox session JSON", "*.json"),
                       ("All files", "*.*")],
            initialfile="sandbox_session.json",
        )
        if not path_str:
            return
        sid = ""
        try:
            if self._sandbox is not None:
                sid = str(getattr(self._sandbox, "session_id", "") or "")
        except Exception:  # noqa: BLE001
            sid = ""
        try:
            saved = save_session(
                Path(path_str), result,
                session_id=sid,
                screenshot_dir=self._current_sandbox_screenshot_dir(),
            )
            self._status.info(f"Sandbox: saved session to {saved}")
        except Exception as exc:  # noqa: BLE001
            try:
                self._status.error(f"Sandbox save failed: {exc}")
            except Exception:  # noqa: BLE001
                pass

    def _on_menu_sandbox_load(self) -> None:
        """Load a saved SessionResult and open the Performance View."""
        from tkinter import filedialog

        from ..backtest.persistence import load_session
        path_str = filedialog.askopenfilename(
            parent=self,
            title="Load Sandbox Session",
            filetypes=[("Sandbox session JSON", "*.json"),
                       ("All files", "*.*")],
        )
        if not path_str:
            return
        try:
            loaded = load_session(Path(path_str))
        except Exception as exc:  # noqa: BLE001
            try:
                self._status.error(f"Sandbox load failed: {exc}")
            except Exception:  # noqa: BLE001
                pass
            return
        # Don't clobber a live session's cache. If sandbox is active,
        # show the loaded one in a perf window only.
        if not self._is_sandbox_active():
            self._last_sandbox_result = loaded.result
            self._last_sandbox_screenshot_dir = loaded.screenshot_dir
        from ..gui.performance_view import PerformanceView
        win = PerformanceView(
            self, loaded.result,
            title=f"Sandbox \u2014 Loaded ({Path(path_str).name})",
            screenshot_dir=loaded.screenshot_dir)
        try:
            win.lift()
        except tk.TclError:
            pass
        try:
            self._status.info(
                f"Sandbox: loaded session ({len(loaded.result.post_trades)} "
                f"closed trade(s))")
        except Exception:  # noqa: BLE001
            pass

    def _on_menu_sandbox_tags(self) -> None:
        """Open the setup-tag taxonomy editor (Phase 1c)."""
        from ..gui.sandbox_review_dialog import TagsEditorDialog
        dlg = TagsEditorDialog(self, self._sandbox_tag_store)
        self.wait_window(dlg)

    def _on_menu_sandbox_prepare_universe(self) -> None:
        """Open the Prepare Universe Data dialog.

        Decoupled from session start: the user runs this to fill the
        disk cache + write a manifest sidecar, then later picks the
        universe in the SandboxStartDialog. Refuses while a sandbox
        session is active to avoid mutating ``_full_cache`` mid-replay.
        """
        if self._is_sandbox_active():
            try:
                self._status.info(
                    "Cannot prepare universe data while a sandbox session "
                    "is active. End the session first")
            except Exception:  # noqa: BLE001
                pass
            return
        src = self.source_var.get()
        fetcher = DATA_SOURCES.get(src)
        if fetcher is None:
            try:
                self._status.error(
                    "No fetcher configured for the selected data source")
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            from ..gui.universe_prepare_dialog import UniversePrepareDialog
        except Exception as exc:  # noqa: BLE001
            try:
                self._status.error(
                    f"Failed to open Prepare Universe Data: {exc}")
            except Exception:  # noqa: BLE001
                pass
            return
        dlg = UniversePrepareDialog(
            self, source_name=src, fetcher=fetcher)
        # Modal: block here so the menu doesn't allow stacking dialogs.
        try:
            self.wait_window(dlg)
        except Exception:  # noqa: BLE001
            pass
        man = getattr(dlg, "result", None)
        if man is not None:
            try:
                self._status.info(
                    f"Universe '{man.id}' prepared: {len(man.symbols)} "
                    f"symbols across {len(man.intervals)} intervals.")
            except Exception:  # noqa: BLE001
                pass
