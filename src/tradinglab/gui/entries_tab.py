"""EntriesTab — right-side notebook tab listing entry strategies + their
runtime arm state.

Surface
-------

Top toolbar:

* **+ New** — opens an :class:`EntriesDialog` with an empty draft.
* **Edit** — opens a dialog seeded with the selected strategy.
* **Delete** — removes the selected strategy from storage.
* **Duplicate** — clones the selected strategy with a fresh id.
* **Import** / **Export** — JSON round-trip via
  :func:`tradinglab.entries.storage.import_from_path` /
  :func:`export_to_path`.
* **Arm** / **Disarm** / **Disarm All** — toggles runtime arm state on
  the evaluator (NOT persisted).
* **Load template…** — picks a JSON template from
  ``data/entry_strategy_templates`` and saves a fresh copy with a new id.

Body:

* Treeview of strategies — columns: id / name / direction / trigger
  kind / enabled / armed / fires-this-session.
* Bottom split: audit-tail (last 50 entries) + stats panel (a textual
  dump of :class:`EvaluatorStats`).

Refresh strategy: on construction; explicit ``refresh()`` after any
mutating button; and a 1-second ``after()`` tick to keep the audit /
stats panes live during sandbox replays.
"""
from __future__ import annotations

import json
import logging
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from .._resources import resource_path as _resource_path
from ..entries import storage as _entries_storage
from ..entries.evaluator import EntryEvaluator, EvaluatorStats
from ..entries.model import (
    CreatedWith,
    EntryStrategy,
)
from .entries_dialog import EntriesDialog

logger = logging.getLogger(__name__)


_TREEVIEW_COLS = (
    "name", "direction", "kind", "enabled", "armed", "fires", "id_short",
)
_TREEVIEW_HEADERS = {
    "name":      "Name",
    "direction": "Dir",
    "kind":      "Trigger",
    "enabled":   "Enabled",
    "armed":     "Armed",
    "fires":     "Fires",
    "id_short":  "Id",
}


def _format_audit_record(rec: dict[str, Any]) -> str:
    """Compact one-line summary mirroring :func:`gui.exits_tab._format_audit_record`.

    When the record carries within-last-N-bars look-back evidence
    (set by :class:`tradinglab.entries.evaluator.EntryEvaluator`
    on the ``entry_fire`` audit record's ``meta["evidence"]``), each
    leaf is rendered as an indented child line so the user can see
    which underlying scanner / indicator condition fired and on
    what bar — e.g. ``"  • c0d1e2 fired 1 bar ago at 10:35:00"``.
    """
    ts = rec.get("ts", "")
    kind = rec.get("kind", "?")
    sid = rec.get("strategy_id") or ""
    sym = rec.get("symbol") or ""
    pid = rec.get("position_id") or ""
    qty = rec.get("qty")
    price = rec.get("price")
    parts = [f"{ts} {kind}"]
    if sid:
        parts.append(f"strat={sid[:6]}")
    if sym:
        parts.append(f"sym={sym}")
    if pid:
        parts.append(f"pos={pid[:6]}")
    if qty is not None:
        try:
            parts.append(f"qty={float(qty):g}")
        except (TypeError, ValueError):
            pass
    if price is not None:
        try:
            parts.append(f"px={float(price):g}")
        except (TypeError, ValueError):
            pass
    meta = rec.get("meta")
    if isinstance(meta, dict):
        reason = meta.get("reason") or meta.get("gate")
        if reason:
            parts.append(f"reason={reason}")
    head = "  ".join(parts)

    evidence = (
        meta.get("evidence") if isinstance(meta, dict) else None
    )
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
        time_part = ts_iso.split("T", 1)[1] if "T" in ts_iso else ts_iso
        if time_part:
            lines.append(f"    \u2022 {node_short} fired {when} at {time_part}")
        else:
            lines.append(f"    \u2022 {node_short} fired {when}")
    return "\n".join(lines)


