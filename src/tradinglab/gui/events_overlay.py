"""Chart overlay: historical earnings / dividend / split glyphs.

Renders the :class:`~tradinglab.events.render.EventGlyph` descriptors
built by :func:`tradinglab.events.render.build_event_glyphs` as
matplotlib text glyphs anchored at the **bottom edge of each price pane**
(plan.md decision 13b). Mixed-transform placement: ``transData`` for X
(bar index, like every other on-chart element), ``transAxes`` for Y
(fixed near the axes bottom so the glyphs never move with the price
y-range). This is the same TradingView convention the user asked for.

Pure-functional surface — no class instance / no Tk state. Callers
(``app._render_event_glyphs_for_slot``) own the artist refs so they can
be torn down between renders alongside the candle/indicator artists,
following the same rebuild-every-frame pattern as
:class:`tradinglab.gui.evidence_overlay.EvidenceOverlay`.

User-facing text taxonomy:

* ``A`` — earnings AMC
* ``B`` — earnings BMO
* ``D`` — dividend ex-date, including special/spinoff cash events
* ``S`` — stock split

Every glyph uses a theme-aware foreground plus a small rounded backing
box so the letter remains readable against candles and chart backgrounds.

Hover hit-testing is performed by :mod:`gui.interaction` — this module
only registers the per-glyph metadata (``ts_ms``, tooltip, bar index)
through the returned ``EventGlyphArtists`` payload. The interaction
layer's bottom-of-pane Y check + ``events_hover_hit_px`` pixel test
operate against that metadata.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from matplotlib.axes import Axes
from matplotlib.text import Text
from matplotlib.transforms import blended_transform_factory

from ..events.render import (
    EVENT_MARKER_GLYPH,
    GLYPH_DIVIDEND,
    GLYPH_EARNINGS_FORWARD,
    GLYPH_EARNINGS_PAST,
    GLYPH_SPECIAL_DIVIDEND,
    GLYPH_SPLIT,
    EventGlyph,
)

_FALLBACK_TEXT_COLOR = "#111111"
_FALLBACK_BBOX_FACE = "#ffffff"
_FALLBACK_BBOX_EDGE = "#7d8794"
_GLYPH_ALPHA = 0.95
_GLYPH_BBOX_ALPHA = 0.85
_GLYPH_FONT_SIZE = 7
_GLYPH_ZORDER = 4  # above indicators (3), below crosshair (5)

# Bottom-of-pane Y anchor in axes-fraction. Sits just above the axes
# bottom spine so the letter box doesn't clip on the lower spine pixel.
_GLYPH_Y = 0.025

# Right-edge forward badge Y / X (axes fraction). Plan §13b: forward
# events outside the visible window render as a flush right-edge label.
_BADGE_AX_X = 0.985
_BADGE_AX_Y = 0.04


_FALLBACK_MARKER_GLYPH: dict[str, str] = {
    GLYPH_EARNINGS_PAST: "E",
    GLYPH_EARNINGS_FORWARD: "E",
    GLYPH_DIVIDEND: EVENT_MARKER_GLYPH["dividend"],
    GLYPH_SPECIAL_DIVIDEND: EVENT_MARKER_GLYPH["dividend"],
    GLYPH_SPLIT: GLYPH_SPLIT,
}


@dataclass
class EventGlyphArtists:
    """The render output: per-glyph matplotlib artist refs + metadata.

    ``artists`` is the flat list of every :class:`Text` produced by
    :func:`draw_event_glyphs`. Callers stash this list on
    ``panel_state[slot]["event_artists"]`` so the next ``_draw_slice``
    can call :func:`clear_event_glyph_artists` before rebuilding.

    ``hit_meta`` is the hover hit-test payload: one tuple per in-pane
    glyph of the form ``(x_data, glyph_kind, tooltip)``. The interaction
    layer uses this to map cursor X to the closest glyph without
    re-walking the descriptor list.

    ``forward_badge_tooltip`` is the (possibly empty) tooltip for the
    right-edge "Next earn T-N" badge. Hover hit-tests against it are
    based on cursor X falling within the rightmost few axes-fraction
    pixels, so no x-data anchor is needed.
    """
    artists: list[Any] = field(default_factory=list)
    hit_meta: list[tuple[float, str, str]] = field(default_factory=list)
    forward_badge_tooltip: str = ""


def _theme_color(theme: Any, keys: tuple[str, ...], fallback: str) -> str:
    if isinstance(theme, dict):
        for key in keys:
            v = theme.get(key)
            if isinstance(v, str) and v:
                return v
    return fallback


def clear_event_glyph_artists(artists: Sequence[Any]) -> None:
    """Remove each artist from its axes.

    Mirrors :func:`tradinglab.app._safe_remove` semantics — any
    artist whose ``.remove()`` raises is silently dropped (the most
    common reason is the axes was already cleared by ``fig.clear()``,
    in which case the artist is already detached and there's nothing
    to do).
    """
    for a in artists:
        try:
            a.remove()
        except Exception:  # noqa: BLE001
            pass


def draw_event_glyphs(
    ax: Axes,
    glyphs: Sequence[EventGlyph],
    *,
    offset: int,
    theme: Any = None,
    show_earnings: bool = True,
    show_dividends: bool = True,
    show_upcoming: bool = True,
) -> EventGlyphArtists:
    """Project ``glyphs`` into matplotlib artists on ``ax``.

    Anchors each in-pane glyph at ``(bar_index + offset, _GLYPH_Y)``
    via a blended (transData, transAxes) transform — X moves with the
    candles, Y stays pinned to the bottom of the price pane. Right-edge
    forward badges (``bar_index == -1``) land as a single axes-fraction
    annotation.

    The three ``show_*`` flags map to the user-facing tunables of the
    same names in :mod:`defaults`. When a kind is hidden, its glyphs
    are skipped entirely (the artist count goes down, so the hover hit
    list doesn't expose them either).

    Returns the :class:`EventGlyphArtists` payload — both the artist
    refs (for teardown) and the metadata (for hover).
    """
    out = EventGlyphArtists()
    if ax is None or not glyphs:
        return out
    text_color = _theme_color(
        theme, ("tooltip_fg", "text", "axis_text", "spine"), _FALLBACK_TEXT_COLOR,
    )
    box_face = _theme_color(theme, ("tooltip_bg", "ax_bg", "fig_bg"), _FALLBACK_BBOX_FACE)
    box_edge = _theme_color(theme, ("spine", "axis_text", "text"), _FALLBACK_BBOX_EDGE)
    trans = blended_transform_factory(ax.transData, ax.transAxes)

    for g in glyphs:
        kind = g.glyph_kind
        # Visibility gating per user tunable.
        if kind in (GLYPH_EARNINGS_PAST, GLYPH_EARNINGS_FORWARD):
            if kind == GLYPH_EARNINGS_FORWARD and not show_upcoming:
                continue
            if not show_earnings:
                continue
        elif kind in (GLYPH_DIVIDEND, GLYPH_SPECIAL_DIVIDEND, GLYPH_SPLIT):
            if not show_dividends:
                continue

        marker_glyph = str(
            getattr(g, "marker_glyph", "") or _FALLBACK_MARKER_GLYPH.get(kind, ""),
        )

        if g.bar_index < 0:
            # Right-edge forward badge. One per render (the descriptor
            # builder already deduplicated to the nearest forward
            # event), painted as a small italic Text in axes coords so
            # it remains legible regardless of the visible-bar count.
            try:
                badge: Text = ax.text(
                    _BADGE_AX_X, _BADGE_AX_Y, g.tooltip,
                    transform=ax.transAxes,
                    ha="right", va="bottom",
                    fontsize=7,
                    color=text_color, alpha=_GLYPH_ALPHA,
                    style="italic",
                    zorder=_GLYPH_ZORDER,
                    clip_on=True,
                )
                out.artists.append(badge)
                out.forward_badge_tooltip = g.tooltip
            except Exception:  # noqa: BLE001
                pass
            continue

        if not marker_glyph:
            continue
        x = float(g.bar_index + offset)
        try:
            text: Text = ax.text(
                x, _GLYPH_Y, marker_glyph,
                transform=trans,
                ha="center", va="center",
                fontsize=_GLYPH_FONT_SIZE,
                fontweight="bold",
                color=text_color,
                alpha=_GLYPH_ALPHA,
                bbox=dict(
                    facecolor=box_face,
                    edgecolor=box_edge,
                    boxstyle="round,pad=0.12",
                    alpha=_GLYPH_BBOX_ALPHA,
                    linewidth=0.5,
                ),
                zorder=_GLYPH_ZORDER,
                clip_on=True,
            )
            out.artists.append(text)
            out.hit_meta.append((x, kind, g.tooltip))
        except Exception:  # noqa: BLE001
            continue

    return out


__all__ = (
    "EventGlyphArtists",
    "draw_event_glyphs",
    "clear_event_glyph_artists",
)
