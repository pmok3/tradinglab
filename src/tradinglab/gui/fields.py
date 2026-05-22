"""Shared labeled-field widgets for modal dialogs.

The UI/UX audit (May 2026) called out that every dialog assembles its
own ``Label + Entry / Combobox + error-label`` triplet by hand with
slightly different padding, grid columns, label widths, and error
foreground colors. The visual drift is small per-dialog but cumulative
across 12+ dialogs, and the per-dialog ``_build_*`` code shows the
seams — every dialog has the same Tk-grid bookkeeping (`row`,
`column`, `sticky`, `pady`).

This module hosts a small toolkit of widgets that compose into a
``[label]  [content]  [error]`` row with consistent spacing. The two
public concerns are:

1.  **Visual consistency** — same label column width, same pad, same
    error color. Drop-in across IndicatorDialog, EntriesDialog,
    ExitsDialog, SandboxStartDialog, CredentialsDialog.
2.  **Composability** — for dialogs that build complex per-row layouts
    (CredentialsDialog with its show/hide eyeball, ExitsDialog with
    its per-kind param grid), :class:`FieldRow` exposes a ``.content``
    slot the caller fills with arbitrary widgets, instead of imposing
    a single hard-coded Entry / Combobox shape.

Layout
------
Each row is an internal ``ttk.Frame`` with three columns via grid::

    column 0           column 1                column 2
    [Label:]          [<content widget(s)>]    [error text]
    (right-aligned,   (sticky=ew, expand 1)    (foreground = ERROR_RED)
     fixed width)

Rows pack vertically. Use ``.pack(fill="x")`` from the caller. The
``content`` frame is exposed via ``row.content`` so the caller can grid
multiple widgets inside one row (e.g. an Entry + a "show" checkbox).

Two flavors of factory exist:

* :class:`FieldRow` — bare row, content frame is yours to fill.
* :func:`LabeledEntry` / :func:`LabeledCombobox` / :func:`LabeledCheckbutton`
  — convenience helpers that build a row + one specific widget. Return
  ``(row, widget)`` so callers can grab the variable / configure focus.

Error labels
------------
Every row owns a ``tk.StringVar`` (``row.error_var``) bound to a small
right-side label. Callers set it during validation:
``row.set_error("must be positive")``. ``row.clear_error()`` wipes it.
This eliminates the per-dialog dictionary of error-vars (see
``entries_dialog.py::_field_errors``).

The helpers never call ``.pack()`` themselves — they return the row so
the caller decides layout. This keeps them composable with both
top-level dialogs (vertical pack) and tab containers (grid in a
notebook page).
"""
from __future__ import annotations

import tkinter as tk
from collections.abc import Iterable
from tkinter import ttk
from typing import Any, Union

from .colors import ERROR_RED

# Tk default font is 9-10 pt depending on platform; 16 chars covers all
# label texts we currently use ("Strategy id:", "Direction:",
# "Cooldown (s):", "Position size:", "Arm window start:") without
# wrapping. Bump cautiously — wider labels eat content width in narrow
# dialogs like CredentialsDialog.
_DEFAULT_LABEL_WIDTH = 18
_ROW_PADY = (2, 2)
_LABEL_PADX = (0, 8)
_ERROR_PADX = (8, 0)


class FieldRow(ttk.Frame):
    """Single ``[label] [content] [error]`` row in a vertical form.

    Public surface:
        ``content`` — the middle ``ttk.Frame`` you populate with one
            or more widgets. Use ``ttk.Entry(row.content)`` and pack /
            grid normally.
        ``error_var`` — ``tk.StringVar`` mirrored by the right-side
            error label. Default text is "".
        ``label`` — the ``ttk.Label`` instance. Exposed in case the
            caller wants to mutate its text dynamically (e.g.
            "Cooldown (s):" → "Cooldown (ms):").

    Methods:
        ``set_error(msg)`` — show ``msg`` in the error slot.
        ``clear_error()`` — hide the error.

    The row uses ``grid`` internally with three columns; the content
    column expands. The row itself is gridless and is intended to be
    ``.pack()``ed or ``.grid()``ed by the caller.
    """

    def __init__(
        self,
        parent: tk.Misc,
        label: str,
        *,
        label_width: int = _DEFAULT_LABEL_WIDTH,
        error_var: tk.StringVar | None = None,
        **frame_kwargs: Any,
    ) -> None:
        super().__init__(parent, **frame_kwargs)
        self.columnconfigure(1, weight=1)

        # Trailing colon on labels is conventional; tolerate callers
        # who already added one (avoid "Name::").
        text = label.rstrip(":").rstrip() + ":" if label.strip() else ""
        self.label = ttk.Label(
            self, text=text, width=label_width, anchor="e",
        )
        self.label.grid(
            row=0, column=0, sticky="e", padx=_LABEL_PADX, pady=_ROW_PADY,
        )

        # Content is a frame so callers can stuff multiple widgets in
        # (Entry + show-checkbox, Combobox + edit-button, etc.).
        self.content = ttk.Frame(self)
        self.content.grid(
            row=0, column=1, sticky="ew", pady=_ROW_PADY,
        )

        self.error_var = error_var if error_var is not None else tk.StringVar(value="")
        self._error_label = ttk.Label(
            self, textvariable=self.error_var, foreground=ERROR_RED,
        )
        self._error_label.grid(
            row=0, column=2, sticky="w", padx=_ERROR_PADX, pady=_ROW_PADY,
        )

    # ------------------------------------------------------------------
    # Error slot helpers
    # ------------------------------------------------------------------
    def set_error(self, msg: str) -> None:
        """Show ``msg`` (truthy) or clear (empty/None) the error label."""
        self.error_var.set(str(msg) if msg else "")

    def clear_error(self) -> None:
        """Equivalent to ``set_error("")``."""
        self.error_var.set("")


