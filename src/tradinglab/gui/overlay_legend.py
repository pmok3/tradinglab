"""Compatibility import for the retired Tk overlay legend.

The runtime legend is rendered by :mod:`gui.readout_legend` inside the
matplotlib readout.  The old ``OverlayLegend`` widget is intentionally gone;
the enumeration helper remains available here for callers that imported it
before the rendering migration.
"""

from __future__ import annotations

from .readout_legend import collect_overlay_configs

__all__ = ("collect_overlay_configs",)
