"""Scanner notebook tab — right-side host for saved sandbox scans.

This widget is self-contained: it owns the scan library (in-memory dict
keyed by scan id), renders a top-level toolbar plus a nested
``ttk.Notebook`` with one sub-tab per saved scan, and exposes a small
public API the app uses to wire it up:

- :meth:`ScannerTab.set_library` — replace all scans (autoload at startup).
- :meth:`ScannerTab.set_results` — push a ``{scan_id: ScanResult}`` dict
  from a sandbox tick; each sub-tab's Treeview diff-updates.
- :meth:`ScannerTab.get_active_scan_definitions` — list of scans the
  runner should evaluate (currently: every saved scan).

Callbacks (all optional, no-op by default):

- ``on_scan_saved(scan_definition)`` — persist via storage layer.
- ``on_scan_deleted(scan_id)`` — delete from storage.
- ``on_row_action(symbol, kind)`` — user picked a row + action; ``kind``
  is one of ``"primary"``, ``"compare"``, ``"watchlist"``.

Per-scan sub-tab layout (top-to-bottom):

1. Header row: rank_by combobox, rank_dir radio (▲ asc / ▼ desc),
   primary-interval combo, "Show insufficient-data rows" checkbox.
2. ``BlockEditor`` — recursive AND/OR group editor.
3. View radio (New / Active) + Treeview (Symbol, Match, Rank, Tick, Time)
   + footer status (visible_count of total).

Result-tree updates use *iid = symbol* so selection persists across
ticks even when row content changes. Visible row cap = 500.

The widget is Tk only (no app coupling): unit-testable in isolation.
"""
from __future__ import annotations

import json
import logging
import tkinter as tk
from collections.abc import Callable, Iterable, Mapping
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from ..scanner.model import (
    RANK_DIR_ASC,
    RANK_DIR_DESC,
    FieldRef,
    Group,
    ScanDefinition,
)
from ..scanner.runner import MatchRow, ScanResult
from .scanner_block_editor import BlockEditor

LOG = logging.getLogger(__name__)


_TREE_COLS: tuple[tuple[str, str, int, str], ...] = (
    ("symbol", "Symbol", 80, "w"),
    ("match", "Match", 70, "center"),
    ("rank", "Rank", 90, "center"),
    ("tick", "Tick", 60, "center"),
    ("time", "Time", 90, "center"),
)

_INTERVAL_CHOICES = ("1m", "2m", "3m", "5m", "10m", "15m", "30m", "1h", "1d")

# A curated list of "fast access" rank options shown at the top of the
# picker. The picker stores them as ``FieldRef``. The full list of
# every scannable indicator / builtin is appended dynamically by
# :func:`_build_rank_presets` so newly registered indicators show up
# automatically without a code change here.
_CURATED_RANK_PRESETS: tuple[tuple[str, FieldRef | None], ...] = (
    ("(none)", None),
    ("RVOL (cumulative)", FieldRef.indicator("rvol", params={"mode": "cumulative"})),
    ("RVOL (rolling)", FieldRef.indicator("rvol", params={"mode": "simple"})),
    ("Volume", FieldRef.builtin("volume")),
    ("Close", FieldRef.builtin("close")),
    ("ATR(14)", FieldRef.indicator("atr", params={"length": 14})),
    ("RSI(14)", FieldRef.indicator("rsi", params={"length": 14})),
)


def _preset_key(ref: FieldRef | None) -> tuple[Any, ...]:
    """Stable key used to dedupe curated vs registry-derived presets."""
    if ref is None:
        return ("__none__",)
    params_items = tuple(sorted(dict(getattr(ref, "params", {}) or {}).items()))
    return (ref.kind, ref.id, ref.output_key, params_items)


def _build_rank_presets() -> tuple[tuple[str, FieldRef | None], ...]:
    """Build the full rank-by preset list.

    Starts with :data:`_CURATED_RANK_PRESETS` (common ranks the user
    picks 95% of the time) then appends EVERY scannable builtin /
    indicator from :func:`tradinglab.scanner.fields.all_fields`. Each
    indicator's defaults come from its ``ParamDef.default`` tuple
    (matching what the BlockEditor's field picker does), and multi-
    output indicators (Bollinger, MACD, ADX, SMI) get one preset per
    output_key. Items already represented in the curated list are
    skipped so the picker has no duplicates.

    Computed lazily on each scan-tab construction; newly registered
    indicators show up the next time a sub-tab is created without
    needing an app restart.
    """
    presets: list[tuple[str, FieldRef | None]] = list(_CURATED_RANK_PRESETS)
    seen = {_preset_key(ref) for _, ref in presets}
    try:
        from ..scanner.fields import all_fields
    except Exception:  # noqa: BLE001
        return tuple(presets)
    try:
        specs = all_fields()
    except Exception:  # noqa: BLE001
        return tuple(presets)
    for spec in specs:
        kind = getattr(spec, "kind", "")
        sid = getattr(spec, "id", "")
        label = getattr(spec, "label", sid) or sid
        if kind == "builtin":
            ref = FieldRef.builtin(sid)
            key = _preset_key(ref)
            if key in seen:
                continue
            seen.add(key)
            presets.append((label, ref))
        elif kind == "indicator":
            try:
                defaults = {p.name: p.default for p in (spec.params_schema or ())}
            except Exception:  # noqa: BLE001
                defaults = {}
            outputs = tuple(spec.output_keys or ("",))
            if len(outputs) <= 1:
                ref = FieldRef.indicator(sid, params=defaults)
                key = _preset_key(ref)
                if key in seen:
                    continue
                seen.add(key)
                presets.append((label, ref))
            else:
                for okey in outputs:
                    ref = FieldRef.indicator(
                        sid, params=defaults, output_key=okey)
                    key = _preset_key(ref)
                    if key in seen:
                        continue
                    seen.add(key)
                    suffix = f" → {okey}" if okey else ""
                    presets.append((f"{label}{suffix}", ref))
    return tuple(presets)


