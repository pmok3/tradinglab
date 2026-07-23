"""App-level sandbox orchestration helpers for :mod:`tradinglab.app`."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import ttk
from typing import Any

from ..data import DATA_SOURCES
from ..data import quality as _quality
from ..models import Candle
from .session import SessionSpec
from .tags import TagStore


def _sandbox_preferred_src(app: Any, interval: str) -> str:
    """Data source a sandbox mid-session fetch should use.

    Mirrors the reference-load choice (perf item #7): the longest/
    highest-quality source the user has configured, so compare / focus
    symbols added mid-session share the reference's data basis instead of
    silently pulling from a different (active-chart) source. Respects an
    explicit synthetic/stub choice and falls back to the active source.
    """
    try:
        return _quality.preferred_source(app.source_var.get(), interval=interval)
    except Exception:  # noqa: BLE001
        return app.source_var.get()


class SandboxAppController:
    """Own sandbox session state and app-facing orchestration helpers."""

    def __init__(self) -> None:
        self._sandbox: Any | None = None
        self._last_result: Any | None = None
        self._last_screenshot_dir: Path | None = None
        self._panel: Any | None = None
        self._panel_window: tk.Toplevel | None = None
        self._tag_store = TagStore()
        self._universe: frozenset[str] = frozenset()
        self._universe_id: str = ""
        self._strict_offline: bool = False

    @property
    def active(self) -> bool:
        sandbox = self._sandbox
        return sandbox is not None and sandbox.is_active()

    @property
    def engine(self):
        return self._sandbox

    @engine.setter
    def engine(self, value) -> None:
        self._sandbox = value

    @property
    def last_result(self):
        return self._last_result

    @last_result.setter
    def last_result(self, value) -> None:
        self._last_result = value

    @property
    def last_screenshot_dir(self) -> Path | None:
        return self._last_screenshot_dir

    @last_screenshot_dir.setter
    def last_screenshot_dir(self, value: Path | None) -> None:
        self._last_screenshot_dir = value

    @property
    def panel(self):
        return self._panel

    @panel.setter
    def panel(self, value) -> None:
        self._panel = value

    @property
    def panel_window(self) -> tk.Toplevel | None:
        return self._panel_window

    @panel_window.setter
    def panel_window(self, value: tk.Toplevel | None) -> None:
        self._panel_window = value

    @property
    def tag_store(self) -> TagStore:
        return self._tag_store

    @tag_store.setter
    def tag_store(self, value: TagStore) -> None:
        self._tag_store = value

    @property
    def universe(self) -> frozenset[str]:
        return self._universe

    @universe.setter
    def universe(self, value: frozenset[str]) -> None:
        self._universe = value

    @property
    def universe_id(self) -> str:
        return self._universe_id

    @universe_id.setter
    def universe_id(self, value: str) -> None:
        self._universe_id = value

    @property
    def strict_offline(self) -> bool:
        return self._strict_offline

    @strict_offline.setter
    def strict_offline(self, value: bool) -> None:
        self._strict_offline = bool(value)

    def build_spec(self, dlg_result: dict[str, Any]) -> SessionSpec:
        """Translate the start-dialog payload into a SessionSpec."""
        from .session import ENGINE_VERSION, SessionSpec

        session_date = dlg_result.get("session_date")
        return SessionSpec(
            deck_seed=int(dlg_result["deck_seed"]),
            tickers=tuple(dlg_result.get("tickers", ()) or ()),
            start_clock_iso=(session_date.isoformat() if session_date is not None else ""),
            slippage_bps=float(dlg_result["slippage_bps"]),
            commission=float(dlg_result["commission"]),
            engine_version=ENGINE_VERSION,
            setup_tags=tuple(self._tag_store.list()),
            starting_cash=float(dlg_result["starting_cash"]),
            universe_id=str(dlg_result.get("universe_id", "") or ""),
            universe_symbols=tuple(dlg_result.get("universe_symbols", ()) or ()),
            strict_offline=bool(dlg_result.get("strict_offline", False)),
            decision_logging_enabled=bool(
                dlg_result.get("decision_logging_enabled", False)),
        )

    def current_result(self):
        """Return the live or last-ended sandbox SessionResult."""
        if self.active and self._sandbox is not None:
            try:
                if hasattr(self._sandbox, "result"):
                    return self._sandbox.result()
                engine = getattr(self._sandbox, "engine", None)
                if engine is not None:
                    return engine.result()
            except Exception:  # noqa: BLE001
                return None
        return self._last_result

    def current_screenshot_dir(self) -> Path | None:
        if self.active and self._sandbox is not None:
            return getattr(self._sandbox, "screenshot_dir", None)
        return self._last_screenshot_dir

    def show_panel(self, *, app: Any, silent_tcl: Any) -> None:
        """Mount the sandbox panel in the notebook tab."""
        if self._sandbox is None:
            return
        nb = getattr(app, "_notebook", None)
        sb_frame = getattr(app, "_sandbox_tab_frame", None)
        if nb is None or sb_frame is None:
            return
        if self._panel is not None:
            with silent_tcl():
                nb.tab(sb_frame, state="normal")
                nb.select(sb_frame)
            return
        from ..gui.sandbox_panel import SandboxPanel

        panel = SandboxPanel(sb_frame, controller=self._sandbox)
        panel.pack(fill=tk.BOTH, expand=True)
        with silent_tcl():
            nb.tab(sb_frame, state="normal")
            nb.select(sb_frame)
        self._panel = panel
        self._panel_window = None

    def hide_panel(self, *, app: Any, silent_tcl: Any) -> None:
        """Tear down the sandbox panel and hide its notebook tab."""
        panel = self._panel
        if panel is not None:
            with silent_tcl():
                panel.destroy()
        nb = getattr(app, "_notebook", None)
        sb_frame = getattr(app, "_sandbox_tab_frame", None)
        if nb is not None and sb_frame is not None:
            with silent_tcl():
                if str(nb.select()) == str(sb_frame):
                    tabs = nb.tabs()
                    if tabs:
                        nb.select(tabs[0])
                nb.tab(sb_frame, state="hidden")
        self._panel = None
        self._panel_window = None

    def maybe_write_resume_metadata(self) -> None:
        """If a sandbox is active, write resume metadata to disk."""
        sandbox = self._sandbox
        if sandbox is None or not getattr(sandbox, "active", False):
            return
        engine = getattr(sandbox, "engine", None)
        if engine is None:
            return
        spec = getattr(engine, "spec", None)
        if spec is None:
            return
        try:
            from .sandbox_resume import build_metadata_from_session, write_resume_metadata
        except Exception:  # noqa: BLE001
            return
        try:
            tickers = getattr(spec, "tickers", ()) or ()
            ticker = str(tickers[0]) if tickers else ""
            interval = str(getattr(sandbox, "interval", "") or "")
            clock = getattr(engine, "clock", None)
            if clock is not None:
                bars = int(getattr(clock, "index", 0) or 0)
                if bars < 0:
                    bars = 0
            else:
                bars = 0
            spec_dict = spec.to_dict() if hasattr(spec, "to_dict") else {}
            meta = build_metadata_from_session(
                session_id=str(getattr(sandbox, "session_id", "") or ""),
                ticker=ticker,
                interval=interval,
                bars_processed=bars,
                spec_dict=spec_dict,
            )
            write_resume_metadata(meta)
        except Exception:  # noqa: BLE001
            pass

    def maybe_prompt_resume(self, *, app: Any) -> None:
        """Show the resume-prompt dialog if metadata is on disk."""
        try:
            from .sandbox_resume import clear_resume_metadata, read_resume_metadata
        except Exception:  # noqa: BLE001
            return
        try:
            meta = read_resume_metadata()
        except Exception:  # noqa: BLE001
            return
        if meta is None:
            return
        try:
            desc = meta.short_description()
        except Exception:  # noqa: BLE001
            desc = "(unknown session)"

        try:
            win = tk.Toplevel(app)
            win.title("Resume sandbox session?")
            win.transient(app)
            win.resizable(False, False)
            frame = ttk.Frame(win, padding=12)
            frame.pack(fill=tk.BOTH, expand=True)
            ttk.Label(
                frame,
                text="A sandbox session was active when you last closed TradingLab:",
                wraplength=380,
                justify="left",
            ).pack(anchor="w")
            ttk.Label(
                frame,
                text=desc,
                font=("TkDefaultFont", 10, "bold"),
            ).pack(anchor="w", pady=(4, 8))
            ttk.Label(
                frame,
                text=(
                    "Keep the resume hint on disk for a future release, or discard it now?"
                ),
                wraplength=380,
                justify="left",
            ).pack(anchor="w")
            btns = ttk.Frame(frame)
            btns.pack(anchor="e", pady=(12, 0))

            def _on_keep() -> None:
                try:
                    win.destroy()
                except tk.TclError:
                    pass

            def _on_discard() -> None:
                try:
                    clear_resume_metadata()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    win.destroy()
                except tk.TclError:
                    pass

            ttk.Button(btns, text="Keep for later", command=_on_keep).pack(side=tk.LEFT, padx=(0, 6))
            ttk.Button(btns, text="Discard", command=_on_discard).pack(side=tk.LEFT)
            win.protocol("WM_DELETE_WINDOW", _on_keep)
            try:
                win.grab_set()
            except tk.TclError:
                pass
            try:
                app.update_idletasks()
                px = app.winfo_rootx()
                py = app.winfo_rooty()
                pw = app.winfo_width()
                ph = app.winfo_height()
                win.update_idletasks()
                ww = win.winfo_width()
                wh = win.winfo_height()
                x = px + max((pw - ww) // 2, 0)
                y = py + max((ph - wh) // 2, 0)
                win.geometry(f"+{x}+{y}")
            except tk.TclError:
                pass
        except Exception:  # noqa: BLE001
            return

    def refresh_scanner_for_sandbox(self, *, app: Any, silent_tcl: Any) -> None:
        """Run saved scans against the current sandbox universe."""
        scanner_tab = getattr(app, "_scanner_tab", None)
        runner = getattr(app, "_scan_runner", None)
        sandbox = self._sandbox
        if scanner_tab is None or runner is None or sandbox is None:
            return
        scans = scanner_tab.get_active_scan_definitions()
        if not scans:
            return
        candles_by_symbol = sandbox.visible_candles_by_symbol
        if not candles_by_symbol:
            return
        app._scan_tick_id += 1
        try:
            ts = sandbox.current_session_date()
        except Exception:  # noqa: BLE001
            ts = None
        if ts is None:
            ts = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            try:
                ts = datetime.combine(ts, datetime.min.time())
            except Exception:  # noqa: BLE001
                ts = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            results = runner.run(
                scans=scans,
                candles_by_symbol=candles_by_symbol,
                interval=sandbox.interval,
                tick_id=app._scan_tick_id,
                timestamp=ts,
                last_bar_forming=False,
            )
        except Exception:  # noqa: BLE001
            return
        app._scan_last_results = results
        with silent_tcl():
            scanner_tab.set_results(results)

    def reset_scanner_state(self, *, app: Any, silent_tcl: Any) -> None:
        """Drop accumulated scanner history between sandbox sessions."""
        runner = getattr(app, "_scan_runner", None)
        if runner is not None:
            try:
                runner.reset_history()
            except Exception:  # noqa: BLE001
                pass
        app._scan_tick_id = 0
        app._scan_last_results = {}
        scanner_tab = getattr(app, "_scanner_tab", None)
        if scanner_tab is not None:
            with silent_tcl():
                scanner_tab.set_results({})

    def can_register(self, *, app: Any, sym: str) -> bool:
        """Strict-offline gate for mid-session symbol registration."""
        if not self.active:
            return True
        if not self._strict_offline:
            return True
        if not self._universe:
            return True
        if sym in self._universe:
            return True
        try:
            app._status.error(
                f"Sandbox strict offline: {sym} is not in the prepared universe "
                f"({self._universe_id or '?'}). Run Sandbox → Download Replay Data… first."
            )
        except Exception:  # noqa: BLE001
            pass
        return False

    def register_compare(self, *, app: Any, symbol: str, silent_tcl: Any) -> bool:
        """Register ``symbol`` and install it on the compare slot."""
        if not self.active or self._sandbox is None:
            return False
        sym = (symbol or "").strip().upper()
        if not sym:
            return False
        if not self.can_register(app=app, sym=sym):
            return False
        interval = self._sandbox.interval
        if sym not in self._sandbox.bars_by_symbol:
            src = _sandbox_preferred_src(app, interval)
            cached = app._full_cache.get((src, sym, interval))
            candles: list[Candle] = list(cached) if cached else []
            if not candles:
                fetcher = DATA_SOURCES.get(src)
                if fetcher is None:
                    return False
                try:
                    app._status.info(f"Sandbox: sync-fetching compare {sym} {interval}…")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    candles = list(fetcher(sym, interval) or [])
                except Exception as exc:  # noqa: BLE001
                    try:
                        app._status.error(f"Sandbox compare fetch failed: {exc}")
                    except Exception:  # noqa: BLE001
                        pass
                    return False
                if candles:
                    app._full_cache[(src, sym, interval)] = list(candles)
            if not candles:
                try:
                    app._status.warn(f"Sandbox: no compare data for {sym} at {interval}")
                except Exception:  # noqa: BLE001
                    pass
                return False
            try:
                self._sandbox.register_ticker(sym, candles)
            except ValueError as exc:
                try:
                    app._status.error(f"Sandbox compare register failed: {exc}")
                except Exception:  # noqa: BLE001
                    pass
                return False
        visible = self._sandbox.visible_candles_by_symbol.get(sym, [])
        self.install_compare_series(
            app=app,
            symbol=sym,
            candles=visible,
            interval=interval,
            silent_tcl=silent_tcl,
        )
        return True

    def sync_compare_to_var(self, *, app: Any, silent_tcl: Any) -> None:
        """Re-install compare to match ``compare_ticker_var`` during sandbox."""
        if not self.active or self._sandbox is None:
            return
        try:
            compare_on = bool(app.compare_var.get())
        except tk.TclError:
            compare_on = False
        if not compare_on:
            return
        try:
            desired = (app.compare_ticker_var.get() or "").strip().upper()
        except tk.TclError:
            desired = ""
        if not desired:
            return
        already = self._sandbox.visible_candles_by_symbol.get(desired)
        if already is not None and app._compare is already:
            return
        ok = self.register_compare(app=app, symbol=desired, silent_tcl=silent_tcl)
        if not ok:
            with silent_tcl():
                app.compare_ticker_var.set(app._confirmed_compare_ticker)
            return
        app._confirmed_compare_ticker = desired

    def register_and_focus(self, *, app: Any, symbol: str) -> bool:
        """Register ``symbol`` with the sandbox and focus it on primary."""
        if not self.active or self._sandbox is None:
            return False
        sym = (symbol or "").strip().upper()
        if not sym:
            return False
        if not self.can_register(app=app, sym=sym):
            return False
        interval = self._sandbox.interval
        if sym in self._sandbox.bars_by_symbol:
            self._sandbox.set_focus(sym)
            return True
        src = _sandbox_preferred_src(app, interval)
        cached = app._full_cache.get((src, sym, interval))
        candles: list[Candle] = list(cached) if cached else []
        if not candles:
            fetcher = DATA_SOURCES.get(src)
            if fetcher is None:
                try:
                    app._status.error("Sandbox: no fetcher for the selected data source")
                except Exception:  # noqa: BLE001
                    pass
                return False
            try:
                app._status.info(f"Sandbox: sync-fetching {sym} {interval}…")
            except Exception:  # noqa: BLE001
                pass
            try:
                candles = list(fetcher(sym, interval) or [])
            except Exception as exc:  # noqa: BLE001
                try:
                    app._status.error(f"Sandbox: fetch failed for {sym}: {exc}")
                except Exception:  # noqa: BLE001
                    pass
                return False
            if candles:
                app._full_cache[(src, sym, interval)] = list(candles)
        if not candles:
            try:
                app._status.warn(f"Sandbox: no data for {sym} at {interval}")
            except Exception:  # noqa: BLE001
                pass
            return False
        try:
            self._sandbox.register_ticker(sym, candles)
        except ValueError as exc:
            try:
                app._status.error(f"Sandbox: register {sym} failed: {exc}")
            except Exception:  # noqa: BLE001
                pass
            return False
        if int(getattr(self._sandbox, "daily_lookback_bars", 0) or 0) > 0:
            daily_key = (src, sym, "1d")
            cached_daily = app._full_cache.get(daily_key)
            daily_candles: list[Candle] = list(cached_daily) if cached_daily else []
            if not daily_candles:
                fetcher = DATA_SOURCES.get(src)
                if fetcher is not None:
                    try:
                        daily_candles = list(fetcher(sym, "1d") or [])
                    except Exception as exc:  # noqa: BLE001
                        try:
                            app._status.warn(
                                f"Sandbox: daily fetch for {sym} failed: {exc} — "
                                "1d context unavailable for this symbol"
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        daily_candles = []
                    if daily_candles:
                        app._full_cache[daily_key] = list(daily_candles)
            if daily_candles:
                try:
                    self._sandbox.register_daily_for(sym, daily_candles)
                except Exception:  # noqa: BLE001
                    pass
        self._sandbox.set_focus(sym)
        try:
            app._status.info(f"Sandbox: registered {sym} ({len(candles)} bars).")
        except Exception:  # noqa: BLE001
            pass
        return True

    def install_compare_series(
        self,
        *,
        app: Any,
        symbol: str,
        candles: list[Candle],
        interval: str,
        silent_tcl: Any,
    ) -> None:
        """Install a sandbox-controlled candle list on the compare slot."""
        sym_norm = (symbol or "").strip().upper()
        with silent_tcl():
            app.compare_ticker_var.set(sym_norm)
        app._confirmed_compare_ticker = sym_norm
        with silent_tcl():
            app.compare_var.set(True)
        app._set_data_state(compare=candles)
        try:
            app._series_cache.clear()
        except (AttributeError, TypeError):
            pass
        try:
            app._render()
        except Exception as exc:  # noqa: BLE001
            try:
                app._status.error(f"Sandbox compare install render failed: {exc}")
            except Exception:  # noqa: BLE001
                pass

    def restrict_toolbar_intervals(
        self,
        *,
        app: Any,
        display_intervals: list[str],
        daily_available: bool,
        silent_tcl: Any,
    ) -> None:
        """Restrict the toolbar interval combobox to sandbox values."""
        toolbar = getattr(app, "_toolbar", None)
        if toolbar is None:
            return
        new_values = list(display_intervals)
        if daily_available and "1d" not in new_values:
            new_values.append("1d")
        with silent_tcl():
            toolbar.lock_for_sandbox(tuple(new_values))

    def restore_toolbar_intervals(self, *, app: Any, silent_tcl: Any) -> None:
        """Undo toolbar interval restriction after sandbox end."""
        toolbar = getattr(app, "_toolbar", None)
        if toolbar is None:
            return
        with silent_tcl():
            toolbar.unlock()

    def reset_compare_for_session_start(
        self,
        *,
        app: Any,
        silent_tcl: Any,
        compare_default: str,
    ) -> None:
        """Clear pre-sandbox compare state before the session starts."""
        with silent_tcl():
            app.compare_var.set(False)
        app._set_data_state(compare=[])
        with silent_tcl():
            app.compare_ticker_var.set(compare_default)
        app._confirmed_compare_ticker = compare_default

    def install_primary_series(
        self,
        *,
        app: Any,
        symbol: str,
        candles: list[Candle],
        interval: str,
        full_session_length: int | None = None,
        silent_tcl: Any,
    ) -> None:
        """Replace the primary slot's data with a sandbox-controlled list."""
        app._cancel_background_fetch_jobs()
        with silent_tcl():
            app.ticker_var.set(symbol)
        app._confirmed_primary_ticker = (symbol or "").strip().upper()
        with silent_tcl():
            app.interval_var.set(interval)
        try:
            app._series_cache.clear()
        except (AttributeError, TypeError):
            pass
        cache = getattr(app, "_indicator_cache", None)
        if cache is not None:
            try:
                cache.clear()
            except AttributeError:
                pass
        app._set_data_state(primary=candles)
        try:
            app._render()
        except Exception as exc:  # noqa: BLE001
            try:
                app._status.error(f"Sandbox install render failed: {exc}")
            except Exception:  # noqa: BLE001
                pass
            return
        if full_session_length is not None and full_session_length > 0:
            ps = app._panel_state.get("primary") or {}
            ax_p = ps.get("price_ax")
            if ax_p is not None:
                lo = -0.5
                hi = float(full_session_length) - 0.5
                try:
                    ax_p.set_xlim(lo, hi)
                    app._sandbox_full_session_xlim = (lo, hi)
                    app._preserve_xlim_on_render = True
                    app._slide_xlim_to_right_edge = False
                    app._autoscale_y_to_visible()
                    app._canvas.draw_idle()
                except Exception:  # noqa: BLE001
                    pass
        else:
            app._sandbox_full_session_xlim = None


__all__ = ("SandboxAppController",)
