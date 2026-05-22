"""ExitsTab — right-side notebook tab listing positions + their exit
strategies, plus a status Treeview and audit-log tail.

Surface
-------

Top toolbar:

* **Edit Strategies…** — opens the modeless ``ExitsDialog``.
* **PANIC: Flatten All** — red button. Two-phase confirmation:
  click 1 → confirm dialog; click 2 → loops over every open
  position calling ``evaluator.panic_flatten_position`` (phase 1)
  then ``evaluator.submit_market_flatten`` (phase 2).
* **Refresh** — re-pulls positions + library + audit tail.
* **Strategies needing attention: N** — badge that lights up if
  any broken strategies exist.

Body:

* Top "Attach" panel: per-open-position rows. Each row has a
  ``ttk.Combobox`` of saved strategy names + an Attach / Detach
  button. Visual marker "NO EXITS — at risk" if no strategy is
  attached.
* Bottom Treeview "Status": one row per ``(position, leg,
  trigger)`` tuple, columns ``Symbol Side Qty Strategy Leg
  Trigger State Current Trigger Distance``. Diff-update on
  ``refresh``; selection is preserved across refresh.

* Audit-log tail: collapsible last-100 lines.

The tab does **not** drive ticks or own the evaluator; the host
``ChartApp`` calls :meth:`refresh` after each tick (this is a cheap
diff-render). Live attach/detach goes through the evaluator the
caller passed in at construction.
"""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..exits import storage as _exits_storage
from ..exits.audit import AuditLog
from ..exits.evaluator import ExitEvaluator
from ..exits.model import ExitStrategy
from ..positions.model import Position
from ..positions.tracker import PositionTracker
from .colors import MUTED_GREY, WARN_AMBER
from .exits_dialog import open_exits_dialog

logger = logging.getLogger(__name__)


_NO_STRATEGY_LABEL = "(none)"
_TREEVIEW_COLS = (
    "symbol", "side", "qty", "strategy",
    "leg", "trigger", "state", "current", "trigger_price", "distance",
)
_TREEVIEW_HEADERS = {
    "symbol":        "Symbol",
    "side":          "Side",
    "qty":           "Qty",
    "strategy":      "Strategy",
    "leg":           "Leg",
    "trigger":       "Trigger",
    "state":         "State",
    "current":       "Current",
    "trigger_price": "Trigger px",
    "distance":      "Distance",
}


def _format_audit_record(rec: Dict[str, Any]) -> str:
    """Compact, human-readable one-line summary of an audit record.

    When the record carries within-last-N-bars look-back evidence (set
    by :class:`tradinglab.exits.evaluator.ExitEvaluator` via
    :class:`tradinglab.exits.spec.Decision.evidence`), each leaf is
    rendered as an indented child line so the user sees what bar the
    underlying condition actually fired on.
    """
    ts = rec.get("ts", "")
    kind = rec.get("kind", "?")
    sid = rec.get("strategy_id") or ""
    pid = rec.get("position_id") or ""
    qty = rec.get("qty")
    price = rec.get("price")
    parts = [f"{ts} {kind}"]
    if sid:
        parts.append(f"strat={sid[:6]}")
    if pid:
        parts.append(f"pos={pid[:6]}")
    if qty is not None:
        parts.append(f"qty={qty:g}")
    if price is not None:
        parts.append(f"px={price:g}")
    head = "  ".join(parts)

    meta = rec.get("meta") or {}
    evidence = meta.get("evidence") if isinstance(meta, dict) else None
    if not evidence:
        return head

    lines = [head]
    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        bars_ago = ev.get("bars_ago")
        ts_iso = ev.get("timestamp") or ""
        node_id = ev.get("node_id") or ""
        node_short = node_id[:6] if node_id else "?"
        if bars_ago is None:
            when = "?"
        elif bars_ago == 0:
            when = "this bar"
        elif bars_ago == 1:
            when = "1 bar ago"
        else:
            when = f"{int(bars_ago)} bars ago"
        # Strip date prefix from full ISO so the line is compact —
        # "2024-01-15T10:35:00" → "10:35:00".
        time_part = ts_iso.split("T", 1)[1] if "T" in ts_iso else ts_iso
        if time_part:
            lines.append(f"    \u2022 {node_short} fired {when} at {time_part}")
        else:
            lines.append(f"    \u2022 {node_short} fired {when}")
    return "\n".join(lines)


