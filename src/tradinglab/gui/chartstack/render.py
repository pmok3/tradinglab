"""ChartStack render — simple daily candlestick rendering.

Per the 2026-05-16 simplification, ChartStack cards render
**only** daily OHLC candlesticks. The earlier M4 visual stack
(volume-stroke sparkline, VWAP, PMH/PML horizontals, pre/post-
market wash, last-3-bars overlay, halted-symbol grey treatment)
has been retired — cards are now miniature daily candle charts,
nothing more.

Public surface:

* :func:`draw_card_placeholder` — symbol text only, for empty /
  single-bar slots.
* :func:`draw_card_candles` — clears the axes and draws OHLC
  candles plus a header row (symbol top-left, last close + %chg
  vs prior close top-right). Optional ``tint`` paints the axes
  spines as a colored border (driven by the alert engine).
* :func:`apply_card_tint` — set / clear the spine-as-border tint.

``draw_card_sparkline`` is preserved as a backwards-compatible
alias for ``draw_card_candles`` so external test fixtures and any
stale call sites keep working. Renderer-specific overlay kwargs
(``show_vwap``, ``show_pmh_pml``, ``show_last_candles``,
``volume_stroke_encoding``, ``halted_at``) are accepted and
ignored.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from matplotlib.axes import Axes

    from .binding import CardBinding
    from .series_cache import Bar


# Bull / bear colors mirror the main chart so positive cards read
# the same hue as positive candles on the main chart.
_UP_COLOR = "#26a69a"
_DOWN_COLOR = "#ef5350"
_FLAT_COLOR = "#6b7280"


def _theme_text_color(theme: Mapping[str, str] | None) -> str | None:
    """Pull the foreground text color from a theme palette mapping.

    Returns ``None`` when ``theme`` is missing the ``text`` key (or
    is itself ``None``); callers should then fall back to
    matplotlib's default text color so the existing test fixtures —
    which never pass a theme — keep behaving as before.
    """
    if theme is None:
        return None
    val = theme.get("text") if isinstance(theme, Mapping) else None
    if isinstance(val, str) and val:
        return val
    return None


def _theme_ax_bg(theme: Mapping[str, str] | None) -> str | None:
    """Pull the axes background color from a theme palette mapping.

    Returns ``None`` when ``theme`` is missing the ``ax_bg`` key.
    Callers must NOT touch ``ax.set_facecolor`` in that case so the
    headless test fixtures keep their default white face.
    """
    if theme is None:
        return None
    val = theme.get("ax_bg") if isinstance(theme, Mapping) else None
    if isinstance(val, str) and val:
        return val
    return None


# ---------------------------------------------------------------------------
# Placeholder
# ---------------------------------------------------------------------------

def draw_card_placeholder(
    ax: Axes,
    binding: CardBinding | None,
    *,
    theme: Mapping[str, str] | None = None,
) -> None:
    """Render the empty-slot placeholder: centred symbol text only.

    Clears the axes first so a refresh cycle doesn't leave stale
    artists behind. ``binding=None`` slots show ``"(empty)"`` so it
    is obvious in screenshots / smoke tests that the slot is
    intentional, not a crash.

    ``theme`` (optional) is a palette mapping with ``text`` /
    ``ax_bg`` keys (matching ``constants.LIGHT_THEME`` /
    ``DARK_THEME``). When provided, the placeholder text adopts
    ``theme["text"]`` and the axes face adopts ``theme["ax_bg"]``
    so theme colors survive ``ax.clear()`` re-renders. Omit
    ``theme`` (or pass ``None``) to keep matplotlib defaults — the
    existing headless renderer tests rely on this.
    """
    ax.clear()
    ax_bg = _theme_ax_bg(theme)
    if ax_bg is not None:
        ax.set_facecolor(ax_bg)
    label = binding.symbol if binding is not None else "(empty)"
    text_kwargs = {
        "ha": "center",
        "va": "center",
        "fontsize": 14,
        "transform": ax.transAxes,
    }
    fg = _theme_text_color(theme)
    if fg is not None:
        text_kwargs["color"] = fg
    ax.text(0.5, 0.5, label, **text_kwargs)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)


# ---------------------------------------------------------------------------
# Tint API
# ---------------------------------------------------------------------------

def apply_card_tint(ax: Axes, color: str | None) -> None:
    """Paint a colored border around a card by coloring its axes spines.

    Passing ``None`` clears the tint (hides spines again). The
    alert engine drives this hook; renderer code never calls it
    except via the ``tint`` kwarg on :func:`draw_card_candles`.
    Idempotent — calling with the same color twice is cheap.
    """
    if color is None:
        for spine in ax.spines.values():
            spine.set_visible(False)
        return
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_edgecolor(color)
        spine.set_linewidth(1.6)


# ---------------------------------------------------------------------------
# Candle drawing
# ---------------------------------------------------------------------------

def _direction_color(open_: float, close: float) -> str:
    """Return bull/bear/flat color for one candle."""
    if close > open_:
        return _UP_COLOR
    if close < open_:
        return _DOWN_COLOR
    return _FLAT_COLOR


def _draw_candles(
    ax: Axes,
    bars: Sequence[Bar],
    xs: Sequence[float],
) -> None:
    """Draw OHLC candles (wicks + bodies) for every bar.

    Wicks are drawn as a single :class:`LineCollection` and bodies
    as a single :class:`PatchCollection` so a 60-bar card renders
    in ~3 ms instead of the ~60 ms a per-bar ``ax.plot`` +
    ``ax.add_patch`` loop produces. Body width spans 70 % of the
    per-bar x-step; a doji bar (open == close) still renders a
    floor-height sliver so it remains visible.
    """
    if not bars or not xs:
        return
    # Import locally so the module stays importable when matplotlib
    # is uninitialised at test collection time.
    from matplotlib.collections import LineCollection, PatchCollection
    from matplotlib.patches import Rectangle

    step = (float(xs[1]) - float(xs[0])) if len(xs) >= 2 else 1.0
    body_w = max(step * 0.7, 0.3)

    wick_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    wick_colors: list[str] = []
    body_patches: list[Rectangle] = []
    body_colors: list[str] = []

    for x, b in zip(xs, bars, strict=False):
        o = float(b.open)
        h = float(b.high)
        lo = float(b.low)
        cl = float(b.close)
        color = _direction_color(o, cl)
        wick_segments.append(((float(x), lo), (float(x), h)))
        wick_colors.append(color)
        body_lo = min(o, cl)
        body_hi = max(o, cl)
        height = max(body_hi - body_lo,
                     (h - lo) * 0.02 if h > lo else 1e-9)
        body_patches.append(Rectangle(
            (float(x) - body_w * 0.5, body_lo),
            body_w,
            height,
        ))
        body_colors.append(color)

    wick_lc = LineCollection(
        wick_segments,
        colors=wick_colors,
        linewidths=0.8,
        capstyle="butt",
        zorder=3,
    )
    ax.add_collection(wick_lc)

    body_pc = PatchCollection(
        body_patches,
        facecolors=body_colors,
        edgecolors=body_colors,
        linewidths=0.5,
        zorder=4,
        # Disable the default "use scalar mappable" path; we want
        # explicit per-patch colors, not a colormap lookup.
        match_original=False,
    )
    ax.add_collection(body_pc)


def draw_card_candles(
    ax: Axes,
    bars: list[Bar],
    *,
    binding: CardBinding | None = None,
    tint: str | None = None,
    theme: Mapping[str, str] | None = None,
    **_ignored_legacy_kwargs: object,
) -> None:
    """Render the card body as daily OHLC candlesticks + header text.

    Composition (back-to-front order = matplotlib zorder):

      1. Candles (wicks zorder=3, bodies zorder=4)
      2. Header text — symbol left, last + %chg right (zorder=10)
      3. Optional tint via axes spines (orthogonal to artists)

    Falls through to :func:`draw_card_placeholder` when fewer than
    two bars are available (a single candle can't compute a
    %chg-vs-prior-close, and the user-perceived "chart" is empty
    anyway).

    ``theme`` (optional) is a palette mapping with ``text`` /
    ``ax_bg`` keys (matching ``constants.LIGHT_THEME`` /
    ``DARK_THEME``). When provided, the axes face adopts
    ``theme["ax_bg"]`` and the symbol header text adopts
    ``theme["text"]`` so theme colors survive ``ax.clear()``
    re-renders. The right-aligned last/%chg label is intentionally
    direction-tinted (bull / bear / flat) and ignores the theme so
    sentiment encoding remains intact. Omit ``theme`` (or pass
    ``None``) to keep matplotlib defaults — the existing headless
    renderer tests rely on this.

    Legacy keyword arguments accepted by the M4 sparkline renderer
    (``show_vwap``, ``show_pmh_pml``, ``show_last_candles``,
    ``volume_stroke_encoding``, ``halted_at``) are swallowed silently
    via ``**_ignored_legacy_kwargs`` so existing call sites continue
    to work during the simplification rollout.
    """
    ax.clear()
    if not bars or len(bars) < 2:
        draw_card_placeholder(ax, binding, theme=theme)
        if tint is not None:
            apply_card_tint(ax, tint)
        return

    ax_bg = _theme_ax_bg(theme)
    if ax_bg is not None:
        ax.set_facecolor(ax_bg)

    xs = list(range(len(bars)))
    lows = [float(b.low) for b in bars]
    highs = [float(b.high) for b in bars]
    closes = [float(b.close) for b in bars]
    opens = [float(b.open) for b in bars]
    lo = min(lows)
    hi = max(highs)
    span = hi - lo
    pad = max(span * 0.08, 1e-9)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(xs[0] - 0.5, xs[-1] + 0.5)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    _draw_candles(ax, bars, xs)

    # Header row — symbol (left), last + %chg vs prior close (right).
    sym = binding.symbol if binding is not None else "?"
    sym_kwargs = {
        "ha": "left", "va": "top", "fontsize": 10, "fontweight": "bold",
        "transform": ax.transAxes, "zorder": 10,
    }
    sym_color = _theme_text_color(theme)
    if sym_color is not None:
        sym_kwargs["color"] = sym_color
    ax.text(0.02, 0.96, sym, **sym_kwargs)
    last = closes[-1]
    prev = closes[-2]
    pct = ((last - prev) / prev * 100.0) if prev else 0.0
    label_color = _direction_color(opens[-1], last)
    ax.text(
        0.98, 0.96, f"{last:,.2f}  {pct:+.2f}%",
        ha="right", va="top", fontsize=9, color=label_color,
        transform=ax.transAxes, zorder=10,
    )

    if tint is not None:
        apply_card_tint(ax, tint)


# Backwards-compatible alias for any caller still on the M4 name.
draw_card_sparkline = draw_card_candles


__all__ = [
    "draw_card_placeholder",
    "draw_card_candles",
    "draw_card_sparkline",
    "apply_card_tint",
]
