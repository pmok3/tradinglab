"""Opt-in wrapping for existing packed action rows."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class FlowLayout:
    """Keep packed controls intact while moving them between measured rows."""

    def __init__(self, container: tk.Misc, *, gap: int = 4) -> None:
        self.container = container
        self.controls = tuple(container.pack_slaves())
        self.gap = max(0, gap)
        self._rows: list[ttk.Frame] = []
        self._job: str | None = None
        self._closed = False
        self._signature: tuple | None = None
        self._bindings = [
            (widget, widget.bind("<Configure>", self._schedule, add="+"))
            for widget in (container, *self.controls)
        ]
        container.bind("<Destroy>", self._on_destroy, add="+")
        self._schedule()

    def _schedule(self, _event: tk.Event | None = None) -> None:
        if not self._closed and self._job is None:
            self._job = self.container.after_idle(self._reflow)

    def _reflow(self) -> None:
        self._job = None
        if self._closed:
            return
        active = [
            widget for widget in self.controls
            if widget.winfo_exists() and widget.winfo_manager() == "pack"
        ]
        available = max(1, self.container.winfo_width())
        signature = (available, tuple((str(widget), widget.winfo_reqwidth()) for widget in active))
        if signature == self._signature:
            return
        self._signature = signature
        groups: list[list[tk.Misc]] = [[]]
        used = 0
        for widget in active:
            width = widget.winfo_reqwidth()
            if groups[-1] and used + self.gap + width > available:
                groups.append([])
                used = 0
            groups[-1].append(widget)
            used += width + (self.gap if used else 0)
        for widget in active:
            widget.pack_forget()
        while len(self._rows) < len(groups):
            self._rows.append(ttk.Frame(self.container))
        for index, row in enumerate(self._rows):
            row.pack_forget()
            if index >= len(groups) or not groups[index]:
                continue
            row.pack(fill="x", pady=(self.gap if index else 0, 0))
            for column, widget in enumerate(groups[index]):
                widget.pack(in_=row, side="left", padx=(self.gap if column else 0, 0))

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self.container:
            return
        self._closed = True
        if self._job is not None:
            self.container.after_cancel(self._job)
            self._job = None
        for widget, binding in self._bindings:
            if binding and widget.winfo_exists():
                widget.unbind("<Configure>", binding)


def wrap_controls(container: tk.Misc, *, gap: int = 4) -> FlowLayout:
    """Wrap the currently packed controls; never manage explicitly hidden ones.

    Controls retain their original parent, identity, tab order, state and
    callbacks. ``pack_forget`` continues to hide them; re-packing shows them.
    A control wider than the available row still needs a minimum or viewport
    policy from its owner rather than being silently compressed by this helper.
    """
    return FlowLayout(container, gap=gap)