class ExitsTab(ttk.Frame):
    """Notebook tab for exit-strategy attach/detach + live status."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        tracker: PositionTracker,
        evaluator: ExitEvaluator,
        audit: Optional[AuditLog] = None,
        on_open_dialog: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master)
        self._tracker = tracker
        self._evaluator = evaluator
        self._audit = audit
        self._on_open_dialog = on_open_dialog

        # --- state ---
        self._library: List[ExitStrategy] = []
        self._broken: List[_exits_storage.BrokenStrategy] = []
        self._panic_armed: bool = False
        # Attach panel: per-position widget state.
        self._attach_rows: Dict[str, "_AttachRow"] = {}
        # Last-good per-position last_price for distance computation.
        self._last_prices: Dict[str, float] = {}

        self._build_layout()
        self.refresh()

    # ----- public API -----

    @property
    def library(self) -> Tuple[ExitStrategy, ...]:
        return tuple(self._library)

    @property
    def broken_count(self) -> int:
        return len(self._broken)

    def refresh(self) -> None:
        """Reload library, redraw attach panel, refresh Treeview + audit tail."""
        try:
            self._library, self._broken = _exits_storage.load_all()
        except Exception:  # noqa: BLE001
            logger.exception("ExitsTab: load_all failed")
            self._library, self._broken = [], []
        self._library = sorted(self._library, key=lambda s: s.name.lower())

        self._refresh_badge()
        self._refresh_attach_panel()
        self._refresh_status_tree()
        self._refresh_audit_tail()

    def attach_for_position(
        self, position_id: str, strategy_id: str,
    ) -> None:
        """Programmatic attach — used by app's auto-bind flow."""
        strat = next((s for s in self._library if s.id == strategy_id), None)
        if strat is None:
            logger.warning("ExitsTab: strategy %r not in library", strategy_id)
            return
        self._evaluator.attach_strategy(position_id, strat)
        self.refresh()

    # ----- layout -----

    def _build_layout(self) -> None:
        # Toolbar
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=4, pady=(4, 2))
        ttk.Button(bar, text="Edit Strategies…",
                   command=self._on_open_dialog_clicked).pack(side="left")
        self._panic_btn = ttk.Button(
            bar, text="PANIC: Flatten All",
            style="Destructive.TButton",
            command=self._on_panic_clicked,
        )
        self._panic_btn.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Refresh", command=self.refresh).pack(side="left", padx=(8, 0))

        self._badge_var = tk.StringVar(value="")
        self._badge_lbl = ttk.Label(
            bar, textvariable=self._badge_var, foreground=WARN_AMBER,
        )
        self._badge_lbl.pack(side="right")

        # Body — paned: attach panel above, status + audit below
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=4, pady=2)

        # Attach panel
        self._attach_frame = ttk.LabelFrame(paned, text="Open positions")
        paned.add(self._attach_frame, weight=1)

        self._attach_holder = ttk.Frame(self._attach_frame)
        self._attach_holder.pack(fill="both", expand=True, padx=4, pady=4)

        self._no_positions_lbl = ttk.Label(
            self._attach_holder, text="(no open positions)", foreground=MUTED_GREY,
        )
        # Shown only when there are no positions

        # Status + audit
        bottom = ttk.Frame(paned)
        paned.add(bottom, weight=2)

        # Status Treeview
        status_lf = ttk.LabelFrame(bottom, text="Trigger status")
        status_lf.pack(fill="both", expand=True)

        self._tree = ttk.Treeview(
            status_lf, columns=_TREEVIEW_COLS, show="headings", height=10,
        )
        for col in _TREEVIEW_COLS:
            self._tree.heading(col, text=_TREEVIEW_HEADERS[col])
            width = 70 if col not in ("strategy", "leg", "trigger") else 100
            self._tree.column(col, width=width, anchor="w", stretch=True)
        sb = ttk.Scrollbar(status_lf, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Audit log tail
        audit_lf = ttk.LabelFrame(bottom, text="Audit log (tail)")
        audit_lf.pack(fill="both", expand=False, pady=(2, 0))
        self._audit_txt = tk.Text(audit_lf, height=6, wrap="none")
        self._audit_txt.pack(fill="both", expand=True, padx=2, pady=2)
        self._audit_txt.configure(state="disabled")

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def _apply_theme(self, theme: Dict[str, str]) -> None:
        """Repaint the non-ttk chrome to match the active palette.

        ttk.Style does NOT cover classic ``tk.Text`` widgets, so the
        audit-tail pane keeps the OS-default white-on-black palette
        unless we set its colours explicitly. Called from
        :meth:`tradinglab.app.ChartApp._apply_theme` after every
        theme switch so toggling Light↔Dark also flips this pane.

        The PANIC button is styled via ``Destructive.TButton`` (defined
        in :func:`constants.build_ttk_style_spec`) so it reads as red
        in both light and dark themes without per-theme overrides here.
        """
        if not theme:
            return
        bg = theme.get("ax_bg") or theme.get("tree_bg") or "#ffffff"
        fg = theme.get("text") or "#111111"
        sel_bg = theme.get("spine") or "#888888"
        sel_fg = fg
        txt = getattr(self, "_audit_txt", None)
        if txt is not None:
            try:
                txt.configure(
                    background=bg, foreground=fg,
                    insertbackground=fg,
                    selectbackground=sel_bg, selectforeground=sel_fg,
                )
            except tk.TclError:
                pass

    # ----- helpers -----

    def _refresh_badge(self) -> None:
        n = len(self._broken)
        if n:
            self._badge_var.set(f"⚠ Strategies needing attention: {n}")
        else:
            self._badge_var.set("")

    def _refresh_attach_panel(self) -> None:
        """Diff-update attach rows: add/remove rows to match open positions."""
        positions = self._tracker.list_open()
        present_ids = {p.id for p in positions}

        # Remove rows for closed positions
        for pid in list(self._attach_rows.keys()):
            if pid not in present_ids:
                self._attach_rows[pid].destroy()
                del self._attach_rows[pid]

        # No positions placeholder
        if not positions:
            try:
                self._no_positions_lbl.pack(fill="x", padx=2, pady=4)
            except tk.TclError:
                pass
            return
        else:
            try:
                self._no_positions_lbl.pack_forget()
            except tk.TclError:
                pass

        # Add/update rows
        for p in positions:
            attached = self._evaluator.attached_strategy(p.id)
            if p.id in self._attach_rows:
                self._attach_rows[p.id].update(p, attached, self._library)
            else:
                row = _AttachRow(
                    self._attach_holder, position=p, attached=attached,
                    library=self._library, tab=self,
                )
                row.pack(fill="x", pady=1)
                self._attach_rows[p.id] = row

    def _refresh_status_tree(self) -> None:
        # Snapshot current selection iids so we can restore.
        prior_selection = set(self._tree.selection())
        # Snapshot existing items by iid for diff-update.
        prior = set(self._tree.get_children(""))
        keep: set = set()

        for pos in self._tracker.list_open():
            strat = self._evaluator.attached_strategy(pos.id)
            cur_price = pos.last_price
            if cur_price is not None:
                self._last_prices[pos.id] = float(cur_price)
            if strat is None:
                continue
            for leg in strat.legs:
                for trig in leg.triggers:
                    iid = f"{pos.id}|{leg.id}|{trig.id}"
                    keep.add(iid)
                    state = self._format_state(pos.id, leg.id, trig.id)
                    trig_px = self._format_trigger_price(pos, leg, trig)
                    distance = self._format_distance(cur_price, trig_px)
                    values = (
                        pos.symbol, pos.side, f"{pos.qty_open:g}",
                        strat.name or strat.id[:6],
                        leg.label or leg.id[:6],
                        f"{trig.kind.value}",
                        state,
                        f"{cur_price:g}" if cur_price is not None else "—",
                        trig_px,
                        distance,
                    )
                    if iid in prior:
                        self._tree.item(iid, values=values)
                    else:
                        self._tree.insert(
                            "", "end", iid=iid, values=values,
                        )

        # Remove rows that no longer exist
        for iid in prior - keep:
            try:
                self._tree.delete(iid)
            except tk.TclError:
                pass

        # Restore selection if any of the previously-selected iids still exist
        sel = [iid for iid in prior_selection if iid in keep]
        if sel:
            try:
                self._tree.selection_set(sel)
            except tk.TclError:
                pass

    def _format_state(self, position_id: str, leg_id: str, trigger_id: str) -> str:
        slot = self._evaluator.trigger_state(position_id, leg_id, trigger_id)
        if slot is None:
            return "—"
        if slot.broken:
            return "BROKEN"
        if not slot.armed:
            return "DISARMED"
        if slot.state.fire_count > 0:
            return f"FIRED×{slot.state.fire_count}"
        return "ARMED"

    def _format_trigger_price(self, pos: Position, leg: Any, trig: Any) -> str:
        # Best-effort: only shows static prices. For trail/indicator we
        # show the dynamic trail_price (if available) or "—" otherwise.
        slot = self._evaluator.trigger_state(pos.id, leg.id, trig.id)
        if slot is not None and slot.state.trail_price is not None:
            return f"{slot.state.trail_price:g}"
        if trig.price is not None:
            return f"{trig.price:g}"
        if trig.offset_pct is not None and pos.avg_entry_price:
            offset = pos.avg_entry_price * (trig.offset_pct / 100.0)
            sign = -1 if pos.side == "short" else 1
            # For STOP/LIMIT below entry → minus on long, plus on short
            return f"{pos.avg_entry_price + sign * offset:g}"
        if trig.offset_dollar is not None and pos.avg_entry_price:
            sign = -1 if pos.side == "short" else 1
            return f"{pos.avg_entry_price + sign * trig.offset_dollar:g}"
        return "—"

    def _format_distance(self, current: Optional[float], trig_px: str) -> str:
        if current is None or trig_px == "—":
            return "—"
        try:
            tp = float(trig_px)
        except ValueError:
            return "—"
        return f"{(tp - float(current)):+.2f}"

    def _refresh_audit_tail(self) -> None:
        if self._audit is None:
            return
        try:
            records = self._audit.tail(100)
        except Exception:  # noqa: BLE001
            logger.exception("ExitsTab: audit.tail raised")
            records = []
        self._audit_txt.configure(state="normal")
        self._audit_txt.delete("1.0", "end")
        for rec in records:
            self._audit_txt.insert("end", _format_audit_record(rec) + "\n")
        self._audit_txt.configure(state="disabled")

    # ----- toolbar callbacks -----

    def _on_open_dialog_clicked(self) -> None:
        if self._on_open_dialog is not None:
            try:
                self._on_open_dialog()
            except Exception:  # noqa: BLE001
                logger.exception("on_open_dialog callback raised")
        else:
            # Fallback: open dialog inline
            open_exits_dialog(self.winfo_toplevel(),
                              on_library_changed=self.refresh)

    def _on_panic_clicked(self) -> None:
        """Two-phase panic: first click arms; second confirms and fires."""
        if not self._panic_armed:
            if not messagebox.askyesno(
                "Panic flatten",
                "PANIC: Flatten ALL open positions and cancel ALL working orders?",
                parent=self.winfo_toplevel(),
            ):
                return
            self._panic_armed = True
            self._panic_btn.configure(text="PANIC: Confirm")
            # Auto-disarm after ~5 seconds
            self.after(5000, self._disarm_panic)
            return
        # Confirmed — execute
        self._do_panic_flatten()
        self._disarm_panic()

    def _disarm_panic(self) -> None:
        if self._panic_armed:
            self._panic_armed = False
            try:
                self._panic_btn.configure(text="PANIC: Flatten All")
            except tk.TclError:
                pass

    def _do_panic_flatten(self) -> None:
        positions = self._tracker.list_open()
        for pos in positions:
            try:
                self._evaluator.panic_flatten_position(pos.id)
            except Exception:  # noqa: BLE001
                logger.exception("panic_flatten_position raised for %s", pos.id)
        for pos in positions:
            try:
                self._evaluator.submit_market_flatten(pos.id)
            except Exception:  # noqa: BLE001
                logger.exception("submit_market_flatten raised for %s", pos.id)
        self.refresh()

    # ----- attach-row callbacks -----

    def attach_strategy_for(self, position_id: str, strategy_id: str) -> None:
        strat = next((s for s in self._library if s.id == strategy_id), None)
        if strat is None:
            logger.warning("ExitsTab: strategy id %r not in library", strategy_id)
            return
        try:
            self._evaluator.attach_strategy(position_id, strat)
        except Exception:  # noqa: BLE001
            logger.exception("attach_strategy raised for %s", position_id)
            return
        self.refresh()

    def detach_strategy_for(self, position_id: str) -> None:
        try:
            self._evaluator.detach_strategy(position_id, reason="user")
        except Exception:  # noqa: BLE001
            logger.exception("detach_strategy raised for %s", position_id)
            return
        self.refresh()


# ---------------------------------------------------------------------------
# Per-position attach row
# ---------------------------------------------------------------------------


class _AttachRow(ttk.Frame):
    """One row in the attach panel: position summary + strategy combobox."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        position: Position,
        attached: Optional[ExitStrategy],
        library: Sequence[ExitStrategy],
        tab: ExitsTab,
    ) -> None:
        super().__init__(master, padding=2, borderwidth=1, relief="ridge")
        self._tab = tab
        self._position_id = position.id

        self._summary_var = tk.StringVar()
        self._strategy_var = tk.StringVar()
        self._warning_var = tk.StringVar()

        ttk.Label(self, textvariable=self._summary_var).pack(side="left", padx=(2, 8))

        self._strategy_combo = ttk.Combobox(
            self, textvariable=self._strategy_var, state="readonly",
            width=24, values=[],
        )
        self._strategy_combo.pack(side="left", padx=(0, 4))

        self._attach_btn = ttk.Button(
            self, text="Attach", command=self._on_attach,
        )
        self._attach_btn.pack(side="left", padx=(0, 2))

        self._detach_btn = ttk.Button(
            self, text="Detach", command=self._on_detach,
        )
        self._detach_btn.pack(side="left", padx=(0, 8))

        self._warning_lbl = ttk.Label(
            self, textvariable=self._warning_var, foreground=WARN_AMBER,
        )
        self._warning_lbl.pack(side="left", fill="x", expand=True)

        self.update(position, attached, library)

    def update(
        self,
        position: Position,
        attached: Optional[ExitStrategy],
        library: Sequence[ExitStrategy],
    ) -> None:
        self._summary_var.set(
            f"{position.symbol}  {position.side.upper()}  qty={position.qty_open:g}  "
            f"@ {position.avg_entry_price:g}"
        )
        names = [_NO_STRATEGY_LABEL] + [
            (s.name or s.id[:6]) for s in library
        ]
        self._strategy_combo["values"] = names
        self._library_snapshot = list(library)
        if attached is not None:
            self._strategy_var.set(attached.name or attached.id[:6])
            self._attach_btn.state(["disabled"])
            self._detach_btn.state(["!disabled"])
            self._warning_var.set("")
        else:
            self._strategy_var.set(_NO_STRATEGY_LABEL)
            self._attach_btn.state(["!disabled"] if library else ["disabled"])
            self._detach_btn.state(["disabled"])
            self._warning_var.set("⚠ NO EXITS — at risk")

    def _on_attach(self) -> None:
        sel = self._strategy_var.get()
        if sel == _NO_STRATEGY_LABEL or not sel:
            return
        # Resolve back to id (names are unique by storage validation)
        for s in self._library_snapshot:
            label = s.name or s.id[:6]
            if label == sel:
                self._tab.attach_strategy_for(self._position_id, s.id)
                return

    def _on_detach(self) -> None:
        if not messagebox.askyesno(
            "Detach",
            "Detach the active exit strategy? "
            "Armed legs will be canceled.",
            parent=self.winfo_toplevel(),
        ):
            return
        self._tab.detach_strategy_for(self._position_id)