# Computed once at import; the registry is populated at app startup and
# does not grow during a session, so a single snapshot is fine. Tests
# that monkey-patch the registry can call :func:`_build_rank_presets`
# directly.
_RANK_PRESETS: tuple[tuple[str, FieldRef | None], ...] = _build_rank_presets()

_VIEW_NEW = "new"
_VIEW_ACTIVE = "active"
_MAX_VISIBLE_ROWS = 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_rank(v: float | None) -> str:
    if v is None:
        return ""
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def _format_match(matched: bool | None, is_new: bool) -> str:
    if matched is True:
        return "● new" if is_new else "●"
    if matched is False:
        return "—"
    return "?"


def _format_time(ts: Any) -> str:
    try:
        return ts.strftime("%H:%M:%S")
    except Exception:  # noqa: BLE001
        return ""


def _rank_preset_label(ref: FieldRef | None) -> str:
    """Reverse-lookup a label for ``ref`` in the preset list. Falls back
    to ``"custom"`` when the FieldRef doesn't match any preset (e.g. it
    was authored via JSON import).

    Multi-output indicators (Bollinger / MACD / ADX / SMI) deliberately
    compare ``output_key`` so an upper-band rank doesn't shadow-match
    the default (middle/+di/etc.) preset.
    """
    if ref is None:
        return "(none)"
    for label, preset in _RANK_PRESETS:
        if preset is None:
            continue
        if preset.kind != ref.kind or preset.id != ref.id:
            continue
        if (preset.output_key or "") != (ref.output_key or ""):
            continue
        if dict(preset.params) == dict(ref.params):
            return label
    return "custom"


# ---------------------------------------------------------------------------
# Per-scan sub-tab
# ---------------------------------------------------------------------------


