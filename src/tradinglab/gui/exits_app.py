"""ExitsAppMixin: glue between :class:`ChartApp` and the exits package.

Owned objects (constructed in :meth:`_build_exits_stack`):

* ``self._audit_log``         — :class:`exits.audit.AuditLog`
* ``self._position_tracker``  — :class:`positions.tracker.PositionTracker`
* ``self._paper_engine``      — :class:`exits.paper_engine.PaperBrokerEngine`
* ``self._paper_sink``        — :class:`exits.signals.PaperBrokerSink`
* ``self._exit_evaluator``    — :class:`exits.evaluator.ExitEvaluator`
* ``self._exits_tab``         — :class:`gui.exits_tab.ExitsTab`
* ``self._exits_overlay``     — :class:`gui.exits_overlay.ExitsOverlay`
* ``self._exits_dialog``      — :class:`gui.exits_dialog.ExitsDialog` (lazy)

Wiring sites in :class:`ChartApp`:

* ``__init__`` calls :meth:`_build_exits_stack` after the Scanner tab is up.
* ``_render`` calls :meth:`_redraw_exits_overlay` at the end (artist family
  must be re-attached after every ``figure.clear()``).
* ``backtest.replay.SandboxController.next_bar`` calls
  :meth:`_refresh_exits_for_sandbox` per tick.
* ``_on_close`` calls :meth:`_close_exits_stack` for clean teardown.

The mixin is a no-op on the chart pipeline if the user never opens or
attaches an exit strategy — the overlay renders zero artists, the
evaluator carries zero attachments, and the per-tick hook walks an
empty position list. This keeps the cost-of-doing-nothing near zero.
"""
from __future__ import annotations

import logging
import tkinter as tk
from typing import Any

logger = logging.getLogger(__name__)


