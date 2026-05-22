"""Per-slot card facade — thin wrapper over one matplotlib ``Axes``.

Cards do **not** own their own ``FigureCanvasTkAgg`` — the §5.1
render strategy mandates a single shared :class:`~matplotlib.figure.Figure`
per panel with N stacked axes (option A). :class:`CardWidget` is
the per-slot view-model that pairs an Axes with a
:class:`CardController` and the current binding, and exposes the
methods the panel calls on a refresh (``set_binding``,
``set_focus_indicator``).

M1: only ``set_binding`` triggers a redraw (placeholder text);
``set_focus_indicator`` is a stub for the M2 click-to-promote
focus ring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .controller import CardController
from .render import draw_card_placeholder

if TYPE_CHECKING:  # pragma: no cover - typing only
    from matplotlib.axes import Axes

    from .binding import CardBinding
    from .panel import ChartStackPanel


class CardWidget:
    """One card slot — owns no Tk widgets, only an :class:`Axes` + state."""

    def __init__(
        self,
        owner_panel: "ChartStackPanel",
        slot_index: int,
        ax: "Axes",
    ) -> None:
        self.owner_panel = owner_panel
        self.slot_index = int(slot_index)
        self.ax = ax
        self.controller = CardController(slot_index, owner_app=getattr(owner_panel, "owner", None))
        self.binding: Optional["CardBinding"] = None
        self._focused = False

    @property
    def bbox(self):
        """Return the Axes' figure-coordinate bbox (cached lazily by mpl)."""
        return self.ax.get_position()

    def set_binding(self, binding: "CardBinding | None") -> None:
        """Update the held binding + redraw the placeholder.

        M2 will replace ``draw_card_placeholder`` with the real
        sparkline draw via ``draw_card_sparkline`` once the series
        cache is populated.

        Forwards the owning panel's resolved theme palette
        (``owner_panel._theme_palette``) so the placeholder text /
        axes facecolor pick up dark-mode colours even before the
        first sparkline render. Falls back to no-theme defaults
        when the panel hasn't received an ``apply_theme`` cascade
        yet (typical for the M1 unit-test path that constructs a
        bare panel and binds without a parent ``ChartApp``).
        """
        self.binding = binding
        self.controller.bind(binding)
        theme = getattr(self.owner_panel, "_theme_palette", None)
        draw_card_placeholder(self.ax, binding, theme=theme)

    def set_focus_indicator(self, focused: bool) -> None:
        """Toggle the focus-ring around the card (M2 wires the visual)."""
        self._focused = bool(focused)
        # M1: no visual change. M2 will draw a thin border via a
        # rectangle patch on the Axes when ``_focused`` is True.

    def is_focused(self) -> bool:
        """Return whether this slot owns the M2 click-to-promote ring."""
        return self._focused


__all__ = ["CardWidget"]