class EntriesTab(ttk.Frame):
    """Notebook tab for entry-strategy library + runtime arm state."""

    # Path to the prepackaged template directory. Resolved via
    # :func:`tradinglab._resources.resource_path` so the lookup
    # works whether we're running from source (``<repo>/data/...``)
    # or from a PyInstaller-frozen bundle
    # (``<bundle>/_internal/data/...``).
    DEFAULT_TEMPLATES_DIR = (
        _resource_path("data", "entry_strategy_templates")
    )
    REFRESH_TICK_MS = 1000

    def __init__(
        self,
        master: tk.Misc,
        *,
        evaluator: EntryEvaluator,
        storage: Any = None,
        exit_storage: Any = None,
        on_chart_focus: Callable[[str], None] | None = None,
        templates_dir: Path | None = None,
    ) -> None:
        super().__init__(master)
        self._evaluator = evaluator
        # ``storage`` and ``exit_storage`` may be the module objects or
        # any object exposing the same surface — we stash both to keep
        # the test injection point simple.
        self._storage = storage or _entries_storage
        self._exit_storage = exit_storage
        self._on_chart_focus = on_chart_focus
        self._templates_dir = Path(templates_dir or self.DEFAULT_TEMPLATES_DIR)

        # Library snapshot (refreshed each refresh()).
        self._library: list[EntryStrategy] = []
        self._broken: list[Any] = []

        # Tracked Toplevel handles for cleanup-style dialogs.
        self._dialog: EntriesDialog | None = None

        # Auto-tick id (so we can cancel on destroy).
        self._tick_after_id: str | None = None

        self._build_layout()
        self.refresh()
        self._schedule_tick()

        self.bind("<Destroy>", self._on_destroy, add="+")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def library(self) -> tuple[EntryStrategy, ...]:
        return tuple(self._library)

    @property
    def selected_strategy_id(self) -> str | None:
        sel = self._tree.selection()
        if not sel:
            return None
        return sel[0]

    def refresh(self) -> None:
        """Reload library + repopulate Treeview + refresh audit/stats."""
        try:
            self._library, self._broken = self._storage.load_all()
        except Exception:  # noqa: BLE001
            logger.exception("EntriesTab: storage.load_all failed")
            self._library, self._broken = [], []
        self._library = sorted(self._library, key=lambda s: s.name.lower())

        # Push the loaded library into the evaluator so its `_strategies`
        # cache is in sync. This is also called after every save/delete.
        try:
            self._evaluator.set_strategies(self._library)
        except Exception:  # noqa: BLE001
            logger.exception("EntriesTab: evaluator.set_strategies failed")

        self._refresh_tree()
        self._refresh_audit_tail()
        self._refresh_stats()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        # Toolbar — two rows for compactness.
        bar1 = ttk.Frame(self)
        bar1.pack(fill="x", padx=4, pady=(4, 2))
        ttk.Button(bar1, text="+ New", command=self._on_new).pack(
            side="left")
        ttk.Button(bar1, text="Edit", command=self._on_edit).pack(
            side="left", padx=(4, 0))
        ttk.Button(bar1, text="Delete", command=self._on_delete).pack(
            side="left", padx=(4, 0))
        ttk.Button(bar1, text="Duplicate", command=self._on_duplicate).pack(
            side="left", padx=(4, 0))
        ttk.Button(bar1, text="Import…", command=self._on_import).pack(
            side="left", padx=(4, 0))
        ttk.Button(bar1, text="Export…", command=self._on_export).pack(
            side="left", padx=(4, 0))

        bar2 = ttk.Frame(self)
        bar2.pack(fill="x", padx=4, pady=(0, 2))
        ttk.Button(bar2, text="Arm", command=self._on_arm).pack(
            side="left")
        ttk.Button(bar2, text="Disarm", command=self._on_disarm).pack(
            side="left", padx=(4, 0))
        ttk.Button(bar2, text="Disarm All", command=self._on_disarm_all).pack(
            side="left", padx=(4, 0))
        ttk.Button(bar2, text="Load template…",
                   command=self._on_load_template).pack(
            side="left", padx=(4, 0))
        ttk.Button(bar2, text="Refresh", command=self.refresh).pack(
            side="right")

        # Body: Treeview + bottom audit/stats split.
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=4, pady=2)

        tree_lf = ttk.LabelFrame(paned, text="Strategies")
        paned.add(tree_lf, weight=2)

        self._tree = ttk.Treeview(
            tree_lf, columns=_TREEVIEW_COLS, show="headings", height=10,
        )
        for col in _TREEVIEW_COLS:
            self._tree.heading(col, text=_TREEVIEW_HEADERS[col])
            width = 140 if col == "name" else 70
            self._tree.column(col, width=width, anchor="w", stretch=True)
        sb = ttk.Scrollbar(tree_lf, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Bottom split.
        bottom_paned = ttk.PanedWindow(paned, orient="horizontal")
        paned.add(bottom_paned, weight=1)

        audit_lf = ttk.LabelFrame(bottom_paned, text="Audit (tail)")
        bottom_paned.add(audit_lf, weight=2)
        self._audit_txt = tk.Text(audit_lf, height=8, wrap="none")
        self._audit_txt.pack(fill="both", expand=True, padx=2, pady=2)
        self._audit_txt.configure(state="disabled")

        stats_lf = ttk.LabelFrame(bottom_paned, text="Stats")
        bottom_paned.add(stats_lf, weight=1)
        self._stats_txt = tk.Text(stats_lf, height=8, wrap="word")
        self._stats_txt.pack(fill="both", expand=True, padx=2, pady=2)
        self._stats_txt.configure(state="disabled")

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def _apply_theme(self, theme: dict[str, str]) -> None:
        """Repaint the non-ttk chrome to match the active palette.

        ttk.Style does NOT cover classic ``tk.Text`` widgets, so the
        audit-tail and stats panes keep the OS-default white-on-black
        palette unless we set their colours explicitly. Called from
        :meth:`tradinglab.app.ChartApp._apply_theme` after every
        theme switch so toggling Light↔Dark also flips these panes.
        """
        if not theme:
            return
        bg = theme.get("ax_bg") or theme.get("tree_bg") or "#ffffff"
        fg = theme.get("text") or "#111111"
        # Selection highlight inside the read-only Text widget — uses
        # ``spine`` (a mid-tone neutral) so highlighted-and-copied
        # text stays legible against either palette.
        sel_bg = theme.get("spine") or "#888888"
        sel_fg = fg
        for txt in (getattr(self, "_audit_txt", None),
                    getattr(self, "_stats_txt", None)):
            if txt is None:
                continue
            try:
                txt.configure(
                    background=bg, foreground=fg,
                    insertbackground=fg,
                    selectbackground=sel_bg, selectforeground=sel_fg,
                    highlightbackground=sel_bg, highlightcolor=sel_bg,
                    highlightthickness=1, borderwidth=0, relief="flat",
                )
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # Tree / panes refresh
    # ------------------------------------------------------------------

    def _refresh_tree(self) -> None:
        prior_sel = set(self._tree.selection())
        # Wipe + rebuild — the library is small enough that diff-update
        # adds no real value here, and a full rebuild keeps column
        # ordering stable.
        for iid in self._tree.get_children(""):
            try:
                self._tree.delete(iid)
            except tk.TclError:
                pass

        armed = set()
        try:
            armed = set(self._evaluator.armed_strategies())
        except Exception:  # noqa: BLE001
            pass

        for s in self._library:
            iid = s.id
            armed_str = "yes" if iid in armed else "no"
            enabled_str = "yes" if s.enabled else "no"
            try:
                fires = self._evaluator._fires_per_strategy_today.get(iid, 0)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                fires = 0
            values = (
                s.name or "(unnamed)",
                s.direction.value.upper(),
                s.trigger.kind.value,
                enabled_str,
                armed_str,
                str(fires),
                s.id[:8],
            )
            try:
                self._tree.insert("", "end", iid=iid, values=values)
            except tk.TclError:
                pass

        # Restore selection if still present.
        keep = [iid for iid in prior_sel if iid in self._tree.get_children("")]
        if keep:
            try:
                self._tree.selection_set(keep)
            except tk.TclError:
                pass

    def _refresh_audit_tail(self) -> None:
        audit = getattr(self._evaluator, "_audit", None)
        records: list[dict[str, Any]] = []
        if audit is not None:
            try:
                records = audit.tail(50)
            except Exception:  # noqa: BLE001
                logger.exception("EntriesTab: audit.tail raised")
                records = []
        try:
            self._audit_txt.configure(state="normal")
            self._audit_txt.delete("1.0", "end")
            for rec in records:
                self._audit_txt.insert("end", _format_audit_record(rec) + "\n")
            self._audit_txt.configure(state="disabled")
        except tk.TclError:
            pass

    def _refresh_stats(self) -> None:
        try:
            stats = self._evaluator.stats()
        except Exception:  # noqa: BLE001
            stats = EvaluatorStats()
        lines = [
            f"fires:                {stats.fires}",
            f"blocked:              {stats.blocked}",
            f"cooldowns:            {stats.cooldowns}",
            f"dedup_skips:          {stats.dedup_skips}",
            f"risk_blocks:          {stats.risk_blocks}",
            f"on_fill_binds:        {stats.on_fill_binds}",
            f"on_fill_bind_fails:   {stats.on_fill_bind_failures}",
            f"indicator_evals:      {stats.indicator_evaluations}",
            f"errors:               {stats.errors}",
        ]
        try:
            self._stats_txt.configure(state="normal")
            self._stats_txt.delete("1.0", "end")
            self._stats_txt.insert("end", "\n".join(lines))
            self._stats_txt.configure(state="disabled")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Periodic tick
    # ------------------------------------------------------------------

    def _schedule_tick(self) -> None:
        try:
            self._tick_after_id = self.after(self.REFRESH_TICK_MS, self._on_tick)
        except (tk.TclError, RuntimeError):
            self._tick_after_id = None

    def _on_tick(self) -> None:
        try:
            self._refresh_audit_tail()
            self._refresh_stats()
            # Refresh the Armed/Fires columns without rebuilding the
            # whole tree (cheap + non-disruptive).
            armed = set(self._evaluator.armed_strategies())
            for iid in self._tree.get_children(""):
                vals = list(self._tree.item(iid, "values"))
                if len(vals) >= 7:
                    vals[4] = "yes" if iid in armed else "no"
                    fires = self._evaluator._fires_per_strategy_today.get(iid, 0)  # type: ignore[attr-defined]
                    vals[5] = str(fires)
                    self._tree.item(iid, values=vals)
        except (tk.TclError, RuntimeError, AttributeError):
            return
        self._schedule_tick()

    def _on_destroy(self, _event: tk.Event) -> None:
        if self._tick_after_id is not None:
            try:
                self.after_cancel(self._tick_after_id)
            except (tk.TclError, ValueError):
                pass
            self._tick_after_id = None

    # ------------------------------------------------------------------
    # Toolbar callbacks
    # ------------------------------------------------------------------

    def _exit_strategy_library(self) -> list[Any]:
        if self._exit_storage is None:
            return []
        try:
            good, _broken = self._exit_storage.load_all()
        except Exception:  # noqa: BLE001
            logger.exception("EntriesTab: exit_storage.load_all raised")
            return []
        return list(good)

    def _open_dialog(self, strategy: EntryStrategy | None) -> None:
        # We construct a fresh dialog per open — there's no singleton-
        # focus invariant for entries (unlike exits).
        try:
            self._dialog = EntriesDialog(
                self.winfo_toplevel(),
                strategy=strategy,
                exit_strategies=self._exit_strategy_library(),
                on_save=self._on_dialog_save,
                on_cancel=lambda: None,
            )
        except Exception:  # noqa: BLE001
            logger.exception("EntriesTab: open dialog raised")

    def _on_new(self) -> None:
        self._open_dialog(None)

    def _on_edit(self) -> None:
        sid = self.selected_strategy_id
        if not sid:
            return
        s = next((x for x in self._library if x.id == sid), None)
        if s is None:
            return
        self._open_dialog(s)

    def _on_delete(self) -> None:
        sid = self.selected_strategy_id
        if not sid:
            return
        s = next((x for x in self._library if x.id == sid), None)
        if s is None:
            return
        if not messagebox.askyesno(
            "Delete entry strategy",
            f"Delete strategy {s.name!r}?",
            parent=self.winfo_toplevel(),
        ):
            return
        try:
            self._storage.delete(sid)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Delete failed", str(exc), parent=self.winfo_toplevel())
            return
        # If the strategy was armed, disarm it first.
        try:
            self._evaluator.disarm(sid)
        except Exception:  # noqa: BLE001
            pass
        self.refresh()

    def _on_duplicate(self) -> None:
        sid = self.selected_strategy_id
        if not sid:
            return
        s = next((x for x in self._library if x.id == sid), None)
        if s is None:
            return
        clone = EntryStrategy.from_dict(s.to_dict())
        from ..entries.model import _new_id
        clone.id = _new_id()
        clone.name = f"{s.name} (copy)"
        try:
            self._storage.save(clone)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Duplicate failed", str(exc), parent=self.winfo_toplevel())
            return
        self.refresh()

    def _on_import(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(),
            title="Import entry strategy",
            filetypes=[("Entry strategy JSON", "*.json"),
                       ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self._storage.import_from_path(Path(path))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Import failed", str(exc), parent=self.winfo_toplevel())
            return
        self.refresh()

    def _on_export(self) -> None:
        sid = self.selected_strategy_id
        if not sid:
            return
        s = next((x for x in self._library if x.id == sid), None)
        if s is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(),
            title="Export entry strategy",
            defaultextension=".json",
            initialfile=f"{(s.name or 'entry').replace(' ', '_')}.json",
            filetypes=[("Entry strategy JSON", "*.json")],
        )
        if not path:
            return
        try:
            self._storage.export_to_path(s, Path(path))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Export failed", str(exc), parent=self.winfo_toplevel())

    def _on_arm(self) -> None:
        sid = self.selected_strategy_id
        if not sid:
            return
        try:
            self._evaluator.arm(sid)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Arm failed", str(exc), parent=self.winfo_toplevel())
            return
        self._refresh_tree()

    def _on_disarm(self) -> None:
        sid = self.selected_strategy_id
        if not sid:
            return
        try:
            self._evaluator.disarm(sid)
        except Exception:  # noqa: BLE001
            logger.exception("EntriesTab: disarm raised")
        self._refresh_tree()

    def _on_disarm_all(self) -> None:
        try:
            self._evaluator.disarm_all()
        except Exception:  # noqa: BLE001
            logger.exception("EntriesTab: disarm_all raised")
        self._refresh_tree()

    def _on_load_template(self) -> None:
        """Pick a template JSON and save a copy with a fresh id."""
        if not self._templates_dir.is_dir():
            messagebox.showerror(
                "Templates",
                f"Template dir not found: {self._templates_dir}",
                parent=self.winfo_toplevel(),
            )
            return
        template_paths = sorted(self._templates_dir.glob("*.json"))
        if not template_paths:
            messagebox.showinfo(
                "Templates",
                f"No templates in {self._templates_dir}",
                parent=self.winfo_toplevel(),
            )
            return
        # Simple text-based picker — names only.
        labels = [p.stem for p in template_paths]
        choice = simpledialog.askstring(
            "Load template",
            "Available:\n" + "\n".join(f"  - {n}" for n in labels) +
            "\n\nEnter template name to load:",
            parent=self.winfo_toplevel(),
        )
        if not choice:
            return
        match = next((p for p in template_paths if p.stem == choice), None)
        if match is None:
            messagebox.showerror(
                "Templates",
                f"Unknown template name: {choice!r}",
                parent=self.winfo_toplevel(),
            )
            return
        try:
            self.load_template_from_path(match)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Templates",
                f"Failed to load template: {exc}",
                parent=self.winfo_toplevel(),
            )
            return
        self.refresh()

    def load_template_from_path(self, path: Path) -> EntryStrategy:
        """Load a template JSON, mint a new id, save a copy. Public for tests."""
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        strat = EntryStrategy.from_dict(data)
        from ..entries.model import _new_id
        strat.id = _new_id()
        # Mark as a template-derived save (tracked for the audit chain).
        strat.created_with = CreatedWith(
            app=strat.created_with.app,
            version=strat.created_with.version,
            template=True,
        )
        self._storage.save(strat)
        return strat

    # ------------------------------------------------------------------
    # Dialog → save callback
    # ------------------------------------------------------------------

    def _on_dialog_save(self, strategy: EntryStrategy) -> None:
        try:
            self._storage.save(strategy)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(
                "Save failed", str(exc), parent=self.winfo_toplevel())
            return
        self.refresh()
