"""GUI-driven sandbox harness.

Runs the real ChartApp Tk UI and drives it programmatically via Tk's
own event mechanisms (``widget.invoke()``, ``widget.event_generate``,
direct text-var writes) — i.e., the same code paths a human user
exercises when clicking the buttons, but without hijacking the OS
mouse / keyboard.

Why this exists: the headless arena (`tools/sandbox_arena.py`) verifies
the engine math; this verifies the **GUI plumbing** — modal dialog
flow, focus order, status messages, Next-bar key bindings, post-trade
review modal, and the chart's response to mid-session ticker
registrations. Any exception caught here is a real UX bug.

Architecture:

* The Tk root runs as normal (``app.mainloop()``).
* We schedule a recurring ``app.after(150, _tick)`` "agent brain" that
  runs every 150 ms. On each tick the agent inspects controller state
  and may queue an action (load ticker, open trade form, advance bar).
* A second recurring ``app.after(50, _dialog_watcher)`` scans
  ``app.winfo_children()`` for new ``Toplevel`` modals (sandbox start
  dialog, pre-trade form, post-trade review) and fills + submits each
  one programmatically. This pattern handles ``wait_window``-style
  modals without blocking the agent brain.
* All exceptions are caught and logged to ``ux_issues.log``.

Usage::

    python -m tools.sandbox_arena_gui
"""
from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, "src")

import tkinter as tk
from tkinter import ttk

from tradinglab.app import ChartApp


# ---------------------------------------------------------------------------
# UX issue log

LOG_PATH = Path("tools/ux_issues.log")


def _log(msg: str, *, level: str = "INFO") -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} [{level}] {msg}"
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ---------------------------------------------------------------------------
# Agent: simple Donchian breakout, blind-mode

class DonchianAgent:
    """Trades the focus symbol on 20-bar Donchian breakouts.

    State machine:
      IDLE → LONG (after we've submitted a BUY pre-trade form)
      LONG → IDLE (after we've submitted a SELL pre-trade form)

    ``has_open_trade_form`` blocks new decisions while a modal is up
    so we don't double-submit while the dialog watcher is mid-fill.
    """

    LOOKBACK = 40

    def __init__(self, app: ChartApp, symbol: str):
        self.app = app
        self.symbol = symbol
        self.position = 0.0   # mirror of controller state, for decision logic
        self.has_open_trade_form = False
        self.last_decision_bar = -1

    def decide(self) -> Optional[Dict[str, Any]]:
        """Return a trade-form payload (side+size) or None."""
        ctl = getattr(self.app, "_sandbox", None)
        if ctl is None or not ctl.is_active():
            return None
        if self.has_open_trade_form:
            return None
        # Get bars for our symbol from the controller's engine.
        try:
            engine = ctl.engine
            bars = engine.bars_by_symbol.get(self.symbol)
        except Exception:  # noqa: BLE001
            return None
        if bars is None:
            if self.last_decision_bar != -2:
                _log(f"agent: {self.symbol} not yet registered in engine "
                     f"(have: {list(getattr(engine, 'bars_by_symbol', {}).keys())})",
                     level="WARN")
                self.last_decision_bar = -2
            return None
        if len(bars) < self.LOOKBACK + 2:
            return None
        # Find the bar index corresponding to "now".
        try:
            now_ts = int(engine.clock.now_ts)
            idx = bars.index_for_ts(now_ts)
        except Exception:  # noqa: BLE001
            return None
        if idx is None or idx < self.LOOKBACK + 1:
            return None
        if idx == self.last_decision_bar:
            return None
        self.last_decision_bar = idx
        last = float(bars.close[idx])
        hi_window = bars.high[idx - self.LOOKBACK:idx]
        lo_window = bars.low[idx - self.LOOKBACK:idx]
        # Look at portfolio for actual position.
        pos_obj = engine.portfolio.positions.get(self.symbol)
        pos = float(pos_obj.quantity) if pos_obj else 0.0
        self.position = pos
        if pos == 0.0 and last > float(hi_window.max()):
            return {"side": "buy", "size": 10.0}
        if pos > 0.0 and last < float(lo_window.min()):
            return {"side": "sell", "size": pos}
        return None


# ---------------------------------------------------------------------------
# Driver

