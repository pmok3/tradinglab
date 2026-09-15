"""Behavioral width probes shared by unit and smoke window cases.

Only allocated, mapped geometry is evidence. Data viewports may compress;
form controls may not disappear behind a parent or a vertical-only Canvas.
Probe callers select notebook pages and exercise rebuilt sections explicitly.
"""
from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from tkinter import font, ttk


@contextmanager
def mapped_window(window: tk.Misc) -> Iterator[tk.Misc]:
    """Map the existing interpreter's host chain; restore it without resizing."""
    hosts: list[tuple[tk.Wm, str]] = []
    widget: tk.Misc | None = window
    while widget is not None:
        if isinstance(widget, tk.Wm):
            hosts.append((widget, widget.state()))
        widget = widget.master
    try:
        for host, _ in reversed(hosts):
            host.deiconify()
        window.update()
        assert window.winfo_ismapped() and window.winfo_width() > 1, (
            f"{window}: width probe requires a mapped, non-1px window"
        )
        yield window
    finally:
        for host, state in hosts:
            if host.winfo_exists():
                if state == "withdrawn":
                    host.withdraw()
                elif state == "iconic":
                    host.iconify()


@contextmanager
def enlarged_fonts(root: tk.Misc, size: int = 16) -> Iterator[None]:
    """Stress named UI fonts in the shared interpreter, restoring every size."""
    names = ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont")
    fonts = [font.nametofont(name, root=root) for name in names]
    sizes = [item.actual("size") for item in fonts]
    try:
        for item in fonts:
            item.configure(size=size)
        root.update_idletasks()
        yield
    finally:
        for item, original in zip(fonts, sizes, strict=True):
            item.configure(size=original)
        root.update_idletasks()


def _descendants(widget: tk.Misc) -> Iterator[tk.Misc]:
    for child in widget.winfo_children():
        if not isinstance(child, tk.Wm):
            yield child
            yield from _descendants(child)


def _description(widget: tk.Misc) -> str:
    text = str(widget.cget("text")) if "text" in widget.keys() else ""
    return f"{widget} {widget.winfo_class()} {text[:65]!r}"


def _allocated_parent(widget: tk.Misc) -> tk.Misc | None:
    manager = widget.winfo_manager()
    if manager in ("pack", "grid", "place"):
        return getattr(widget, f"{manager}_info")().get("in", widget.master)
    return widget.master


def _explicitly_hidden(widget: tk.Misc, window: tk.Misc) -> bool:
    current: tk.Misc | None = widget
    while current is not None and current is not window:
        parent = _allocated_parent(current)
        if not current.winfo_manager():
            return True
        if isinstance(parent, ttk.Notebook) and parent.select() != str(current):
            return True
        if isinstance(parent, tk.Canvas) and str(parent.cget("yscrollcommand")):
            for item in parent.find_all():
                if parent.type(item) == "window" and parent.itemcget(item, "window") == str(current):
                    bounds = parent.bbox(item)
                    if bounds and (bounds[3] <= parent.canvasy(0)
                                   or bounds[1] >= parent.canvasy(parent.winfo_height())):
                        return True
        current = parent
    return False


def _horizontal_scroll(widget: tk.Canvas | ttk.Treeview, window: tk.Misc) -> bool:
    """Prove both scrollbar directions are wired, not merely that one exists."""
    start = tuple(map(float, widget.xview()))
    if start[0] <= 0 and start[1] >= 1:
        return False
    bars = [
        bar for bar in _descendants(window)
        if isinstance(bar, (tk.Scrollbar, ttk.Scrollbar)) and bar.winfo_ismapped()
        and not _explicitly_hidden(bar, window)
        and str(bar.cget("orient")) == "horizontal"
    ]

    def agrees(first, second):
        return all(abs(a - b) < 0.01 for a, b in zip(first, second, strict=True))

    try:
        # Identify the associated thumb by moving only the tested viewport.
        # Calling unrelated scrollbar commands would mutate other app views.
        widget.xview_moveto(0)
        window.update_idletasks()
        left = tuple(map(float, widget.xview()))
        thumbs = {bar: tuple(map(float, bar.get())) for bar in bars}
        canvas_left = widget.canvasx(0) if isinstance(widget, tk.Canvas) else 0
        widget.xview_moveto(1)
        window.update_idletasks()
        right = tuple(map(float, widget.xview()))
        if isinstance(widget, tk.Canvas):
            canvas_right = widget.canvasx(widget.winfo_width())
            for item in widget.find_all():
                if widget.type(item) == "window":
                    bounds = widget.bbox(item)
                    if bounds and (bounds[0] < canvas_left - 2 or bounds[2] > canvas_right + 2):
                        return False
        for bar in bars:
            if not agrees(left, thumbs[bar]) or not agrees(right, tuple(map(float, bar.get()))):
                continue
            if right[0] <= left[0] or thumbs[bar] == tuple(map(float, bar.get())):
                continue
            command = str(bar.cget("command"))
            if not command or not str(widget.cget("xscrollcommand")):
                continue
            widget.tk.call(*widget.tk.splitlist(command), "moveto", 0)
            window.update_idletasks()
            left = tuple(map(float, widget.xview()))
            left_thumb = tuple(map(float, bar.get()))
            widget.tk.call(*widget.tk.splitlist(command), "moveto", 1)
            window.update_idletasks()
            right = tuple(map(float, widget.xview()))
            right_thumb = tuple(map(float, bar.get()))
            if (
                left[0] <= 0.001 and right[1] >= 0.999
                and right[0] > left[0]
                and agrees(left, left_thumb) and agrees(right, right_thumb)
            ):
                return True
    finally:
        widget.xview_moveto(start[0])
        window.update_idletasks()
    return False


