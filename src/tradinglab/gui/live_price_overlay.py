"""Chart overlay: sticky horizontal dotted line at the current live price.

Mirrors ``gui.exits_overlay`` and ``gui.entries_overlay`` in shape and
lifecycle, but draws a single dotted line per slot tracking the *current*
price — TradingView's "live quote line".

Design
------

* **One line per slot** (``primary``, ``compare``). Drilldown re-uses
  the ``primary`` slot's price axis, so it gets covered automatically.
* **Price source** (caller-resolved): freshest of latest stream-tick
  close, else the last non-gap candle close. The overlay itself is
  source-agnostic — the caller resolves the float and passes it in.
* **Color**: neutral. Caller passes ``theme["text"]`` so the line
  reads as "informational, not a level"; explicitly not direction-coded
  (no green/red).
* **Style**: dotted ``(0, (2, 3))`` for "this is a live cursor, not a
  user-placed level".
* **Right-edge label**: matches the style used by exits/entries overlays
  via ``blended_transform_factory(ax.transAxes, ax.transData)`` — the
  label sticks to the right margin even as xlim shifts.
* **Z-order**: ``zorder=3`` — below exits/entries overlay lines (z=4),
  below crosshair (z=10/11), above grid (z<3).
* **No subscription, no event loop**. The overlay is a passive renderer.
  Callers drive ``redraw`` from the end of ``_render`` and
  ``update_in_place`` from ``_refresh_view_after_tick`` (or any other
  per-tick path).

Lifecycle
---------

* :meth:`redraw` rebuilds artists for every visible slot. Called from
  ``ChartApp._render`` after ``figure.clear()`` has wiped the previous
  pass's axes.
* :meth:`update_in_place(slot, price)` mutates ``line.set_ydata`` and
  ``label.set_y`` / ``set_text`` without re-rendering the figure. Called
  from ``ChartApp._refresh_view_after_tick``.
* :meth:`clear` drops Python refs. ``figure.clear()`` already wipes the
  matplotlib artists; this just keeps the overlay's internal map from
  leaking. Called automatically at the start of every ``redraw``.

Public symbols
--------------

* :func:`format_price` — module-level helper for label text. Three-decimal
  for sub-dollar prices, two-decimal otherwise. Kept module-level so
  tests can pin the formatting contract without instantiating the class.
* :class:`LivePriceOverlay` — the artist owner.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from matplotlib.axes import Axes
from matplotlib.lines import Line2D
from matplotlib.text import Text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Visual constants
# ---------------------------------------------------------------------------


#: Dotted line pattern. 2-on, 3-off in points. Sparser than the grid
#: (which is solid) so the live-price cursor reads as "moving" rather
#: than "static level".
LIVE_PRICE_LINESTYLE: tuple[int, tuple[int, int]] = (0, (2, 3))

#: Z-order for the line + label artists. Below exits/entries overlay
#: lines (4) and below the crosshair (10/11), above grid (<3).
LIVE_PRICE_ZORDER: int = 3
LIVE_PRICE_LABEL_ZORDER: int = LIVE_PRICE_ZORDER + 1


def format_price(price: float) -> str:
    """Format ``price`` for the right-edge label.

    * Sub-dollar (``|price| < 1``): three decimals, e.g. ``0.075``.
    * Otherwise: two decimals with thousands separator, e.g. ``1,234.56``.

    Non-finite inputs (NaN, inf, -inf, None) → empty string so the
    label never displays ``"nan"`` / ``"inf"``.
    """
    if price is None:
        return ""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(p):
        return ""
    if abs(p) < 1.0:
        return f"{p:.3f}"
    return f"{p:,.2f}"


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------


class LivePriceOverlay:
    """Owns the dotted live-price line + right-edge label for every slot.

    Stateless w.r.t. price source — the caller resolves the price (from
    latest stream tick OR last candle close) and passes it in. The
    overlay knows only about drawing.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = bool(enabled)
        self._artists: dict[str, tuple[Line2D, Text | None]] = {}
        # Cached colours from the most recent ``redraw`` so theme-change
        # repaints can read them without the caller having to pass the
        # theme dict in again. Updated whenever a draw applies a colour.
        self._line_color: str = "#888888"
        self._label_bg: str = "#ffffff"
        self._label_fg: str = "#111111"
        self._label_edge: str = "#888888"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self.clear()

    @property
    def slot_count(self) -> int:
        """Number of slots currently tracked. Testing helper."""
        return len(self._artists)

    def get_artists(self, slot: str) -> tuple[Line2D, Text | None] | None:
        """Return ``(line, label)`` for ``slot`` or ``None``. Testing helper."""
        return self._artists.get(slot)

    def clear(self) -> None:
        """Detach every overlay artist from its axes, then drop refs.

        Detaching (not merely dropping refs) makes the overlay safe to clear
        WITHOUT a surrounding ``figure.clear()`` — required by the
        topology-preserving paint pipeline fast path
        (``docs/PAINT_PIPELINE_REFACTOR.md``). Idempotent + defensive: an
        artist already detached (e.g. by a prior ``figure.clear()``) raises on
        ``.remove()``, which is swallowed. End state is identical to the old
        ref-drop in the current ``figure.clear()`` flow.
        """
        for line, label in self._artists.values():
            for art in (line, label):
                if art is not None:
                    try:
                        art.remove()
                    except Exception:  # noqa: BLE001
                        pass
        self._artists.clear()

    def close(self) -> None:
        self.clear()

    # ------------------------------------------------------------------
    # Render hooks
    # ------------------------------------------------------------------

    def redraw(
        self,
        *,
        ax_by_slot: dict[str, Axes],
        price_by_slot: dict[str, float | None],
        color: str = "#888888",
        label_suffix: str = "",
        label_bg: str | None = None,
        label_fg: str | None = None,
        label_edge: str | None = None,
    ) -> None:
        """Rebuild artists for every slot in ``ax_by_slot``.

        Parameters
        ----------
        ax_by_slot
            ``{"primary": ax_p1, "compare": ax_p2, ...}``. Slots
            without an entry are skipped. Slots with a ``None`` axis
            are skipped.
        price_by_slot
            Resolved current price per slot. A ``None`` or non-finite
            price suppresses the line for that slot (no axhline drawn).
        color
            **Line** colour. Pass ``theme["text"]`` for the neutral
            "axis text" appearance per design. The right-edge label
            uses ``label_fg`` (defaults to ``color`` for back-compat
            but should be ``theme["tooltip_fg"]`` for the TradingView-
            style boxed badge).
        label_suffix
            Optional trailing text after the price (e.g. ``" live"``).
            Default empty — just the formatted price.
        label_bg, label_fg, label_edge
            Box facecolor / text colour / box edgecolor for the right-
            edge price badge. Mirrors the cursor crosshair price label
            (``gui/interaction.py:_build_hover_artists``) so the live
            price reads as the same "current value badge" idiom. When
            any of these is ``None`` the legacy unboxed appearance is
            retained (label colour = ``color``, no bbox) — this keeps
            tests that pre-date the boxed label working.

        After this call, ``get_artists(slot)`` returns ``(line, label)``
        for every slot we successfully drew on.
        """
        self.clear()
        if not self._enabled:
            return
        # Cache colours for later ``apply_theme`` calls. Even when the
        # caller passes ``None`` for the label colours we still keep
        # the previous values so subsequent redraws can fall back.
        self._line_color = color
        if label_fg is not None:
            self._label_fg = label_fg
        if label_bg is not None:
            self._label_bg = label_bg
        if label_edge is not None:
            self._label_edge = label_edge
        for slot, ax in ax_by_slot.items():
            if ax is None:
                continue
            price = price_by_slot.get(slot)
            if price is None:
                continue
            try:
                p = float(price)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(p):
                continue
            try:
                self._draw_one(
                    slot,
                    ax,
                    p,
                    color=color,
                    label_suffix=label_suffix,
                    label_bg=label_bg,
                    label_fg=label_fg,
                    label_edge=label_edge,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "LivePriceOverlay: _draw_one raised for slot %s", slot
                )

    def apply_theme(
        self,
        *,
        line_color: str,
        label_bg: str,
        label_fg: str,
        label_edge: str,
    ) -> None:
        """Recolour every existing artist in place.

        Called by :class:`gui.theme_controller.ThemeController` when the
        light/dark mode flips. Mutates the line colour and the label's
        bbox + text colour without rebuilding the overlay — the next
        full ``_render`` will rebuild anyway, but this keeps the live
        price line in sync immediately after the theme toggle (matches
        the behaviour of the hover annotation + crosshair badges).
        """
        self._line_color = line_color
        self._label_bg = label_bg
        self._label_fg = label_fg
        self._label_edge = label_edge
        for entry in self._artists.values():
            line, label = entry
            try:
                line.set_color(line_color)
            except Exception:  # noqa: BLE001
                pass
            if label is None:
                continue
            try:
                bbox = label.get_bbox_patch()
                if bbox is not None:
                    bbox.set_facecolor(label_bg)
                    bbox.set_edgecolor(label_edge)
                label.set_color(label_fg)
            except Exception:  # noqa: BLE001
                pass

    def update_in_place(
        self,
        slot: str,
        price: float | None,
        *,
        label_suffix: str = "",
    ) -> bool:
        """Update the line + label for ``slot`` without re-rendering.

        Returns ``True`` if the artist was mutated, ``False`` otherwise
        (no artist for slot, non-finite price, or mutation raised). Use
        this from per-tick paths to keep the live-price cursor moving
        with the latest stream tick.
        """
        if not self._enabled:
            return False
        entry = self._artists.get(slot)
        if entry is None:
            return False
        line, label = entry
        if price is None:
            return False
        try:
            p = float(price)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(p):
            return False
        try:
            line.set_ydata([p, p])
            if label is not None:
                # Re-anchor the label's y in data coordinates. We may
                # be holding either a ``Text`` artist (legacy unboxed
                # path) or an ``Annotation`` (boxed badge). For
                # ``Annotation`` we must mutate ``.xy`` (the data-
                # coords anchor); ``set_position`` would touch only
                # the ``xytext`` offset which is in offset-points and
                # stays constant.
                from matplotlib.text import Annotation as _MplAnnotation
                if isinstance(label, _MplAnnotation):
                    cur_xy = label.xy
                    label.xy = (cur_xy[0], p)
                    label.set_text(format_price(p) + label_suffix)
                else:
                    pos = label.get_position()
                    label.set_position((pos[0], p))
                    label.set_text(" " + format_price(p) + label_suffix)
        except Exception:  # noqa: BLE001
            logger.exception(
                "LivePriceOverlay: update_in_place raised for slot %s", slot
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _draw_one(
        self,
        slot: str,
        ax: Axes,
        price: float,
        *,
        color: str,
        label_suffix: str,
        label_bg: str | None = None,
        label_fg: str | None = None,
        label_edge: str | None = None,
    ) -> None:
        line = ax.axhline(
            y=price,
            color=color,
            linestyle=LIVE_PRICE_LINESTYLE,
            linewidth=1.0,
            alpha=0.7,
            zorder=LIVE_PRICE_ZORDER,
        )
        label: Text | None = None
        try:
            from matplotlib.transforms import blended_transform_factory
            tr = blended_transform_factory(ax.transAxes, ax.transData)
            # Boxed badge (TradingView / Sierra Chart "current price" pill).
            # Mirrors the cursor crosshair price label in
            # ``gui/interaction.py:_build_hover_artists`` so the live price
            # reads as the same "current value" idiom. When no box colours
            # are provided we fall back to the legacy unboxed style (line-
            # coloured plain text) for back-compat with older callers.
            boxed = (
                label_bg is not None
                and label_fg is not None
                and label_edge is not None
            )
            if boxed:
                bbox_kw = dict(
                    boxstyle="round,pad=0.30",
                    fc=label_bg, ec=label_edge,
                    alpha=1.0, linewidth=0.8,
                )
                text_color = label_fg
                # Slight nudge so the box doesn't visually crash into the
                # right spine — matches the crosshair badge's
                # ``xytext=(3, 0) textcoords="offset points"`` offset.
                label = ax.annotate(
                    format_price(price) + label_suffix,
                    xy=(1.0, price), xycoords=tr,
                    xytext=(3, 0), textcoords="offset points",
                    ha="left", va="center",
                    bbox=bbox_kw,
                    color=text_color,
                    fontsize=8, clip_on=False,
                    zorder=LIVE_PRICE_LABEL_ZORDER,
                )
            else:
                label = ax.text(
                    1.0, price, " " + format_price(price) + label_suffix,
                    transform=tr,
                    ha="left", va="center",
                    fontsize=8, color=color,
                    clip_on=False,
                    zorder=LIVE_PRICE_LABEL_ZORDER,
                )
        except Exception:  # noqa: BLE001
            logger.exception("LivePriceOverlay: label render failed")
        self._artists[slot] = (line, label)


# ---------------------------------------------------------------------------
# Price-source resolution
# ---------------------------------------------------------------------------


def resolve_price(
    symbol: str,
    *,
    last_stream_price: dict[str, float],
    panel_state_slot: dict[str, Any] | None,
) -> float | None:
    """Return the freshest known price for ``symbol`` or ``None``.

    Resolution order (newest wins):

    1. ``last_stream_price[symbol]`` if it's a finite float — this is
       the most recent stream-tick price, updated by
       ``ChartApp._apply_stream_tick``.
    2. The last non-gap candle's ``close`` in
       ``panel_state_slot["candles"]``. Gap candles (no trade, e.g.
       weekend bars carried over) are skipped because their close is
       NaN.
    3. ``None`` if neither source has a usable price.

    Pure function so tests can drive it with plain dicts. Caller is
    responsible for symbol normalisation (upper-case) before passing.
    """
    sym = (symbol or "").strip().upper()
    if sym and sym in last_stream_price:
        try:
            v = float(last_stream_price[sym])
            if math.isfinite(v):
                return v
        except (TypeError, ValueError):
            pass
    if panel_state_slot is None:
        return None
    candles = panel_state_slot.get("candles") or []
    for c in reversed(candles):
        if getattr(c, "is_gap", False):
            continue
        try:
            v = float(c.close)
        except (TypeError, ValueError, AttributeError):
            continue
        if math.isfinite(v):
            return v
    return None