# ---------------------------------------------------------------------------
# Convenience builders
# ---------------------------------------------------------------------------
def LabeledEntry(
    parent: tk.Misc,
    label: str,
    *,
    textvariable: tk.Variable | None = None,
    show: str | None = None,
    width: int | None = None,
    label_width: int = _DEFAULT_LABEL_WIDTH,
    error_var: tk.StringVar | None = None,
    state: str | None = None,
    **entry_kwargs: Any,
) -> tuple[FieldRow, ttk.Entry]:
    """Build a :class:`FieldRow` with one ``ttk.Entry`` inside.

    Returns ``(row, entry)``. Caller is responsible for ``row.pack()``
    or ``row.grid()`` placement.

    ``show`` (e.g. ``"*"``) masks the entry for password-style fields.
    ``width`` controls the entry's character width; ``None`` lets it
    expand to fill the content slot.
    """
    row = FieldRow(parent, label, label_width=label_width, error_var=error_var)
    kwargs: dict = dict(entry_kwargs)
    if textvariable is not None:
        kwargs["textvariable"] = textvariable
    if show is not None:
        kwargs["show"] = show
    if width is not None:
        kwargs["width"] = int(width)
    entry = ttk.Entry(row.content, **kwargs)
    if state is not None:
        entry.configure(state=state)
    entry.pack(side="left", fill="x", expand=True)
    return row, entry


def LabeledCombobox(
    parent: tk.Misc,
    label: str,
    *,
    textvariable: tk.Variable | None = None,
    values: Iterable[str] = (),
    width: int | None = None,
    label_width: int = _DEFAULT_LABEL_WIDTH,
    state: str = "readonly",
    error_var: tk.StringVar | None = None,
    **combo_kwargs: Any,
) -> tuple[FieldRow, ttk.Combobox]:
    """Build a :class:`FieldRow` with one ``ttk.Combobox`` inside.

    ``state="readonly"`` is the dropdown-only default. Pass ``"normal"``
    for editable combos (the dialog needs to validate user-typed
    values).
    """
    row = FieldRow(parent, label, label_width=label_width, error_var=error_var)
    kwargs: dict = dict(combo_kwargs)
    if textvariable is not None:
        kwargs["textvariable"] = textvariable
    if values is not None:
        kwargs["values"] = list(values)
    if width is not None:
        kwargs["width"] = int(width)
    combo = ttk.Combobox(row.content, state=state, **kwargs)
    combo.pack(side="left", fill="x", expand=True)
    return row, combo


def LabeledCheckbutton(
    parent: tk.Misc,
    label: str,
    *,
    variable: tk.BooleanVar | None = None,
    text: str | None = None,
    label_width: int = _DEFAULT_LABEL_WIDTH,
    error_var: tk.StringVar | None = None,
    **chk_kwargs: Any,
) -> tuple[FieldRow, ttk.Checkbutton]:
    """Build a :class:`FieldRow` with one ``ttk.Checkbutton`` inside.

    ``label`` is the left-side row label (e.g. "Enabled:"); ``text``
    is the checkbutton's own caption (e.g. "Strategy is active"). If
    ``text`` is None the checkbutton has no caption and just shows a
    naked checkbox.
    """
    row = FieldRow(parent, label, label_width=label_width, error_var=error_var)
    kwargs: dict = dict(chk_kwargs)
    if variable is not None:
        kwargs["variable"] = variable
    if text is not None:
        kwargs["text"] = text
    chk = ttk.Checkbutton(row.content, **kwargs)
    chk.pack(side="left")
    return row, chk


def LabeledSpinbox(
    parent: tk.Misc,
    label: str,
    *,
    textvariable: tk.Variable | None = None,
    from_: float = 0,
    to: float = 100,
    increment: float = 1,
    width: int | None = None,
    label_width: int = _DEFAULT_LABEL_WIDTH,
    error_var: tk.StringVar | None = None,
    **spin_kwargs: Any,
) -> tuple[FieldRow, ttk.Spinbox]:
    """Build a :class:`FieldRow` with one ``ttk.Spinbox`` inside.

    Use for numeric fields where ± step buttons aid input (cooldowns,
    max-fires counters). Pass ``increment`` as ``0.01`` for currency
    or ``1`` for integer counts.
    """
    row = FieldRow(parent, label, label_width=label_width, error_var=error_var)
    kwargs: dict = dict(spin_kwargs)
    if textvariable is not None:
        kwargs["textvariable"] = textvariable
    if width is not None:
        kwargs["width"] = int(width)
    spin = ttk.Spinbox(
        row.content,
        from_=from_, to=to, increment=increment,
        **kwargs,
    )
    spin.pack(side="left")
    return row, spin


__all__ = [
    "FieldRow",
    "LabeledEntry",
    "LabeledCombobox",
    "LabeledCheckbutton",
    "LabeledSpinbox",
]


# Avoid unused-import lint flag — ``Union`` was reserved for a future
# variant of LabeledEntry that accepts ``textvariable=Union[StringVar,
# IntVar]`` with explicit coercion.
_ = Union
