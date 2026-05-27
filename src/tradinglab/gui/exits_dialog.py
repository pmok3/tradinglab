"""Modeless "Edit Exit Strategies" dialog.

Mirrors the singleton manager-subscription pattern from
``gui/indicator_dialog.py``: a single instance lives on the app as
``app._exits_dialog``; re-opening focuses the existing window.

Surface area
------------

* Left pane: **strategy library**. Lists every saved strategy in
  ``<cache>/exit_strategies/<id>.json``. Buttons:

  * **+ New** — empty draft loaded into editor
  * **+ Bracket** — opens a small modal asking for target/stop
    units+values + qty% allocation; produces a two-leg OCO
    (``cancel_on="full_closeout"``)
  * **Import…** / **Export…** — round-trip strategies as JSON
  * **Delete** — confirms, removes file + library entry

* Right pane: **editor**

  * Header: ``name`` + ``EOD kill-switch`` checkbox + offset minutes
    spinbox (B5).
  * Legs section: scrollable list of *leg cards*. Each card shows
    label / enabled / qty% + a list of *trigger rows* (OR within the
    leg). Per-leg buttons: **+ Trigger**, **× Delete leg**.
  * Each trigger row: kind dropdown + per-kind param widgets.
    INDICATOR triggers embed
    :class:`tradinglab.gui.scanner_block_editor.BlockEditor` for
    the condition tree.
  * **OCO group editor** (below legs): each group is a row of
    leg-label chips that can be toggled on/off, plus a ``cancel_on``
    dropdown (default ``full_closeout``). New-group / delete-group
    buttons. **Disjoint validation** is checked inline — a leg
    appearing in two groups paints both chips red.
  * Footer: status label + **Validate** + **Save** + **Close**.
    ``Save`` runs ``validate_strategy`` first; on failure it shows
    the joined error list in the status label and refuses to save.

The dialog **owns no live position state**. It is a pure editor over
:class:`ExitStrategy` aggregates and the on-disk library. Live
attach/detach is handled by :mod:`gui.exits_tab`.
"""
from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..exits import storage as _exits_storage
from ..exits.model import (
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
    OCOGroup,
    TriggerKind,
    validate_strategy,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Widget classes (extracted)
# ---------------------------------------------------------------------------


from ._modal_base import make_scrollable_form  # noqa: E402
from ._modal_keys import bind_modal_keys  # noqa: E402
from .colors import ERROR_RED  # noqa: E402
from .exits_dialog_widgets import (  # noqa: E402
    _OCO_CANCEL_ON_CHOICES,
    _BracketDialog,
    _LegFrame,
    _OCOGroupRow,
)

# ---------------------------------------------------------------------------
# Singleton entry point
# ---------------------------------------------------------------------------


def open_exits_dialog(
    app: tk.Misc,
    *,
    on_library_changed: Callable[[], None] | None = None,
) -> ExitsDialog:
    """Open or re-focus the singleton Edit Exit Strategies dialog.

    Stores the instance on ``app._exits_dialog``. ``on_library_changed``
    is invoked whenever the user saves, deletes, or imports a strategy
    so the Exits tab can refresh its strategy dropdown.
    """
    existing = getattr(app, "_exits_dialog", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                try:
                    existing.deiconify()
                    existing.lift()
                    existing.focus_set()
                except tk.TclError:
                    pass
                return existing
        except tk.TclError:
            pass
        try:
            app._exits_dialog = None  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    dlg = ExitsDialog(app, on_library_changed=on_library_changed)
    try:
        app._exits_dialog = dlg  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return dlg


# ---------------------------------------------------------------------------
# Bracket-template factory
# ---------------------------------------------------------------------------


def make_bracket_strategy(
    *,
    target_unit: str,
    target_value: float,
    stop_unit: str,
    stop_value: float,
    qty_pct: float = 100.0,
    name: str = "Bracket",
) -> ExitStrategy:
    """Build a 2-leg OCO bracket: profit-target LIMIT + protective STOP.

    Both legs reference each other in a single OCOGroup with
    ``cancel_on="full_closeout"`` (B6: partial fill of the target keeps
    the stop alive against the remaining size). Units are 'percent' or
    'dollar' interpreted as offset_pct / offset_dollar against the
    position's average entry price (resolved by the evaluator at fire
    time).
    """
    target_offsets: dict[str, float | None] = {"offset_pct": None, "offset_dollar": None}
    stop_offsets: dict[str, float | None] = {"offset_pct": None, "offset_dollar": None}
    if target_unit == "percent":
        target_offsets["offset_pct"] = float(target_value)
    elif target_unit == "dollar":
        target_offsets["offset_dollar"] = float(target_value)
    else:
        raise ValueError(f"target_unit must be 'percent' or 'dollar', got {target_unit!r}")
    if stop_unit == "percent":
        stop_offsets["offset_pct"] = float(stop_value)
    elif stop_unit == "dollar":
        stop_offsets["offset_dollar"] = float(stop_value)
    else:
        raise ValueError(f"stop_unit must be 'percent' or 'dollar', got {stop_unit!r}")

    target = ExitLeg(
        label="target",
        triggers=[ExitTrigger(
            kind=TriggerKind.LIMIT,
            offset_pct=target_offsets["offset_pct"],
            offset_dollar=target_offsets["offset_dollar"],
            qty_pct=qty_pct,
        )],
    )
    stop = ExitLeg(
        label="stop",
        triggers=[ExitTrigger(
            kind=TriggerKind.STOP,
            offset_pct=stop_offsets["offset_pct"],
            offset_dollar=stop_offsets["offset_dollar"],
            qty_pct=100.0,  # stop always closes whatever remains
        )],
    )
    return ExitStrategy(
        name=name,
        legs=[target, stop],
        oco_groups=[OCOGroup(leg_ids=(target.id, stop.id), cancel_on="full_closeout")],
    )


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class ExitsDialog(tk.Toplevel):
    """The Edit Exit Strategies window."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_library_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Edit Exit Strategies")
        try:
            self.transient(master)
        except tk.TclError:
            pass
        # Geometry persistence — restore last-used size + position;
        # fall back to legacy 1400x780.
        try:
            from .geometry_store import attach_persistent_geometry
            attach_persistent_geometry(self, "dlg.exits", "1400x780")
        except tk.TclError:
            self.geometry("1400x780")
        self.minsize(900, 500)
        self._on_library_changed = on_library_changed

        # Library state
        self._library: list[ExitStrategy] = []
        self._broken: list[_exits_storage.BrokenStrategy] = []
        # Currently-edited strategy (clone of library entry, or None)
        self._draft: ExitStrategy | None = None
        # Per-leg-id frame map (cleared on rebuild)
        self._leg_frames: dict[str, _LegFrame] = {}
        # Inline-validation cache: leg_id -> bool (is duplicate in OCO)
        self._oco_dup_legs: set = set()

        self._build_layout()
        self.refresh_library()
        # Initial state: no draft loaded yet → disable add buttons + clear form.
        self._rebuild_editor()
        bind_modal_keys(self, cancel=self.destroy, primary=self._on_save)

    # ----- Public test/UX hooks -----

    @property
    def draft(self) -> ExitStrategy | None:
        """Snapshot of the currently-edited strategy (read-only view)."""
        return self._draft

    @property
    def library(self) -> tuple[ExitStrategy, ...]:
        return tuple(self._library)

    @property
    def broken(self) -> tuple[_exits_storage.BrokenStrategy, ...]:
        return tuple(self._broken)

    def refresh_library(self) -> None:
        """Reload library from disk and repopulate the listbox."""
        try:
            strategies, broken = _exits_storage.load_all()
        except Exception:  # noqa: BLE001
            logger.exception("ExitsDialog: failed to load library")
            strategies, broken = [], []
        self._library = sorted(strategies, key=lambda s: s.name.lower())
        self._broken = list(broken)
        self._populate_library_listbox()

    def load_strategy_into_editor(self, strategy: ExitStrategy | None) -> None:
        """Clone ``strategy`` into the editor, or clear the editor."""
        if strategy is None:
            self._draft = None
        else:
            # deep clone via dict round-trip so user edits don't leak
            # back into the library list snapshot.
            self._draft = ExitStrategy.from_dict(strategy.to_dict())
        self._rebuild_editor()

    def get_draft(self) -> ExitStrategy | None:
        """Return the currently-edited strategy (live, not a copy)."""
        return self._draft

    # ----- Layout -----

    def _build_layout(self) -> None:
        outer = ttk.PanedWindow(self, orient="horizontal")
        outer.pack(fill="both", expand=True, padx=6, pady=6)

        # Library pane
        lib = ttk.Frame(outer)
        outer.add(lib, weight=1)

        ttk.Label(lib, text="Strategies", font=("", 10, "bold")).pack(anchor="w")
        self._library_lb = tk.Listbox(lib, exportselection=False, height=20, width=28)
        self._library_lb.pack(fill="both", expand=True, padx=2, pady=(4, 4))
        self._library_lb.bind("<<ListboxSelect>>", self._on_library_select)

        btnrow = ttk.Frame(lib)
        btnrow.pack(fill="x")
        ttk.Button(btnrow, text="+ New",     command=self._on_new).pack(side="left", padx=(0, 2))
        ttk.Button(btnrow, text="+ Bracket", command=self._on_bracket).pack(side="left", padx=2)
        ttk.Button(btnrow, text="Delete",    command=self._on_delete).pack(side="left", padx=2)
        btnrow2 = ttk.Frame(lib)
        btnrow2.pack(fill="x", pady=(2, 0))
        ttk.Button(btnrow2, text="Import…",  command=self._on_import).pack(side="left", padx=(0, 2))
        ttk.Button(btnrow2, text="Export…",  command=self._on_export).pack(side="left", padx=2)

        # Editor pane
        editor_outer = ttk.Frame(outer)
        outer.add(editor_outer, weight=4)

        # Header
        header = ttk.Frame(editor_outer)
        header.pack(fill="x", padx=4, pady=(0, 6))
        ttk.Label(header, text="Name:").grid(row=0, column=0, sticky="w")
        self._name_var = tk.StringVar()
        self._name_entry = ttk.Entry(header, textvariable=self._name_var, width=40)
        self._name_entry.grid(row=0, column=1, sticky="ew", padx=(4, 12))
        self._name_var.trace_add("write", lambda *_: self._on_name_changed())

        self._eod_var = tk.BooleanVar(value=True)
        self._eod_chk = ttk.Checkbutton(
            header, text="EOD kill-switch", variable=self._eod_var,
            command=self._on_eod_changed,
        )
        self._eod_chk.grid(row=0, column=2, sticky="w")
        ttk.Label(header, text="offset min:").grid(row=0, column=3, sticky="w", padx=(8, 0))
        self._eod_offset_var = tk.IntVar(value=5)
        self._eod_offset_sp = ttk.Spinbox(
            header, from_=0, to=120, increment=1, width=5,
            textvariable=self._eod_offset_var,
            command=self._on_eod_changed,
        )
        self._eod_offset_sp.grid(row=0, column=4, sticky="w", padx=(2, 0))
        header.columnconfigure(1, weight=1)

        # Body — scrollable legs + OCO area. Skeleton lives in
        # :func:`_modal_base.make_scrollable_form` (audit item #5).
        # Horizontal scrollbar present so wide indicator trigger
        # condition rows (e.g. ``ema(3) crosses_above ema(8)``) can
        # scroll instead of being clipped or stretching the dialog.
        # ``bind_mousewheel=False`` preserves the historical behaviour
        # (the dialog never bound the wheel here — users scroll via
        # the scrollbars). Flip to True if user feedback ever asks
        # for wheel scrolling on the legs area.
        body = ttk.Frame(editor_outer)
        body.pack(fill="both", expand=True, padx=4, pady=2)
        self._legs_holder, canvas = make_scrollable_form(
            body, horizontal=True, bind_mousewheel=False,
        )
        self._legs_canvas = canvas

        # Legs section header + add-leg button
        self._legs_section = ttk.LabelFrame(self._legs_holder, text="Legs")
        self._legs_section.pack(fill="x", padx=2, pady=(2, 6))
        self._legs_inner = ttk.Frame(self._legs_section)
        self._legs_inner.pack(fill="x")
        addleg_row = ttk.Frame(self._legs_section)
        addleg_row.pack(fill="x", pady=(2, 4))
        self._add_leg_btn = ttk.Button(
            addleg_row, text="+ Add leg", command=self._on_add_leg,
        )
        self._add_leg_btn.pack(side="left")

        # OCO section
        self._oco_section = ttk.LabelFrame(self._legs_holder, text="OCO Groups")
        self._oco_section.pack(fill="x", padx=2, pady=(2, 6))
        self._oco_inner = ttk.Frame(self._oco_section)
        self._oco_inner.pack(fill="x")
        addoco_row = ttk.Frame(self._oco_section)
        addoco_row.pack(fill="x", pady=(2, 4))
        self._add_oco_btn = ttk.Button(
            addoco_row, text="+ Add OCO group", command=self._on_add_oco,
        )
        self._add_oco_btn.pack(side="left")

        # Footer
        footer = ttk.Frame(editor_outer)
        footer.pack(fill="x", padx=4, pady=(2, 4))
        self._status_var = tk.StringVar(value="")
        self._status_lbl = ttk.Label(footer, textvariable=self._status_var, foreground=ERROR_RED)
        self._status_lbl.pack(side="left", fill="x", expand=True)
        # Footer buttons: Windows dialog convention (audit
        # ``button-order-windows``) — visual order left→right
        # ``[Validate] [Save] [Close]`` with the dismiss action
        # (Close) rightmost. ``side="right"`` reverses pack order,
        # so pack Close first (lands rightmost), then Save, then
        # Validate.
        ttk.Button(footer, text="Close",    command=self.destroy).pack(side="right", padx=(2, 0))
        ttk.Button(footer, text="Save",     command=self._on_save).pack(side="right", padx=(2, 0))
        ttk.Button(footer, text="Validate", command=self._on_validate).pack(side="right", padx=(2, 0))

    # ----- Library -----

    def _populate_library_listbox(self) -> None:
        self._library_lb.delete(0, "end")
        for s in self._library:
            label = s.name or f"(unnamed {s.id[:6]})"
            self._library_lb.insert("end", label)
        if self._broken:
            self._library_lb.insert("end", f"⚠ {len(self._broken)} broken")

    def _on_library_select(self, _event: tk.Event) -> None:
        sel = self._library_lb.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self._library):
            self.load_strategy_into_editor(self._library[idx])

    def _on_new(self) -> None:
        self.load_strategy_into_editor(ExitStrategy(name="(new)"))
        self._library_lb.selection_clear(0, "end")

    def _on_bracket(self) -> None:
        dlg = _BracketDialog(self)
        self.wait_window(dlg)
        result = dlg.result
        if result is None:
            return
        try:
            strat = make_bracket_strategy(**result)
        except ValueError as exc:
            messagebox.showerror("Bracket", str(exc), parent=self)
            return
        self.load_strategy_into_editor(strat)
        self._library_lb.selection_clear(0, "end")

    def _on_delete(self) -> None:
        if self._draft is None or not self._draft.id:
            self._status_var.set("Nothing to delete")
            return
        if not messagebox.askyesno(
            "Delete", f"Delete strategy {self._draft.name!r}?", parent=self,
        ):
            return
        try:
            _exits_storage.delete(self._draft.id)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Delete failed", str(exc), parent=self)
            return
        self.load_strategy_into_editor(None)
        self.refresh_library()
        if self._on_library_changed is not None:
            try:
                self._on_library_changed()
            except Exception:  # noqa: BLE001
                logger.exception("on_library_changed callback raised")

    def _on_import(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Import exit strategy",
            filetypes=[("Exit strategy JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            outcome = _exits_storage.import_strategy(
                Path(path),
                on_collision=lambda _local, _inc: _exits_storage.CollisionDecision.RENAME,
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Import failed", str(exc), parent=self)
            return
        self.refresh_library()
        # Try to load the just-imported strategy
        if isinstance(outcome, ExitStrategy):
            self.load_strategy_into_editor(outcome)
        if self._on_library_changed is not None:
            try:
                self._on_library_changed()
            except Exception:  # noqa: BLE001
                logger.exception("on_library_changed callback raised")

    def _on_export(self) -> None:
        if self._draft is None:
            self._status_var.set("Nothing to export")
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export exit strategy",
            defaultextension=".json",
            initialfile=f"{(self._draft.name or 'strategy').replace(' ', '_')}.json",
            filetypes=[("Exit strategy JSON", "*.json")],
        )
        if not path:
            return
        try:
            _exits_storage.export_strategy(self._draft, Path(path))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        self._status_var.set(f"Exported to {path}")

    # ----- Header edits -----

    def _on_name_changed(self) -> None:
        if self._draft is None:
            return
        self._draft.name = self._name_var.get()

    def _on_eod_changed(self) -> None:
        if self._draft is None:
            return
        self._draft.eod_kill_switch = bool(self._eod_var.get())
        try:
            self._draft.eod_offset_min = int(self._eod_offset_var.get())
        except (tk.TclError, ValueError):
            pass

    # ----- Editor rebuild -----

    def _rebuild_editor(self) -> None:
        # Clear leg + OCO frames
        for child in list(self._legs_inner.winfo_children()):
            child.destroy()
        for child in list(self._oco_inner.winfo_children()):
            child.destroy()
        self._leg_frames.clear()
        self._oco_dup_legs = set()
        self._status_var.set("")
        if self._draft is None:
            self._name_var.set("")
            self._eod_var.set(True)
            self._eod_offset_var.set(5)
            self._add_leg_btn.state(["disabled"])
            self._add_oco_btn.state(["disabled"])
            return
        self._add_leg_btn.state(["!disabled"])
        self._add_oco_btn.state(["!disabled"])
        self._name_var.set(self._draft.name)
        self._eod_var.set(self._draft.eod_kill_switch)
        self._eod_offset_var.set(self._draft.eod_offset_min)
        for leg in self._draft.legs:
            frame = _LegFrame(self._legs_inner, leg=leg, dialog=self)
            frame.pack(fill="x", pady=2)
            self._leg_frames[leg.id] = frame
        for oco in self._draft.oco_groups:
            row = _OCOGroupRow(self._oco_inner, oco=oco, dialog=self)
            row.pack(fill="x", pady=1)
        self._refresh_oco_disjoint_validation()

    def _on_add_leg(self) -> None:
        if self._draft is None:
            return
        leg = ExitLeg(label=f"leg {len(self._draft.legs) + 1}")
        self._draft.legs.append(leg)
        self._rebuild_editor()

    def remove_leg(self, leg_id: str) -> None:
        if self._draft is None:
            return
        self._draft.legs = [l for l in self._draft.legs if l.id != leg_id]
        # Also drop the leg from any OCO groups (and drop now-empty groups).
        new_groups: list[OCOGroup] = []
        for g in self._draft.oco_groups:
            remaining = tuple(x for x in g.leg_ids if x != leg_id)
            if len(remaining) >= 2:
                new_groups.append(OCOGroup(leg_ids=remaining, cancel_on=g.cancel_on))
        self._draft.oco_groups = new_groups
        self._rebuild_editor()

    def _on_add_oco(self) -> None:
        if self._draft is None:
            return
        if len(self._draft.legs) < 2:
            self._status_var.set("Need ≥ 2 legs to make an OCO group")
            return
        # Pre-populate with first two legs not yet in any group.
        used = {lid for g in self._draft.oco_groups for lid in g.leg_ids}
        free = [l for l in self._draft.legs if l.id not in used]
        if len(free) < 2:
            self._status_var.set(
                "All legs are already in OCO groups; legs must be disjoint."
            )
            return
        ids = (free[0].id, free[1].id)
        self._draft.oco_groups.append(OCOGroup(leg_ids=ids, cancel_on="full_closeout"))
        self._rebuild_editor()

    def remove_oco_group(self, group_index: int) -> None:
        if self._draft is None:
            return
        if 0 <= group_index < len(self._draft.oco_groups):
            del self._draft.oco_groups[group_index]
            self._rebuild_editor()

    def toggle_leg_in_group(self, group_index: int, leg_id: str) -> None:
        if self._draft is None:
            return
        if not (0 <= group_index < len(self._draft.oco_groups)):
            return
        g = self._draft.oco_groups[group_index]
        ids = list(g.leg_ids)
        if leg_id in ids:
            ids = [x for x in ids if x != leg_id]
        else:
            ids.append(leg_id)
        self._draft.oco_groups[group_index] = OCOGroup(
            leg_ids=tuple(ids), cancel_on=g.cancel_on,
        )
        self._refresh_oco_disjoint_validation()
        self._rebuild_editor()

    def set_oco_cancel_on(self, group_index: int, cancel_on: str) -> None:
        if self._draft is None:
            return
        if not (0 <= group_index < len(self._draft.oco_groups)):
            return
        g = self._draft.oco_groups[group_index]
        if cancel_on not in _OCO_CANCEL_ON_CHOICES:
            return
        self._draft.oco_groups[group_index] = OCOGroup(
            leg_ids=g.leg_ids, cancel_on=cancel_on,
        )

    def _refresh_oco_disjoint_validation(self) -> None:
        """Compute which legs appear in >1 OCO group; cache on self."""
        if self._draft is None:
            self._oco_dup_legs = set()
            return
        seen: dict[str, int] = {}
        for g in self._draft.oco_groups:
            for lid in g.leg_ids:
                seen[lid] = seen.get(lid, 0) + 1
        self._oco_dup_legs = {lid for lid, n in seen.items() if n > 1}

    @property
    def oco_duplicate_leg_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._oco_dup_legs))

    # ----- Validate / Save -----

    def _on_validate(self) -> list[str]:
        if self._draft is None:
            self._status_var.set("No strategy loaded")
            return ["no strategy"]
        errors = list(validate_strategy(self._draft))
        if errors:
            self._status_var.set("Errors: " + "; ".join(errors[:3]))
        else:
            self._status_var.set("Valid ✓")
        return errors

    def _on_save(self) -> None:
        if self._draft is None:
            self._status_var.set("No strategy loaded")
            return
        errors = list(validate_strategy(self._draft))
        if errors:
            self._status_var.set("Save refused — " + "; ".join(errors[:3]))
            return
        try:
            _exits_storage.save(self._draft)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Save failed", str(exc), parent=self)
            return
        self.refresh_library()
        if self._on_library_changed is not None:
            try:
                self._on_library_changed()
            except Exception:  # noqa: BLE001
                logger.exception("on_library_changed callback raised")
        self._status_var.set(f"Saved {self._draft.name!r}")


