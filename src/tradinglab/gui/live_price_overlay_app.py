"""ChartApp-side wiring for the live-price overlay.

Holds the two methods that decide *when* and *for which slot* the
:class:`~tradinglab.gui.live_price_overlay.LivePriceOverlay` helper
should redraw or in-place-update the TradingView-style dotted live
price line. The overlay math itself stays in
``gui/live_price_overlay.py`` — this mixin only carries the
``ChartApp``-side glue.

Mixin rules: no ``__init__``; relies on attributes that
:class:`ChartApp.__init__` already initialises
(``_live_price_overlay``, ``_panel_state``, ``_last_stream_price``).
"""
from __future__ import annotations

import logging
from typing import Any  # noqa: F401  # type-only parity

logger = logging.getLogger(__name__)


class LivePriceOverlayAppMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    def _redraw_live_price_overlay(self) -> None:
        """Rebuild the TradingView-style dotted live-price line per slot.

        Called from the end of :meth:`_render`. For every price slot in
        ``_panel_state``, resolves the freshest known price for the
        slot's symbol (latest stream tick or last non-gap candle close)
        and asks :class:`LivePriceOverlay` to draw a horizontal dotted
        line + right-edge label. The line lives at zorder 3 — below
        the exits/entries overlays (zorder 4) and below the crosshair,
        above the grid.

        The overlay is always-on per design (no toggle). Slots with no
        resolvable price are silently skipped — the overlay handles
        ``None`` / NaN by drawing nothing for that slot.
        """
        overlay = getattr(self, "_live_price_overlay", None)
        if overlay is None:
            return
        from .gui.live_price_overlay import resolve_price as _resolve_live_price
        ax_by_slot: dict[str, Any] = {}
        price_by_slot: dict[str, Any] = {}
        for slot_key, ps in self._panel_state.items():
            ax = ps.get("price_ax")
            if ax is None:
                continue
            try:
                ticker = self._slot_symbol(slot_key)
            except Exception:  # noqa: BLE001
                ticker = ""
            ax_by_slot[slot_key] = ax
            try:
                price_by_slot[slot_key] = _resolve_live_price(
                    ticker,
                    last_stream_price=self._last_stream_price,
                    panel_state_slot=ps,
                )
            except Exception:  # noqa: BLE001
                price_by_slot[slot_key] = None
        try:
            theme = self._theme or {}
        except Exception:  # noqa: BLE001
            theme = {}
        color = str(theme.get("text", "#888888"))
        label_bg = str(theme.get("tooltip_bg", "#ffffff"))
        label_fg = str(theme.get("tooltip_fg", "#111111"))
        label_edge = str(theme.get("spine", "#888888"))
        try:
            overlay.redraw(
                ax_by_slot=ax_by_slot,
                price_by_slot=price_by_slot,
                color=color,
                label_bg=label_bg,
                label_fg=label_fg,
                label_edge=label_edge,
            )
        except Exception:  # noqa: BLE001
            logger.exception("LivePriceOverlay: redraw raised")

    def _update_live_price_overlay_for_slot(self, slot: str) -> None:
        """Mutate the live-price line in place for ``slot``.

        Called from :meth:`_refresh_view_after_tick` after the
        rightmost candle/volume artists have been mutated. Reads the
        freshest price the same way :meth:`_redraw_live_price_overlay`
        does (stream tick first, candle close fallback) and pokes the
        overlay's per-slot artist. The overlay no-ops if it has never
        been redrawn (e.g. before the first ``_render``).
        """
        overlay = getattr(self, "_live_price_overlay", None)
        if overlay is None:
            return
        ps = self._panel_state.get(slot)
        if ps is None:
            return
        try:
            ticker = self._slot_symbol(slot)
        except Exception:  # noqa: BLE001
            ticker = ""
        from .gui.live_price_overlay import resolve_price as _resolve_live_price
        try:
            price = _resolve_live_price(
                ticker,
                last_stream_price=self._last_stream_price,
                panel_state_slot=ps,
            )
        except Exception:  # noqa: BLE001
            price = None
        try:
            overlay.update_in_place(slot, price)
        except Exception:  # noqa: BLE001
            logger.exception(
                "LivePriceOverlay: update_in_place raised for slot %s", slot
            )