def assert_window_width(
    window: tk.Misc,
    *,
    elided_labels: Mapping[tk.Misc, str] | None = None,
) -> int:
    """Assert visible controls fit their allocation and all clipping ancestors.

    Text/Entry/Combobox/Listbox/plot Canvas are intentional data viewports,
    not natural-width forms. Treeview columns additionally need working
    horizontal scrolling if they overflow. ``elided_labels`` is restricted to
    named, mapped labels with a specific intentional-elision policy; their
    allocated rectangles still must fit. Returns checked-control count.
    """
    window.update_idletasks()
    assert window.winfo_ismapped() and window.winfo_width() > 1, (
        f"{window}: width probe requires a mapped, non-1px window"
    )
    elided_labels = elided_labels or {}
    children = list(_descendants(window))
    for widget, reason in elided_labels.items():
        assert widget in children and widget.winfo_ismapped() and not _explicitly_hidden(widget, window), (
            f"Stale elided label: {widget}"
        )
        assert isinstance(widget, (tk.Label, ttk.Label)) and reason.strip(), (
            f"Elision needs a label and a precise reason: {widget}"
        )
    scrollable: set[tk.Misc] = set()
    for widget in children:
        if not widget.winfo_ismapped() or _explicitly_hidden(widget, window):
            continue
        if isinstance(widget, ttk.Treeview):
            view = tuple(map(float, widget.xview()))
            assert view[0] <= 0 and view[1] >= 1 or _horizontal_scroll(widget, window), (
                f"{_description(widget)}: columns need reachable horizontal scrolling"
            )
        if isinstance(widget, tk.Canvas) and any(
            widget.type(item) == "window" for item in widget.find_all()
        ):
            if _horizontal_scroll(widget, window):
                scrollable.add(widget)

    text_controls = (tk.Label, tk.Button, tk.Checkbutton, tk.Radiobutton,
                     ttk.Label, ttk.Button, ttk.Checkbutton, ttk.Radiobutton, ttk.Menubutton)
    checked = 0
    errors: list[str] = []
    for widget in children:
        if _explicitly_hidden(widget, window):
            continue
        if not widget.winfo_ismapped():
            if (
                widget.winfo_manager() in ("pack", "grid", "place")
                and not _explicitly_hidden(widget, window)
                and isinstance(widget, text_controls)
                and (str(widget.cget("text")) or "image" in widget.keys() and widget.cget("image"))
            ):
                errors.append(f"{_description(widget)}: managed control is not allocated/mapped")
            continue
        width = widget.winfo_width()
        left = widget.winfo_rootx()
        right = left + width
        ancestor = _allocated_parent(widget)
        while ancestor is not None:
            if ancestor in scrollable:
                break
            aleft = ancestor.winfo_rootx()
            aright = aleft + ancestor.winfo_width()
            if left < aleft - 2 or right > aright + 2:
                errors.append(
                    f"{_description(widget)}: bounds [{left}, {right}] outside "
                    f"{ancestor.winfo_class()} [{aleft}, {aright}]"
                )
                break
            if ancestor is window:
                break
            ancestor = _allocated_parent(ancestor)
        if isinstance(widget, text_controls):
            checked += 1
            if widget not in elided_labels and (
                str(widget.cget("text")) or "image" in widget.keys() and widget.cget("image")
            ) and width + 2 < widget.winfo_reqwidth():
                errors.append(
                    f"{_description(widget)}: usable width {width} < "
                    f"wrapped request {widget.winfo_reqwidth()}"
                )
        elif not widget.winfo_children() and not isinstance(
            widget, (tk.Frame, tk.LabelFrame, ttk.Frame, ttk.LabelFrame, ttk.Separator)
        ):
            checked += 1
            if width <= 1:
                errors.append(f"{_description(widget)}: unusable {width}px viewport")
    assert checked, f"{window}: no mapped controls were checked"
    assert not errors, f"{window.winfo_class()} width regression:\n" + "\n".join(errors[:20])
    return checked
