"""AVWAP anchor-pick mode for :class:`tradinglab.app.ChartApp`.

Owns the four methods that implement the "Pick Anchor…" click flow
previously inline in ``app.py``:

* :meth:`AnchorPickAppMixin._begin_anchor_pick` — arm one-shot capture
  (crosshair cursor, hide overlapping indicator dialogs, status hint).
* :meth:`AnchorPickAppMixin._cancel_anchor_pick` — clear the mode and
  restore the hidden dialogs / cursor / status.
* :meth:`AnchorPickAppMixin._on_anchor_pick_escape` — Esc handler.
* :meth:`AnchorPickAppMixin._handle_anchor_pick_click` — consume the
  next left-click, snap to a non-gap regular-session bar, and write the
  resolved timestamp into the AVWAP config (symbol-keyed or shared).

The mode is armed from ``IndicatorDialog`` / per-indicator dialogs and
its click is dispatched by ``InteractionMixin._on_button_press`` (which
checks ``self._anchor_pick_state`` before pan/zoom). See
``gui/anchor_pick_app.spec.md`` and ``indicators/avwap.spec.md``.

Mixin rules (see AGENTS.md §7.24): no ``__init__``, no cooperative
``super()``. Relies on state initialised in ``ChartApp.__init__``
(``_anchor_pick_state``, ``_pan_state``, ``_zoom_state``, ``_drag_press``,
``_canvas``, ``_status``, ``_indicator_manager``, ``_indicator_dialog``,
``_per_indicator_dialogs``, ``_ax_candle_map``) and on
``_slot_key_for_axes`` / ``_slot_symbol`` defined on :class:`ChartApp`.
"""

from __future__ import annotations

import tkinter as tk
from typing import Any


