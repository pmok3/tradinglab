"""Watchlist "Columns…" dialog.

Per-watchlist column editor: a left pane listing the active columns
(``ticker`` locked first) with reorder / remove / rename / format, and a
right pane that builds a **signal** column from the scanner
``_FieldRefPicker`` (field + params) plus an interval and a display
format. On OK the validated column list is handed to ``on_apply``.

Reuses ``gui.scanner_block_editor._FieldRefPicker`` for field selection
so watchlist columns stay semantically identical to scanner / entry /
exit field references — no watchlist-specific field math. See
``watchlist_columns_dialog.spec.md`` and ``docs/WATCHLIST_COLUMNS.md``.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from dataclasses import replace
from tkinter import simpledialog, ttk
from typing import Any

from ..scanner.model import FieldRef
from ..watchlists.columns import (
    KIND_SIGNAL,
    KIND_SYSTEM,
    LOCKED_COLUMN_ID,
    SYSTEM_COLUMN_IDS,
    WatchlistColumn,
    default_columns,
    header_label,
    signal_column_id,
    validate_columns,
)
from ._modal_base import BaseModalDialog, protect_combobox_wheel
from .colors import ERROR_RED
from .native_theme import apply_listbox_theme, current_theme
from .scanner_block_editor import _FieldRefPicker

# (label, fmt-preset) pairs for the display-format combo. The preset
# strings are consumed by ``watchlists.signals.format_value``.
_FMT_CHOICES: tuple[tuple[str, str], ...] = (
    ("Auto (2 dp)", "auto"),
    ("Number (0 dp)", "number:0"),
    ("Number (1 dp)", "number:1"),
    ("Number (2 dp)", "number:2"),
    ("Percent", "percent"),
    ("Signed %", "signed_pct"),
    ("Multiplier ×", "multiplier"),
    ("Integer", "int"),
    ("Up/Down glyph", "glyph"),
)
_FMT_LABEL_BY_VALUE = {v: lbl for lbl, v in _FMT_CHOICES}
_FMT_VALUE_BY_LABEL = {lbl: v for lbl, v in _FMT_CHOICES}

# (label, interval) pairs. "Daily" maps to ``None`` so a daily column
# has a single canonical id (``signal_column_id`` keys interval="").
_INTERVAL_CHOICES: tuple[tuple[str, str | None], ...] = (
    ("Daily", None),
    ("1 week", "1wk"),
    ("1 hour", "1h"),
    ("30 min", "30m"),
    ("15 min", "15m"),
    ("5 min", "5m"),
    ("1 min", "1m"),
)
_INTERVAL_VALUE_BY_LABEL = {lbl: iv for lbl, iv in _INTERVAL_CHOICES}


class WatchlistColumnsDialog(BaseModalDialog):
    """Modal editor for one watchlist's column set.

    Reuses ``_FieldRefPicker`` to define a signal column's field /
    params. ``ticker`` is shown locked and cannot be removed or moved
    above position 0. On OK, ``on_apply(validate_columns(columns))`` is
    invoked with the ordered, validated column list.
    """

    def __init__(
        self,
        parent: Any,
        *,
        watchlist_name: str,
        columns: Sequence[WatchlistColumn],
        on_apply: Callable[[list[WatchlistColumn]], None],
        **kwargs: Any,
    ) -> None:
        super().__init__(
            parent,
            title=f"Watchlist Columns — {watchlist_name}",
            geometry_key="dlg.watchlist_columns",
            default_geometry="860x560",
            resizable=(True, True),
        )
        self.app = parent
        self._watchlist_name = watchlist_name
        self._on_apply = on_apply
        self.result: list[WatchlistColumn] | None = None
        # Working copy — validated so ticker is present + first.
        self._cols: list[WatchlistColumn] = validate_columns(list(columns) or default_columns())

        self._build()
        self._refresh_list()
        # Guard every Combobox/Spinbox against wheel-scroll value drift
        # (§7.11). Re-applied after picker rebuilds via the picker's
        # ``on_change`` callback below.
        protect_combobox_wheel(self)
        self._finalize_modal(primary=self._on_ok, cancel=self._on_cancel)

    # -- layout ---------------------------------------------------------
    def _build(self) -> None:
        pad = {"padx": 6, "pady": 4}
        root = ttk.Frame(self)
        root.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)

        # ----- LEFT: active columns ------------------------------------
        left = ttk.LabelFrame(root, text="Active columns")
        left.grid(row=0, column=0, sticky="nsew", **pad)
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self._listbox = tk.Listbox(left, height=14, exportselection=False)
        apply_listbox_theme(self._listbox, current_theme(self.app))
        self._listbox.grid(row=0, column=0, sticky="nsew", **pad)
        self._listbox.bind("<<ListboxSelect>>", lambda _e: self._on_select())

        lbtn = ttk.Frame(left)
        lbtn.grid(row=0, column=1, sticky="ns", **pad)
        ttk.Button(lbtn, text="↑", width=3, command=lambda: self._on_move(-1)) \
            .pack(side=tk.TOP, pady=2)
        ttk.Button(lbtn, text="↓", width=3, command=lambda: self._on_move(1)) \
            .pack(side=tk.TOP, pady=2)
        self._btn_remove = ttk.Button(lbtn, text="Remove", command=self._on_remove)
        self._btn_remove.pack(side=tk.TOP, pady=(10, 2))
        self._btn_rename = ttk.Button(lbtn, text="Rename…", command=self._on_rename)
        self._btn_rename.pack(side=tk.TOP, pady=2)

        fmt_row = ttk.Frame(left)
        fmt_row.grid(row=1, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(fmt_row, text="Format:").pack(side=tk.LEFT)
        self._fmt_var = tk.StringVar(value=_FMT_CHOICES[0][0])
        self._fmt_combo = ttk.Combobox(
            fmt_row, textvariable=self._fmt_var, state="readonly", width=16,
            values=tuple(lbl for lbl, _v in _FMT_CHOICES),
        )
        self._fmt_combo.pack(side=tk.LEFT, padx=4)
        self._fmt_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_set_format())

        # Quick re-add for removed system columns.
        sysrow = ttk.Frame(left)
        sysrow.grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(sysrow, text="Add system:").pack(side=tk.LEFT)
        for cid in SYSTEM_COLUMN_IDS:
            if cid == LOCKED_COLUMN_ID:
                continue
            ttk.Button(
                sysrow, text=cid.replace("_", " ").title(), width=9,
                command=lambda c=cid: self._on_add_system(c),
            ).pack(side=tk.LEFT, padx=2)

        # ----- RIGHT: add a signal column ------------------------------
        right = ttk.LabelFrame(root, text="Add signal column")
        right.grid(row=0, column=1, sticky="nsew", **pad)
        right.columnconfigure(0, weight=1)

        ttk.Label(right, text="Field:").grid(row=0, column=0, sticky="w", **pad)
        self._picker = _FieldRefPicker(
            right,
            ref=FieldRef.builtin("close"),
            on_change=lambda: protect_combobox_wheel(self),
            display_mode="detailed",
        )
        self._picker.grid(row=1, column=0, sticky="ew", **pad)

        ivrow = ttk.Frame(right)
        ivrow.grid(row=2, column=0, sticky="w", **pad)
        ttk.Label(ivrow, text="Interval:").pack(side=tk.LEFT)
        self._interval_var = tk.StringVar(value=_INTERVAL_CHOICES[0][0])
        ttk.Combobox(
            ivrow, textvariable=self._interval_var, state="readonly", width=10,
            values=tuple(lbl for lbl, _v in _INTERVAL_CHOICES),
        ).pack(side=tk.LEFT, padx=4)

        ttk.Label(ivrow, text="Format:").pack(side=tk.LEFT, padx=(10, 0))
        self._new_fmt_var = tk.StringVar(value=_FMT_CHOICES[0][0])
        ttk.Combobox(
            ivrow, textvariable=self._new_fmt_var, state="readonly", width=16,
            values=tuple(lbl for lbl, _v in _FMT_CHOICES),
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(right, text="Add column  ▶", command=self._on_add_signal) \
            .grid(row=3, column=0, sticky="e", **pad)

        self._error_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self._error_var, foreground=ERROR_RED) \
            .grid(row=4, column=0, sticky="w", **pad)

        # ----- footer --------------------------------------------------
        footer = ttk.Frame(self)
        footer.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        ttk.Button(footer, text="Reset to defaults", command=self._on_reset) \
            .pack(side=tk.LEFT)
        ttk.Button(footer, text="Cancel", command=self._on_cancel) \
            .pack(side=tk.RIGHT, padx=4)
        ttk.Button(footer, text="OK", command=self._on_ok) \
            .pack(side=tk.RIGHT, padx=4)

    # -- list helpers ---------------------------------------------------
    def _refresh_list(self) -> None:
        self._cols = validate_columns(self._cols)
        self._listbox.delete(0, tk.END)
        for col in self._cols:
            label = header_label(col)
            if col.id == LOCKED_COLUMN_ID:
                label = f"{label}  (locked)"
            elif col.kind == KIND_SYSTEM:
                label = f"{label}  · system"
            elif col.fmt and col.fmt != "auto":
                label = f"{label}  · {col.fmt}"
            self._listbox.insert(tk.END, label)
        self._on_select()

    def _selected_index(self) -> int | None:
        sel = self._listbox.curselection()
        if not sel:
            return None
        idx = int(sel[0])
        if 0 <= idx < len(self._cols):
            return idx
        return None

    def _on_select(self) -> None:
        idx = self._selected_index()
        col = self._cols[idx] if idx is not None else None
        locked = col is not None and col.id == LOCKED_COLUMN_ID
        self._btn_remove.configure(state=(tk.DISABLED if (col is None or locked) else tk.NORMAL))
        self._btn_rename.configure(state=(tk.DISABLED if col is None else tk.NORMAL))
        # Reflect the selected column's format in the Format combo.
        if col is not None:
            self._fmt_var.set(_FMT_LABEL_BY_VALUE.get(col.fmt, _FMT_CHOICES[0][0]))
        self._fmt_combo.configure(
            state=("disabled" if (col is None or col.kind != KIND_SIGNAL) else "readonly")
        )

    # -- edit actions ---------------------------------------------------
    def _on_move(self, delta: int) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        j = idx + delta
        # ticker is pinned at 0; nothing may move above it.
        if j < 1 or j >= len(self._cols) or idx == 0:
            return
        self._cols[idx], self._cols[j] = self._cols[j], self._cols[idx]
        self._refresh_list()
        self._listbox.selection_set(j)
        self._on_select()

    def _on_remove(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        if self._cols[idx].id == LOCKED_COLUMN_ID:
            return
        del self._cols[idx]
        self._refresh_list()

    def _on_rename(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        col = self._cols[idx]
        new = simpledialog.askstring(
            "Rename column", "Header label:", initialvalue=col.label or header_label(col),
            parent=self,
        )
        if new is None:
            return
        self._cols[idx] = replace(col, label=new.strip())
        self._refresh_list()
        self._listbox.selection_set(idx)

    def _on_set_format(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        col = self._cols[idx]
        if col.kind != KIND_SIGNAL:
            return
        fmt = _FMT_VALUE_BY_LABEL.get(self._fmt_var.get(), "auto")
        self._cols[idx] = replace(col, fmt=fmt)
        self._refresh_list()
        self._listbox.selection_set(idx)

    def _on_add_signal(self) -> None:
        try:
            ref = self._picker.get()
        except Exception:  # noqa: BLE001
            self._error_var.set("Could not read the selected field.")
            return
        interval = _INTERVAL_VALUE_BY_LABEL.get(self._interval_var.get())
        ref = replace(ref, interval=interval)
        fmt = _FMT_VALUE_BY_LABEL.get(self._new_fmt_var.get(), "auto")
        col = WatchlistColumn(
            kind=KIND_SIGNAL, id=signal_column_id(ref), ref=ref, fmt=fmt,
        )
        if any(c.id == col.id for c in self._cols):
            self._error_var.set("That column is already present.")
            return
        self._error_var.set("")
        self._cols.append(col)
        self._refresh_list()
        self._listbox.selection_set(len(self._cols) - 1)
        self._on_select()

    def _on_add_system(self, cid: str) -> None:
        if any(c.id == cid for c in self._cols):
            return
        from ..watchlists.columns import _SYSTEM_DISPLAY  # local: display metadata
        label, width, anchor = _SYSTEM_DISPLAY.get(cid, (cid, 80, "center"))
        self._cols.append(
            WatchlistColumn(kind=KIND_SYSTEM, id=cid, label=label, width=width, anchor=anchor)
        )
        self._refresh_list()

    def _on_reset(self) -> None:
        self._cols = default_columns()
        self._error_var.set("")
        self._refresh_list()

    # -- lifecycle ------------------------------------------------------
    def _on_ok(self) -> None:
        self.result = validate_columns(self._cols)
        try:
            self._on_apply(self.result)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def open_columns_dialog(app: Any, watchlist_name: str) -> WatchlistColumnsDialog | None:
    """Open the Columns… dialog for ``watchlist_name`` (menu / header action).

    Reads the current columns from the app's ``WatchlistManager``, and on
    apply persists them + rebuilds the sub-tab so the new column set takes
    effect and its signal cells start filling.
    """
    mgr = getattr(app, "_watchlists", None)
    if mgr is None:
        return None
    try:
        columns = mgr.columns_for(watchlist_name)
    except Exception:  # noqa: BLE001
        columns = default_columns()

    def _apply(new_cols: list[WatchlistColumn]) -> None:
        try:
            mgr.set_columns(watchlist_name, new_cols)
        except Exception:  # noqa: BLE001
            return
        try:
            app._watchlist_row_cache.pop(watchlist_name, None)
        except Exception:  # noqa: BLE001
            pass
        try:
            app._rebuild_watchlist_subtabs()
        except Exception:  # noqa: BLE001
            pass
        try:
            app._preload_watchlist_signals()
        except Exception:  # noqa: BLE001
            pass

    return WatchlistColumnsDialog(
        app, watchlist_name=watchlist_name, columns=columns, on_apply=_apply,
    )


__all__ = ("WatchlistColumnsDialog", "open_columns_dialog")
