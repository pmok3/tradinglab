"""Configure Local Data dialog — manage BYOD roots (Tools menu).

A simple Toplevel dialog with:

* An ``Enable local data sources`` checkbox.
* A list of configured roots (name + path), with Add / Edit / Remove buttons.
* A footer note pointing at ``docs/LOCAL_DATA.md``.
* ``Save and Close`` / ``Cancel`` buttons (live-commit paradigm is
  unsuitable here — the root list is composing a definition, not
  adjusting a setting).

On save:

1. Settings are written via :func:`tradinglab.settings.set` under the
   ``local_data`` key.
2. Existing combobox entries that match the ``<root_name>-<subdir>``
   pattern are stripped from ``DATA_SOURCES`` (so removed/edited roots
   immediately disappear).
3. :func:`tradinglab.data.register_local_sources` is called to
   re-register everything from the new settings.
4. The optional ``on_changed`` callback (passed by ``ChartApp``) is
   invoked so the source-selector combobox refreshes.

Discoverability: opened via ``Tools → Configure Local Data…``, paired
with ``Configure Credentials…`` in the same menu.
"""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ._modal_base import BaseModalDialog, protect_combobox_wheel
from ._modal_keys import bind_modal_keys
from .colors import MUTED_GREY

# Shape of one root row in the in-dialog model.
RootRow = tuple[str, str]  # (name, path)


def _load_roots_from_settings() -> tuple[bool, list[RootRow]]:
    """Read the current ``local_data`` setting into (enabled, roots).

    Falls back to ``(False, [])`` when the key is missing or malformed
    so the dialog opens cleanly on a fresh install.
    """
    from .. import settings as _settings
    cfg = _settings.get("local_data") or {}
    if not isinstance(cfg, dict):
        return False, []
    enabled = bool(cfg.get("enabled"))
    raw_roots = cfg.get("roots") or []
    roots: list[RootRow] = []
    if isinstance(raw_roots, list):
        for entry in raw_roots:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            path = str(entry.get("path") or "").strip()
            if name and path:
                roots.append((name, path))
    return enabled, roots


def _save_roots_to_settings(enabled: bool, roots: list[RootRow]) -> None:
    """Persist ``(enabled, roots)`` under the ``local_data`` settings key."""
    from .. import settings as _settings
    payload = {
        "enabled": bool(enabled),
        "roots": [{"name": n, "path": p} for n, p in roots],
    }
    _settings.set("local_data", payload)


def _refresh_data_registry() -> None:
    """Strip every BYOD source from ``DATA_SOURCES``, then re-register.

    Identifying BYOD entries: every registered local source has a key
    of the form ``<root_name>-<subfolder>`` (always contains a hyphen
    and is not one of the built-in source names).
    """
    from .. import defaults
    from ..data import DATA_SOURCES, register_local_sources

    # Force defaults to re-read settings.json on next get() — without
    # this the cached value would still report the old roots.
    defaults.reload()

    # Drop every entry from DATA_SOURCES that looks like a BYOD source
    # (contains a hyphen and isn't one of the built-ins).
    builtins = {
        "yfinance", "synthetic", "synthetic-stream",
        "schwab", "alpaca", "polygon",
    }
    for key in list(DATA_SOURCES.keys()):
        if key in builtins:
            continue
        if "-" in key:
            DATA_SOURCES.pop(key, None)

    register_local_sources()


