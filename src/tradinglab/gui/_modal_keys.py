"""Shared `<Escape>` / `<Return>` key-binding helper for modal dialogs.

The UI/UX audit (May 2026) called out that ESC-to-cancel and Enter-to-
submit are inconsistently bound across the 12+ modal Toplevels in the
GUI. This helper exists so each dialog can opt in with a single call
and get the same semantics:

* ``<Escape>`` invokes ``cancel`` (commonly ``destroy`` or
  ``_on_cancel`` / ``_on_close``).
* ``<Return>`` invokes ``primary`` UNLESS the keystroke originated
  from a multi-line ``tk.Text`` widget (where Enter should keep
  inserting newlines).

A small number of dialogs override the close handler to enforce
mandatory journaling (e.g. ``PostTradeReviewDialog``). Those callers
should NOT bind Escape via this helper; the helper only enforces the
default-discard semantic.

Usage
-----
::

    from ._modal_keys import bind_modal_keys

    bind_modal_keys(self, cancel=self._on_cancel, primary=self._on_save)

Either callback may be ``None`` to skip that binding.
"""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable


def _focus_is_multiline_text(root: tk.Misc) -> bool:
    """Return ``True`` if the currently-focused widget is a ``tk.Text``.

    Used by the ``<Return>`` handler to ignore Enter keystrokes when
    the user is composing a multi-line journal entry (PreTrade thesis,
    PostTrade review, etc.). ``ttk.Entry`` widgets are single-line so
    Enter on them correctly submits the form.
    """
    try:
        focused = root.focus_get()
    except (tk.TclError, KeyError):
        return False
    if focused is None:
        return False
    if isinstance(focused, tk.Text):
        return True
    cls = focused.winfo_class()
    return cls == "Text"


def bind_modal_keys(
    toplevel: tk.Misc,
    *,
    cancel: Callable[[], None] | None = None,
    primary: Callable[[], None] | None = None,
) -> None:
    """Wire ``<Escape>`` → ``cancel`` and ``<Return>`` → ``primary``.

    ``<Return>`` is suppressed when a ``tk.Text`` widget has focus,
    so Enter still inserts newlines in multi-line journal fields.

    Both callbacks are wrapped so exceptions surface via Tk's
    ``report_callback_exception`` rather than crashing the dialog.
    """
    if cancel is not None:
        def _on_escape(_event: object) -> str:
            cancel()
            return "break"
        toplevel.bind("<Escape>", _on_escape)

    if primary is not None:
        def _on_return(_event: object) -> str:
            if _focus_is_multiline_text(toplevel):
                return ""
            primary()
            return "break"
        toplevel.bind("<Return>", _on_return)
        toplevel.bind("<KP_Enter>", _on_return)


__all__ = ["bind_modal_keys"]