class _ScanSubTab(ttk.Frame):
    """Editor + result table for one ScanDefinition."""

    def __init__(
        self,
        parent: tk.Misc,
        scan: ScanDefinition,
        *,
        on_change: Callable[[_ScanSubTab], None],
        on_row_action: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.scan = scan
        self._on_change_cb = on_change
        self._on_row_action = on_row_action
        self._latest_result: ScanResult | None = None
        self._sort_col: str | None = "rank"
        self._sort_reverse: bool = True

        # ---- header row -------------------------------------------------
        hdr = ttk.Frame(self)
        hdr.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(4, 0))

        ttk.Label(hdr, text="Rank by:").pack(side=tk.LEFT)
        self._rank_var = tk.StringVar(value=_rank_preset_label(scan.rank_by))
        self._rank_combo = ttk.Combobox(
            hdr,
            textvariable=self._rank_var,
            values=[label for label, _ in _RANK_PRESETS],
            state="readonly",
            width=32,
        )
        self._rank_combo.pack(side=tk.LEFT, padx=(4, 8))
        self._rank_combo.bind("<<ComboboxSelected>>", self._on_rank_change)
        # Preserve any custom (imported) rank label gracefully.
        if self._rank_var.get() == "custom":
            self._rank_combo.set("custom")

        self._rank_dir_var = tk.StringVar(value=scan.rank_dir)
        ttk.Radiobutton(
            hdr, text="▼", variable=self._rank_dir_var,
            value=RANK_DIR_DESC, command=self._on_rank_dir_change,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            hdr, text="▲", variable=self._rank_dir_var,
            value=RANK_DIR_ASC, command=self._on_rank_dir_change,
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(hdr, text="Interval:").pack(side=tk.LEFT)
        self._interval_var = tk.StringVar(value=scan.primary_interval)
        self._interval_combo = ttk.Combobox(
            hdr, textvariable=self._interval_var,
            values=_INTERVAL_CHOICES, state="readonly", width=6,
        )
        self._interval_combo.pack(side=tk.LEFT, padx=(4, 8))
        self._interval_combo.bind(
            "<<ComboboxSelected>>", self._on_interval_change)

        self._show_insuf_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            hdr, text="Show insufficient",
            variable=self._show_insuf_var,
            command=self._refresh_tree,
        ).pack(side=tk.LEFT, padx=(0, 8))

        # ---- conditions: summary row + popup editor --------------------
        # The full BlockEditor lives in a withdrawn Toplevel so users get
        # a roomy authoring window without crowding the scanner tab.
        # The Toplevel is created up-front (so `self._editor` is always
        # available for tests / interval propagation) and shown / hidden
        # on demand via ``deiconify`` / ``withdraw``.
        cond_row = ttk.LabelFrame(self, text="Conditions")
        cond_row.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)
        self._cond_summary_var = tk.StringVar(value="")
        ttk.Label(
            cond_row, textvariable=self._cond_summary_var,
            foreground="#888",
        ).pack(side=tk.LEFT, padx=(6, 6), pady=4)
        self._edit_cond_btn = ttk.Button(
            cond_row, text="Edit conditions…",
            command=self._open_conditions_window,
        )
        self._edit_cond_btn.pack(side=tk.RIGHT, padx=6, pady=4)

        self._cond_window: tk.Toplevel | None = tk.Toplevel(self)
        self._cond_window.withdraw()
        try:
            self._cond_window.title(f"Conditions — {scan.name}")
        except Exception:  # noqa: BLE001
            pass
        # Geometry persistence: restore last-used size + position;
        # fall back to the legacy 900x600 for first-time users.
        try:
            from .geometry_store import attach_persistent_geometry
            attach_persistent_geometry(
                self._cond_window, "dlg.scanner_conditions", "900x600",
            )
            self._cond_window.minsize(640, 360)
        except Exception:  # noqa: BLE001
            try:
                self._cond_window.geometry("900x600")
                self._cond_window.minsize(640, 360)
            except Exception:  # noqa: BLE001
                pass
        # Closing the window hides instead of destroys, so reopening
        # preserves widget state and `self._editor` remains valid.
        self._cond_window.protocol(
            "WM_DELETE_WINDOW", self._hide_conditions_window)
        self._editor = BlockEditor(
            self._cond_window,
            root=scan.root,
            default_interval=scan.primary_interval,
            on_change=self._on_editor_change,
        )
        self._editor.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self._refresh_cond_summary()

        # ---- view toggle ------------------------------------------------
        view_row = ttk.Frame(self)
        view_row.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(4, 0))
        ttk.Label(view_row, text="View:").pack(side=tk.LEFT)
        self._view_var = tk.StringVar(value=_VIEW_NEW)
        ttk.Radiobutton(
            view_row, text="New", variable=self._view_var,
            value=_VIEW_NEW, command=self._refresh_tree,
        ).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Radiobutton(
            view_row, text="Active", variable=self._view_var,
            value=_VIEW_ACTIVE, command=self._refresh_tree,
        ).pack(side=tk.LEFT, padx=(4, 8))
        self._status_var = tk.StringVar(value="0 results")
        ttk.Label(view_row, textvariable=self._status_var,
                  foreground="#888").pack(side=tk.RIGHT)

        # ---- result tree ------------------------------------------------
        tree_frame = ttk.Frame(self)
        tree_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True,
                        padx=4, pady=(2, 4))
        self._tree = ttk.Treeview(
            tree_frame,
            columns=tuple(c for c, _, _, _ in _TREE_COLS),
            show="headings",
            selectmode="browse",
        )
        for col, label, w, anchor in _TREE_COLS:
            self._tree.heading(
                col, text=label,
                command=lambda c=col: self._on_sort_click(c),
            )
            self._tree.column(col, width=w, anchor=anchor)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                            command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.tag_configure("new", foreground="#3fb950")
        self._tree.tag_configure("err", foreground="#f0883e")
        self._tree.tag_configure("insuf", foreground="#666")

        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.bind("<Button-3>", self._on_right_click)
        self._refresh_sort_arrows()

    # -- public API ----------------------------------------------------------

    def update_result(self, result: ScanResult | None) -> None:
        self._latest_result = result
        self._refresh_tree()

    # -- event wiring --------------------------------------------------------

    def _on_editor_change(self) -> None:
        # BlockEditor mutates scan.root in place; just notify parent.
        self._refresh_cond_summary()
        self._on_change_cb(self)

    # -- conditions popup ----------------------------------------------------

    def _refresh_cond_summary(self) -> None:
        """Update the inline summary label with leaf-condition count."""
        try:
            n = len(self.scan.all_conditions())
        except Exception:  # noqa: BLE001
            n = 0
        word = "condition" if n == 1 else "conditions"
        self._cond_summary_var.set(f"{n} {word}")

    def _open_conditions_window(self) -> None:
        """Show the conditions Toplevel; bring to front if already open."""
        win = self._cond_window
        if win is None:
            return
        try:
            win.deiconify()
            win.lift()
            win.focus_set()
        except tk.TclError:
            # Window was destroyed (e.g. parent torn down). Recreate.
            self._cond_window = None

    def _hide_conditions_window(self) -> None:
        """Hide the conditions Toplevel without destroying it."""
        win = self._cond_window
        if win is None:
            return
        try:
            win.withdraw()
        except tk.TclError:
            pass

    def destroy(self) -> None:  # type: ignore[override]
        # Tear down the popup Toplevel so it doesn't outlive the sub-tab.
        win = self._cond_window
        self._cond_window = None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass
        super().destroy()

    def _on_rank_change(self, _event=None) -> None:
        label = self._rank_var.get()
        for preset_label, preset in _RANK_PRESETS:
            if preset_label == label:
                self.scan.rank_by = preset
                break
        else:
            return  # "custom" — leave as-is
        self._on_change_cb(self)
        self._refresh_tree()

    def _on_rank_dir_change(self) -> None:
        self.scan.rank_dir = self._rank_dir_var.get()
        self._on_change_cb(self)
        self._refresh_tree()

    def _on_interval_change(self, _event=None) -> None:
        new_iv = self._interval_var.get()
        self.scan.primary_interval = new_iv
        self._editor.set_default_interval(new_iv)
        self._on_change_cb(self)

    # -- sort ----------------------------------------------------------------

    def _on_sort_click(self, col: str) -> None:
        if self._sort_col == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_col = col
            self._sort_reverse = (col in ("rank", "tick"))
        self._refresh_sort_arrows()
        self._refresh_tree()

    def _refresh_sort_arrows(self) -> None:
        for col, label, _, _ in _TREE_COLS:
            arrow = ""
            if col == self._sort_col:
                arrow = "  ▼" if self._sort_reverse else "  ▲"
            try:
                self._tree.heading(col, text=label + arrow)
            except tk.TclError:
                pass

    # -- tree refresh --------------------------------------------------------

    def _select_rows(self, result: ScanResult | None) -> list[MatchRow]:
        if result is None:
            return []
        view = self._view_var.get()
        if view == _VIEW_NEW:
            return list(result.new_rows)
        # Active: matched-True rows. Optionally include insufficient.
        rows = [r for r in result.rows if r.matched is True]
        if self._show_insuf_var.get():
            rows = rows + [r for r in result.rows if r.matched is None]
        return rows

    def _sort_rows(self, rows: list[MatchRow]) -> list[MatchRow]:
        col = self._sort_col
        rev = self._sort_reverse
        if col is None:
            return rows
        # Partition missing values to the bottom regardless of direction.
        def key(r: MatchRow):
            if col == "symbol":
                v = r.symbol
            elif col == "rank":
                v = r.rank_value
            elif col == "tick":
                v = None  # no per-row tick column populated yet
            elif col == "match":
                v = 0 if r.matched is True else (1 if r.matched is None else 2)
            elif col == "time":
                v = None
            else:
                v = None
            return (v is None, v if v is not None else "")
        rows = list(rows)
        rows.sort(key=key, reverse=rev)
        return rows

    def _refresh_tree(self) -> None:
        result = self._latest_result
        rows = self._select_rows(result)
        rows = self._sort_rows(rows)
        if len(rows) > _MAX_VISIBLE_ROWS:
            rows = rows[:_MAX_VISIBLE_ROWS]

        # Preserve current selection by symbol.
        try:
            sel = self._tree.selection()
            sel_symbols = {self._tree.set(iid, "symbol") for iid in sel}
        except tk.TclError:
            sel_symbols = set()

        # Diff update: iid = symbol.
        existing = set(self._tree.get_children(""))
        target_iids: list[str] = []
        for r in rows:
            iid = r.symbol
            target_iids.append(iid)
            tag = ()
            if r.error:
                tag = ("err",)
            elif r.matched is None:
                tag = ("insuf",)
            elif r.is_new:
                tag = ("new",)
            values = (
                r.symbol,
                _format_match(r.matched, r.is_new),
                _format_rank(r.rank_value),
                "",  # tick — populated by ScanResult.tick_id at parent level
                _format_time(result.timestamp) if result is not None else "",
            )
            if iid in existing:
                self._tree.item(iid, values=values, tags=tag)
            else:
                try:
                    self._tree.insert("", "end", iid=iid, values=values,
                                      tags=tag)
                except tk.TclError:
                    pass

        # Remove rows that fell out of the target set.
        target_set = set(target_iids)
        for iid in existing - target_set:
            try:
                self._tree.delete(iid)
            except tk.TclError:
                pass

        # Re-order to match target sequence.
        for idx, iid in enumerate(target_iids):
            try:
                self._tree.move(iid, "", idx)
            except tk.TclError:
                pass

        # Restore selection.
        restore = [iid for iid in target_iids if iid in sel_symbols]
        if restore:
            try:
                self._tree.selection_set(restore)
            except tk.TclError:
                pass

        total = len(result.rows) if result is not None else 0
        matched = sum(1 for r in (result.rows if result is not None else [])
                      if r.matched is True)
        if self._view_var.get() == _VIEW_NEW:
            self._status_var.set(
                f"{len(rows)} new ({matched} active of {total})")
        else:
            self._status_var.set(
                f"{len(rows)} of {total} ({matched} matched)")

    # -- row interactions ----------------------------------------------------

    def _selected_symbol(self) -> str | None:
        try:
            sel = self._tree.selection()
            if not sel:
                return None
            return self._tree.set(sel[0], "symbol") or None
        except tk.TclError:
            return None

    def _row_at(self, event) -> str | None:
        try:
            iid = self._tree.identify_row(event.y)
            if not iid:
                return None
            return self._tree.set(iid, "symbol") or None
        except tk.TclError:
            return None

    def _on_double_click(self, event) -> None:
        symbol = self._row_at(event) or self._selected_symbol()
        if symbol and self._on_row_action is not None:
            try:
                self._on_row_action(symbol, "primary")
            except Exception:  # noqa: BLE001
                LOG.exception("on_row_action(primary) failed")

    def _on_right_click(self, event) -> None:
        symbol = self._row_at(event)
        if not symbol:
            return
        try:
            self._tree.selection_set(symbol)
        except tk.TclError:
            pass
        if self._on_row_action is None:
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label=f"Set {symbol} as primary",
            command=lambda: self._fire_action(symbol, "primary"))
        menu.add_command(
            label=f"Add {symbol} to compare",
            command=lambda: self._fire_action(symbol, "compare"))
        menu.add_command(
            label=f"Add {symbol} to watchlist",
            command=lambda: self._fire_action(symbol, "watchlist"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass

    def _fire_action(self, symbol: str, kind: str) -> None:
        if self._on_row_action is None:
            return
        try:
            self._on_row_action(symbol, kind)
        except Exception:  # noqa: BLE001
            LOG.exception("on_row_action(%r) failed", kind)


# ---------------------------------------------------------------------------
# Top-level scanner tab
# ---------------------------------------------------------------------------


class ScannerTab(ttk.Frame):
    """Right-side Scanner tab: toolbar + nested per-scan notebook.

    The library (saved scans on disk) is decoupled from *open* tabs.
    By default the tab opens at most one scan at startup — the most
    recently updated one — so a user with dozens of saved scans does
    not see dozens of tabs. The remaining library is reachable via the
    *Load…* toolbar button. Closing a tab unloads it without deleting
    the underlying scan; *Delete* still removes from library + disk.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        library: Mapping[str, ScanDefinition] | None = None,
        on_scan_saved: Callable[[ScanDefinition], None] | None = None,
        on_scan_deleted: Callable[[str], None] | None = None,
        on_row_action: Callable[[str, str], None] | None = None,
        new_scan_factory: Callable[[str], ScanDefinition] | None = None,
        initial_open_ids: Iterable[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._library: dict[str, ScanDefinition] = dict(library or {})
        self._on_scan_saved = on_scan_saved
        self._on_scan_deleted = on_scan_deleted
        self._on_row_action = on_row_action
        self._new_scan_factory = new_scan_factory or _default_new_scan
        self._sub_tabs: dict[str, _ScanSubTab] = {}
        self._save_jobs: dict[str, str] = {}  # scan_id → after-job id
        # Ordered list of currently-open scan ids (mirrors notebook tab order).
        self._open_ids: list[str] = list(
            self._resolve_initial_open_ids(initial_open_ids))

        self._build_toolbar()
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 2))
        # Right-click context menu on the sub-notebook tab strip.
        self._notebook.bind("<Button-3>", self._on_subtab_right_click)
        self._build_empty_state()
        self._rebuild_subtabs()

    def _resolve_initial_open_ids(
        self, requested: Iterable[str] | None,
    ) -> list[str]:
        """Decide which scans to auto-open at construction.

        Explicit ``initial_open_ids`` wins (filtered to known ids).
        Otherwise auto-open the single most-recently-updated scan, or
        none if the library is empty.
        """
        if requested is not None:
            return [sid for sid in requested if sid in self._library]
        if not self._library:
            return []
        # Most recently updated first; ISO-8601 sorts lexicographically.
        ranked = sorted(
            self._library.values(),
            key=lambda s: (s.updated_at or s.created_at or "", s.name.lower()),
            reverse=True,
        )
        return [ranked[0].id]

    # -- public API ----------------------------------------------------------

    def set_library(self, scans: Mapping[str, ScanDefinition]) -> None:
        """Replace the library. Open tabs are preserved when their scan
        survives in the new library; otherwise closed. If no open ids
        remain, auto-opens the most-recently-updated one (or none)."""
        self._library = dict(scans)
        self._open_ids = [sid for sid in self._open_ids if sid in self._library]
        if not self._open_ids and self._library:
            self._open_ids = self._resolve_initial_open_ids(None)
        self._rebuild_subtabs()

    def get_library(self) -> dict[str, ScanDefinition]:
        return dict(self._library)

    def get_active_scan_definitions(self) -> list[ScanDefinition]:
        """Return scans that are *open* (have a sub-tab). The runner
        only evaluates these — closed library scans cost nothing."""
        return [self._library[sid] for sid in self._open_ids
                if sid in self._library]

    def open_scan(self, scan_id: str) -> bool:
        """Open ``scan_id`` as a sub-tab. Returns False if not in library
        or already open. Selects the tab on success."""
        if scan_id not in self._library or scan_id in self._open_ids:
            sub = self._sub_tabs.get(scan_id)
            if sub is not None:
                try:
                    self._notebook.select(sub)
                except tk.TclError:
                    pass
            return False
        self._open_ids.append(scan_id)
        self._rebuild_subtabs()
        sub = self._sub_tabs.get(scan_id)
        if sub is not None:
            try:
                self._notebook.select(sub)
            except tk.TclError:
                pass
        return True

    def close_scan(self, scan_id: str) -> bool:
        """Unload ``scan_id``'s sub-tab. The scan stays in the library
        and on disk — re-open via *Load…*. Returns False if not open."""
        if scan_id not in self._open_ids:
            return False
        self._open_ids.remove(scan_id)
        # Cancel any pending debounced save for the closed scan.
        job = self._save_jobs.pop(scan_id, None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        self._rebuild_subtabs()
        return True

    def set_results(self, results: Mapping[str, ScanResult]) -> None:
        for scan_id, sub in self._sub_tabs.items():
            sub.update_result(results.get(scan_id))

    def current_scan_id(self) -> str | None:
        try:
            sel = self._notebook.select()
            if not sel:
                return None
            for sid, sub in self._sub_tabs.items():
                if str(sub) == sel:
                    return sid
        except tk.TclError:
            return None
        return None

    # -- toolbar / empty state ----------------------------------------------

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)
        ttk.Button(bar, text="+ New", command=self._on_new_scan,
                   width=8).pack(side=tk.LEFT)
        ttk.Button(bar, text="Load…", command=self._on_load_scan,
                   width=8).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Button(bar, text="Rename", command=self._on_rename_scan,
                   width=8).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Button(bar, text="Close", command=self._on_close_current,
                   width=8).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Button(bar, text="Delete", command=self._on_delete_scan,
                   width=8).pack(side=tk.LEFT, padx=(2, 8))
        ttk.Button(bar, text="Import…", command=self._on_import,
                   width=8).pack(side=tk.LEFT)
        ttk.Button(bar, text="Export…", command=self._on_export,
                   width=8).pack(side=tk.LEFT, padx=(2, 0))

    def _build_empty_state(self) -> None:
        self._empty_frame = ttk.Frame(self._notebook)
        ttk.Label(
            self._empty_frame,
            text=("No scans yet.\n\nClick + New to create one."),
            justify="center",
        ).pack(expand=True, pady=40)

    # -- library mutation ----------------------------------------------------

    def _rebuild_subtabs(self) -> None:
        # Remember selection so we can re-select after rebuild.
        selected_id = self.current_scan_id()

        for sub in list(self._sub_tabs.values()):
            try:
                self._notebook.forget(sub)
                sub.destroy()
            except tk.TclError:
                pass
        self._sub_tabs.clear()

        try:
            self._notebook.forget(self._empty_frame)
        except tk.TclError:
            pass

        # Filter open ids to those still in the library, preserving order.
        self._open_ids = [sid for sid in self._open_ids if sid in self._library]

        if not self._open_ids:
            self._notebook.add(self._empty_frame, text="(empty)")
            return

        # Insert open scans in the order maintained by self._open_ids.
        for scan_id in list(self._open_ids):
            scan = self._library.get(scan_id)
            if scan is None:
                continue
            self._add_subtab(scan)

        if selected_id and selected_id in self._sub_tabs:
            try:
                self._notebook.select(self._sub_tabs[selected_id])
            except tk.TclError:
                pass

    def _add_subtab(self, scan: ScanDefinition) -> _ScanSubTab:
        sub = _ScanSubTab(
            self._notebook, scan,
            on_change=self._on_subtab_change,
            on_row_action=self._on_row_action,
        )
        self._notebook.add(sub, text=scan.name)
        self._sub_tabs[scan.id] = sub
        return sub

    def _on_subtab_change(self, sub: _ScanSubTab) -> None:
        # Debounce saves: many micro-edits during typing should coalesce
        # to a single save call. Use 250 ms after-job, replacing any
        # pending one for this scan.
        scan_id = sub.scan.id
        prev = self._save_jobs.pop(scan_id, None)
        if prev is not None:
            try:
                self.after_cancel(prev)
            except tk.TclError:
                pass
        try:
            job = self.after(250, lambda: self._flush_save(scan_id))
        except tk.TclError:
            return
        self._save_jobs[scan_id] = job

    def _flush_save(self, scan_id: str) -> None:
        self._save_jobs.pop(scan_id, None)
        sub = self._sub_tabs.get(scan_id)
        if sub is None or self._on_scan_saved is None:
            return
        try:
            self._on_scan_saved(sub.scan)
        except Exception:  # noqa: BLE001
            LOG.exception("on_scan_saved failed for %s", scan_id)

    # -- toolbar actions -----------------------------------------------------

    def _ask_unique_name(self, prompt: str, initial: str = "",
                        exclude_id: str | None = None) -> str | None:
        existing = {
            s.name for sid, s in self._library.items() if sid != exclude_id
        }
        while True:
            name = simpledialog.askstring(
                "Scan name", prompt, initialvalue=initial, parent=self)
            if name is None:
                return None
            name = name.strip()
            if not name:
                continue
            if name in existing:
                messagebox.showerror(
                    "Duplicate name",
                    f"A scan named {name!r} already exists.",
                    parent=self,
                )
                continue
            return name

    def _on_new_scan(self) -> None:
        name = self._ask_unique_name("Name for the new scan:")
        if name is None:
            return
        scan = self._new_scan_factory(name)
        self.add_scan(scan)

    def add_scan(self, scan: ScanDefinition) -> None:
        """Public hook: add ``scan`` to the library and open its tab."""
        self._library[scan.id] = scan
        if scan.id not in self._open_ids:
            self._open_ids.append(scan.id)
        self._rebuild_subtabs()
        if scan.id in self._sub_tabs:
            try:
                self._notebook.select(self._sub_tabs[scan.id])
            except tk.TclError:
                pass
        if self._on_scan_saved is not None:
            try:
                self._on_scan_saved(scan)
            except Exception:  # noqa: BLE001
                LOG.exception("on_scan_saved failed for new scan %s", scan.id)

    def _on_rename_scan(self) -> None:
        scan_id = self.current_scan_id()
        scan = self._library.get(scan_id) if scan_id else None
        if scan is None:
            return
        new_name = self._ask_unique_name(
            "New name:", initial=scan.name, exclude_id=scan.id)
        if new_name is None:
            return
        scan.name = new_name
        sub = self._sub_tabs.get(scan.id)
        if sub is not None:
            try:
                self._notebook.tab(sub, text=new_name)
            except tk.TclError:
                pass
        if self._on_scan_saved is not None:
            try:
                self._on_scan_saved(scan)
            except Exception:  # noqa: BLE001
                LOG.exception("on_scan_saved failed in rename")

    def _on_delete_scan(self) -> None:
        scan_id = self.current_scan_id()
        scan = self._library.get(scan_id) if scan_id else None
        if scan is None:
            return
        if not messagebox.askyesno(
            "Delete scan", f"Delete scan {scan.name!r}?", parent=self,
        ):
            return
        self.delete_scan(scan.id)

    def delete_scan(self, scan_id: str) -> None:
        """Public hook: remove ``scan_id`` from the library and close
        its tab if open."""
        if scan_id not in self._library:
            return
        del self._library[scan_id]
        if scan_id in self._open_ids:
            self._open_ids.remove(scan_id)
        self._rebuild_subtabs()
        if self._on_scan_deleted is not None:
            try:
                self._on_scan_deleted(scan_id)
            except Exception:  # noqa: BLE001
                LOG.exception("on_scan_deleted failed for %s", scan_id)

    # -- import / export -----------------------------------------------------

    def _on_load_scan(self) -> None:
        """Pop a chooser of library scans not currently open."""
        candidates = [
            (sid, scan) for sid, scan in self._library.items()
            if sid not in self._open_ids
        ]
        if not candidates:
            messagebox.showinfo(
                "Load scan",
                "All saved scans are already open.\n\n"
                "Use + New to create another, or Import… to load from file.",
                parent=self,
            )
            return
        chosen_id = _LoadScanDialog.ask(self, candidates)
        if chosen_id is not None:
            self.open_scan(chosen_id)

    def _on_close_current(self) -> None:
        scan_id = self.current_scan_id()
        if scan_id is None:
            return
        self.close_scan(scan_id)

    def _on_subtab_right_click(self, event: tk.Event) -> None:
        """Right-click context menu on the sub-notebook tab strip.

        Identifies the clicked tab via ``notebook.index(f"@{x},{y}")``
        and offers Close / Delete on the matching scan id. Mirrors the
        Watchlist right-click pattern.
        """
        try:
            idx = self._notebook.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        tabs = self._notebook.tabs()
        if not (0 <= idx < len(tabs)):
            return
        tab_path = tabs[idx]
        scan_id = next(
            (sid for sid, sub in self._sub_tabs.items()
             if str(sub) == tab_path),
            None,
        )
        if scan_id is None:
            # Empty-state tab — nothing to do.
            return
        scan = self._library.get(scan_id)
        if scan is None:
            return
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(
            label=f"Close tab ({scan.name})",
            command=lambda sid=scan_id: self.close_scan(sid),
        )
        menu.add_command(
            label=f"Delete scan ({scan.name})…",
            command=lambda sid=scan_id, nm=scan.name: self._confirm_and_delete(sid, nm),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _confirm_and_delete(self, scan_id: str, name: str) -> None:
        if messagebox.askyesno(
            "Delete scan", f"Delete scan {name!r}?", parent=self,
        ):
            self.delete_scan(scan_id)

    def _on_export(self) -> None:
        scan_id = self.current_scan_id()
        scan = self._library.get(scan_id) if scan_id else None
        if scan is None:
            messagebox.showwarning(
                "Export", "No scan selected.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".json",
            initialfile=f"{scan.name}.json",
            filetypes=[("Scan JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(scan.to_dict(), f, indent=2)
        except OSError as e:
            messagebox.showerror(
                "Export failed", str(e), parent=self)

    def _on_import(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            filetypes=[("Scan JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            scan = ScanDefinition.from_dict(data)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            messagebox.showerror(
                "Import failed", f"Could not parse scan: {e}", parent=self)
            return
        # Rename on collision.
        if any(s.name == scan.name and s.id != scan.id
               for s in self._library.values()):
            new_name = self._ask_unique_name(
                f"A scan named {scan.name!r} already exists.\nNew name:",
                initial=scan.name + " (imported)",
                exclude_id=scan.id,
            )
            if new_name is None:
                return
            scan.name = new_name
        self.add_scan(scan)


# ---------------------------------------------------------------------------
# Default new-scan factory
# ---------------------------------------------------------------------------


def _default_new_scan(name: str) -> ScanDefinition:
    """Create a blank scan with an empty AND group at default 5m interval."""
    return ScanDefinition(
        name=name,
        root=Group(combinator="and", children=[]),
        primary_interval="5m",
    )


# ---------------------------------------------------------------------------
# Load-scan chooser dialog
# ---------------------------------------------------------------------------


class _LoadScanDialog(tk.Toplevel):
    """Modal Listbox chooser used by *Load…*. Returns the chosen scan id
    (or None on cancel) via :meth:`ask`. Sorted by name."""

    def __init__(
        self,
        parent: tk.Misc,
        candidates: list[tuple[str, ScanDefinition]],
    ) -> None:
        super().__init__(parent)
        self.title("Load scan")
        self.transient(parent)
        self.resizable(True, True)
        self._result: str | None = None
        # Geometry persistence — small chooser dialog defaults to a
        # compact size but users can grow it on dense scan libraries.
        try:
            from .geometry_store import attach_persistent_geometry
            attach_persistent_geometry(self, "dlg.load_scan", "420x360")
        except tk.TclError:
            pass
        # Sorted by name (case-insensitive); ids hidden in a parallel list.
        self._items = sorted(candidates, key=lambda p: p[1].name.lower())

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        ttk.Label(
            body, text=f"Select a saved scan to load ({len(self._items)} available):",
        ).pack(anchor="w", pady=(0, 4))
        list_frame = ttk.Frame(body)
        list_frame.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self._listbox = tk.Listbox(
            list_frame, height=min(15, max(5, len(self._items))),
            yscrollcommand=scrollbar.set, exportselection=False,
            activestyle="dotbox",
        )
        scrollbar.config(command=self._listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        for _, scan in self._items:
            self._listbox.insert(tk.END, scan.name)
        if self._items:
            self._listbox.selection_set(0)
            self._listbox.activate(0)
        self._listbox.bind("<Double-Button-1>", lambda _e: self._on_ok())
        self._listbox.bind("<Return>", lambda _e: self._on_ok())

        button_frame = ttk.Frame(body)
        button_frame.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(button_frame, text="Cancel",
                   command=self._on_cancel).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Load",
                   command=self._on_ok).pack(side=tk.RIGHT, padx=(0, 4))

        # ESC dismisses, Return loads the selection. Routed through the
        # shared modal-keys helper so the multi-line-Text guard
        # (irrelevant here, but cheap) is consistent with every other
        # dialog. The listbox-local <Return> binding is preserved
        # above so the keyboard journey "type to select -> Enter to
        # load" still works while the listbox itself has focus.
        try:
            from ._modal_keys import bind_modal_keys
            bind_modal_keys(self, cancel=self._on_cancel, primary=self._on_ok)
        except tk.TclError:
            pass
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_ok(self) -> None:
        sel = self._listbox.curselection()
        if not sel:
            return
        self._result = self._items[sel[0]][0]
        self.destroy()

    def _on_cancel(self) -> None:
        self._result = None
        self.destroy()

    @classmethod
    def ask(
        cls,
        parent: tk.Misc,
        candidates: list[tuple[str, ScanDefinition]],
    ) -> str | None:
        if not candidates:
            return None
        dlg = cls(parent, candidates)
        dlg.update_idletasks()
        try:
            dlg.grab_set()
        except tk.TclError:
            pass
        try:
            dlg.wait_window()
        except tk.TclError:
            pass
        return dlg._result