def _validate_root_name(name: str) -> str | None:
    """Validate a BYOD root name. Returns ``None`` if valid, else an error string.

    Rules:
    * Non-empty after stripping whitespace.
    * Only alphanumerics and underscores. **No hyphens** — combobox keys
      are formed as ``<root_name>-<subdir>``, so allowing a hyphen in
      the name would make the parser downstream ambiguous.
    * No whitespace or path separators (already covered by the alnum
      rule, but called out explicitly in the error message).
    """
    s = (name or "").strip()
    if not s:
        return "Name is required (must not be empty)."
    if not all(c.isalnum() or c == "_" for c in s):
        return (
            "Name must be alphanumeric / underscores only "
            "(no hyphens, spaces, or special characters)."
        )
    return None


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class LocalDataDialog(BaseModalDialog):
    """Configure-local-data dialog (Tools → Configure Local Data…)."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            parent,
            title="Configure Local Data",
            geometry_key="dlg.local_data",
            default_geometry="560x420",
        )
        self.minsize(560, 360)

        self._on_changed = on_changed
        enabled, roots = _load_roots_from_settings()
        self._enabled_var = tk.BooleanVar(value=enabled)
        self._roots: list[RootRow] = list(roots)

        self._build_widgets()
        protect_combobox_wheel(self)
        self._finalize_modal(primary=self._on_save, cancel=self._on_cancel)

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Checkbutton(
            outer,
            text="Enable local data sources",
            variable=self._enabled_var,
        ).pack(anchor="w", pady=(0, 8))

        ttk.Label(
            outer,
            text=(
                "Each root is a folder whose top-level subfolders are "
                "original sources (yfinance/, polygon/, alpaca/, …).\n"
                "Each subfolder appears in the source selector as "
                "<root-name>-<subfolder>. See docs/LOCAL_DATA.md."
            ),
            foreground=MUTED_GREY,
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        list_frame = ttk.LabelFrame(outer, text="Configured roots", padding=8)
        list_frame.pack(fill="both", expand=True)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        cols = ("name", "path")
        self._tree = ttk.Treeview(
            list_frame, columns=cols, show="headings", height=6, selectmode="browse",
        )
        self._tree.heading("name", text="Name")
        self._tree.heading("path", text="Folder")
        self._tree.column("name", width=140, anchor="w")
        self._tree.column("path", width=380, anchor="w")
        self._tree.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=0, column=1, sticky="ns")

        btn_row = ttk.Frame(list_frame)
        btn_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(btn_row, text="Add…", command=self._on_add).pack(side="left")
        ttk.Button(btn_row, text="Edit…", command=self._on_edit).pack(side="left", padx=(6, 0))
        ttk.Button(btn_row, text="Remove", command=self._on_remove).pack(side="left", padx=(6, 0))

        self._refresh_tree()

        # Status / docs hint.
        self._status_var = tk.StringVar(value="")
        ttk.Label(
            outer, textvariable=self._status_var, foreground=MUTED_GREY,
        ).pack(anchor="w", pady=(8, 0))

        # Save / cancel buttons at the bottom.
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="Cancel", command=self._on_cancel).pack(side="right", padx=(6, 0))
        ttk.Button(bottom, text="Save and Close", command=self._on_save).pack(side="right")

    # ---- list operations ----------------------------------------------

    def _refresh_tree(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)
        for name, path in self._roots:
            self._tree.insert("", "end", values=(name, path))

    def _selected_index(self) -> int | None:
        sel = self._tree.selection()
        if not sel:
            return None
        children = self._tree.get_children()
        try:
            return children.index(sel[0])
        except ValueError:
            return None

    def _on_add(self) -> None:
        result = _prompt_for_root(self, initial_name="", initial_path="")
        if result is None:
            return
        name, path = result
        if any(n == name for n, _ in self._roots):
            messagebox.showerror(
                "Configure Local Data",
                f"A root named {name!r} already exists. Pick a different name.",
                parent=self,
            )
            return
        self._roots.append((name, path))
        self._refresh_tree()

    def _on_edit(self) -> None:
        idx = self._selected_index()
        if idx is None:
            self._status_var.set("Select a row to edit.")
            return
        name, path = self._roots[idx]
        result = _prompt_for_root(self, initial_name=name, initial_path=path)
        if result is None:
            return
        new_name, new_path = result
        # If renaming, ensure uniqueness.
        if new_name != name and any(n == new_name for n, _ in self._roots):
            messagebox.showerror(
                "Configure Local Data",
                f"A root named {new_name!r} already exists.",
                parent=self,
            )
            return
        self._roots[idx] = (new_name, new_path)
        self._refresh_tree()

    def _on_remove(self) -> None:
        idx = self._selected_index()
        if idx is None:
            self._status_var.set("Select a row to remove.")
            return
        name, _ = self._roots[idx]
        confirm = messagebox.askyesno(
            "Configure Local Data",
            f"Remove root {name!r}? (Files on disk are not deleted.)",
            parent=self,
        )
        if not confirm:
            return
        del self._roots[idx]
        self._refresh_tree()

    # ---- save / cancel -------------------------------------------------

    def _validate_before_save(self) -> str | None:
        """Return ``None`` if valid, else an error message to show."""
        if not self._enabled_var.get():
            return None  # Empty config is fine when disabled.
        # Each path must resolve to a real directory OR a real zip
        # file (audit ``local-source-zip``).
        for name, path in self._roots:
            p = Path(path)
            if p.is_dir():
                continue
            if p.is_file() and p.suffix.lower() == ".zip":
                continue
            return f"Root {name!r}: path {path!r} is not a folder or .zip file."
        # Uniqueness already enforced on add/edit.
        return None

    def _on_save(self) -> None:
        err = self._validate_before_save()
        if err:
            messagebox.showerror("Configure Local Data", err, parent=self)
            return
        _save_roots_to_settings(self._enabled_var.get(), self._roots)
        try:
            _refresh_data_registry()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(
                "Configure Local Data",
                f"Settings saved, but failed to refresh the data registry: {e}",
                parent=self,
            )
        else:
            if self._on_changed is not None:
                try:
                    self._on_changed()
                except Exception:  # noqa: BLE001
                    pass
        self.destroy()

    def _on_cancel(self) -> None:
        self.destroy()


# ---------------------------------------------------------------------------
# Add / Edit sub-dialog
# ---------------------------------------------------------------------------


def _prompt_for_root(
    parent: tk.Misc, *, initial_name: str, initial_path: str,
) -> tuple[str, str] | None:
    """Modal prompt for a single (name, path) pair. Returns None on cancel."""
    win = tk.Toplevel(parent)
    win.title("Local Data Root")
    win.transient(parent)
    win.grab_set()
    win.resizable(False, False)

    result: dict = {"value": None}

    frm = ttk.Frame(win, padding=12)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="Name:").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=2)
    name_var = tk.StringVar(value=initial_name)
    name_entry = ttk.Entry(frm, width=28, textvariable=name_var)
    name_entry.grid(row=0, column=1, columnspan=2, sticky="we", pady=2)

    ttk.Label(frm, text="Folder/Zip:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=2)
    path_var = tk.StringVar(value=initial_path)
    path_entry = ttk.Entry(frm, width=44, textvariable=path_var)
    path_entry.grid(row=1, column=1, sticky="we", pady=2)

    def _browse() -> None:
        # Audit ``local-source-zip``: the user can pick either a
        # folder root (the original BYOD shape) or a zip archive
        # produced by Export Bars to CSV.
        from tkinter import messagebox as _mb
        choice = _mb.askyesno(
            "Local Data Root",
            "Pick a folder?\n\nYes = folder of subfolders\n"
            "No = single .zip archive produced by Export Bars to CSV",
            parent=win,
        )
        if choice:
            chosen = filedialog.askdirectory(parent=win, mustexist=True)
        else:
            chosen = filedialog.askopenfilename(
                parent=win,
                title="Select a zip archive",
                filetypes=[("ZIP archive", "*.zip"), ("All files", "*.*")],
            )
        if chosen:
            path_var.set(chosen)

    ttk.Button(frm, text="Browse…", command=_browse).grid(row=1, column=2, sticky="w", padx=(6, 0))

    ttk.Label(
        frm,
        text=(
            "Folder roots: subfolders become source-selector entries; "
            "files inside are <TICKER>_<INTERVAL>.csv.\n"
            "Zip roots: top-level directories inside the archive become "
            "source-selector entries — drop a file produced by "
            "Export Bars to CSV here to load it back without unzipping.\n"
            "See docs/LOCAL_DATA.md."
        ),
        foreground=MUTED_GREY,
        wraplength=420,
        justify="left",
    ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 4))

    err_var = tk.StringVar(value="")
    ttk.Label(frm, textvariable=err_var, foreground="#c33").grid(
        row=3, column=0, columnspan=3, sticky="w", pady=(2, 4),
    )

    def _ok() -> None:
        name = (name_var.get() or "").strip()
        path = (path_var.get() or "").strip()
        name_err = _validate_root_name(name)
        if name_err is not None:
            err_var.set(name_err)
            return
        if not path:
            err_var.set("Folder or zip is required.")
            return
        pth = Path(path)
        if pth.is_file() and pth.suffix.lower() == ".zip":
            pass  # zip-as-root accepted
        elif pth.is_dir():
            pass  # folder accepted
        else:
            err_var.set(f"Not a folder or .zip file: {path}")
            return
        result["value"] = (name, path)
        win.destroy()

    def _cancel() -> None:
        win.destroy()

    btn_row = ttk.Frame(frm)
    btn_row.grid(row=4, column=0, columnspan=3, sticky="e", pady=(6, 0))
    ttk.Button(btn_row, text="Cancel", command=_cancel).pack(side="right", padx=(6, 0))
    ttk.Button(btn_row, text="OK", command=_ok).pack(side="right")

    bind_modal_keys(win, cancel=_cancel, primary=_ok)
    name_entry.focus_set()

    parent.wait_window(win)
    return result["value"]


def open_local_data_dialog(
    parent: tk.Misc, *, on_changed: Callable[[], None] | None = None,
) -> LocalDataDialog:
    """Convenience opener used by ``Tools → Configure Local Data…``."""
    return LocalDataDialog(parent, on_changed=on_changed)
