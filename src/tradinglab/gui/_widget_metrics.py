"""Shared font / widget pixel metrics for inline-width estimators.

Centralises the Tk font + widget metrics (``px / char`` + a constant
overhead per widget shape) consumed by the fit-based layout
classifiers across the GUI:

* :mod:`gui.scanner_block_editor` — :class:`_ConditionFrame`'s
  inline ↔ stacked flip (CLAUDE.md §7.19).
* :mod:`gui.indicator_dialog` — ``IndicatorDialog
  ._compute_max_cols_for_schema`` param-wrap column count.

The numbers only need to be in the right ballpark: both callers
apply a small hysteresis / discretisation buffer (the classifier's
80 px hysteresis; the column-count's integer floor) so an
off-by-twenty doesn't cause UI thrashing during resize drags.

Historically the values were hardcoded Windows Segoe UI 9pt
constants (audit #9 in ``files/generalization-audit.md``). They
now defer to runtime measurement of the active named Tk font via
:func:`metrics_for`, falling back to the Windows constants when
Tk is unavailable (module import during test discovery, headless
unit tests with no default root). The
:class:`_ConstantProxy` shims keep the historical
``from ._widget_metrics import _CHAR_PX``-style API alive without
forcing every consumer to migrate to ``metrics_for()["char_px"]``
explicitly.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

#: Hardcoded fallbacks — used ONLY when Tk isn't initialised yet
#: (e.g. module-level imports during test discovery) or when the
#: named font can't be resolved. Match the prior Windows Segoe UI
#: 9pt values so behavior is identical on Windows pre-measurement.
_CHAR_PX_FALLBACK: int = 7
_COMBO_OVERHEAD_FALLBACK: int = 25
_SPINBOX_OVERHEAD_FALLBACK: int = 20
_ENTRY_OVERHEAD_FALLBACK: int = 12

#: ``ttk.Checkbutton`` indicator + small inline label overhead. Not
#: derived from font metrics today — kept as a plain int because the
#: caller already adds a separate font-measured label width on top.
_CHECKBOX_PX: int = 22

#: Default per-gap horizontal padx allowance between widgets
#: packed/gridded into a single row. Pure layout constant — not
#: font-dependent.
_FRAME_PAD_PX: int = 6

_METRICS_CACHE: dict[str, dict[str, int]] = {}


def metrics_for(font_name: str = "TkDefaultFont") -> dict[str, int]:
    """Return font-measured widget metrics for ``font_name``.

    Returns a dict with four positive-integer keys:
    ``char_px``, ``combo_overhead``, ``spinbox_overhead``,
    ``entry_overhead``. The same dict instance is returned on
    repeat calls for the same ``font_name`` (one-shot measurement
    cache keyed by font name).

    Falls back to the Windows-Segoe-UI-9pt constants when Tk
    isn't initialised (no default root window, missing font) so
    module-level imports during test discovery don't crash.
    """
    cached = _METRICS_CACHE.get(font_name)
    if cached is not None:
        return cached
    try:
        f = tkfont.nametofont(font_name)
        # Average alphanumeric glyph width. Historical ``_CHAR_PX = 7``
        # was a Segoe-UI-9pt *average*, not the widest-glyph max — and
        # callers do ``N * _CHAR_PX`` against arbitrary strings, so an
        # average is the right model. ``f.measure("M")`` would
        # over-estimate by ~50% (M is the widest narrow-latin glyph).
        sample = "abcdefghijklmnopqrstuvwxyz0123456789"
        sample_px = int(f.measure(sample))
        if sample_px <= 0:
            raise RuntimeError("zero-width measurement")
        char_px = max(1, round(sample_px / len(sample)))
        # Per-widget overheads come from the font's line height
        # (linespace). The multipliers were calibrated against the
        # prior Windows Segoe UI 9pt constants (linespace ≈ 13 px):
        # 1.9× ≈ 25 (combo), 1.5× ≈ 20 (spinbox), 0.9× ≈ 12 (entry).
        # ``max(fallback, ...)`` so a tiny font never under-estimates
        # below the documented Windows baseline.
        linespace = int(f.metrics("linespace") or 13)
        result = {
            "char_px":          char_px,
            "combo_overhead":   max(_COMBO_OVERHEAD_FALLBACK, int(linespace * 1.9)),
            "spinbox_overhead": max(_SPINBOX_OVERHEAD_FALLBACK, int(linespace * 1.5)),
            "entry_overhead":   max(_ENTRY_OVERHEAD_FALLBACK, int(linespace * 0.9)),
        }
    except (tk.TclError, RuntimeError, AttributeError):
        result = {
            "char_px":          _CHAR_PX_FALLBACK,
            "combo_overhead":   _COMBO_OVERHEAD_FALLBACK,
            "spinbox_overhead": _SPINBOX_OVERHEAD_FALLBACK,
            "entry_overhead":   _ENTRY_OVERHEAD_FALLBACK,
        }
    _METRICS_CACHE[font_name] = result
    return result


def invalidate_metrics_cache() -> None:
    """Drop the measurement cache.

    Call after a theme / font change so the next :func:`metrics_for`
    re-measures against the new named-font state. The
    :class:`~tradinglab.gui.theme_controller.ThemeController` fires
    this on every theme apply.
    """
    _METRICS_CACHE.clear()


class _ConstantProxy:
    """Defer reads of a single ``metrics_for()`` key until used.

    Lets the module expose backwards-compatible
    ``_CHAR_PX`` / ``_COMBO_OVERHEAD`` / etc. names that look and
    behave like ints in every consumer (multiplication, addition,
    comparison, ``int(...)`` cast) while sourcing their value from
    :func:`metrics_for` at READ time — so a theme-driven cache
    invalidation is picked up on the very next use without
    consumers having to re-import or refetch a constant.
    """

    __slots__ = ("_key",)

    def __init__(self, key: str) -> None:
        self._key = key

    def _value(self) -> int:
        return metrics_for()[self._key]

    def __int__(self) -> int:
        return self._value()

    def __index__(self) -> int:
        return self._value()

    def __float__(self) -> float:
        return float(self._value())

    def __add__(self, other):
        return self._value() + other

    def __radd__(self, other):
        return other + self._value()

    def __sub__(self, other):
        return self._value() - other

    def __rsub__(self, other):
        return other - self._value()

    def __mul__(self, other):
        return self._value() * other

    def __rmul__(self, other):
        return other * self._value()

    def __floordiv__(self, other):
        return self._value() // other

    def __rfloordiv__(self, other):
        return other // self._value()

    def __truediv__(self, other):
        return self._value() / other

    def __rtruediv__(self, other):
        return other / self._value()

    def __eq__(self, other):
        return self._value() == other

    def __ne__(self, other):
        return self._value() != other

    def __lt__(self, other):
        return self._value() < other

    def __le__(self, other):
        return self._value() <= other

    def __gt__(self, other):
        return self._value() > other

    def __ge__(self, other):
        return self._value() >= other

    def __hash__(self):
        return hash(self._value())

    def __repr__(self) -> str:
        return f"_widget_metrics.{self._key}({self._value()})"


#: Pixels per character. Was a hardcoded ``7`` (Windows Segoe UI 9pt);
#: now sourced from :func:`metrics_for` at read time.
_CHAR_PX = _ConstantProxy("char_px")

#: ``ttk.Combobox`` border + dropdown arrow overhead. Was ``25``.
_COMBO_OVERHEAD = _ConstantProxy("combo_overhead")

#: ``ttk.Spinbox`` border + up/down arrow overhead. Was ``20``.
_SPINBOX_OVERHEAD = _ConstantProxy("spinbox_overhead")

#: ``ttk.Entry`` border overhead. Was ``12``.
_ENTRY_OVERHEAD = _ConstantProxy("entry_overhead")


__all__ = (
    "_CHAR_PX",
    "_COMBO_OVERHEAD",
    "_SPINBOX_OVERHEAD",
    "_CHECKBOX_PX",
    "_ENTRY_OVERHEAD",
    "_FRAME_PAD_PX",
    "metrics_for",
    "invalidate_metrics_cache",
)
