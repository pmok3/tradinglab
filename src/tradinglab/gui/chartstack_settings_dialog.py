"""``ChartStack Settings…`` popup — per-slot fixed-preset editor.

A small modal :class:`BaseModalDialog` reachable from
View → ChartStack Settings… that edits the per-slot
``chartstack.fixed_preset_symbols`` list. Saving persists via
:mod:`tradinglab.settings` and flips ``chartstack.binding.mode``
to ``"FIXED_PRESET"`` so the user's picks become authoritative
even when they were previously on ``HYBRID``.

Audit tag: ``chartstack-fixed-preset``.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from .. import settings as _settings
from ._modal_base import BaseModalDialog, protect_combobox_wheel
from .chartstack import settings_adapter as _adapter

#: Hardcoded default preset — top of the stack to the bottom.
#: Mirrors :data:`chartstack.settings_adapter.DEFAULTS["chartstack.fixed_preset_symbols"]`
#: by intent; duplicated here so the Reset button doesn't need to
#: round-trip through the settings store.
DEFAULT_PRESET: tuple[str, ...] = ("SPY", "QQQ", "VXX")


class ChartStackSettingsDialog(BaseModalDialog):
    """Per-slot fixed-preset symbol editor.

    Construction reads the current preset + card count from
    settings via :mod:`tradinglab.gui.chartstack.settings_adapter`,
    builds one ``ttk.Entry`` per slot, and pre-populates them.

    Save: writes the entries' upper-cased contents back to
    :data:`chartstack.fixed_preset_symbols`, switches
    :data:`chartstack.binding.mode` to ``"FIXED_PRESET"`` if not
    already, and (if the owner has a ``_chartstack`` attribute
    pointing at the live panel) calls ``panel.refresh()`` so the
    cards re-bind immediately.

    Cancel: leaves all settings untouched.

    Reset to Defaults: rewrites the entry contents (NOT the
    persisted settings) to :data:`DEFAULT_PRESET`, padded with
    blanks to ``card_count``. The user still needs to click Save
    to commit.
    """

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(
            parent,
            title="ChartStack Settings",
            geometry_key="dlg.chartstack_settings",
            default_geometry="340x260",
            resizable=(False, False),
        )
        self._parent_ref: Any = parent

        n = _adapter.card_count()
        seed = _adapter.fixed_preset_symbols()  # already padded to n

        body = ttk.Frame(self)
        body.pack(padx=12, pady=(12, 6), fill="both", expand=True)
        ttk.Label(
            body,
            text=(
                "Per-slot symbols for the ChartStack panel.\n"
                "Slot 1 sits at the top of the stack."
            ),
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self._entries: list[ttk.Entry] = []
        for i in range(n):
            ttk.Label(body, text=f"Slot {i + 1}:").grid(
                row=i + 1, column=0, sticky="e", padx=(0, 6), pady=2,
            )
            ent = ttk.Entry(body, width=14)
            ent.grid(row=i + 1, column=1, sticky="ew", pady=2)
            ent.insert(0, seed[i])
            self._entries.append(ent)

        body.columnconfigure(1, weight=1)

        footer = ttk.Frame(self)
        footer.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(
            footer, text="Reset to Defaults",
            command=self._on_reset_to_defaults,
        ).pack(side="left")
        ttk.Button(footer, text="Cancel", command=self._on_cancel).pack(
            side="right", padx=(6, 0),
        )
        ttk.Button(footer, text="Save", command=self._on_save).pack(
            side="right",
        )

        # CLAUDE.md §7.11 — forward-compat (no Comboboxes today, but
        # idempotent + future-proof if a binding-mode dropdown is
        # added later).
        protect_combobox_wheel(self)
        self._finalize_modal(primary=self._on_save, cancel=self._on_cancel)

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        """Persist the entry contents + flip binding mode + refresh."""
        symbols = [(e.get() or "").strip().upper() for e in self._entries]
        _settings.set("chartstack.fixed_preset_symbols", symbols)
        _settings.set("chartstack.binding.mode", "FIXED_PRESET")
        # Refresh the live panel if mounted so the user sees the new
        # bindings instantly. Owner is duck-typed: any object with a
        # ``_chartstack.refresh()`` method counts.
        panel = getattr(self._parent_ref, "_chartstack", None)
        if panel is not None:
            try:
                panel.refresh()
            except Exception:  # noqa: BLE001 — never let refresh crash Save
                pass
        self._dismiss()

    def _on_cancel(self) -> None:
        self._dismiss()

    def _on_reset_to_defaults(self) -> None:
        """Rewrite entry contents (not settings) to DEFAULT_PRESET."""
        for i, entry in enumerate(self._entries):
            entry.delete(0, "end")
            value = DEFAULT_PRESET[i] if i < len(DEFAULT_PRESET) else ""
            entry.insert(0, value)

    def _dismiss(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


def open_chartstack_settings(parent: tk.Misc) -> ChartStackSettingsDialog:
    """Open the ChartStack Settings popup; return the constructed
    dialog (caller may call ``wait_window`` if it wants modal
    blocking, but the dialog itself runs in its own
    ``BaseModalDialog`` ``grab_set`` so the View menu callback can
    fire-and-forget). The View menu callback in
    :class:`tradinglab.app.ChartApp` uses this fire-and-forget
    pattern."""
    return ChartStackSettingsDialog(parent)


__all__ = [
    "DEFAULT_PRESET",
    "ChartStackSettingsDialog",
    "open_chartstack_settings",
]
