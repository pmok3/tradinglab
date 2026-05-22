"""EntriesAppMixin: glue between :class:`ChartApp` and the entries package.

Owned objects (constructed in :meth:`_build_entries_stack`):

* ``self._entries_audit_log``  — :class:`entries.audit.AuditLog`
* ``self._entry_paper_sink``   — :class:`entries.signals.EntryPaperSink`
* ``self._entry_evaluator``    — :class:`entries.evaluator.EntryEvaluator`
* ``self._entries_tab``        — :class:`gui.entries_tab.EntriesTab`
* ``self._entries_overlay``    — :class:`gui.entries_overlay.EntriesOverlay`

The mixin assumes :class:`ExitsAppMixin._build_exits_stack` has already
run (the contract requires entries-after-exits in MRO + entries-before-
exits in tab insert order — see :class:`ChartApp` wiring). Specifically,
the entries stack reuses ``self._position_tracker`` and
``self._paper_engine`` from the exits stack so both subsystems share a
single tracker + broker.

Wiring sites in :class:`ChartApp`:

* ``__init__`` calls :meth:`_build_entries_stack` AFTER
  :meth:`_build_exits_stack` (entries depends on exits' tracker +
  paper_engine).
* :meth:`_render` calls :meth:`_redraw_entries_overlay` AFTER
  :meth:`_redraw_exits_overlay`.
* ``backtest.replay.SandboxController.next_bar`` calls
  :meth:`_refresh_entries_for_sandbox` per tick, BEFORE the existing
  :meth:`_refresh_exits_for_sandbox` call (entries-fire-first ordering).
* :meth:`_on_close` calls :meth:`_close_entries_stack` BEFORE
  :meth:`_close_exits_stack`.

ScanRunner subscribe-signature bridge:
    The real :class:`scanner.runner.ScanRunner.subscribe` invokes
    ``cb(scan_id, ScanResult)`` (two positional args) but
    :meth:`EntryEvaluator._on_scan_results` expects a
    ``Dict[scan_id, ScanResult]``. We construct the evaluator with
    ``scan_runner=None`` and then manually subscribe an adapter lambda
    that wraps the ``(scan_id, result)`` callback into a single-entry
    dict the evaluator understands.
"""
from __future__ import annotations

import logging
import tkinter as tk
from typing import Any, Optional

logger = logging.getLogger(__name__)


