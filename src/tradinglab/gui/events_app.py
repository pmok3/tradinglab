"""Events-overlay glue for :class:`tradinglab.app.ChartApp`.

Owns the four methods that fetch/gate historical event bundles (earnings,
dividends) and paint them as glyphs at the bottom of each price pane,
extracted from ``app.py`` (mixin-extraction wave-4, AGENTS.md §7.24):

* :meth:`EventsAppMixin._get_events_view_for_slot` — resolve a gated
  ``EventsView`` for the symbol in a slot (sandbox-clock aware, else
  ``_events_cache`` gated against wall-clock).
* :meth:`EventsAppMixin._render_event_glyphs_for_slot` — delegate to the
  renderer to paint the glyphs (honours sandbox blind mode).
* :meth:`EventsAppMixin._load_events_async` — submit a background
  ``EventBundle`` fetch on ``_fetch_executor``, token-gated, then request
  a redraw when it lands.
* :meth:`EventsAppMixin._request_redraw_for_events` — repaint the event
  glyphs for the visible slots after a fetch completes.

Mixin rules (AGENTS.md §7.24): no ``__init__``, no ``super()``. Relies on
``ChartApp.__init__`` state (``_events_cache``, ``_events_fetch_token``,
``_events_fetch_inflight``, ``_fetch_executor``, ``_sandbox_controller``,
``_panel_state``, ``_figure``, ``_blit_bg``, ``_theme``) and on ChartApp /
sibling-mixin methods (``_slot_symbol``, ``_await_future_on_tk``,
``_ensure_renderer``, ``_schedule_watchlist_tab_refresh``). See
``gui/events_app.spec.md``.
"""
from __future__ import annotations

from typing import Any  # noqa: F401


class EventsAppMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    def _get_events_view_for_slot(self, slot: str):
        """Return a gated EventsView for the symbol displayed in ``slot``.

        Routes:

        * **Sandbox active** — delegates to the controller's
          :meth:`SandboxController.events_visible_for`, which honors the
          session clock + blind flag. Returns ``None`` if no bundle
          has arrived yet.
        * **Non-sandbox** — looks the bundle up in ``self._events_cache``
          (populated by :meth:`_load_events_async`) and gates it
          against ``time.time()*1000`` with ``blind=False``. Forward
          earnings within the ``forward_window_days`` window are
          visible; everything else is past.

        Returns ``None`` when no bundle is known for the symbol or the
        gating import fails — the caller renders an empty glyph list
        in that case.
        """
        symbol = self._slot_symbol(slot)
        if not symbol:
            return None
        ctl = getattr(self, "_sandbox_controller", None)
        if ctl is not None and getattr(ctl, "is_active", lambda: False)():
            try:
                return ctl.events_visible_for(symbol)
            except Exception:  # noqa: BLE001
                return None
        bundle = self._events_cache.get(symbol)
        if bundle is None:
            return None
        try:
            import time as _time

            from ..events.gating import events_visible_for as _gate
            now_ms = int(_time.time() * 1000)
            return _gate(bundle, now_ms, blind=False)
        except Exception:  # noqa: BLE001
            return None

    def _render_event_glyphs_for_slot(self, slot: str) -> None:
        ctl = getattr(self, "_sandbox_controller", None)
        sandbox_blind = False
        if ctl is not None and getattr(ctl, "is_active", lambda: False)():
            sandbox_blind = bool(getattr(ctl, "blind", False))
        self._ensure_renderer().render_event_glyphs_for_slot(
            slot,
            get_events_view=self._get_events_view_for_slot,
            theme=self._theme,
            sandbox_blind=sandbox_blind,
        )

    def _load_events_async(self, symbol: str) -> None:
        """Submit a background EventBundle fetch for ``symbol``.

        Sibling of :meth:`_load_data_async`: runs on ``_fetch_executor``,
        marshals the result back to the Tk main thread via
        :meth:`_await_future_on_tk` (never ``add_done_callback`` +
        ``self.after`` from a worker thread — see ``app.spec.md``
        Recent history → "Worker-inbox queue").

        Token-gated: a superseded ``_load_events_async`` no-ops on
        arrival. Inflight-deduped per symbol so a typed-ticker storm
        doesn't fan out to N parallel fetches of the same data.

        On success the result lands in ``self._events_cache[symbol]``
        and a redraw is requested so the bottom-pane glyphs (and any
        watchlist "Next Earn" column rows) pick up the new bundle.
        """
        sym = str(symbol or "").strip().upper()
        if not sym:
            return
        if sym in self._events_fetch_inflight:
            return
        # In-memory cache hit short-circuits the executor submit so the
        # candle-cache fast path stays "no submit" (N7 smoke invariant).
        # Disk-cache hydration still happens lazily on the worker the
        # first time a symbol is requested in a process.
        if self._events_cache.get(sym) is not None:
            return
        try:
            from .. import defaults as _defaults_mod
            from ..events import EVENT_SOURCES
        except ImportError:
            return
        source_name = str(_defaults_mod.get("events_source") or "yfinance")
        fetcher = EVENT_SOURCES.get(source_name) or EVENT_SOURCES.get("synthetic")
        if fetcher is None:
            return
        executor = getattr(self, "_fetch_executor", None)
        await_helper = getattr(self, "_await_future_on_tk", None)
        if executor is None or await_helper is None:
            return

        self._events_fetch_token += 1
        token = self._events_fetch_token
        self._events_fetch_inflight.add(sym)

        def _work():
            try:
                return fetcher(sym)
            except Exception:  # noqa: BLE001
                return None

        def _on_done(bundle) -> None:
            self._events_fetch_inflight.discard(sym)
            if token != self._events_fetch_token and bundle is None:
                # Stale + empty — drop. (A stale-but-non-empty result
                # is still valid for the cache, since bundles are
                # immutable in the past zone.)
                return
            if bundle is None:
                return
            self._events_cache[sym] = bundle
            # Re-paint so the glyphs appear without waiting for the
            # next user interaction. Best-effort; failures revert to
            # "glyphs will appear on next render".
            try:
                self._request_redraw_for_events()
            except Exception:  # noqa: BLE001
                pass

        try:
            fut = executor.submit(_work)
        except (RuntimeError, AttributeError):
            self._events_fetch_inflight.discard(sym)
            return
        try:
            await_helper(fut, _on_done)
        except Exception:  # noqa: BLE001
            self._events_fetch_inflight.discard(sym)

    def _request_redraw_for_events(self) -> None:
        """Re-render glyphs into the existing axes after a bundle arrives.

        Cheap path: only ``_render_event_glyphs_for_slot`` per slot,
        then a canvas redraw. No candle / indicator rebuild — those
        haven't changed. Falls back to a full ``_render()`` if any
        slot lookup fails (defensive; should be unreachable when called
        from ``_load_events_async``'s success path).
        """
        try:
            for slot_key in list(self._panel_state.keys()):
                ps = self._panel_state.get(slot_key)
                if not ps or ps.get("price_ax") is None:
                    continue
                # Tear down previous glyph artists before redrawing.
                try:
                    from .events_overlay import clear_event_glyph_artists
                    clear_event_glyph_artists(list(ps.get("event_artists", []) or []))
                except Exception:  # noqa: BLE001
                    pass
                ps["event_artists"] = []
                ps["event_hit_meta"] = []
                ps["event_badge_tooltip"] = ""
                self._render_event_glyphs_for_slot(slot_key)
            # Force a canvas refresh so the new artists show up.
            try:
                self._figure.canvas.draw_idle()
            except Exception:  # noqa: BLE001
                pass
            self._blit_bg = None
            # Also poke the watchlist tab in case the "Next Earn"
            # column wants to recompute now that a bundle landed.
            try:
                self._schedule_watchlist_tab_refresh()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
