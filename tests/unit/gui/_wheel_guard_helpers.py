"""Shared helpers for per-dialog Combobox/Spinbox wheel-guard regression tests.

The repo's combobox wheel guard (``gui._modal_base.protect_combobox_wheel``)
must be re-applied after every dynamic widget rebuild in dialogs that
``bind_all("<MouseWheel>")`` for canvas scrolling. Each guarded dialog
ships a small regression test that:

1. Builds the dialog headlessly.
2. Snapshots the persisted state.
3. Wheel-bombs every Combobox / Spinbox descendant.
4. Asserts state is unchanged.

This module centralises the wheel-bombing walker so each per-dialog
test stays focused on construction + snapshot diff.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def wheel_bomb_all(root: tk.Misc, *, ticks: int = 8) -> int:
    """Synthesize ``<MouseWheel>`` events on every Combobox/Spinbox.

    Sends ``ticks`` down-scroll events followed by ``ticks`` up-scroll
    events on every ``ttk.Combobox`` / ``ttk.Spinbox`` descendant of
    ``root``. Without the wheel-guard installed this is sufficient to
    walk a readonly combobox's selection across multiple values and
    silently mutate persisted state.

    Returns the count of widgets bombed (helpful for sanity-checking
    that the walker actually found the expected widgets).
    """
    count = 0

    def _walk(w: tk.Misc) -> None:
        nonlocal count
        try:
            children = w.winfo_children()
        except tk.TclError:
            return
        for child in children:
            if isinstance(child, (ttk.Combobox, ttk.Spinbox)):
                count += 1
                try:
                    for _ in range(ticks):
                        child.event_generate(
                            "<MouseWheel>", delta=-120, x=5, y=5,
                        )
                    for _ in range(ticks):
                        child.event_generate(
                            "<MouseWheel>", delta=+120, x=5, y=5,
                        )
                except tk.TclError:
                    pass
            _walk(child)

    _walk(root)
    try:
        root.update_idletasks()
    except tk.TclError:
        pass
    return count


def snapshot_combobox_spinbox_values(root: tk.Misc) -> list[tuple[str, str]]:
    """Snapshot ``(widget_class, current_value)`` for every Combobox/Spinbox.

    Used to detect silent value-mutation without requiring access to a
    parent app's persisted state. Order is the widget-tree pre-order
    traversal so two snapshots taken before / after a wheel storm can
    be compared element-wise.
    """
    out: list[tuple[str, str]] = []

    def _walk(w: tk.Misc) -> None:
        try:
            children = w.winfo_children()
        except tk.TclError:
            return
        for child in children:
            if isinstance(child, (ttk.Combobox, ttk.Spinbox)):
                try:
                    out.append((type(child).__name__, child.get()))
                except tk.TclError:
                    out.append((type(child).__name__, ""))
            _walk(child)

    _walk(root)
    return out