class EntriesAppMixin:
    """Mixin for :class:`ChartApp`. See module docstring for the contract."""

    # --- Type stubs to keep static type checkers happy -----------------
    _notebook: Any
    _ax_price: Any
    _sandbox: Any
    ticker_var: tk.StringVar

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_entries_stack(self) -> None:
        """Construct the entries stack + UI and attach to the right notebook.

        Inserted into the notebook BEFORE the Exits tab (entries fire
        first conceptually). Failure here must NOT block app startup —
        degrade to no-op + log.
        """
        from ..entries.audit import AuditLog as EntriesAuditLog
        from ..entries.evaluator import EntryEvaluator
        from ..entries.signals import EntryPaperSink
        from .entries_overlay import EntriesOverlay
        from .entries_tab import EntriesTab

        self._entries_audit_log: Optional[EntriesAuditLog] = None
        self._entry_paper_sink: Optional[EntryPaperSink] = None
        self._entry_evaluator: Optional[EntryEvaluator] = None
        self._entries_tab: Optional[EntriesTab] = None
        self._entries_overlay: Optional[EntriesOverlay] = None
        self._entries_dialog = None
        self._entries_scan_unsubscribe = None

        # The entries stack rides on the same tracker + paper engine as
        # exits. If exits failed to build, we degrade to no-op cleanly.
        tracker = getattr(self, "_position_tracker", None)
        paper_engine = getattr(self, "_paper_engine", None)
        if tracker is None or paper_engine is None:
            logger.warning(
                "EntriesAppMixin: tracker/paper_engine missing; "
                "skipping entries stack"
            )
            return

        try:
            self._entries_audit_log = EntriesAuditLog()
            self._entry_paper_sink = EntryPaperSink(paper_engine)
            self._entry_evaluator = EntryEvaluator(
                tracker=tracker,
                sink=self._entry_paper_sink,
                audit=self._entries_audit_log,
                bars_registry=getattr(self, "_bars_registry", None),
                # ScanRunner bridge: subscribe manually below.
                scan_runner=None,
                exit_evaluator=getattr(self, "_exit_evaluator", None),
                exit_storage=self._lazy_exit_storage(),
                get_active_symbol=self._get_active_symbol_for_entries,
            )

            # Subscribe to the modal-bracket-pick request stream so that
            # filled entries with no on_fill_exit_ids prompt the user.
            try:
                self._entry_evaluator.subscribe_modal_request(
                    self._on_entries_modal_request)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "EntriesAppMixin: subscribe_modal_request raised")

            # Manual ScanRunner bridge.
            scan_runner = getattr(self, "_scan_runner", None)
            if scan_runner is not None and hasattr(scan_runner, "subscribe"):
                evaluator = self._entry_evaluator

                def _scan_bridge(scan_id, result):
                    try:
                        evaluator._on_scan_results({scan_id: result})
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "EntriesAppMixin: scan bridge raised")

                try:
                    self._entries_scan_unsubscribe = scan_runner.subscribe(
                        _scan_bridge)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "EntriesAppMixin: scan_runner.subscribe raised")

            # Build the tab + insert it BEFORE the Exits tab.
            self._entries_tab = EntriesTab(
                self._notebook,
                evaluator=self._entry_evaluator,
                exit_storage=self._lazy_exit_storage(),
            )
            exits_tab = getattr(self, "_exits_tab", None)
            insert_idx = "end"
            if exits_tab is not None:
                try:
                    insert_idx = self._notebook.index(str(exits_tab))
                except (tk.TclError, AttributeError):
                    insert_idx = "end"
            try:
                self._notebook.insert(insert_idx, self._entries_tab,
                                      text="Entries")
            except (tk.TclError, AttributeError):
                # Fallback: append.
                self._notebook.add(self._entries_tab, text="Entries")

            self._entries_overlay = EntriesOverlay(
                evaluator=self._entry_evaluator,
                paper_engine=paper_engine,
                request_redraw=self._request_entries_overlay_redraw,
            )

            # Within-last-N-bars evidence overlay: vertical markers on
            # the primary chart at every bar where a fired entry / exit
            # trigger's look-back walk found a confirming match. Reads
            # from both audit logs + the position tracker (to resolve
            # exit fire records to symbols). Safe no-op if either
            # audit handle isn't ready yet.
            try:
                from .evidence_overlay import EvidenceOverlay
                self._evidence_overlay = EvidenceOverlay(
                    entries_audit=self._entries_audit_log,
                    exits_audit=getattr(self, "_exits_audit_log", None),
                    tracker=tracker,
                    request_redraw=self._request_entries_overlay_redraw,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "EntriesAppMixin: failed to build evidence overlay; "
                    "degrading to no-op"
                )
                self._evidence_overlay = None
        except Exception:  # noqa: BLE001
            logger.exception(
                "EntriesAppMixin: failed to build entries stack; "
                "degrading to no-op"
            )
            self._close_entries_stack()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _lazy_exit_storage(self):
        """Return the exits storage module (for on_fill bracket binding)."""
        try:
            from ..exits import storage as exit_storage
            return exit_storage
        except Exception:  # noqa: BLE001
            logger.exception("EntriesAppMixin: exits.storage import failed")
            return None

    def _get_active_symbol_for_entries(self) -> Optional[str]:
        """Provide the primary chart symbol to the evaluator (used by
        ``Universe.from_attached_chart``)."""
        try:
            sym = (self.ticker_var.get() or "").strip()
            return sym or None
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Per-tick + per-render hooks
    # ------------------------------------------------------------------

    def _refresh_entries_for_sandbox(self) -> None:
        """Per-tick hook called from :meth:`replay.SandboxController.next_bar`.

        For each symbol with visible candles, build a
        :class:`exits.spec.Bar` from the last visible candle and feed
        them all to :meth:`EntryEvaluator.on_tick` as a single
        ``Dict[symbol, Bar]``. ``last_bar_forming=False`` because each
        replay tick advances by one CLOSED bar.
        """
        evaluator = getattr(self, "_entry_evaluator", None)
        engine = getattr(self, "_paper_engine", None)
        sb = getattr(self, "_sandbox", None)
        if evaluator is None or sb is None:
            return
        candles_by_symbol = getattr(sb, "visible_candles_by_symbol", None) or {}
        if not candles_by_symbol:
            return

        from ..exits.spec import Bar

        bars: dict = {}
        ts = None
        for sym, candles in candles_by_symbol.items():
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
            sym_norm = (sym or "").strip().upper()
            if not sym_norm:
                continue
            bars[sym_norm] = bar
            if ts is None:
                ts = bar.date

        if not bars:
            return

        # Ensure on-bar-fill of pending entries happens BEFORE evaluator
        # tick (so a fill triggers tracker open → evaluator may bracket
        # via on_fill_exit_ids on the same tick).
        if engine is not None:
            for sym_norm, bar in bars.items():
                try:
                    engine.on_bar_for_pending(sym_norm, bar, is_close=True)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "EntriesAppMixin: paper.on_bar_for_pending raised "
                        "for %s", sym_norm)

        try:
            evaluator.on_tick(bars, ts, last_bar_forming=False)
        except Exception:  # noqa: BLE001
            logger.exception("EntriesAppMixin: evaluator.on_tick raised")

        tab = getattr(self, "_entries_tab", None)
        if tab is not None:
            try:
                tab.refresh()
            except (tk.TclError, AttributeError):
                pass

    def _redraw_entries_overlay(self) -> None:
        """Re-attach the entries overlay artists to the primary axes.

        Called from :meth:`ChartApp._render` AFTER
        :meth:`_redraw_exits_overlay`. Safe no-op if the overlay was
        never built.
        """
        overlay = getattr(self, "_entries_overlay", None)
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
            logger.exception("EntriesAppMixin: overlay.redraw raised")

    def _redraw_evidence_overlay(self) -> None:
        """Re-attach the within-last-N-bars evidence-marker artists.

        Called from :meth:`ChartApp._render` AFTER the entries overlay
        so the markers paint on top of the price-line/order overlays.
        Safe no-op if the overlay was never built or if the primary
        chart isn't active.
        """
        overlay = getattr(self, "_evidence_overlay", None)
        if overlay is None:
            return
        ax = getattr(self, "_ax_price", None)
        symbol = ""
        try:
            symbol = (self.ticker_var.get() or "").strip()
        except Exception:  # noqa: BLE001
            symbol = ""
        candles = getattr(self, "_primary", None)
        try:
            overlay.redraw(ax, symbol or None, candles)
        except Exception:  # noqa: BLE001
            logger.exception("EntriesAppMixin: evidence overlay redraw raised")

    def _request_entries_overlay_redraw(self) -> None:
        """Callback handed to :class:`EntriesOverlay` for non-render repaint."""
        try:
            self.after(50, self._safe_full_render_for_entries)
        except (tk.TclError, RuntimeError):
            pass

    def _safe_full_render_for_entries(self) -> None:
        """Render guard. Calls :meth:`ChartApp._render` if available."""
        render = getattr(self, "_render", None)
        if render is None:
            return
        try:
            render()
        except Exception:  # noqa: BLE001
            logger.exception("EntriesAppMixin: _render raised")

    # ------------------------------------------------------------------
    # Dialog launch
    # ------------------------------------------------------------------

    def _on_open_entries_dialog(self, *_args) -> None:
        """Manage Entries menu / button → opens the EntriesTab + brings the
        notebook to it.

        Unlike exits, there is no separate library-editor singleton —
        editing happens via the per-row "Edit" button on the
        :class:`EntriesTab`. This handler simply focuses the tab.
        """
        tab = getattr(self, "_entries_tab", None)
        notebook = getattr(self, "_notebook", None)
        if tab is None or notebook is None:
            return
        try:
            notebook.select(str(tab))
        except (tk.TclError, AttributeError):
            pass

    def _on_open_entries_new_dialog(self, *_args) -> None:
        """New Entry Strategy menu item → opens a fresh dialog."""
        tab = getattr(self, "_entries_tab", None)
        if tab is None:
            return
        try:
            tab._on_new()
        except Exception:  # noqa: BLE001
            logger.exception("EntriesAppMixin: _on_new raised")

    def _on_entries_disarm_all(self, *_args) -> None:
        """Disarm All menu item handler."""
        evaluator = getattr(self, "_entry_evaluator", None)
        if evaluator is None:
            return
        try:
            evaluator.disarm_all()
        except Exception:  # noqa: BLE001
            logger.exception("EntriesAppMixin: disarm_all raised")
        tab = getattr(self, "_entries_tab", None)
        if tab is not None:
            try:
                tab._refresh_tree()
            except (tk.TclError, AttributeError):
                pass

    def _on_entries_modal_request(self, pending_position_id: str,
                                  strategy: Any) -> None:
        """Modal-bracket-pick callback from :class:`EntryEvaluator`.

        Stub: log + audit. The full GUI flow (popup picker over the
        exit-strategy library) is out of scope for entries-v1; the
        evaluator already wrote an ``entry_modal_requested`` audit
        record before invoking this callback.
        """
        logger.info(
            "EntriesAppMixin: modal bracket-pick requested for "
            "pending_position_id=%s strategy=%s",
            pending_position_id, getattr(strategy, "id", "?"),
        )

    def _on_entries_library_changed(self, *_args) -> None:
        """Library mutation callback → refresh the tab."""
        tab = getattr(self, "_entries_tab", None)
        if tab is not None:
            try:
                tab.refresh()
            except (tk.TclError, AttributeError):
                pass

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def _close_entries_stack(self) -> None:
        """Idempotent teardown. Safe to call from :meth:`_on_close`."""
        unsub = getattr(self, "_entries_scan_unsubscribe", None)
        if unsub is not None:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass
            self._entries_scan_unsubscribe = None

        overlay = getattr(self, "_entries_overlay", None)
        if overlay is not None:
            try:
                overlay.close()
            except Exception:  # noqa: BLE001
                pass
            self._entries_overlay = None

        ev_overlay = getattr(self, "_evidence_overlay", None)
        if ev_overlay is not None:
            try:
                ev_overlay.close()
            except Exception:  # noqa: BLE001
                pass
            self._evidence_overlay = None

        evaluator = getattr(self, "_entry_evaluator", None)
        if evaluator is not None:
            try:
                evaluator.close()
            except Exception:  # noqa: BLE001
                pass
            self._entry_evaluator = None

        self._entries_tab = None
        self._entry_paper_sink = None
        self._entries_audit_log = None
        self._entries_dialog = None
