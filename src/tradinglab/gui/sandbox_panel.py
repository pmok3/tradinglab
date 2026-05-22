"""Sandbox sidebar widget (Phase 1b).

Live status panel shown only while a sandbox session is active. Mounts
to the right of the chart and surfaces:

* Clock readout (current bar timestamp + bar index / total).
* "Next bar" button (also bound to the Right-arrow key globally on the app).
* Cash + open-positions Treeview (symbol, qty, avg_cost).
* Ticker focus list (radio-style — clicking swaps the primary chart).
* Buy / Sell buttons that open :class:`PreTradeFormDialog` for the
  currently-focused ticker.

The panel is dumb: every refresh pulls fresh state from the controller.
The controller calls :meth:`refresh` after every state-changing event.
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime, timezone
from tkinter import ttk
from typing import Any, Dict, Optional

_MS_PER_DAY = 86_400_000


def _proximity_notice(
    earnings_tag: str,
    dividend_tag: str,
    prox: Dict[str, Any],
    submit_ts_s: int,
) -> str:
    """Build the human-readable pre-trade proximity notice.

    Called from :meth:`SandboxPanel._on_trade_button` to compose the
    inline label rendered at the top of :class:`PreTradeFormDialog`.
    Empty string means "nothing to surface" — the dialog hides the
    notice row entirely.

    The notice uses the same plain-English vocabulary that lands on
    the journal record's ``earnings_proximity_tag`` /
    ``dividend_proximity_tag`` fields, so a trader scanning their
    Performance View aggregates sees the matching language.
    """
    submit_ts_ms = int(submit_ts_s) * 1000
    parts: list[str] = []
    if earnings_tag == "earnings_pre_print":
        next_ts = int(prox.get("next_earnings_ts") or 0)
        if next_ts > submit_ts_ms:
            days = max(1, (next_ts - submit_ts_ms + _MS_PER_DAY - 1)
                       // _MS_PER_DAY)
            parts.append(f"⚠ Earnings in ~{int(days)} day(s) "
                         f"— pre-print proximity")
        else:
            parts.append("⚠ Pre-earnings window")
    elif earnings_tag == "earnings_post_print":
        last_ts = int(prox.get("last_earnings_ts") or 0)
        if last_ts and submit_ts_ms > last_ts:
            days = max(1, (submit_ts_ms - last_ts + _MS_PER_DAY - 1)
                       // _MS_PER_DAY)
            parts.append(f"⚠ Post-earnings (last print ~{int(days)} day(s) ago)")
        else:
            parts.append("⚠ Post-earnings window")
    if dividend_tag == "ex_div_day":
        parts.append("⚠ Ex-dividend day")
    elif dividend_tag == "post_special_div":
        parts.append("⚠ Special-dividend window")
    return "  ·  ".join(parts)


class SandboxPanel(ttk.Frame):
    """Sidebar widget for an active sandbox session."""

    def __init__(self, app: Any, controller: Any, **kwargs):
        super().__init__(app, **kwargs)
        self.app = app
        self.controller = controller
        self._build()
        # Phase 1c: hand the controller a callback that opens the
        # mandatory post-trade review modal. The controller invokes it
        # synchronously after each tick that produces a closed trade.
        try:
            controller.set_post_trade_callback(self._open_post_trade_modal)
        except AttributeError:
            pass
        self.refresh()

    def _build(self) -> None:
        pad = {"padx": 6, "pady": 4}

        ttk.Label(self, text="Sandbox", font=("TkDefaultFont", 11, "bold")) \
            .grid(row=0, column=0, sticky="w", **pad)

        self._clock_var = tk.StringVar(value="(no clock)")
        ttk.Label(self, textvariable=self._clock_var) \
            .grid(row=1, column=0, sticky="w", **pad)

        self._cash_var = tk.StringVar(value="Cash: $—")
        ttk.Label(self, textvariable=self._cash_var) \
            .grid(row=2, column=0, sticky="w", **pad)

        ttk.Button(self, text="Next bar (\u2192)", command=self._on_next_bar) \
            .grid(row=3, column=0, sticky="ew", **pad)

        # Focus list — pick which ticker is on the primary chart.
        ttk.Label(self, text="Focus ticker:").grid(row=4, column=0, sticky="w", **pad)
        self._focus_lb = tk.Listbox(self, height=4, exportselection=False)
        self._focus_lb.grid(row=5, column=0, sticky="ew", **pad)
        self._focus_lb.bind("<<ListboxSelect>>", self._on_focus_select)

        # Trade buttons.
        btns = ttk.Frame(self)
        btns.grid(row=6, column=0, sticky="ew", **pad)
        ttk.Button(btns, text="Buy",
                   command=lambda: self._on_trade_button("buy")) \
            .pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(btns, text="Sell",
                   command=lambda: self._on_trade_button("sell")) \
            .pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        # Positions table.
        ttk.Label(self, text="Positions:").grid(row=7, column=0, sticky="w", **pad)
        cols = ("symbol", "qty", "avg")
        self._pos_tree = ttk.Treeview(self, columns=cols, show="headings", height=5)
        for c, w in (("symbol", 60), ("qty", 60), ("avg", 70)):
            self._pos_tree.heading(c, text=c.title())
            self._pos_tree.column(c, width=w, anchor="w")
        self._pos_tree.grid(row=8, column=0, sticky="ew", **pad)

        # End session.
        ttk.Button(self, text="End session", command=self._on_end_session) \
            .grid(row=9, column=0, sticky="ew", **pad)

        # Bind Right-arrow globally (only fires when sandbox active —
        # controller checks ``is_active``). Migrated from the legacy
        # KeyPress-N binding which interfered with typing tickers.
        try:
            self.app.bind_all("<KeyPress-Right>", self._on_right_key, add="+")
        except tk.TclError:
            pass

    def _display_tz(self):
        """Return ``(label, ZoneInfo or None)`` matching the chart's tz.

        Mirrors ``ChartApp._display_tz`` semantics so the sandbox clock
        readout stays aligned with the x-axis, hover tooltip, and OHLC
        table:
          * empty string → ET-native (the chart's implicit tz for
            provider-tz-aware US-equity candles); label "ET",
            ``ZoneInfo("America/New_York")``.
          * non-empty IANA name → that zone; label is the last
            ``/``-segment for compactness (e.g. ``Europe/London`` →
            "London"), with two well-known abbreviations
            (``America/New_York`` → "ET", ``UTC`` → "UTC").
          * unrecognized / missing ``tzdata`` → ``(name, None)`` so the
            caller falls back to UTC raw rather than crashing.
        Routes through ``controller.app`` because ``self.app`` is the
        Toplevel host, not the ChartApp.
        """
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            return ("UTC", None)
        host = getattr(self.controller, "app", None)
        name = getattr(host, "_display_tz", "") or ""
        name = name.strip()
        if not name:
            try:
                return ("ET", ZoneInfo("America/New_York"))
            except Exception:  # noqa: BLE001
                return ("UTC", None)
        try:
            zone = ZoneInfo(name)
        except Exception:  # noqa: BLE001
            return (name, None)
        if name == "America/New_York":
            label = "ET"
        elif name == "UTC":
            label = "UTC"
        else:
            label = name.rsplit("/", 1)[-1]
        return (label, zone)

    # ------------------------------------------------------------------
    # Refresh + event handlers
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        ctl = self.controller
        ts = ctl.clock_ts() if ctl is not None else None
        # Phase 1d: in blind / auto-cycle mode, suppress the date
        # portion of the clock so the user can't bias their analysis
        # by knowing which historical day they're replaying.
        blind = bool(getattr(ctl, "blind", False)) if ctl is not None else False
        if ts is None:
            self._clock_var.set("(no clock)")
        else:
            try:
                # Convert to the chart's display tz so the sandbox clock
                # reads in the same wall clock the user sees on the
                # x-axis, hover tooltip, and OHLC table.
                # ``ChartApp._display_tz`` is the IANA name; an empty
                # value means "ET-native" — the implicit chart tz for
                # provider-tz-aware (US equities → America/New_York)
                # candles. Mirror that policy here: empty → ET; non-
                # empty → that zone; bad zone → fall back to UTC raw
                # so we never crash the panel render path.
                tz_label, tz_zone = self._display_tz()
                dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                if tz_zone is not None:
                    try:
                        dt = dt.astimezone(tz_zone)
                    except Exception:  # noqa: BLE001
                        tz_label = "UTC"
                if blind:
                    cycle_n = int(getattr(ctl, "_cycle_index", 0)) + 1
                    self._clock_var.set(
                        f"Clock: {dt.strftime('%H:%M')} {tz_label}  "
                        f"(blind cycle #{cycle_n})")
                else:
                    self._clock_var.set(
                        f"Clock: {dt.strftime('%Y-%m-%d %H:%M')} {tz_label}")
            except Exception:  # noqa: BLE001
                self._clock_var.set(f"Clock ts: {ts}")
        try:
            self._cash_var.set(f"Cash: ${ctl.cash():,.2f}")
        except Exception:  # noqa: BLE001
            self._cash_var.set("Cash: $—")

        # Focus list.
        focus = ctl.focus_symbol if ctl is not None else None
        # Defensive sync: keep the toolbar Ticker label aligned with the
        # controller's current focus symbol. ``_install_sandbox_primary_series``
        # already sets ``ticker_var`` on every focus swap, but this catches
        # any path that swaps the displayed symbol without going through
        # that helper (e.g. early-return branches, recovery from a failed
        # render). Routes through ``ctl.app`` because ``self.app`` here is
        # the Toplevel host, not the ChartApp itself.
        if ctl is not None and focus and ctl.is_active():
            try:
                host = getattr(ctl, "app", None)
                tv = getattr(host, "ticker_var", None) if host else None
                if tv is not None:
                    cur = (tv.get() or "").strip().upper()
                    if cur != focus.upper():
                        tv.set(focus)
            except (tk.TclError, AttributeError):
                pass
        tickers = ctl.tickers() if ctl is not None else []
        current = list(self._focus_lb.get(0, tk.END))
        if current != tickers:
            self._focus_lb.delete(0, tk.END)
            for t in tickers:
                self._focus_lb.insert(tk.END, t)
        if focus in tickers:
            try:
                idx = tickers.index(focus)
                self._focus_lb.selection_clear(0, tk.END)
                self._focus_lb.selection_set(idx)
            except (ValueError, tk.TclError):
                pass

        # Positions. Skip the tree rebuild when the snapshot hasn't
        # changed — Treeview delete + re-insert is surprisingly costly
        # on Tk and dominates per-tick refresh cost when there are no
        # open positions (which is the common case for most ticks).
        if ctl is not None:
            snap = ctl.positions_snapshot()
            sig = tuple(
                (p["symbol"], float(p["quantity"]), float(p["avg_cost"]))
                for p in snap)
            if sig != getattr(self, "_pos_sig", None):
                for iid in self._pos_tree.get_children():
                    self._pos_tree.delete(iid)
                for p in snap:
                    self._pos_tree.insert("", tk.END, values=(
                        p["symbol"],
                        f"{p['quantity']:g}",
                        f"{p['avg_cost']:,.4f}",
                    ))
                self._pos_sig = sig

    def _on_next_bar(self) -> None:
        ctl = self.controller
        if ctl is None or not ctl.is_active():
            return
        if not ctl.next_bar():
            try:
                self.app._status.info("Sandbox: end of replay reached")
            except Exception:  # noqa: BLE001
                pass

    def _on_right_key(self, _event=None) -> None:
        # Suppress while a Text/Entry/Combobox has focus so the user can
        # use Right-arrow for normal cursor navigation inside form fields
        # (e.g. the pre-trade thesis box) without accidentally advancing
        # the replay clock. Other widgets (Listbox, buttons, the chart
        # canvas itself) don't fall in this set so Right still ticks
        # there.
        try:
            w = self.app.focus_get()
        except tk.TclError:
            w = None
        if isinstance(w, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox)):
            return
        ctl = self.controller
        if ctl is None or not ctl.is_active():
            return
        self._on_next_bar()

    def _on_focus_select(self, _event=None) -> None:
        ctl = self.controller
        if ctl is None or not ctl.is_active():
            return
        sel = self._focus_lb.curselection()
        if not sel:
            return
        sym = self._focus_lb.get(sel[0])
        ctl.set_focus(sym)

    def _on_trade_button(self, side: str) -> None:
        from .sandbox_dialog import PreTradeFormDialog
        ctl = self.controller
        if ctl is None or not ctl.is_active() or not ctl.focus_symbol:
            return
        try:
            tags = ctl.tag_store.list()
        except AttributeError:
            tags = []

        # Audit ``mandatory-journal-skip``: when the user opted into
        # rapid scalp-practice, bypass the modal and submit the
        # order directly with a placeholder thesis so
        # ``SandboxController.submit_order`` (which still requires
        # non-empty thesis) accepts it.
        skip_journal = False
        try:
            from ..defaults import get as _get_default
            skip_journal = bool(_get_default("sandbox_skip_detailed_journal"))
        except Exception:  # noqa: BLE001
            skip_journal = False
        if skip_journal:
            self._submit_quickfire_order(ctl, side)
            return

        # Build the inline pre-earnings / dividend-proximity notice
        # (plan.md decision 12). We surface the same tags the journal
        # would auto-attach via ``_compute_event_proximity``, but as a
        # passive read-only label at the top of the dialog. Empty
        # notice + empty suggested_tags means "no proximity context";
        # the dialog skips the row entirely.
        notice = ""
        suggested: list[str] = []
        try:
            from ..defaults import get as _get_default
            if bool(_get_default("pre_earnings_warn_in_journal")):
                ts = ctl.clock_ts()
                if ts is not None:
                    prox = ctl._compute_event_proximity(ctl.focus_symbol, int(ts))
                    et = str(prox.get("earnings_proximity_tag") or "")
                    dt = str(prox.get("dividend_proximity_tag") or "")
                    if et:
                        suggested.append(et)
                    if dt:
                        suggested.append(dt)
                    notice = _proximity_notice(et, dt, prox, int(ts))
        except Exception:  # noqa: BLE001
            notice = ""
            suggested = []

        dlg = PreTradeFormDialog(
            self.app,
            symbol=ctl.focus_symbol,
            default_side=side,
            default_size=1.0,
            setup_tags=tags,
            notice=notice,
            suggested_tags=suggested,
        )
        self.app.wait_window(dlg)
        if dlg.result is None:
            return
        try:
            ctl.submit_order(
                symbol=dlg.result["symbol"],
                side=dlg.result["side"],
                quantity=dlg.result["quantity"],
                pre_trade_data=dlg.result["pre_trade_data"],
            )
            try:
                self.app._status.info(
                    f"Sandbox: queued {dlg.result['side']} "
                    f"{dlg.result['quantity']:g} {dlg.result['symbol']}"
                )
            except Exception:  # noqa: BLE001
                pass
        except (ValueError, RuntimeError) as exc:
            try:
                self.app._status.warn(f"Sandbox order rejected: {exc}")
            except Exception:  # noqa: BLE001
                pass

    def _submit_quickfire_order(self, ctl: Any, side: str) -> None:
        """Submit a sandbox order without showing the pre-trade modal.

        Audit ``mandatory-journal-skip``: a user who opts into rapid
        scalp-practice still needs the underlying
        :meth:`SandboxController.submit_order` contract honoured —
        thesis non-empty, quantity positive. We stamp a sentinel
        ``"(skipped)"`` thesis so any post-hoc analysis can identify
        these orders, default quantity 1, and leave every other
        field blank. The post-trade modal is suppressed by the
        same toggle in :meth:`_open_post_trade_modal`.
        """
        symbol = ctl.focus_symbol
        quantity = 1.0
        pre_trade_data = {
            "setup_tag": "",
            "thesis": "(skipped)",
            "conviction": 3,
            "size": quantity,
            "target": None,
            "notes": "",
        }
        try:
            ctl.submit_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                pre_trade_data=pre_trade_data,
            )
            try:
                self.app._status.info(
                    f"Sandbox: queued {side} {quantity:g} {symbol} "
                    f"(journal skipped)"
                )
            except Exception:  # noqa: BLE001
                pass
        except (ValueError, RuntimeError) as exc:
            try:
                self.app._status.warn(f"Sandbox order rejected: {exc}")
            except Exception:  # noqa: BLE001
                pass

    def _open_post_trade_modal(self, post_trade: Any) -> Optional[str]:
        """Callback handed to the controller: opens the mandatory review modal.

        Audit ``mandatory-journal-skip``: when the user has opted
        into rapid scalp-practice via
        ``sandbox_skip_detailed_journal``, return an empty review
        string so the engine records the close without surfacing
        the modal.
        """
        try:
            from ..defaults import get as _get_default
            if bool(_get_default("sandbox_skip_detailed_journal")):
                return ""
        except Exception:  # noqa: BLE001
            pass
        from .sandbox_review_dialog import PostTradeReviewDialog
        try:
            dlg = PostTradeReviewDialog(self.app, post_trade)
            self.app.wait_window(dlg)
            return dlg.result
        except tk.TclError:
            return None

    def _on_end_session(self) -> None:
        """Forward the End-Session button to the host's tear-down path.

        ``app._on_menu_sandbox_end`` already wraps each tear-down stage
        in its own try/except + status surface, so we don't add another
        blanket suppression here \u2014 a raise at this layer signals a
        wiring bug and should surface in the console rather than be
        silently eaten.
        """
        self.app._on_menu_sandbox_end()


__all__ = ("SandboxPanel",)
