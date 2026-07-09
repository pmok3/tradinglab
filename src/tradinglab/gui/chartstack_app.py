"""ChartStack sidebar glue for :class:`tradinglab.app.ChartApp`.

Owns the seven methods that toggle / promote / lay out the opt-in
ChartStack mini-chart sidebar, extracted from ``app.py`` (mixin-extraction
wave-4, AGENTS.md §7.24):

* :meth:`ChartStackAppMixin._toggle_chartstack` — mount/unmount the
  ``ChartStackPanel`` as the leftmost pane of ``_main_paned`` (lazy panel
  creation; wires ``on_card_promote`` to ``_on_chartstack_promote``;
  restores/persists the sash).
* :meth:`ChartStackAppMixin._on_chartstack_promote` — promote a card's
  symbol to the primary chart (preserving drilldown/time window) and
  demote the previously focused symbol back into the strip.
* :meth:`ChartStackAppMixin._on_accel_toggle_chartstack` /
  ``_on_view_toggle_chartstack`` — keyboard / View-menu toggles.
* :meth:`ChartStackAppMixin._on_view_chartstack_settings` — open the
  ChartStack settings dialog.
* :meth:`ChartStackAppMixin._chartstack_currently_visible` — predicate.
* :meth:`ChartStackAppMixin._apply_chartstack_toggle_sash` — recompute +
  apply the paned-window sash positions on show/hide.

Mixin rules (AGENTS.md §7.24): no ``__init__``, no ``super()``. Relies on
``ChartApp.__init__`` state (``_chartstack``, ``_main_paned``,
``_chartstack_visible_var``, ``_geometry_store``, ``_initial_geometry``,
``ticker_var``, ``interval_var``, ``_drilldown_day``, ``_status``) and on
ChartApp / sibling-mixin methods (``_reload_preserving_drilldown``,
``_load_data``/``_load_data_async``, ``_global_shortcut_allowed``,
``_capture_notebook_boundary``, ``_apply_forced_sash``). See
``gui/chartstack_app.spec.md``.
"""
from __future__ import annotations


class ChartStackAppMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    def _on_chartstack_promote(self, symbol: str) -> None:
        """ChartStack callback: a card was clicked → promote to main chart.

        Same-slot demote semantics (synthesis §2.5): the previously
        focused ticker is rebound to the just-vacated card slot, so
        the strip stays full and the user can swap back with one
        click. No-op when the symbol matches the current ticker
        (clicking a card showing what's already on the main chart
        does nothing).

        **Anchor / visible-window consistency** (locked in by
        ``check_d72_chartstack_promote_preserves_view``): the
        ticker-switch path mirrors
        :meth:`~tradinglab.gui.watchlist_tab.WatchlistTabMixin._on_watchlist_double`,
        which is the other "click in a sidebar to swap symbols" flow.
        Both flows must produce the same chart state for the new
        symbol so an AVWAP-anchored bar (or any time-anchored
        artifact: drilldown day, panned time window) lands at the
        same screen position regardless of which sidebar the user
        clicked from.

        Specifically:

        * If a 5m drill-down day is locked, route through
          :meth:`_reload_preserving_drilldown` so the new symbol
          re-zooms to that calendar day (with most-recent-day
          fallback per the drilldown helper).
        * Otherwise set ``_preserve_xlim_by_time_on_render`` so the
          render layer remaps the previous primary's time window
          onto the new symbol's bar-index axis. The AVWAP / anchor
          bar then stays visually anchored to its date instead of
          snapping to the right edge.

        Sandbox-active path is allowed — :meth:`_load_data_async`
        already gates ticker changes through the sandbox controller,
        matching the watchlist-double behavior during sessions.
        """
        if not symbol:
            return
        try:
            current = (self.ticker_var.get() or "").strip().upper()
        except Exception:  # noqa: BLE001
            current = ""
        target = symbol.strip().upper()
        if not target or target == current:
            return
        try:
            self.ticker_var.set(target)
        except Exception:  # noqa: BLE001
            return
        # Mirror the watchlist-double ticker-switch path so the new
        # symbol lands with the same visible window / anchor bar as
        # any other sidebar-driven swap.
        try:
            in_drilldown = (
                getattr(self, "_drilldown_day", None) is not None
                and self.interval_var.get() == "5m"
            )
        except Exception:  # noqa: BLE001
            in_drilldown = False
        try:
            if in_drilldown:
                self._reload_preserving_drilldown(self._load_data)
            else:
                try:
                    self._preserve_xlim_by_time_on_render = True
                except Exception:  # noqa: BLE001
                    pass
                self._load_data_async()
        except Exception:  # noqa: BLE001
            pass
        # Same-slot demote: rebind the just-promoted card to the
        # previously focused symbol so the strip remains full.
        cs = getattr(self, "_chartstack", None)
        if cs is not None and current:
            try:
                cs.demote_to(target, current)
            except Exception:  # noqa: BLE001
                pass

    def _on_accel_toggle_chartstack(self, _event=None):
        """Ctrl+\u0060 \u2014 show / hide the ChartStack mini-chart strip.

        Routes through :py:meth:`_toggle_chartstack` so the keyboard
        shortcut, View-menu checkbutton, and any future button all
        share the same construct-on-demand + settings-persistence
        logic.
        """
        if not self._global_shortcut_allowed():
            return None
        self._toggle_chartstack()
        return "break"

    def _on_view_toggle_chartstack(self) -> None:
        """View menu callback for the "ChartStack" checkbutton.

        ``self._chartstack_visible_var`` was already flipped by the
        Tk checkbutton itself before the command fires; pass the new
        intent through to :py:meth:`_toggle_chartstack` so it can
        construct or destroy the panel to match.
        """
        try:
            target = bool(self._chartstack_visible_var.get())
        except Exception:  # noqa: BLE001
            target = False
        self._toggle_chartstack(target=target)

    def _on_view_chartstack_settings(self) -> None:
        """View menu callback: open the ChartStack Settings popup.

        Per-slot fixed-preset symbol editor — audit
        ``chartstack-fixed-preset``. Delegates to
        :func:`gui.chartstack_settings_dialog.open_chartstack_settings`
        so the heavy Tk widget construction lives in its own
        importable module + can be unit-tested without spinning up
        a full ChartApp.

        Swallow construction exceptions defensively (e.g. a Tk
        init failure on a headless run) so a broken popup doesn't
        propagate into the Tk event loop and bring down the chart.
        """
        try:
            from .chartstack_settings_dialog import open_chartstack_settings
            open_chartstack_settings(self)
        except Exception:  # noqa: BLE001
            try:
                self._status.warn("ChartStack Settings failed to open")
            except Exception:  # noqa: BLE001
                pass

    def _chartstack_currently_visible(self, paned: object) -> bool:
        """Return True if the ChartStack panel is currently a pane of
        ``paned``. Duck-typed so unit tests can pass a stub paned with
        a ``panes()`` method."""
        cs = getattr(self, "_chartstack", None)
        if cs is None:
            return False
        try:
            return str(cs) in list(paned.panes())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return False

    def _apply_chartstack_toggle_sash(
        self, paned: object, notebook_boundary: int, *,
        chartstack_visible: bool,
    ) -> None:
        """Pin sash positions so the watchlist column stays put across
        a ChartStack toggle; only the chart pane resizes.

        Uses the **live** paned width (``winfo_width``) — NOT the
        stale startup ``_initial_geometry`` — so the boundary is
        correct even after the user has resized / maximised the
        window. That stale-width read was the root cause of the
        "watchlist jumps to half the screen" bug
        (audit ``chartstack-toggle-preserves-notebook``).

        Falls back to the ratio-based
        :func:`constants.compute_main_paned_sashes` only when the
        captured boundary is unusable (e.g. the sash wasn't laid out
        yet, so ``_capture_notebook_boundary`` returned ``0``).
        """
        try:
            live_w = int(paned.winfo_width())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            live_w = 0
        if notebook_boundary and int(notebook_boundary) > 0:
            try:
                from ..constants import compute_toggle_sashes
            except Exception:  # noqa: BLE001
                return
            positions = compute_toggle_sashes(
                live_w, int(notebook_boundary),
                chartstack_visible=chartstack_visible)
        else:
            try:
                from ..constants import compute_main_paned_sashes
            except Exception:  # noqa: BLE001
                return
            main_w = live_w
            if main_w <= 0:
                try:
                    main_w = int(
                        self._initial_geometry.split('+')[0].split('x')[0])
                except (ValueError, IndexError, AttributeError):
                    main_w = 1280
            positions = compute_main_paned_sashes(
                main_w, chartstack_visible=chartstack_visible)
        try:
            self._apply_forced_sash(paned, positions)
        except Exception:  # noqa: BLE001
            pass

    def _toggle_chartstack(self, *, target: bool | None = None) -> None:
        """Show or hide the ChartStack panel.

        ``target=None`` (the default) flips the current state. Passing
        an explicit ``bool`` forces that state — used by the View-menu
        checkbutton which has already flipped its variable when the
        user clicked.

        Behavior:

        * **First activation in a session**: constructs the panel
          lazily (the ``__init__`` path skipped it because
          ``chartstack.enabled`` was ``False``), inserts it as the
          leftmost pane (index 0) of ``self._main_paned``, and wires
          the ``on_card_promote`` callback.
        * **Subsequent show**: just re-inserts the existing panel
          (state preserved).
        * **Hide**: removes the panel from the paned window. The
          panel object stays alive in ``self._chartstack`` so a
          re-show is instant.

        Persists ``chartstack.enabled`` so the choice survives a
        restart. Keeps ``self._chartstack_visible_var`` in sync with
        the actual paned-window state so the menu checkmark never
        lies.
        """
        paned = getattr(self, "_main_paned", None)
        if paned is None:
            return
        try:
            panes = list(paned.panes())
        except Exception:  # noqa: BLE001
            panes = []

        cs = getattr(self, "_chartstack", None)
        currently_visible = (cs is not None and str(cs) in panes)
        if target is None:
            target = not currently_visible

        # Capture the chart|notebook boundary BEFORE mutating panes so
        # the watchlist column can be pinned to its current position
        # across the toggle (audit ``chartstack-toggle-preserves-notebook``).
        notebook_boundary = self._capture_notebook_boundary(
            paned, currently_visible)

        if target and not currently_visible:
            if cs is None:
                try:
                    from .chartstack import (
                        ChartStackPanel as _ChartStackPanel,
                    )
                    cs = _ChartStackPanel(
                        paned, owner=self,
                        geometry_store=getattr(self, "_geometry_store", None),
                    )
                    self._chartstack = cs
                    try:
                        cs.on_card_promote = self._on_chartstack_promote
                    except Exception:  # noqa: BLE001
                        pass
                except Exception:  # noqa: BLE001
                    self._chartstack = None
                    cs = None
            if cs is not None:
                try:
                    paned.insert(0, cs, weight=0)
                except Exception:  # noqa: BLE001
                    pass
                # Pin the watchlist column to its CURRENT position so
                # toggling ChartStack only steals pixels from the
                # chart, never moves the notebook. ``after_idle``
                # defers until the inserted pane has been laid out so
                # ``winfo_width`` is sane. See
                # ``_apply_chartstack_toggle_sash`` + audit
                # ``chartstack-toggle-preserves-notebook``.
                def _force_3pane_layout(_p=paned, _b=notebook_boundary):
                    self._apply_chartstack_toggle_sash(
                        _p, _b, chartstack_visible=True)
                try:
                    self.after_idle(_force_3pane_layout)
                except Exception:  # noqa: BLE001
                    _force_3pane_layout()
        elif (not target) and currently_visible and cs is not None:
            try:
                paned.forget(cs)
            except Exception:  # noqa: BLE001
                pass
            # Reclaim the ChartStack pixels into the chart while
            # holding the notebook column fixed at its current
            # position (audit ``chartstack-toggle-preserves-notebook``).
            def _force_2pane_layout(_p=paned, _b=notebook_boundary):
                self._apply_chartstack_toggle_sash(
                    _p, _b, chartstack_visible=False)
            try:
                self.after_idle(_force_2pane_layout)
            except Exception:  # noqa: BLE001
                _force_2pane_layout()

        try:
            self._chartstack_visible_var.set(bool(target))
        except Exception:  # noqa: BLE001
            pass

        try:
            from .. import settings as _settings
            _settings.set("chartstack.enabled", bool(target))
        except Exception:  # noqa: BLE001
            pass
