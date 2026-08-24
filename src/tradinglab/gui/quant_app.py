"""``ChartApp`` glue for the **Quant** side tab (`gui/quant_tab.py`).

Extracted as a mixin per AGENTS.md §7.24 — it holds no ``__init__`` and all
persistent state lives on ``ChartApp``.

Three jobs:

1. **Lifecycle.** The tab is added to the side notebook once at startup with
   ``state="hidden"``, mirroring the Sandbox tab, so notebook indices stay
   stable for the life of the process. The Tools → Quant checkbutton flips
   it between ``"hidden"`` and ``"normal"``.
2. **Activation.** A double-click routes the row's symbol to the primary or
   compare slot using the same ``_last_hovered_slot`` rule the watchlist uses
   — the slot the user was last looking at is the one that loads.
3. **The Last column.** Quant rows are macro gauges, so Last is derived from
   **daily** bars regardless of the chart's interval: an intraday last for
   ``VIX/15.87`` would read as a different quantity from the row's stated
   meaning. Values are refreshed lazily and only while the tab is on screen.

The refresh deliberately reuses ``_apply_watchlist_snapshot_from_bars`` — the
app's shared snapshot seam — rather than deriving a last close locally. That
seam already owns the sandbox-clock slicing that keeps replay sessions free
of look-ahead bias, and §7.34 is explicit that re-copying a primitive is how
these paths drift apart. ``_watchlist_snapshot`` is a flat ``symbol -> dict``
store; the ``watchlist`` in the name is historical.
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from typing import Any

logger = logging.getLogger(__name__)

#: Interval used for the Last column. See the module docstring.
QUANT_LAST_INTERVAL = "1d"

#: Cadence of the visible-tab refresh tick. Generous on purpose: these are
#: daily series, and a fresh cache short-circuits the tick to zero HTTP.
QUANT_REFRESH_MS = 30_000


class QuantAppMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _build_quant_tab(self) -> None:
        """Construct the hidden **Quant** notebook tab.

        Added at startup (not on first reveal) so notebook tab indices are
        fixed for the process lifetime — the same reason the Sandbox tab is
        added hidden. A construction failure degrades to "no tab": the menu
        entry then no-ops rather than raising into the menubar callback.
        """
        from .quant_tab import QuantTab

        self._quant_tab: Any = None
        self._quant_refresh_job: str | None = None
        self._quant_fetch_inflight: set[str] = set()
        try:
            self._quant_tab = QuantTab(
                self._notebook,
                on_row_activate=self._on_quant_row_activate,
                on_unavailable=self._on_quant_row_unavailable,
            )
            self._notebook.add(self._quant_tab, text="Quant", state="hidden")
        except Exception:  # noqa: BLE001
            logger.exception("Failed to build the Quant tab")
            self._quant_tab = None
            return
        self._apply_quant_theme()

    def _on_tools_toggle_quant(self) -> None:
        """**Tools → Quant** checkbutton — reveal or hide the tab.

        Revealing also selects the tab and kicks an immediate refresh, so the
        user sees the panel populate rather than a column of blanks.
        """
        tab = getattr(self, "_quant_tab", None)
        try:
            want = bool(self._quant_visible_var.get())
        except Exception:  # noqa: BLE001
            want = False
        if tab is None:
            return
        try:
            self._notebook.tab(tab, state="normal" if want else "hidden")
        except tk.TclError:
            return
        if want:
            try:
                self._notebook.select(tab)
            except tk.TclError:
                pass
            self._apply_quant_theme()
            self._start_quant_refresh_loop()
        else:
            self._stop_quant_refresh_loop()

    def _quant_tab_visible(self) -> bool:
        """True when the Quant tab is revealed AND is the selected tab.

        Deliberately keyed off ``Notebook.select()`` rather than
        ``winfo_viewable()``. Mapping is asynchronous: immediately after
        ``select()`` the widget is not yet viewable, so a ``winfo_viewable``
        gate skipped the very first refresh and left the user staring at an
        empty Last column until the next tick. Selection is exact and
        available synchronously.

        Falls back to ``True`` when the notebook can't be probed so a
        headless harness never starves a genuinely-revealed tab.
        """
        tab = getattr(self, "_quant_tab", None)
        if tab is None:
            return False
        try:
            if not bool(self._quant_visible_var.get()):
                return False
        except Exception:  # noqa: BLE001
            return False
        try:
            return self._notebook.select() == str(tab)
        except Exception:  # noqa: BLE001
            return True

    # ------------------------------------------------------------------
    # Row activation
    # ------------------------------------------------------------------

    def _on_quant_row_activate(self, symbol: str) -> None:
        """Load a double-clicked Quant row onto the chart.

        Mirrors ``_on_watchlist_double``: the target slot is whichever chart
        panel the mouse was last over (``_last_hovered_slot``), and compare
        routing only applies when compare mode is actually on. The Notebook
        is intentionally NOT switched away from the Quant tab, so the user
        can click through several gauges in a row.
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return
        slot = getattr(self, "_last_hovered_slot", "primary") or "primary"
        try:
            compare_on = bool(self.compare_var.get())
        except Exception:  # noqa: BLE001
            compare_on = False
        try:
            if slot == "compare" and compare_on:
                if sym == self.compare_ticker_var.get().strip().upper():
                    return
                self.compare_ticker_var.set(sym)
            else:
                if sym == self.ticker_var.get().strip().upper():
                    return
                self.ticker_var.set(sym)
        except Exception:  # noqa: BLE001
            return
        if (getattr(self, "_drilldown_day", None) is not None
                and self.interval_var.get() == "5m"):
            self._reload_preserving_drilldown(self._load_data)
            return
        try:
            self._preserve_xlim_by_time_on_render = (
                self._ticker_change_should_time_preserve())
        except Exception:  # noqa: BLE001
            pass
        self._load_data_async()

    def _on_quant_row_unavailable(self, row: Any) -> None:
        """Explain an inert row in the status bar instead of doing nothing."""
        try:
            self._status.warn(f"{row.name}: {row.unavailable_reason}")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Last column
    # ------------------------------------------------------------------

    def _start_quant_refresh_loop(self) -> None:
        """Refresh now, then re-arm the periodic tick (idempotent)."""
        self._stop_quant_refresh_loop()
        self._quant_refresh_tick()

    def _stop_quant_refresh_loop(self) -> None:
        job = getattr(self, "_quant_refresh_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except (tk.TclError, ValueError):
                pass
        self._quant_refresh_job = None

    def _quant_refresh_tick(self) -> None:
        """Repaint Last from snapshots, submit fetches, re-arm.

        The tick keeps running while the tab is merely *revealed* — the
        visibility gate only suppresses network work, not the repaint. That
        way switching back to the tab shows current values immediately
        rather than after a full period.
        """
        self._quant_refresh_job = None
        tab = getattr(self, "_quant_tab", None)
        if tab is None:
            return
        try:
            self._paint_quant_last_values()
            if self._quant_tab_visible():
                self._submit_quant_fetches()
        except Exception:  # noqa: BLE001
            logger.exception("Quant refresh tick failed")
        try:
            if bool(self._quant_visible_var.get()):
                self._quant_refresh_job = self.after(
                    QUANT_REFRESH_MS, self._quant_refresh_tick)
        except Exception:  # noqa: BLE001
            pass

    def _paint_quant_last_values(self) -> None:
        """Push formatted Last text for every symbol that has a snapshot."""
        tab = getattr(self, "_quant_tab", None)
        if tab is None:
            return
        snapshots = getattr(self, "_watchlist_snapshot", {})
        values: dict[str, str] = {}
        for sym in tab.symbols():
            last = (snapshots.get(sym.upper()) or {}).get("last")
            if isinstance(last, (int, float)):
                values[sym] = self._format_quant_last(last)
        if values:
            tab.set_last_values(values)

    @staticmethod
    def _format_quant_last(value: float) -> str:
        """Format one Last cell.

        Quant rows span four orders of magnitude — ``BTC-USD`` near 80,000
        and ``RSP/SPY`` near 0.29 — so a single fixed precision is unreadable
        at one end or the other. Precision therefore scales with magnitude.
        """
        magnitude = abs(value)
        if magnitude >= 1000:
            return f"{value:,.0f}"
        if magnitude >= 10:
            return f"{value:,.2f}"
        if magnitude >= 1:
            return f"{value:,.3f}"
        return f"{value:,.4f}"

    def _submit_quant_fetches(self) -> None:
        """Fetch daily bars for any Quant symbol without a fresh cache entry.

        Guarded three ways so a 30-second tick over ~29 symbols costs nothing
        in steady state: a fresh ``_full_cache`` entry short-circuits, an
        in-flight marker prevents double submission, and a missing executor
        (teardown, unit harness) skips silently.
        """
        tab = getattr(self, "_quant_tab", None)
        executor = getattr(self, "_fetch_executor", None)
        if tab is None or executor is None:
            return
        try:
            src = self.source_var.get()
        except Exception:  # noqa: BLE001
            return
        snapshots = getattr(self, "_watchlist_snapshot", {})
        for sym in tab.symbols():
            key = (src, sym, QUANT_LAST_INTERVAL)
            if sym in self._quant_fetch_inflight:
                continue
            cached = self._full_cache.get(key)
            if cached:
                # Cache is warm. Derive the snapshot if it is missing (a
                # restart repopulates _full_cache from disk before any Quant
                # snapshot exists), otherwise leave it alone.
                if "last" not in (snapshots.get(sym.upper()) or {}):
                    try:
                        self._apply_watchlist_snapshot_from_bars(
                            sym, src, QUANT_LAST_INTERVAL, cached)
                    except Exception:  # noqa: BLE001
                        pass
                if not self._cache_is_stale(cached, QUANT_LAST_INTERVAL):
                    continue
            self._quant_fetch_inflight.add(sym)
            try:
                executor.submit(self._fetch_quant_last, sym, src)
            except Exception:  # noqa: BLE001
                self._quant_fetch_inflight.discard(sym)

    def _fetch_quant_last(self, symbol: str, src: str) -> None:
        """Worker-thread body: fetch daily bars and record the snapshot.

        Runs off the Tk thread, so it must not call ``self.after`` (§7.15).
        Bars are handed to the Tk thread through ``_worker_inbox``; the
        snapshot seam is called directly, matching ``_preload_one_last``.
        """
        from ..data import DATA_SOURCES

        try:
            fetcher = DATA_SOURCES.get(src)
            if fetcher is None:
                return
            bars = fetcher(symbol, QUANT_LAST_INTERVAL)
            if not bars:
                return
            try:
                self._apply_watchlist_snapshot_from_bars(
                    symbol, src, QUANT_LAST_INTERVAL, bars)
            except Exception:  # noqa: BLE001
                pass
            key = (src, symbol, QUANT_LAST_INTERVAL)
            rows = list(bars)
            if threading.current_thread() is threading.main_thread():
                self._stash_full_cache(key, rows)
            else:
                self._worker_inbox.put_nowait(("stash", (key, rows)))
        except Exception:  # noqa: BLE001
            logger.debug("Quant last fetch failed for %s", symbol, exc_info=True)
        finally:
            self._quant_fetch_inflight.discard(symbol)

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def _apply_quant_theme(self) -> None:
        """Re-tag Quant rows for the active palette.

        ``ttk.Style`` reaches the Treeview body but not per-tag foregrounds
        (§7.31 is the same class of gap for classic widgets), so the muted
        colour of a disabled row has to be re-applied on every theme flip.
        """
        tab = getattr(self, "_quant_tab", None)
        if tab is None:
            return
        try:
            dark = bool(self.dark_var.get())
        except Exception:  # noqa: BLE001
            dark = False
        muted = "#7a7a7a" if dark else "#9a9a9a"
        group = "#d0d0d0" if dark else "#333333"
        try:
            tab.apply_theme(muted_fg=muted, group_fg=group)
        except Exception:  # noqa: BLE001
            pass


__all__ = ["QUANT_LAST_INTERVAL", "QUANT_REFRESH_MS", "QuantAppMixin"]
