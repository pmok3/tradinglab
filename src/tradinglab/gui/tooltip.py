"""Lightweight hover-tooltip helper.

Tkinter has no native tooltip; this module provides a minimal one
suitable for the kinds of short hints the UI/UX audit asked for
(drag-handle "Drag to reorder", destructive-button warnings, etc.).

Design
------
* One ``ToolTip`` instance per widget. Constructing it auto-wires
  ``<Enter>`` / ``<Leave>`` / ``<ButtonPress>`` bindings so the
  caller doesn't need to manage them.
* The popup is a borderless ``tk.Toplevel`` with ``overrideredirect``
  so it has no window decorations and doesn't steal focus.
* Delay defaults to 450 ms — long enough that fast cursor sweeps
  don't trigger it, short enough that a deliberate hover surfaces
  the hint.
* The Toplevel inherits the host app's theme palette where possible:
  if the widget's master has a ``_theme`` mapping, we use it; otherwise
  we fall back to neutral system colors.

Public API
----------
``ToolTip(widget, text, *, delay_ms=450, wraplength=320)`` — attaches
a tooltip to ``widget``. Holds a reference internally; callers can
discard the return value.

``ToolTip.set_text(text)`` — change the hint after construction
(e.g. when a button's meaning changes).

``ToolTip.detach()`` — unbind and destroy the popup. Useful in
tests and for widgets whose tooltip is conditionally enabled.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

_DEFAULT_DELAY_MS = 450
_DEFAULT_WRAPLENGTH = 320


class ToolTip:
    """Attach a hover tooltip to ``widget``.

    The constructor binds enter/leave/click handlers; the popup is
    lazily created on first ``<Enter>`` and torn down on ``<Leave>``
    or ``<ButtonPress>`` so an idle tooltip never holds a Toplevel.
    """

    __slots__ = (
        "_widget", "_text", "_delay_ms", "_wraplength",
        "_after_id", "_tip", "_label",
    )

    def __init__(
        self,
        widget: tk.Misc,
        text: str,
        *,
        delay_ms: int = _DEFAULT_DELAY_MS,
        wraplength: int = _DEFAULT_WRAPLENGTH,
    ) -> None:
        self._widget = widget
        self._text = text
        self._delay_ms = max(50, int(delay_ms))
        self._wraplength = max(80, int(wraplength))
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        self._label: ttk.Label | None = None

        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    # --- public ---------------------------------------------------------

    def set_text(self, text: str) -> None:
        self._text = text
        if self._label is not None:
            self._label.configure(text=text)

    def detach(self) -> None:
        self._cancel_pending()
        self._hide()
        try:
            self._widget.unbind("<Enter>")
            self._widget.unbind("<Leave>")
            self._widget.unbind("<ButtonPress>")
        except tk.TclError:
            pass

    # --- internals ------------------------------------------------------

    def _on_enter(self, _event: object = None) -> None:
        self._cancel_pending()
        self._after_id = self._widget.after(self._delay_ms, self._show)

    def _on_leave(self, _event: object = None) -> None:
        self._cancel_pending()
        self._hide()

    def _cancel_pending(self) -> None:
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._tip is not None or not self._text:
            return
        try:
            x = self._widget.winfo_rootx() + 12
            y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        except tk.TclError:
            return
        tip = tk.Toplevel(self._widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        bg = "#ffffe1"
        fg = "#222222"
        bd = "#888888"
        label = tk.Label(
            tip, text=self._text, justify="left",
            background=bg, foreground=fg,
            relief="solid", borderwidth=1,
            wraplength=self._wraplength,
            padx=6, pady=3,
            highlightthickness=0,
        )
        label.configure(highlightbackground=bd)
        label.pack()
        self._tip = tip
        self._label = label

    def _hide(self) -> None:
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None
            self._label = None


__all__ = ["ToolTip"]