class GuiDriver:
    """Sequencer that walks the app through a sandbox session."""

    PHASES = (
        "BOOT", "LOAD_TICKER", "OPEN_SANDBOX", "RUN_SESSION",
        "END_SESSION", "DONE",
    )

    def __init__(self, app: ChartApp, *, symbol: str = "AAPL",
                 max_bars: int = 30, blind: bool = False,
                 target_days: int = 1):
        self.app = app
        self.symbol = symbol
        self.max_bars = max_bars
        self.blind = blind
        self.target_days = target_days
        self.bars_advanced = 0
        self.phase = "BOOT"
        self.phase_started_at = time.monotonic()
        self.agent = DonchianAgent(app, symbol)
        self.handled_dialogs: set = set()
        self._t0 = time.monotonic()
        self._dates_seen: set = set()
        self._last_session_date = None

    # -- phase helpers -------------------------------------------------------

    def _set_phase(self, name: str) -> None:
        _log(f"phase {self.phase} → {name}")
        self.phase = name
        self.phase_started_at = time.monotonic()

    def _phase_age(self) -> float:
        return time.monotonic() - self.phase_started_at

    # -- dialog watcher ------------------------------------------------------

    def _scan_toplevels(self) -> List[tk.Toplevel]:
        out: List[tk.Toplevel] = []
        for w in self.app.winfo_children():
            if isinstance(w, tk.Toplevel):
                out.append(w)
        return out

    def _install_pretrade_autofill(self) -> None:
        """Replace PreTradeFormDialog.__init__ so newly-opened pre-trade
        forms auto-fill from the driver's current ``_pending_pretrade``
        and submit themselves. Avoids any polling race against the
        dialog's modal lifecycle.
        """
        from tradinglab.gui import sandbox_dialog as sd
        original_init = sd.PreTradeFormDialog.__init__
        driver = self

        def patched_init(self_dlg, app, symbol, default_side="buy",
                         default_size=1.0, setup_tags=None):
            original_init(self_dlg, app, symbol, default_side,
                          default_size, setup_tags)
            decision = getattr(driver, "_pending_pretrade", None)
            if not decision:
                _log(f"PreTradeFormDialog opened for {symbol} but no "
                     f"pending decision; cancelling.", level="WARN")
                self_dlg.after(50, self_dlg._on_cancel)
                return
            _log(f"auto-filling PreTradeFormDialog: {decision}")
            try:
                self_dlg._side_var.set(decision["side"])
                self_dlg._size_var.set(str(decision["size"]))
                self_dlg._tag_var.set("breakout")
                self_dlg._thesis_text.delete("1.0", "end")
                self_dlg._thesis_text.insert(
                    "1.0",
                    f"Donchian-{driver.agent.LOOKBACK} "
                    f"{decision['side'].upper()} signal on {symbol}.")
                self_dlg._conv_var.set(3)
                self_dlg.update_idletasks()
                # Verify thesis actually went in.
                thesis_now = self_dlg._thesis_text.get("1.0", "end").strip()
                if not thesis_now:
                    _log("auto-fill: thesis still empty after insert!",
                         level="ERROR")
                    return
                # Schedule the Submit click on the next idle so the
                # dialog finishes mapping before we tear it down.
                self_dlg.after(120, self_dlg._on_submit)
                driver._pending_pretrade = None
                driver.agent.has_open_trade_form = False
            except Exception:  # noqa: BLE001
                _log(f"auto-fill exception:\n{traceback.format_exc()}",
                     level="ERROR")

        sd.PreTradeFormDialog.__init__ = patched_init  # type: ignore[assignment]
        _log("installed PreTradeFormDialog auto-fill patch")

    def _install_posttrade_autofill(self) -> None:
        """Replace PostTradeReviewDialog.__init__ so newly-opened
        post-trade review dialogs auto-fill the mandatory ``review``
        text and self-submit. The dialog refuses an empty body, so we
        synthesise a one-line review per closed trade.
        """
        from tradinglab.gui import sandbox_review_dialog as srd
        original_init = srd.PostTradeReviewDialog.__init__

        def patched_init(self_dlg, app, post_trade):
            original_init(self_dlg, app, post_trade)
            try:
                pnl = float(getattr(post_trade, "pnl", 0.0))
                pnl_pct = float(getattr(post_trade, "pnl_pct", 0.0)) * 100.0
                sign = "+" if pnl >= 0 else ""
                review = (f"Auto-review: {post_trade.symbol} "
                          f"{post_trade.side.upper()} closed "
                          f"{sign}${pnl:.2f} ({sign}{pnl_pct:.2f}%). "
                          f"Driven by sandbox_arena_gui agent.")
                _log(f"auto-filling PostTradeReviewDialog: "
                     f"{post_trade.symbol} pnl={pnl:.2f}")
                self_dlg._review_text.delete("1.0", "end")
                self_dlg._review_text.insert("1.0", review)
                self_dlg.update_idletasks()
                self_dlg.after(120, self_dlg._on_submit)
            except Exception:  # noqa: BLE001
                _log(f"posttrade auto-fill exception:\n"
                     f"{traceback.format_exc()}", level="ERROR")

        srd.PostTradeReviewDialog.__init__ = patched_init  # type: ignore[assignment]
        _log("installed PostTradeReviewDialog auto-fill patch")

    def _dialog_watcher(self) -> None:
        try:
            for tl in self._scan_toplevels():
                key = (str(tl), tl.winfo_class(), tl.title())
                if key in self.handled_dialogs:
                    continue
                title = tl.title() or ""
                if title == "Start Sandbox Session":
                    self._fill_start_dialog(tl)
                    self.handled_dialogs.add(key)
                elif title.startswith("Pre-Trade Form"):
                    self._fill_pretrade_dialog(tl)
                    self.handled_dialogs.add(key)
                elif "Review" in title or "Post-Trade" in title:
                    self._dismiss_review_dialog(tl)
                    self.handled_dialogs.add(key)
        except Exception:  # noqa: BLE001
            _log(f"dialog_watcher exception:\n{traceback.format_exc()}",
                 level="ERROR")
        finally:
            self.app.after(50, self._dialog_watcher)

    def _fill_start_dialog(self, dlg: tk.Toplevel) -> None:
        _log("filling SandboxStartDialog")
        try:
            if hasattr(dlg, "_blind_var"):
                dlg._blind_var.set(bool(self.blind))
                if hasattr(dlg, "_on_blind_toggle"):
                    dlg._on_blind_toggle()
            # Smaller cash for visibility.
            dlg._cash_var.set("100000")
            dlg._slip_var.set("2")
            dlg._comm_var.set("1")
            dlg._lookback_var.set("1")
            dlg._daily_bars_var.set("50")
            # Make sure 5m is checked, others off.
            for itv, var in dlg._interval_vars.items():
                var.set(itv == "5m")
            if hasattr(dlg, "_on_interval_change"):
                dlg._on_interval_change()
            if self.blind:
                _log(f"  blind=True (auto-cycle); target_days={self.target_days}")
            else:
                # Pick most recent eligible date if available.
                if hasattr(dlg, "_eligible_provider"):
                    eligible = dlg._eligible_provider("5m") or []
                    if eligible:
                        target = eligible[-1]
                        if hasattr(dlg, "_date_var"):
                            dlg._date_var.set(str(target))
                        _log(f"  picked session_date={target}")
                    else:
                        _log("  no eligible dates — using fetch_provider", level="WARN")
                        if hasattr(dlg, "_fetch_provider"):
                            ok = dlg._fetch_provider("5m")
                            _log(f"  fetch_provider returned {ok}")
                            eligible = dlg._eligible_provider("5m") or []
                            if eligible and hasattr(dlg, "_date_var"):
                                dlg._date_var.set(str(eligible[-1]))
            # Click Start.
            dlg.update_idletasks()
            self.app.after(100, lambda: self._safe_invoke(dlg, "_on_start"))
        except Exception:  # noqa: BLE001
            _log(f"_fill_start_dialog exception:\n{traceback.format_exc()}",
                 level="ERROR")

    def _fill_pretrade_dialog(self, dlg: tk.Toplevel) -> None:
        decision = getattr(self, "_pending_pretrade", None)
        if not decision:
            _log("PreTradeFormDialog appeared but no pending decision; "
                 "cancelling.", level="WARN")
            self._safe_invoke(dlg, "_on_cancel")
            return
        _log(f"filling PreTradeFormDialog: {decision}")
        try:
            dlg._side_var.set(decision["side"])
            dlg._size_var.set(str(decision["size"]))
            dlg._tag_var.set("breakout")
            dlg._thesis_text.delete("1.0", "end")
            dlg._thesis_text.insert(
                "1.0",
                f"Donchian-{self.agent.LOOKBACK} "
                f"{decision['side'].upper()} signal on {self.symbol}.")
            dlg._conv_var.set(3)
            self.app.after(80, lambda: self._safe_invoke(dlg, "_on_submit"))
            self.agent.has_open_trade_form = False
            self._pending_pretrade = None
        except Exception:  # noqa: BLE001
            _log(f"_fill_pretrade_dialog exception:\n{traceback.format_exc()}",
                 level="ERROR")

    def _dismiss_review_dialog(self, dlg: tk.Toplevel) -> None:
        _log(f"dismissing review dialog: {dlg.title()!r}")
        # Try common button names; fall back to destroying.
        for name in ("_on_skip", "_on_save", "_on_submit", "_on_ok"):
            fn = getattr(dlg, name, None)
            if callable(fn):
                try:
                    fn()
                    return
                except Exception:  # noqa: BLE001
                    pass
        try:
            dlg.destroy()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _safe_invoke(obj: Any, method: str) -> None:
        fn = getattr(obj, method, None)
        if not callable(fn):
            _log(f"missing method {method} on {obj!r}", level="ERROR")
            return
        try:
            fn()
        except Exception:  # noqa: BLE001
            _log(f"{method} raised:\n{traceback.format_exc()}", level="ERROR")

    # -- agent loop ----------------------------------------------------------

    def _agent_tick(self) -> None:
        try:
            self._step()
        except Exception:  # noqa: BLE001
            _log(f"agent_tick exception:\n{traceback.format_exc()}",
                 level="ERROR")
        finally:
            if self.phase != "DONE":
                self.app.after(30, self._agent_tick)

    def _step(self) -> None:
        if self.phase == "BOOT":
            if self._phase_age() > 1.5:
                self._set_phase("LOAD_TICKER")
            return
        if self.phase == "LOAD_TICKER":
            try:
                self.app.ticker_var.set(self.symbol)
                # Trigger the same code path as pressing Enter on the entry.
                if hasattr(self.app, "_on_ticker_changed"):
                    self.app._on_ticker_changed()
                elif hasattr(self.app, "_apply_primary_ticker"):
                    self.app._apply_primary_ticker(self.symbol)
                _log(f"loaded primary ticker = {self.symbol}")
            except Exception:  # noqa: BLE001
                _log(f"LOAD_TICKER exception:\n{traceback.format_exc()}",
                     level="ERROR")
            self._set_phase("OPEN_SANDBOX")
            return
        if self.phase == "OPEN_SANDBOX":
            if self._phase_age() > 4.0:
                # Wait for fetch to complete then open the dialog.
                _log("invoking app._on_menu_sandbox_start()")
                # Use after_idle so it runs after the dialog watcher
                # is primed for the about-to-appear modal.
                self.app.after_idle(self.app._on_menu_sandbox_start)
                self._set_phase("RUN_SESSION")
            return
        if self.phase == "RUN_SESSION":
            ctl = getattr(self.app, "_sandbox", None)
            if ctl is None or not ctl.is_active():
                if self._phase_age() > 30.0:
                    _log("session never became active", level="ERROR")
                    self._set_phase("DONE")
                return
            # Make sure our trading symbol is registered with the engine.
            if self.symbol not in ctl.engine.bars_by_symbol:
                try:
                    self.app._sandbox_register_and_focus(self.symbol)
                    _log(f"registered {self.symbol} with sandbox engine")
                except Exception:  # noqa: BLE001
                    _log(f"_sandbox_register_and_focus({self.symbol}):\n"
                         f"{traceback.format_exc()}", level="ERROR")
                return
            # If we have a pending decision the dialog watcher hasn't
            # filled yet, wait.
            if self.agent.has_open_trade_form:
                return
            decision = self.agent.decide()
            if decision is not None:
                _log(f"agent decision: {decision}")
                # Set focus to our symbol so Buy/Sell button knows
                # what to trade.
                try:
                    if ctl.focus_symbol != self.symbol:
                        ctl.set_focus(self.symbol)
                except Exception:  # noqa: BLE001
                    pass
                # Pre-stage the decision so the dialog watcher fills it.
                self._pending_pretrade = decision
                self.agent.has_open_trade_form = True
                # Click the Buy/Sell button on the panel.
                self._click_panel_button(decision["side"])
                return
            # Otherwise: advance to next bar.
            # Track distinct session dates seen (auto-cycle in blind mode
            # rolls into a new random date when EOD is hit).
            try:
                cur_date = getattr(ctl, "session_date", None)
                if cur_date is not None and cur_date != self._last_session_date:
                    if self._last_session_date is not None:
                        # New day after auto-cycle: bar indexing resets,
                        # positions are flat (auto-flattened by cycle),
                        # and any in-flight trade-form flag is stale.
                        self.agent.last_decision_bar = -1
                        self.agent.has_open_trade_form = False
                        self._pending_pretrade = None
                    self._last_session_date = cur_date
                    self._dates_seen.add(cur_date)
                    _log(f"session_date={cur_date} (days_seen="
                         f"{len(self._dates_seen)}/{self.target_days})")
            except Exception:  # noqa: BLE001
                pass
            if self.blind:
                if len(self._dates_seen) >= self.target_days:
                    # We've covered the requested number of days. The
                    # current day may still have bars left; let the agent
                    # finish the in-flight day so positions get flattened
                    # naturally, then end on the next cycle attempt.
                    if len(self._dates_seen) > self.target_days:
                        _log(f"reached target_days={self.target_days}")
                        self._set_phase("END_SESSION")
                        return
            else:
                if self.bars_advanced >= self.max_bars:
                    _log(f"reached max_bars={self.max_bars}")
                    self._set_phase("END_SESSION")
                    return
            self._click_panel_button("next")
            self.bars_advanced += 1
            return
        if self.phase == "END_SESSION":
            self._summarize()
            try:
                if hasattr(self.app, "_on_menu_sandbox_end"):
                    self.app._on_menu_sandbox_end()
                    _log("ended sandbox session")
            except Exception:  # noqa: BLE001
                _log(f"END_SESSION exception:\n{traceback.format_exc()}",
                     level="ERROR")
            self._set_phase("DONE")
            self.app.after(2000, self._shutdown)
            return

    def _click_panel_button(self, kind: str) -> None:
        """Find the SandboxPanel and invoke its Buy/Sell/Next-bar buttons."""
        from tradinglab.gui.sandbox_panel import SandboxPanel
        panel: Optional[SandboxPanel] = None
        for w in self._walk(self.app):
            if isinstance(w, SandboxPanel):
                panel = w
                break
        if panel is None:
            _log("could not find SandboxPanel widget", level="ERROR")
            return
        if kind == "next":
            self._safe_invoke(panel, "_on_next_bar")
        elif kind in ("buy", "sell"):
            try:
                panel._on_trade_button(kind)
            except Exception:  # noqa: BLE001
                _log(f"_on_trade_button({kind}):\n{traceback.format_exc()}",
                     level="ERROR")

    def _walk(self, w: tk.Misc):
        yield w
        try:
            for c in w.winfo_children():
                yield from self._walk(c)
        except Exception:  # noqa: BLE001
            return

    def _summarize(self) -> None:
        ctl = getattr(self.app, "_sandbox", None)
        if ctl is None:
            return
        try:
            cash = ctl.engine.portfolio.cash
            fills = list(ctl.engine.fills)
            n_post = len(ctl.engine.post_trades)
            archived_post = list(getattr(ctl, "_archived_post_trades", []) or [])
            archived_fills = list(getattr(ctl, "_archived_fills", []) or [])
            total_post = n_post + len(archived_post)
            total_fills = len(fills) + len(archived_fills)
            all_post = list(archived_post) + list(ctl.engine.post_trades)
            wins = sum(1 for p in all_post if float(p.pnl) > 0)
            losses = sum(1 for p in all_post if float(p.pnl) < 0)
            gross_pnl = sum(float(p.pnl) for p in all_post)
            starting_cash = 100000.0
            equity_now = float(cash)
            ret_pct = (equity_now - starting_cash) / starting_cash * 100.0
            _log("=" * 70)
            _log(f"FINAL: days_seen={len(self._dates_seen)} "
                 f"target_days={self.target_days}")
            _log(f"FINAL: cash=${cash:,.2f}  return={ret_pct:+.2f}%  "
                 f"gross_pnl(closed)=${gross_pnl:+,.2f}")
            _log(f"FINAL: round_trips={total_post}  "
                 f"wins={wins}  losses={losses}  "
                 f"win_rate={(wins / total_post * 100 if total_post else 0):.1f}%")
            _log(f"FINAL: total_fills={total_fills}  "
                 f"bars_advanced={self.bars_advanced}  "
                 f"elapsed={time.monotonic() - self._t0:.1f}s")
            _log("=" * 70)
        except Exception:  # noqa: BLE001
            _log(f"summary exception:\n{traceback.format_exc()}",
                 level="ERROR")

    def _shutdown(self) -> None:
        _log("shutting down Tk root")
        try:
            self.app.quit()
        except Exception:  # noqa: BLE001
            pass

    def start(self) -> None:
        self._install_pretrade_autofill()
        self._install_posttrade_autofill()
        self.app.after(100, self._dialog_watcher)
        self.app.after(500, self._agent_tick)


def main() -> int:
    if LOG_PATH.exists():
        LOG_PATH.unlink()
    _log("=" * 70)
    _log("GUI sandbox driver starting")
    _log("=" * 70)
    app = ChartApp()
    driver = GuiDriver(app, symbol="AAPL", blind=True, target_days=30,
                       max_bars=10000)
    driver.start()
    try:
        app.mainloop()
    except Exception:  # noqa: BLE001
        _log(f"mainloop exception:\n{traceback.format_exc()}", level="ERROR")
    finally:
        try:
            app._on_close()
        except Exception:  # noqa: BLE001
            pass
    _log("driver finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
