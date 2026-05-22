"""ChartStack — vertical strip of miniature chart cards.

Re-exports the public surface used by ``ChartApp`` (and unit
tests). M1 ships the wireframe only — empty axes labeled with
resolved binding symbols. See ``__init__.spec.md`` for the locked
spec inherited from the chartstack-spec synthesis.
"""

from __future__ import annotations

from .binding import BindingMode, CardBinding, resolve_bindings
from .panel import ChartStackPanel

__all__ = [
    "ChartStackPanel",
    "BindingMode",
    "CardBinding",
    "resolve_bindings",
]
