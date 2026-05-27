"""Shared font / widget pixel metrics for inline-width estimators.

Centralises the empirically-derived Tk font + widget metrics
(``px / char`` + a constant overhead per widget shape) consumed by
the fit-based layout classifiers across the GUI:

* :mod:`gui.scanner_block_editor` — :class:`_ConditionFrame`'s
  inline ↔ stacked flip (CLAUDE.md §7.19).
* :mod:`gui.indicator_dialog` — ``IndicatorDialog
  ._compute_max_cols_for_schema`` param-wrap column count.

The numbers only need to be in the right ballpark: both callers
apply a small hysteresis / discretisation buffer (the classifier's
80 px hysteresis; the column-count's integer floor) so an
off-by-twenty doesn't cause UI thrashing during resize drags.

Lives in its own module so future font / widget-style tuning
happens once — keep these in sync with the ttk default Segoe UI
9pt rendering used throughout the app.
"""
from __future__ import annotations

#: Pixels per character. Matches the ttk default Segoe UI 9pt
#: rendering on Windows; close enough on macOS / Linux that the
#: classifiers' hysteresis swallows the per-platform delta.
_CHAR_PX: int = 7

#: ``ttk.Combobox`` border + dropdown arrow overhead.
_COMBO_OVERHEAD: int = 25

#: ``ttk.Spinbox`` border + up/down arrow overhead.
_SPINBOX_OVERHEAD: int = 20

#: ``ttk.Checkbutton`` indicator + small inline label overhead.
_CHECKBOX_PX: int = 22

#: ``ttk.Entry`` border overhead.
_ENTRY_OVERHEAD: int = 12

#: Default per-gap horizontal padx allowance between widgets
#: packed/gridded into a single row.
_FRAME_PAD_PX: int = 6


__all__ = (
    "_CHAR_PX",
    "_COMBO_OVERHEAD",
    "_SPINBOX_OVERHEAD",
    "_CHECKBOX_PX",
    "_ENTRY_OVERHEAD",
    "_FRAME_PAD_PX",
)
