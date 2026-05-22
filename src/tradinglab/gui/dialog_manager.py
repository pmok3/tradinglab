from __future__ import annotations

import tkinter as tk
from collections.abc import Callable


class DialogManager:
    """Manages modeless dialog singletons.

    Replaces ad-hoc singleton checks with a unified registry.
    Each dialog is identified by a string key.
    """

    def __init__(self, root: tk.Tk):
        self._root = root
        self._registry: dict[str, tk.Toplevel] = {}

    def _forget_if_current(self, key: str, dlg: tk.Toplevel) -> None:
        if self._registry.get(key) is dlg:
            del self._registry[key]

    def register(self, key: str, dlg: tk.Toplevel) -> tk.Toplevel:
        """Track an existing dialog under ``key``."""
        self._registry[key] = dlg

        def _forget(_event: object | None = None, *, dialog_key: str = key, dialog: tk.Toplevel = dlg) -> None:
            try:
                self._forget_if_current(dialog_key, dialog)
            except Exception:  # noqa: BLE001
                pass

        try:
            dlg.bind("<Destroy>", _forget, add="+")
        except tk.TclError:
            pass
        return dlg

    def forget(self, key: str, dlg: tk.Toplevel | None = None) -> None:
        """Drop ``key`` when it points at ``dlg`` (or unconditionally)."""
        existing = self._registry.get(key)
        if existing is None:
            return
        if dlg is not None and existing is not dlg:
            return
        del self._registry[key]

    def rekey(self, old_key: str, new_key: str, dlg: tk.Toplevel | None = None) -> None:
        """Move a tracked dialog from ``old_key`` to ``new_key``."""
        existing = self._registry.get(old_key)
        if existing is None:
            if dlg is not None:
                self.register(new_key, dlg)
            return
        if dlg is not None and existing is not dlg:
            return
        del self._registry[old_key]
        self.register(new_key, existing)

    def open_or_focus(self, key: str, factory: Callable[[], tk.Toplevel]) -> tk.Toplevel:
        """Return existing dialog if alive, else create via factory."""
        existing = self.get(key)
        if existing is not None:
            try:
                existing.deiconify()
                existing.lift()
                existing.focus_set()
            except tk.TclError:
                pass
            return existing
        dlg = factory()
        return self.register(key, dlg)

    def close(self, key: str) -> None:
        """Close and unregister a dialog."""
        dlg = self._registry.pop(key, None)
        if dlg is not None:
            try:
                dlg.destroy()
            except tk.TclError:
                pass

    def close_all(self) -> None:
        """Close all registered dialogs."""
        for key in list(self._registry):
            self.close(key)

    def is_open(self, key: str) -> bool:
        dlg = self.get(key)
        if dlg is None:
            return False
        try:
            return bool(dlg.winfo_exists())
        except tk.TclError:
            return False

    def get(self, key: str) -> tk.Toplevel | None:
        """Get dialog if alive, else None."""
        dlg = self._registry.get(key)
        if dlg is None:
            return None
        try:
            if dlg.winfo_exists():
                return dlg
        except tk.TclError:
            pass
        self._registry.pop(key, None)
        return None