class ExitsAppMixin:
    """Mixin for :class:`ChartApp`. See module docstring for the contract."""

    # --- Type stubs to keep static type checkers happy -----------------
    # These attributes are owned by ChartApp but referenced here.
    _notebook: Any
    _ax_price: Any
    _sandbox: Any
    ticker_var: tk.StringVar

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_exits_stack(self) -> None:
        """Construct the exits stack + UI and attach to the right notebook.

        Called from :meth:`ChartApp.__init__` after :meth:`_build_scanner_tab`.
        Failure here must NOT block app startup — degrade to no-op + log.
        """
        # Lazy imports keep import cost off the cold-start path for users
        # who never touch exits.
        from ..exits.audit import AuditLog
        from ..exits.evaluator import ExitEvaluator
        from ..exits.paper_engine import PaperBrokerEngine
        from ..exits.signals import PaperBrokerSink
        from ..positions.tracker import PositionTracker
        from .exits_overlay import ExitsOverlay
        from .exits_tab import ExitsTab

        self._audit_log: AuditLog | None = None
        self._position_tracker: PositionTracker | None = None
        self._paper_engine: PaperBrokerEngine | None = None
        self._paper_sink: PaperBrokerSink | None = None
        self._exit_evaluator: ExitEvaluator | None = None
        self._exits_tab: ExitsTab | None = None
        self._exits_overlay: ExitsOverlay | None = None
        self._exits_dialog = None  # lazy modeless singleton

        try:
            self._audit_log = AuditLog()
            self._position_tracker = PositionTracker()
            self._paper_engine = PaperBrokerEngine(self._position_tracker)
            self._paper_sink = PaperBrokerSink(self._paper_engine)
            self._exit_evaluator = ExitEvaluator(
                tracker=self._position_tracker,
                sink=self._paper_sink,
                audit=self._audit_log,
            )
            self._exits_tab = ExitsTab(
                self._notebook,
                tracker=self._position_tracker,
                evaluator=self._exit_evaluator,
                audit=self._audit_log,
                on_open_dialog=self._on_open_exits_dialog,
            )
            self._notebook.add(self._exits_tab, text="Exits")
            self._exits_overlay = ExitsOverlay(
                evaluator=self._exit_evaluator,
                tracker=self._position_tracker,
                request_redraw=self._request_exits_overlay_redraw,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "ExitsAppMixin: failed to build exits stack; degrading to no-op")
            # Best-effort cleanup of partial state.
            self._close_exits_stack()

    # ------------------------------------------------------------------
    # Per-tick + per-render hooks
    # ------------------------------------------------------------------

    def _refresh_exits_for_sandbox(self) -> None:
        """Per-tick hook called from :meth:`replay.SandboxController.next_bar`.

        For each open position whose symbol has visible candles, build a
        :class:`exits.spec.Bar` from the latest visible candle and feed
        it to BOTH the evaluator (trigger evaluation) and the paper
        engine (working-order fills). Order matters: evaluator first so
        any newly-fired exits land as working orders BEFORE the same
        bar's paper-engine pass tries to fill them.
        """
        evaluator = getattr(self, "_exit_evaluator", None)
        engine = getattr(self, "_paper_engine", None)
        tracker = getattr(self, "_position_tracker", None)
        sb = getattr(self, "_sandbox", None)
        if evaluator is None or tracker is None or sb is None:
            return
        candles_by_symbol = getattr(sb, "visible_candles_by_symbol", None) or {}
        if not candles_by_symbol:
            return

        from ..exits.spec import Bar

        for pos in list(tracker.list_open()):
            sym = (pos.symbol or "").strip().upper()
            candles = candles_by_symbol.get(sym) or candles_by_symbol.get(pos.symbol)
            if not candles:
                continue
            last = candles[-1]
            try:
                bar = Bar(
                    open=float(last.open),
                    high=float(last.high),
                    low=float(last.low),
                    close=float(last.close),
                    volume=float(getattr(last, "volume", 0.0) or 0.0),
                    date=getattr(last, "date", None),
                )
            except Exception:  # noqa: BLE001
                continue
            try:
                evaluator.on_bar(pos.id, bar, is_close=True)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "ExitsAppMixin: evaluator.on_bar raised for %s", pos.id)
            if engine is not None:
                try:
                    engine.on_bar(pos.id, bar, is_close=True)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "ExitsAppMixin: paper_engine.on_bar raised for %s",
                        pos.id)

        # Refresh the Exits tab UI to reflect any state changes.
        tab = getattr(self, "_exits_tab", None)
        if tab is not None:
            try:
                tab.refresh()
            except tk.TclError:
                pass

    def _redraw_exits_overlay(self) -> None:
        """Re-attach the overlay artists to the freshly-built primary axes.

        Called from the end of :meth:`ChartApp._render` (after
        :meth:`_ensure_overlay_artists`). Safe to call when the overlay
        was never built (graceful no-op).
        """
        overlay = getattr(self, "_exits_overlay", None)
        if overlay is None:
            return
        ax = getattr(self, "_ax_price", None)
        symbol = ""
        try:
            symbol = (self.ticker_var.get() or "").strip()
        except Exception:  # noqa: BLE001
            symbol = ""
        try:
            overlay.redraw(ax, symbol or None)
        except Exception:  # noqa: BLE001
            logger.exception("ExitsAppMixin: overlay.redraw raised")

    def _request_exits_overlay_redraw(self) -> None:
        """Callback handed to :class:`ExitsOverlay` for non-render-driven repaint.

        We use ``after(50, ...)`` to debounce bursts (e.g., open + fill
        events arriving back-to-back from a single tick). The ``_render``
        path is the canonical artist-rebuild site, so we call it here.
        """
        try:
            self.after(50, self._safe_full_render)
        except (tk.TclError, RuntimeError):
            pass

    def _safe_full_render(self) -> None:
        """Render guard. Calls :meth:`ChartApp._render` if available."""
        render = getattr(self, "_render", None)
        if render is None:
            return
        try:
            render()
        except Exception:  # noqa: BLE001
            logger.exception("ExitsAppMixin: _render raised")

    # ------------------------------------------------------------------
    # Dialog launch
    # ------------------------------------------------------------------

    def _on_open_exits_dialog(self) -> None:
        """ExitsTab "Edit Strategies…" button + menu entry click.

        Lazily constructs (or re-shows) the modeless library editor.
        """
        from .exits_dialog import open_exits_dialog
        try:
            open_exits_dialog(self, on_library_changed=self._on_exits_library_changed)
        except Exception:  # noqa: BLE001
            logger.exception("ExitsAppMixin: open_exits_dialog raised")

    def _on_exits_library_changed(self) -> None:
        """Library mutation callback from the dialog → refresh the tab."""
        tab = getattr(self, "_exits_tab", None)
        if tab is not None:
            try:
                tab.refresh()
            except (tk.TclError, AttributeError):
                pass

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def _close_exits_stack(self) -> None:
        """Idempotent teardown. Safe to call from :meth:`_on_close`."""
        # Order: dialog → overlay → evaluator → tab. Each catches its
        # own errors so partial teardown still progresses.
        dialog = getattr(self, "_exits_dialog", None)
        if dialog is not None:
            try:
                dialog.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._exits_dialog = None

        overlay = getattr(self, "_exits_overlay", None)
        if overlay is not None:
            try:
                overlay.close()
            except Exception:  # noqa: BLE001
                pass
            self._exits_overlay = None

        evaluator = getattr(self, "_exit_evaluator", None)
        if evaluator is not None:
            try:
                evaluator.close()
            except Exception:  # noqa: BLE001
                pass
            self._exit_evaluator = None

        # Tab destroys itself with the notebook on window close.
        self._exits_tab = None
        self._paper_sink = None
        self._paper_engine = None
        self._position_tracker = None
        self._audit_log = None