class AnchorPickAppMixin:
    """Extracted from ``ChartApp``; see module docstring."""

    def _begin_anchor_pick(self, config_id: int) -> None:
        """Arm one-shot anchor-pick capture for ``config_id``.

        Called from :class:`tradinglab.gui.indicator_dialog.IndicatorDialog`'s
        "Pick Anchor…" button. Sets cursor to crosshair, defensively
        clears pan/zoom drag state, and shows a status hint. The next
        left-click in a chart axis is intercepted by
        :meth:`_handle_anchor_pick_click` (gated in the
        :class:`InteractionMixin`'s ``_on_button_press``); a miss
        keeps the mode active so the user can retry.

        While pick mode is active EVERY visible indicator dialog is
        withdrawn (fully hidden — NOT just minimised) so the chart
        underneath is unobstructed and the user can reach any candle.
        This covers BOTH the Manage Indicators dialog (``self.
        _indicator_dialog``) AND every per-indicator dialog
        (``self._per_indicator_dialogs`` — any of which may overlap
        the chart geometry). Each hidden dialog's original window
        state is captured and restored when pick mode ends (success,
        cancel, or Esc) via :meth:`_cancel_anchor_pick`.

        Audit ``avwap-anchor-pick-iconifies-per-indicator-dialog``.
        """
        cfg = self._indicator_manager.get(config_id)
        if cfg is None or getattr(cfg, "kind_id", "") != "avwap":
            return
        # Collect EVERY visible indicator dialog so we can hide all of
        # them and restore later. Capture each dialog's current state
        # (`state()` returns "normal" / "iconic" / "withdrawn" /
        # "zoomed") so an already-hidden dialog stays in its original
        # state on restore.
        candidates: list[tk.Toplevel] = []
        mgr_dlg = getattr(self, "_indicator_dialog", None)
        if mgr_dlg is not None:
            candidates.append(mgr_dlg)
        per_dlgs = getattr(self, "_per_indicator_dialogs", None) or {}
        for d in per_dlgs.values():
            if d is not None and d is not mgr_dlg:
                candidates.append(d)
        hidden: list[tuple[Any, str | None]] = []
        for dlg in candidates:
            try:
                if not dlg.winfo_exists():
                    continue
            except Exception:  # noqa: BLE001
                continue
            try:
                prior_state = dlg.state()
            except Exception:  # noqa: BLE001
                prior_state = None
            # Only hide dialogs that are currently visible; leave an
            # already-hidden (iconic / withdrawn) dialog untouched so we
            # don't force-show it on restore.
            if prior_state in ("iconic", "withdrawn"):
                continue
            try:
                # ``withdraw`` (NOT ``iconify``): on Windows ``iconify``
                # only minimises to the taskbar — the window stays listed
                # there and grabs focus for a beat. ``withdraw`` removes
                # it entirely so the chart is cleanly unobstructed while
                # the user clicks the anchor bar. Restored via
                # ``deiconify`` in :meth:`_cancel_anchor_pick`.
                dlg.withdraw()
            except Exception:  # noqa: BLE001
                continue
            hidden.append((dlg, prior_state))
        self._anchor_pick_state = {
            "config_id": config_id,
            "hidden_dialogs": hidden,
            # Prior window state of the first-hidden dialog (typically
            # Manage Indicators) — kept for callers that inspect it.
            "dialog_prior_state": hidden[0][1] if hidden else None,
        }
        self._pan_state = None
        self._zoom_state = None
        self._drag_press = None
        try:
            tk_widget = self._canvas.get_tk_widget()
            tk_widget.configure(cursor="crosshair")
            tk_widget.focus_set()
            tk_widget.bind("<Escape>", self._on_anchor_pick_escape, add="+")
        except Exception:  # noqa: BLE001
            pass
        try:
            self._status.info(
                "Click a bar to anchor VWAP — Esc to cancel"
            )
        except Exception:  # noqa: BLE001
            pass

    def _cancel_anchor_pick(self, *, status_msg: str | None = None) -> None:
        """Clear anchor-pick mode and restore the cursor / status.

        Every indicator dialog hidden by :meth:`_begin_anchor_pick`
        is deiconified back to its prior state (typically "normal"
        but preserving "zoomed" if that's what it was) and lifted
        over the chart so the user can keep editing params right
        where they left off.

        Audit ``avwap-anchor-pick-iconifies-per-indicator-dialog``.
        """
        hidden: list[tuple[Any, str | None]] = []
        if self._anchor_pick_state is not None:
            hidden = list(
                self._anchor_pick_state.get("hidden_dialogs", []) or []
            )
        self._anchor_pick_state = None
        try:
            tk_widget = self._canvas.get_tk_widget()
            tk_widget.configure(cursor="")
            tk_widget.unbind("<Escape>")
        except Exception:  # noqa: BLE001
            pass
        for dlg, prior_state in hidden:
            try:
                if not dlg.winfo_exists():
                    continue
            except Exception:  # noqa: BLE001
                continue
            try:
                # Only re-show if it WAS visible before we hid it. If the
                # user had it withdrawn/iconic for some reason, preserve
                # that state (those were skipped at hide time anyway).
                if prior_state in ("normal", "zoomed", None):
                    dlg.deiconify()
                    if prior_state == "zoomed":
                        try:
                            dlg.state("zoomed")
                        except Exception:  # noqa: BLE001
                            pass
                    try:
                        dlg.lift()
                        dlg.focus_set()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass
        if status_msg:
            try:
                self._status.info(status_msg)
            except Exception:  # noqa: BLE001
                pass

    def _on_anchor_pick_escape(self, _event: Any) -> str:
        self._cancel_anchor_pick(status_msg="Anchor pick canceled")
        return "break"

    def _handle_anchor_pick_click(self, event: Any) -> bool:
        """Consume a left-click while anchor-pick mode is active.

        Returns ``True`` if the event was consumed (always ``True``
        when this method is reached — pick mode swallows ALL left
        clicks regardless of whether a candle was hit, so the user
        can't accidentally start a pan/zoom while the prompt is
        live). On a successful candle hit the AVWAP config's params
        are updated (merged so ``price_source`` / ``bands`` are
        preserved) and pick mode is cleared. On a miss / pre-post
        snap-failure, pick mode stays armed.
        """
        state = self._anchor_pick_state
        if not state:
            return False
        cfg_id = int(state.get("config_id", -1))
        ax = getattr(event, "inaxes", None)
        if ax is None or getattr(event, "xdata", None) is None:
            try:
                self._status.info("Click inside a chart panel — Esc to cancel")
            except Exception:  # noqa: BLE001
                pass
            return True
        entry = self._ax_candle_map.get(ax)
        if entry is None:
            try:
                self._status.info("Click a price/volume panel — Esc to cancel")
            except Exception:  # noqa: BLE001
                pass
            return True
        candles, _kind, offset = entry
        idx = int(round(event.xdata - offset))
        if idx < 0 or idx >= len(candles):
            return True
        if abs(event.xdata - (idx + offset)) > 0.3:
            try:
                self._status.info("Click closer to a bar — Esc to cancel")
            except Exception:  # noqa: BLE001
                pass
            return True
        # Snap forward to the nearest non-gap regular-session bar
        # (matches the AVWAP compute eligibility rule). If the user
        # clicked a pre/post bar we want the stored anchor to be a
        # bar the indicator can actually start on.
        snap_idx = idx
        while snap_idx < len(candles):
            c = candles[snap_idx]
            if not getattr(c, "is_gap", False) and c.session == "regular":
                break
            snap_idx += 1
        if snap_idx >= len(candles):
            try:
                self._status.info(
                    "No regular-session bar at/after this click — Esc to cancel"
                )
            except Exception:  # noqa: BLE001
                pass
            return True
        c = candles[snap_idx]
        try:
            from ..indicators.avwap import _strip_tz
            ts = _strip_tz(c.date).isoformat()
        except Exception:  # noqa: BLE001
            ts = c.date.isoformat()
        cfg = self._indicator_manager.get(cfg_id)
        if cfg is None:
            self._cancel_anchor_pick()
            return True
        params = dict(cfg.params or {})
        # Symbol-keyed anchors: write into the slot the click landed in
        # so the primary and compare panes keep independent anchors. In
        # shared mode one anchor applies to every symbol. See
        # indicators/avwap.spec.md "Symbol-keyed anchors".
        slot = self._slot_key_for_axes(ax) or "primary"
        symbol = (self._slot_symbol(slot) or "").upper()
        if params.get("anchor_shared"):
            params["shared_anchor_ts"] = ts
            scope_note = "all symbols"
        else:
            anchors = dict(params.get("anchors") or {})
            if symbol:
                anchors[symbol] = ts
                scope_note = symbol
            else:
                # No confirmed ticker for this slot (rare pre-confirm
                # state) — fall back to the legacy scalar so the pick
                # isn't silently dropped.
                params["anchor_ts"] = ts
                scope_note = "this symbol"
            params["anchors"] = anchors
        self._indicator_manager.update(cfg_id, params=params)
        self._cancel_anchor_pick(
            status_msg=f"Anchor set ({scope_note}): {ts[:16].replace('T', ' ')}")
        return True
