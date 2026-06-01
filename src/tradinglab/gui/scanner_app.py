"""ScannerAppMixin — Scanner-tab construction + per-row action routing.

Extracted from :class:`tradinglab.app.ChartApp` in wave-3 of the
god-file shrink (see CLAUDE.md §7.24). Owns six methods that bridge
the right-side ``ScannerTab`` notebook page to the rest of the app:

* :meth:`_build_scanner_tab` — autoloads saved scans from
  ``<cache>/scans/``, wires the per-row action callback, mounts the
  tab on ``self._notebook``.
* :meth:`_on_scanner_scan_saved` — persist to
  ``scanner.storage`` after a Save in the editor.
* :meth:`_on_scanner_scan_deleted` — delete from
  ``scanner.storage`` and clear the runner's per-scan history.
* :meth:`_on_scanner_row_action` — primary / compare / watchlist
  routing of a single scan result row. Goes through the
  sandbox register-and-focus paths when a session is active,
  else falls back to ``ticker_var.set`` / watchlist append.
* :meth:`_refresh_scanner_for_sandbox` — thin delegate to
  ``_sandbox_ctrl.refresh_scanner_for_sandbox``.
* :meth:`_reset_scanner_state` — thin delegate to
  ``_sandbox_ctrl.reset_scanner_state``.

Mixin rules (§7.24):

1. NO ``__init__``, NO ``super().__init__()``. All instance state
   (``self._scanner_storage``, ``self._scan_runner``, ``self._scan_tick_id``,
   ``self._scan_last_results``, ``self._scanner_tab``) is owned by
   ``ChartApp.__init__`` / ``_build_scanner_tab``.
2. Must be inserted alphabetically among the mixin block in the
   ``ChartApp`` MRO declaration.
3. ``tk.Tk`` stays last.

The ``_silent_tcl`` helper imported here is a module-local clone of
the one in ``app.py``: a thin ``contextmanager`` that swallows
``tk.TclError`` (plus extra exception classes passed by the caller).
The sandbox-controller delegates use it; isolating it here keeps the
mixin import-clean (no back-import of ``tradinglab.app``).
"""

from __future__ import annotations

import logging
import tkinter as tk
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

logger = logging.getLogger(__name__)


@contextmanager
def _silent_tcl(*extra_excs: type[BaseException]):
    """Swallow ``tk.TclError`` (and any extra exception classes).

    Mirrors the helper of the same name in ``tradinglab.app``. Used
    by sandbox-controller delegates so a torn-down widget during
    teardown doesn't surface in user-facing status messages.
    """
    classes: tuple[type[BaseException], ...] = (tk.TclError, *extra_excs)
    try:
        yield
    except classes:  # noqa: BLE001 — surface intentionally
        pass


class ScannerAppMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    # ------------------------------------------------------------------
    # Scanner integration (sandbox-driven block-tree screener)
    # ------------------------------------------------------------------

    def _build_scanner_tab(self) -> None:
        """Construct the right-side Scanner notebook tab.

        Auto-loads any saved scans from ``<cache>/scans/`` and wires
        the per-row action callback to the existing primary/compare
        register-and-focus paths. Failures during autoload degrade
        gracefully to an empty library — the user can re-import.
        """
        from ..scanner import storage as _scan_storage
        from ..scanner.runner import ScanRunner
        from .scanner_tab import ScannerTab

        library: dict[str, Any] = {}
        try:
            library = {s.id: s for s in _scan_storage.load_all()}
        except Exception:  # noqa: BLE001
            try:
                self._status.warn(
                    "Scanner: failed to load saved scans; starting empty")
            except Exception:  # noqa: BLE001
                pass
            library = {}

        self._scanner_storage = _scan_storage
        self._scan_runner = ScanRunner()
        self._scan_tick_id: int = 0
        self._scan_last_results: dict[str, Any] = {}

        self._scanner_tab = ScannerTab(
            self._notebook,
            library=library,
            on_scan_saved=self._on_scanner_scan_saved,
            on_scan_deleted=self._on_scanner_scan_deleted,
            on_row_action=self._on_scanner_row_action,
        )
        self._notebook.add(self._scanner_tab, text="Scanner")

    def _on_scanner_scan_saved(self, scan: Any) -> None:
        try:
            self._scanner_storage.save(scan)
        except Exception:  # noqa: BLE001
            try:
                self._status.error(
                    f"Scanner: failed to save scan {scan.name!r}")
            except Exception:  # noqa: BLE001
                pass

    def _on_scanner_scan_deleted(self, scan_id: str) -> None:
        try:
            self._scanner_storage.delete(scan_id)
        except Exception:  # noqa: BLE001
            try:
                self._status.error(
                    f"Scanner: failed to delete scan {scan_id!r}")
            except Exception:  # noqa: BLE001
                pass
        # Drop any stale history so a re-created scan starts fresh.
        runner = getattr(self, "_scan_runner", None)
        if runner is not None:
            try:
                runner.reset_history(scan_id)
            except Exception:  # noqa: BLE001
                pass

    def _on_scanner_row_action(self, symbol: str, kind: str) -> None:
        """User picked a row + an action from the Scanner result table.

        ``kind`` is ``"primary"``, ``"compare"`` or ``"watchlist"``.
        Routes through the existing sandbox register-and-focus paths
        when a session is active; otherwise falls back to the regular
        ``ticker_var.set`` / ``compare_ticker_var.set`` plumbing.
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return
        sandbox_on = self._is_sandbox_active()
        try:
            if kind == "primary":
                if sandbox_on:
                    self._sandbox_register_and_focus(sym)
                else:
                    self.ticker_var.set(sym)
                    if hasattr(self, "_load_data"):
                        try:
                            self._load_data()
                        except Exception:  # noqa: BLE001
                            pass
            elif kind == "compare":
                if sandbox_on:
                    try:
                        self.compare_var.set(True)
                    except Exception:  # noqa: BLE001
                        pass
                    self._sandbox_register_compare(sym)
                else:
                    try:
                        self.compare_var.set(True)
                        self.compare_ticker_var.set(sym)
                    except Exception:  # noqa: BLE001
                        pass
            elif kind == "watchlist":
                # Best-effort: append to the active pinned watchlist if
                # the watchlist manager is available. Tolerate missing
                # APIs (smoke tests run without one configured).
                wl_mgr = getattr(self, "_watchlist_manager", None)
                if wl_mgr is None:
                    return
                try:
                    name = self.watchlist_var.get()
                except Exception:  # noqa: BLE001
                    name = ""
                if not name:
                    return
                try:
                    wl_mgr.add_ticker(name, sym)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self._populate_watchlist_tab(name)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            try:
                self._status.error(
                    f"Scanner: action {kind!r} on {sym} failed")
            except Exception:  # noqa: BLE001
                pass

    def _refresh_scanner_for_sandbox(self) -> None:
        self._sandbox_ctrl.refresh_scanner_for_sandbox(
            app=self, silent_tcl=_silent_tcl,
        )

    def _reset_scanner_state(self) -> None:
        self._sandbox_ctrl.reset_scanner_state(
            app=self, silent_tcl=_silent_tcl,
        )
