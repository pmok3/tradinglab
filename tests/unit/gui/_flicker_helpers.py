"""Shared helpers for the cross-dialog combobox-flicker meta-test.

The flicker antipattern (see ``indicator_dialog.spec.md`` + CLAUDE.md
§7.11 neighbourhood): a ``ttk.Combobox`` change handler bound to
``<<ComboboxSelected>>`` / ``<FocusOut>`` that tears down + recreates
widgets (and/or re-walks the whole window re-theming it) even when the
selected value did NOT change. On Windows ttk fires ``<FocusOut>`` when
a dropdown popdown is merely posted/dismissed, so *clicking* a dropdown
re-runs the heavy rebuild and the window visibly flickers.

The generic, reliably-detectable signature of that rebuild is **widget
identity churn**: destroying + recreating widgets gives the replacements
fresh auto-generated Tk path-names, so the recursive set of descendant
widget paths changes. An idempotent handler (no-op when the value is
unchanged) leaves the widget tree byte-for-byte identical.

This module centralises:

* :func:`collect_widget_paths` — recursive snapshot of the widget tree;
* :func:`fire_combobox_noop_events` — re-fire ``<<ComboboxSelected>>`` /
  ``<FocusOut>`` on every Combobox WITHOUT changing its value;
* :func:`assert_no_combobox_noop_rebuild` — the actual assertion used by
  the per-dialog meta-test cases.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def collect_widget_paths(root: tk.Misc) -> set[str]:
    """Recursively collect the Tk path-name of every descendant widget.

    Widget path-names (``str(widget)`` / ``widget._w``) are stable for a
    given widget instance but auto-generated fresh (``!frame2``,
    ``!combobox5`` …) whenever a widget is created. A destroy+recreate
    therefore shows up as a changed path set.
    """
    paths: set[str] = set()

    def _walk(w: tk.Misc) -> None:
        try:
            children = w.winfo_children()
        except tk.TclError:
            return
        for child in children:
            try:
                paths.add(str(child))
            except tk.TclError:
                pass
            _walk(child)

    _walk(root)
    return paths


def _all_comboboxes(root: tk.Misc) -> list[ttk.Combobox]:
    out: list[ttk.Combobox] = []

    def _walk(w: tk.Misc) -> None:
        try:
            children = w.winfo_children()
        except tk.TclError:
            return
        for child in children:
            if isinstance(child, ttk.Combobox):
                out.append(child)
            _walk(child)

    _walk(root)
    return out


def fire_combobox_noop_events(root: tk.Misc, *, repeats: int = 3) -> int:
    """Fire value-preserving combobox events on every Combobox descendant.

    For each ``ttk.Combobox`` in the tree (snapshotted up-front so a
    rebuild mid-walk can't trip iteration), fires ``<<ComboboxSelected>>``
    and ``<FocusOut>`` ``repeats`` times each WITHOUT mutating the
    widget's value. This mimics the two real spurious triggers:

    * re-picking the currently-selected item (``<<ComboboxSelected>>``);
    * the dropdown popdown posting/dismissing (Windows ttk emits
      ``<FocusOut>`` on the combobox entry).

    Returns the number of comboboxes exercised.
    """
    combos = _all_comboboxes(root)
    for cb in combos:
        for _ in range(repeats):
            for seq in ("<<ComboboxSelected>>", "<FocusOut>"):
                try:
                    cb.event_generate(seq)
                except tk.TclError:
                    # Widget may have been destroyed by a (buggy) handler
                    # mid-storm — that itself is the flicker we detect via
                    # the path-set diff, so swallow and continue.
                    break
    try:
        root.update_idletasks()
    except tk.TclError:
        pass
    return len(combos)


def assert_no_combobox_noop_rebuild(dialog: tk.Misc, *, label: str = "") -> int:
    """Assert no widget churn results from value-preserving combobox events.

    Snapshots the descendant-widget path set, fires the no-op combobox
    events, and asserts the path set is unchanged. A changed set means a
    handler tore down + recreated widgets in response to an event that
    did not change any value — i.e. the flicker bug.

    Returns the number of comboboxes exercised (for sanity-checking that
    the dialog actually had combobox surface area to protect).
    """
    try:
        dialog.update_idletasks()
    except tk.TclError:
        pass
    before = collect_widget_paths(dialog)
    n = fire_combobox_noop_events(dialog)
    after = collect_widget_paths(dialog)

    added = after - before
    removed = before - after
    if added or removed:
        ctx = f" [{label}]" if label else ""
        raise AssertionError(
            f"combobox no-op event rebuilt widgets{ctx}: a "
            f"<<ComboboxSelected>>/<FocusOut> with an UNCHANGED value "
            f"destroyed/recreated widgets (the dropdown-click flicker). "
            f"{len(removed)} removed, {len(added)} added. "
            f"Make the change handler idempotent (no-op when the resolved "
            f"value equals the current one). "
            f"sample added={sorted(added)[:3]} removed={sorted(removed)[:3]}"
        )
    return n
