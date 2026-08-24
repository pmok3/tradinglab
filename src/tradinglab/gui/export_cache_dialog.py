"""Export Bars to CSV dialog — dumps the disk cache for BYOD sharing.

Opened via ``Tools → Export Bars to CSV…``. Lists every
``(source, ticker, interval)`` tuple currently in the disk cache as a
checkable Treeview (all checked by default). The user picks a
destination zip file (default name ``tradinglab-export-YYYY-MM-DD.zip``);
each selected entry becomes a member
``<SOURCE>/<TICKER>_<INTERVAL>.csv`` inside the archive, in the strict
canonical schema — so unzipping the archive and pointing Configure
Local Data at that folder yields a perfect round-trip.

Empty-cache case: the dialog opens, shows a friendly "no cached data
yet" message, and disables Export.

A **Quant only** checkbox narrows the list to the series underlying the
Quant side tab. It is a pure view filter — it never fetches, so a symbol
the user has not charted yet simply is not there. Select All / Select
None and Export all act on the filtered list, so the visible selection is
always exactly what gets written.

This is the symmetric counterpart to
:mod:`tradinglab.gui.local_data_dialog`.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ._modal_base import BaseModalDialog, protect_combobox_wheel
from .colors import MUTED_GREY


def _load_cache_index() -> list[tuple[str, str, str]]:
    """Return ``[(source, ticker, interval), ...]`` for every cached entry."""
    from .. import disk_cache
    return disk_cache.list_entries()


def _load_cache_candles(source: str, ticker: str, interval: str):
    """Load candles for one cache key (used at export time, not on dialog open)."""
    from .. import disk_cache
    return disk_cache.load(source, ticker, interval)


def is_quant_entry(ticker: str) -> bool:
    """True iff ``ticker`` is one of the Quant tab's underlying series.

    Matching is done on the canonical symbol key, not the literal string,
    because the two sides spell index symbols differently: the catalog says
    ``VIX`` while the disk cache holds whatever the fetching source used
    (``^VIX`` on yfinance, ``$VIX`` on Schwab). Collapsing both sides is
    what makes a cache populated by a yfinance session still classify
    correctly for a user who has since switched vendors.

    The comparison set is the catalog's **legs**, not its rows. A ratio row
    like ``RSP/SPY`` is never persisted (AGENTS.md §7.37), so it can never
    appear in ``disk_cache.list_entries()`` — matching against it would be
    dead code, and its legs are exactly what a user wanting to reconstruct
    it needs to export.
    """
    if not ticker:
        return False
    try:
        from ..data.index_aliases import canonical_symbol_key
        from ..quant.catalog import quant_leg_symbols
    except Exception:  # noqa: BLE001
        return False
    return canonical_symbol_key(ticker) in {
        canonical_symbol_key(s) for s in quant_leg_symbols()
    }


class ExportCacheDialog(BaseModalDialog):
    """Pick-and-export dialog over the disk cache."""

    # Treeview column ids.
    _COL_CHECK = "check"
    _COL_SOURCE = "source"
    _COL_TICKER = "ticker"
    _COL_INTERVAL = "interval"

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(
            parent,
            title="Export Bars to CSV",
            geometry_key="dlg.export_cache",
            default_geometry="560x420",
            resizable=(True, True),
        )
        self.minsize(560, 420)

        self._entries: list[tuple[str, str, str]] = _load_cache_index()
        self._selected: dict[str, bool] = {
            self._key(s, t, i): True for s, t, i in self._entries
        }
        self._destination: Path | None = None
        # Precomputed once: classifying 100 cache entries against the quant
        # catalog is cheap, but doing it inside every tree repaint is not.
        self._quant_tickers: set[str] = {
            t for _s, t, _i in self._entries if is_quant_entry(t)
        }
        self._quant_only_var = tk.BooleanVar(value=False)

        self._build_widgets()
        protect_combobox_wheel(self)
        self._finalize_modal(primary=self._on_export, cancel=self._on_cancel)

    @staticmethod
    def _key(source: str, ticker: str, interval: str) -> str:
        return f"{source}__{ticker}__{interval}"

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        if not self._entries:
            ttk.Label(
                outer,
                text=(
                    "The disk cache is empty — there's nothing to export yet.\n"
                    "Load a chart first so the cache populates, then come back."
                ),
                foreground=MUTED_GREY,
                wraplength=460,
                justify="left",
            ).pack(anchor="w", pady=(12, 12))
            bottom = ttk.Frame(outer)
            bottom.pack(fill="x", pady=(8, 0))
            ttk.Button(bottom, text="Close", command=self._on_cancel).pack(side="right")
            return

        ttk.Label(
            outer,
            text=(
                f"{len(self._entries)} cached entries. Uncheck any you "
                "don't want to include, then pick a destination zip "
                "file. Each entry becomes "
                "<SOURCE>/<TICKER>_<INTERVAL>.csv inside the zip.\n"
                "Unzip the archive into a folder and drop that folder "
                "into Configure Local Data to load it back.\n"
                "\"Quant only\" narrows the list to the series behind the "
                "Quant tab. Its ratio rows are computed from these, never "
                "cached, so exporting the legs is what lets you rebuild them."
            ),
            foreground=MUTED_GREY,
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        # All / None toggle row.
        toggle_row = ttk.Frame(outer)
        toggle_row.pack(fill="x", pady=(0, 6))
        ttk.Button(toggle_row, text="Select All", command=self._select_all).pack(side="left")
        ttk.Button(toggle_row, text="Select None", command=self._select_none).pack(side="left", padx=(6, 0))
        # Quant filter. Purely a view filter over what is already cached —
        # it never fetches. Ratio rows (RSP/SPY, VIX/15.87) are absent by
        # construction: they are derived and never persisted, so what the
        # user gets is the legs those rows are computed from.
        self._quant_check = ttk.Checkbutton(
            toggle_row,
            text=f"Quant only ({len(self._quant_tickers)})",
            variable=self._quant_only_var,
            command=self._on_quant_only_toggle,
        )
        self._quant_check.pack(side="left", padx=(16, 0))
        if not self._quant_tickers:
            self._quant_check.state(["disabled"])
        self._selected_count_var = tk.StringVar(value=self._selected_count_text())
        ttk.Label(toggle_row, textvariable=self._selected_count_var, foreground=MUTED_GREY).pack(side="right")

        list_frame = ttk.Frame(outer)
        list_frame.pack(fill="both", expand=True)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        cols = (self._COL_CHECK, self._COL_SOURCE, self._COL_TICKER, self._COL_INTERVAL)
        self._tree = ttk.Treeview(
            list_frame, columns=cols, show="headings", height=12, selectmode="none",
        )
        self._tree.heading(self._COL_CHECK, text="✓")
        self._tree.heading(self._COL_SOURCE, text="Source")
        self._tree.heading(self._COL_TICKER, text="Ticker")
        self._tree.heading(self._COL_INTERVAL, text="Interval")
        self._tree.column(self._COL_CHECK, width=32, anchor="center", stretch=False)
        self._tree.column(self._COL_SOURCE, width=180, anchor="w")
        self._tree.column(self._COL_TICKER, width=120, anchor="w")
        self._tree.column(self._COL_INTERVAL, width=80, anchor="w")
        self._tree.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=0, column=1, sticky="ns")

        # Click on a row toggles its checkbox glyph in the first column.
        self._tree.bind("<Button-1>", self._on_tree_click, add=True)

        self._refresh_tree()

        # Destination row.
        dest_frame = ttk.Frame(outer)
        dest_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(dest_frame, text="Zip file:").pack(side="left")
        self._dest_var = tk.StringVar(value="(not chosen)")
        ttk.Label(
            dest_frame, textvariable=self._dest_var, foreground=MUTED_GREY,
        ).pack(side="left", padx=(6, 0), fill="x", expand=True)
        ttk.Button(dest_frame, text="Browse…", command=self._on_browse).pack(side="right")

        # Status.
        self._status_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self._status_var, foreground=MUTED_GREY).pack(anchor="w", pady=(6, 0))

        # Bottom buttons.
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="Cancel", command=self._on_cancel).pack(side="right", padx=(6, 0))
        self._export_btn = ttk.Button(bottom, text="Export", command=self._on_export)
        self._export_btn.pack(side="right")

    # ---- helpers --------------------------------------------------------

    def visible_entries(self) -> list[tuple[str, str, str]]:
        """Entries currently listed, honouring the Quant-only filter.

        The filter is deliberately load-bearing rather than cosmetic:
        Select All / Select None and Export all operate on this list, so
        what the user sees is what they act on. A filter that only hid rows
        while Export still wrote the hidden ones would be a data-loss-shaped
        surprise in the other direction — a 3 GB archive from a 40 MB
        selection.
        """
        try:
            quant_only = bool(self._quant_only_var.get())
        except Exception:  # noqa: BLE001
            quant_only = False
        if not quant_only:
            return list(self._entries)
        return [e for e in self._entries if e[1] in self._quant_tickers]

    def _on_quant_only_toggle(self) -> None:
        self._refresh_tree()

    def _selected_count_text(self) -> str:
        visible = self.visible_entries()
        on = sum(
            1 for s, t, i in visible
            if self._selected.get(self._key(s, t, i), False)
        )
        return f"{on} of {len(visible)} selected"

    def _refresh_tree(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)
        for source, ticker, interval in self.visible_entries():
            checked = "☑" if self._selected[self._key(source, ticker, interval)] else "☐"
            self._tree.insert(
                "", "end",
                iid=self._key(source, ticker, interval),
                values=(checked, source, ticker, interval),
            )
        self._selected_count_var.set(self._selected_count_text())

    def _select_all(self) -> None:
        for s, t, i in self.visible_entries():
            self._selected[self._key(s, t, i)] = True
        self._refresh_tree()

    def _select_none(self) -> None:
        for s, t, i in self.visible_entries():
            self._selected[self._key(s, t, i)] = False
        self._refresh_tree()

    def _on_tree_click(self, event: tk.Event) -> None:
        # Identify which cell was clicked; only react when the click
        # landed on the check column (so users can drag-scroll the
        # other columns without toggling state).
        region = self._tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self._tree.identify_column(event.x)
        # Tk column ids are 1-indexed strings (#1 = check column).
        if col != "#1":
            return
        row = self._tree.identify_row(event.y)
        if not row or row not in self._selected:
            return
        self._selected[row] = not self._selected[row]
        self._refresh_tree()

    def _on_browse(self) -> None:
        # Audit ``local-export-zip``: replaced the legacy directory
        # picker with a save-as zip picker. The default filename is
        # date-stamped so back-to-back exports don't silently
        # overwrite each other.
        from ..data.local_export import default_zip_filename
        chosen = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".zip",
            filetypes=[("ZIP archive", "*.zip"), ("All files", "*.*")],
            initialfile=default_zip_filename(),
            title="Save exported bars as…",
        )
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix.lower() != ".zip":
            path = path.with_suffix(".zip")
        self._destination = path
        self._dest_var.set(str(self._destination))

    # ---- export action --------------------------------------------------

    def _on_export(self) -> None:
        if not self._entries:
            self.destroy()
            return
        if self._destination is None:
            self._status_var.set("Pick a zip file first.")
            return
        selected = [
            (s, t, i) for (s, t, i) in self.visible_entries()
            if self._selected.get(self._key(s, t, i), False)
        ]
        if not selected:
            self._status_var.set("Nothing selected.")
            return

        from ..data.local_export import export_entries_zip

        # Load candles per entry as we go — keeps memory bounded for
        # very large caches (one entry materialised at a time).
        def _iter():
            for source, ticker, interval in selected:
                candles = _load_cache_candles(source, ticker, interval)
                if candles is None:
                    yield (source, ticker, interval, [])
                else:
                    yield (source, ticker, interval, candles)

        try:
            results = export_entries_zip(_iter(), self._destination)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(
                "Export Bars to CSV", f"Export failed: {e}", parent=self,
            )
            return

        ok = sum(1 for *_p, n, err in results if err is None)
        fail = len(results) - ok
        msg = (
            f"Exported {ok} of {len(results)} entries to "
            f"{self._destination}."
        )
        if fail:
            sample_errs = [f"{s}/{t}/{i}: {err}" for s, t, i, _n, err in results if err][:5]
            msg += f"\n\n{fail} failed:\n - " + "\n - ".join(sample_errs)
            if fail > 5:
                msg += f"\n (+{fail - 5} more — see logs)"
        messagebox.showinfo("Export Bars to CSV", msg, parent=self)
        self.destroy()

    def _on_cancel(self) -> None:
        self.destroy()


def open_export_cache_dialog(parent: tk.Misc) -> ExportCacheDialog:
    """Convenience opener used by ``Tools → Export Bars to CSV…``."""
    return ExportCacheDialog(parent)
