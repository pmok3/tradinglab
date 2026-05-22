"""Matplotlib drawing primitives for candlestick + volume charts.

The drawing API is designed to support **virtualized** rendering: each draw
call accepts an ``[start, end)`` slice into ``candles`` and builds artists
only for bars in that range. Callers manage the slice lifecycle (adding,
removing, replacing artists) so the axes' one-time styling (grid, spines,
formatters) is not rebuilt on every pan re-render.
"""

from __future__ import annotations

import colorsys
from typing import List, Mapping, Optional, Tuple

from matplotlib.colors import to_rgba

from .constants import BEAR_COLOR, BULL_COLOR
from .formatting import fmt_volume
from .models import Candle


def brighter_shade(rgba: Tuple[float, float, float, float], *, dark_mode: bool) -> Tuple[float, float, float, float]:
    """Return a fully-saturated, theme-aware accent variant of ``rgba``.

    Used by the *Highlight Flat HA Candles* overlay to derive a bright
    body-fill colour from the bar's normal bull/bear hue. Keeping the
    derivation here (rather than per-theme constants) means a future
    palette tweak to ``BULL_COLOR`` / ``BEAR_COLOR`` automatically
    propagates to the accent.

    Algorithm
    ---------
    1. Convert RGB → HLS, preserving alpha.
    2. Set saturation to ``1.0`` (vivid hue).
    3. Adjust lightness for legibility against the active background:

       * **Dark mode** — lightness is clamped *up* to at least ``0.55``
         so the accent reads against a dark axes background.
       * **Light mode** — lightness is clamped to ``[0.40, 0.55]`` so
         the accent is vivid but not so light it blends into the
         near-white axes background.
    4. Convert back to RGB; alpha passes through unchanged.
    """
    r, g, b, a = rgba
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    s_new = 1.0
    if dark_mode:
        l_new = max(0.55, l)
    else:
        l_new = min(0.55, max(0.40, l))
    r2, g2, b2 = colorsys.hls_to_rgb(h, l_new, s_new)
    return (r2, g2, b2, a)


def darker_shade(
    rgba: Tuple[float, float, float, float], *, dark_mode: bool,
) -> Tuple[float, float, float, float]:
    """Return a theme-aware darker variant of ``rgba``.

    Companion to :func:`brighter_shade`. Used by
    :mod:`tradinglab.gui.volume_tod_overlay` to derive the outline
    (full-day envelope) hue from the volume bar's bull/bear fill — same
    hue, more saturated, lower lightness — so the outline reads as the
    SAME-COLOR-BUT-DARKER form of the bar (plan.md decision 17 for the
    Volume TOD feature).

    Algorithm
    ---------
    1. Convert RGB → HLS, preserve alpha.
    2. Boost saturation moderately (HLS ``s`` → ``min(1.0, s + 0.15)``)
       so the outline doesn't read washed-out compared to the bar.
    3. Drop lightness:

       * **Dark mode** — clamp ``l`` *down* by 0.10 (relative), floor 0.10.
         Outlines paint on a dark axes background, so we can only go so
         dark before the line becomes invisible against the spine.
       * **Light mode** — clamp ``l`` *down* by 0.18 (relative), floor 0.18.
         Light-mode bars are already mid-bright; the outline needs the
         bigger drop to read distinctly.
    4. Convert HLS → RGB; alpha passes through unchanged.
    """
    r, g, b, a = rgba
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    s_new = min(1.0, s + 0.15)
    if dark_mode:
        l_new = max(0.10, l - 0.10)
    else:
        l_new = max(0.18, l - 0.18)
    r2, g2, b2 = colorsys.hls_to_rgb(h, l_new, s_new)
    return (r2, g2, b2, a)


_BODY_WIDTH = 0.6
_BODY_HALF = _BODY_WIDTH / 2

#: When the visible window squeezes below this many CSS pixels per
#: bar, ``bar_geometry`` shrinks the body half-width so the bodies
#: don't overlap their neighbours. At the floor density (≈ 1 px/bar)
#: bodies degenerate to a hairline at the bar's wick — the wick
#: stays visible regardless and remains the dominant glyph.
_DENSE_PX_PER_BAR_THRESHOLD = 4.0

#: Floor for the body half-width when extreme zoom-out collapses
#: bodies. 0.05 = a 10%-of-step body — still visible but unmistakably
#: thinner, signaling to the user that they're seeing macro density.
_BODY_HALF_FLOOR = 0.05


def dynamic_body_half(ax, n_visible: int) -> float:
    """Return a clamped body half-width for an axes' current zoom.

    At dense zooms (the usual case) returns ``_BODY_HALF`` (= 0.30
    in data units). At extreme zoom-out, where each bar occupies
    fewer than ``_DENSE_PX_PER_BAR_THRESHOLD`` CSS pixels, scales
    the half-width down linearly so neighbours don't overlap.
    Clamped to ``[_BODY_HALF_FLOOR, _BODY_HALF]``.

    Defensive against unrealized axes (``ax.bbox.width`` may be 1
    before first paint): treats invalid widths as "dense enough" and
    returns the legacy default. Callers can safely invoke this on
    any axes at any time.
    """
    try:
        width_px = float(ax.bbox.width)
    except Exception:  # noqa: BLE001
        return _BODY_HALF
    if width_px <= 1.0 or n_visible <= 0:
        return _BODY_HALF
    px_per_bar = width_px / float(n_visible)
    if px_per_bar >= _DENSE_PX_PER_BAR_THRESHOLD:
        return _BODY_HALF
    ratio = max(0.0, px_per_bar) / _DENSE_PX_PER_BAR_THRESHOLD
    scaled = _BODY_HALF * ratio
    return max(_BODY_HALF_FLOOR, min(_BODY_HALF, scaled))


def safe_remove(artist) -> None:
    """Remove a matplotlib artist, ignoring errors if already detached."""
    if artist is None:
        return
    try:
        artist.remove()
    except Exception:  # noqa: BLE001 - best-effort; mpl may already have detached it
        pass

# Alpha applied to pre-market and post-market bars so the user can see at a
# glance which bars are from extended-hours sessions. RTH bars render at
# full opacity.
_EXTENDED_ALPHA = 0.45


def _bar_rgba(c: Candle) -> tuple:
    """Return an (r, g, b, a) tuple for ``c``'s body/wick.

    Green/red from bull/bear; alpha reduced for extended-hours bars so they
    read as visually quieter than regular trading hours.
    """
    base = BULL_COLOR if c.is_bull else BEAR_COLOR
    alpha = _EXTENDED_ALPHA if c.is_extended else 1.0
    return to_rgba(base, alpha)


def bar_geometry(
    c: Candle, x: float, body_half: Optional[float] = None,
) -> Tuple[Tuple, Tuple, tuple]:
    """Return ``(wick_segment, body_verts, color_rgba)`` for a single candle.

    Used by both :func:`draw_candlesticks` (slice rebuild) and
    ``ChartApp._apply_tick_to_artists`` (H1 stream-tick fastpath) so the
    two code paths produce byte-identical geometry.

    ``body_half`` controls the body's half-width in data-axis units.
    Defaults to the module's ``_BODY_HALF`` (= 0.3) — the legacy
    constant width — so existing callers see no change. Callers that
    want to clamp body width at extreme zoom-out (where bars overlap)
    can pass a value computed via :func:`dynamic_body_half`.
    """
    if body_half is None:
        body_half = _BODY_HALF
    color = _bar_rgba(c)
    wick_seg = ((x, c.low), (x, c.high))
    body_low = c.open if c.close >= c.open else c.close
    body_high = c.close if c.close >= c.open else c.open
    if body_high == body_low:
        span = c.high - c.low
        pad = span * 0.01 if span else 0.01
        body_low -= pad
        body_high += pad
    x0 = x - body_half
    x1 = x + body_half
    body_verts = (
        (x0, body_low), (x0, body_high),
        (x1, body_high), (x1, body_low),
    )
    return wick_seg, body_verts, color


def vol_geometry(
    c: Candle, x: float, body_half: Optional[float] = None,
) -> Tuple[Tuple, tuple]:
    """Return ``(vol_verts, color_rgba)`` for a single candle's volume bar.

    ``body_half`` matches :func:`bar_geometry`. Passing the same
    width keeps volume bars visually aligned with their price bodies.
    """
    if body_half is None:
        body_half = _BODY_HALF
    base = BULL_COLOR if c.is_bull else BEAR_COLOR
    alpha = 0.7 * (_EXTENDED_ALPHA if c.is_extended else 1.0)
    color = to_rgba(base, alpha)
    x0 = x - body_half
    x1 = x + body_half
    v = c.volume
    return ((x0, 0), (x0, v), (x1, v), (x1, 0)), color


def draw_candlesticks(
    ax,
    candles: List[Candle],
    x_offset: int = 0,
    start: int = 0,
    end: Optional[int] = None,
    hollow_indices: Optional["set[int]"] = None,
    flat_overlay: Optional[Mapping[str, object]] = None,
    body_half: Optional[float] = None,
) -> Tuple[object, object]:
    """Build and attach wick + body Collections for ``candles[start:end]``.

    Bars are drawn at X = ``i + x_offset`` where ``i`` is the **global**
    candle index, so the X coordinate of any candle is stable across
    re-renders regardless of the current slice.

    When ``hollow_indices`` is provided (set of global candle indices),
    those bars are rendered as hollow candles: the body is drawn with a
    transparent face and a slightly thicker outline in the bar's normal
    bull/bear color. This is the mechanic used by the *Highlight key
    bars* View toggle. Wicks are unchanged. Pass ``None`` (or omit) for
    the legacy all-solid behavior.

    When ``flat_overlay`` is provided, HA-flat bars receive a hatched
    overlay drawn ON TOP of their normal bull/bear body. The body fill
    keeps its native bull/bear hue — the hatch is what makes the
    pattern visually distinct from a regular HA bar. The dict shape:

    .. code-block:: python

        flat_overlay = {
            # global candle indices (sets / iterables of int):
            "bull_indices": {3, 7, 12},  # bull flat-bottom
            "bear_indices": {15, 18},    # bear flat-top
            # RGBA tuples used as the hatch line color (the polygon
            # face is transparent so the underlying body shows through):
            "bull_color": (r, g, b, a),
            "bear_color": (r, g, b, a),
            # matplotlib hatch strings; denser = more "x"s/"/"s:
            "bull_hatch": "xxx",
            "bear_hatch": "xxx",
        }

    The hatched overlays are drawn as two additional ``PolyCollection``
    artists (one for bull-flat, one for bear-flat) so each side can have
    its own hatch pattern and color. The overlay collections are
    stashed on ``bodies._sc_flat_hatch_collections`` so the caller can
    remove them alongside ``bodies`` when tearing down the slice.

    ``hollow_indices`` takes priority over ``flat_overlay`` when a bar
    appears in both — the hollow treatment is the more dramatic
    emphasis (key bars > flat HA), so a bar that's both key-bar AND
    flat-HA renders hollow (no hatch overlay) to avoid visual
    competition between the two overlays.

    Returns the ``(wicks, bodies)`` artist handles so the caller can
    remove them later when redrawing a different slice. Does **not**
    call ``ax.clear()`` and does **not** set xlim — those are the
    caller's responsibility so one-time axes styling is not wiped on
    pan re-renders.
    """
    from matplotlib.collections import LineCollection, PolyCollection

    if end is None:
        end = len(candles)
    if start < 0:
        start = 0
    if end > len(candles):
        end = len(candles)
    if end <= start:
        return None, None

    wick_segments: List = []
    wick_colors: List = []
    body_polys: List = []
    colors: List = []
    src_indices: List[int] = []
    for src_i in range(start, end):
        c = candles[src_i]
        if c.is_gap:
            # Leave this x-slot visually empty — don't add wick/body artists.
            continue
        x = src_i + x_offset
        wick_seg, body_verts, color = bar_geometry(c, x, body_half=body_half)
        if hollow_indices and src_i in hollow_indices:
            # Split the wick around the body so a hollow candle reads as
            # truly empty — no vertical line cutting through the interior.
            # ``body_verts`` is ordered (low-left, high-left, high-right,
            # low-right); pull the body Y bounds back out (these include
            # the doji-pad nudge from ``bar_geometry``).
            body_low = body_verts[0][1]
            body_high = body_verts[1][1]
            if c.high > body_high:
                wick_segments.append(((x, body_high), (x, c.high)))
                wick_colors.append(color)
            if c.low < body_low:
                wick_segments.append(((x, c.low), (x, body_low)))
                wick_colors.append(color)
        else:
            wick_segments.append(wick_seg)
            wick_colors.append(color)
        body_polys.append(body_verts)
        colors.append(color)
        src_indices.append(src_i)

    if not body_polys:
        return None, None

    wicks = LineCollection(wick_segments, colors=wick_colors, linewidths=1.0, zorder=2)
    # Disable matplotlib's path-snap on BOTH the wick LineCollection and
    # the body PolyCollection. Snap rounds path vertices to the nearest
    # pixel column to keep edges crisp, but it snaps each artist
    # **independently** — and in practice the rounding directions are
    # asymmetric:
    #   * The wick (1 px line at ``x``) snaps to the nearest column.
    #   * The body's left edge (``x - 0.3``) and right edge (``x + 0.3``)
    #     each snap to their own nearest columns; at low pixels-per-bar
    #     densities (e.g. zoomed-out 1d view ≈ 7.5 px/bar where the body
    #     is only ~4.5 px wide) those independent rounds shift the
    #     body's visual center off ``x`` while the wick stays at ``x``.
    # The end result is the wick visibly aligning with the body's LEFT
    # edge instead of its center — exactly the regression users see in
    # Reset View. Turning snap off on both lets matplotlib use sub-pixel
    # anti-aliasing for both artists, which keeps them mathematically
    # centered (wick at ``x``, body symmetrically about ``x``) at every
    # zoom level. Edges still read crisply at typical zooms because the
    # AA bleed is at most a fraction of a pixel; in exchange the wick is
    # always centered.
    wicks.set_snap(False)

    # Per-bar face / linewidth resolution. A single PolyCollection covers
    # all rendering modes (solid / hollow) — the *flat_overlay* hatch
    # mechanism layers ADDITIONAL collections on top rather than
    # mutating per-bar face color, so the underlying bull/bear hue is
    # preserved on flat HA bars.
    has_hollow = bool(hollow_indices)
    if has_hollow:
        face_list = []
        line_widths = []
        for src_i, base_color in zip(src_indices, colors):
            if src_i in hollow_indices:
                # RGBA with alpha=0 keeps the path-data-aware hit testing
                # while rendering nothing inside the body.
                face_list.append((0.0, 0.0, 0.0, 0.0))
                line_widths.append(1.4)
            else:
                face_list.append(base_color)
                line_widths.append(0.8)
        bodies = PolyCollection(
            body_polys, facecolors=face_list, edgecolors=colors,
            linewidths=line_widths, zorder=3,
        )
    else:
        bodies = PolyCollection(
            body_polys, facecolors=colors, edgecolors=colors,
            linewidths=0.8, zorder=3,
        )
    bodies.set_snap(False)
    # H1 fastpath caches: stash the source lists + global index mapping on
    # the artists so ``_apply_tick_to_artists`` can mutate the rightmost
    # element in place without re-walking ``candles``. We additionally
    # tag whether this body collection was built in hollow mode or with
    # a flat-HA overlay — the fastpath bails on either (per-bar
    # facecolor / per-bar hatch mutations are the caller's job to
    # detect, not the artist's).
    wicks._sc_segments = wick_segments
    wicks._sc_colors = wick_colors
    wicks._sc_src_indices = src_indices
    bodies._sc_verts = body_polys
    bodies._sc_colors = colors  # shared list — wicks + bodies use same colors
    bodies._sc_src_indices = src_indices
    bodies._sc_hollow_mode = has_hollow
    # Build the per-direction hatched overlay collections. Bull and bear
    # get separate collections because matplotlib's hatch is a
    # per-PolyCollection property — one pattern per collection. We pull
    # vertex data from the body polygons we just built so the overlay
    # geometry is byte-identical (no risk of sub-pixel drift between
    # body and hatch).
    flat_hatches: List = []
    has_flat = False
    if flat_overlay:
        bull_idx_set = set(flat_overlay.get("bull_indices") or ())
        bear_idx_set = set(flat_overlay.get("bear_indices") or ())
        # Key bars (hollow) win over flat-HA emphasis — exclude any bar
        # that's already rendering hollow so we don't paint a hatch
        # inside a "deliberately empty" body.
        if has_hollow:
            bull_idx_set -= set(hollow_indices)
            bear_idx_set -= set(hollow_indices)
        bull_polys = [
            v for v, i in zip(body_polys, src_indices) if i in bull_idx_set
        ]
        bear_polys = [
            v for v, i in zip(body_polys, src_indices) if i in bear_idx_set
        ]
        if bull_polys:
            bull_color = to_rgba(flat_overlay.get("bull_color") or "#000000")
            bull_hatch = str(flat_overlay.get("bull_hatch") or "xxx")
            bull_hatch_col = PolyCollection(
                bull_polys,
                facecolors=(0.0, 0.0, 0.0, 0.0),
                edgecolors=bull_color,
                linewidths=0.0,
                hatch=bull_hatch,
                zorder=3.5,
            )
            bull_hatch_col.set_snap(False)
            ax.add_collection(bull_hatch_col)
            flat_hatches.append(bull_hatch_col)
            has_flat = True
        if bear_polys:
            bear_color = to_rgba(flat_overlay.get("bear_color") or "#000000")
            bear_hatch = str(flat_overlay.get("bear_hatch") or "xxx")
            bear_hatch_col = PolyCollection(
                bear_polys,
                facecolors=(0.0, 0.0, 0.0, 0.0),
                edgecolors=bear_color,
                linewidths=0.0,
                hatch=bear_hatch,
                zorder=3.5,
            )
            bear_hatch_col.set_snap(False)
            ax.add_collection(bear_hatch_col)
            flat_hatches.append(bear_hatch_col)
            has_flat = True
    bodies._sc_flat_hatch_collections = flat_hatches
    # Keep the legacy ``_sc_accent_mode`` flag name so the H1 fastpath
    # bail (which checks this attribute) continues to work without
    # changes. Semantically: "this body collection has a non-trivial
    # overlay; the per-bar fastpath cannot safely tick-mutate".
    bodies._sc_accent_mode = has_flat
    ax.add_collection(wicks)
    ax.add_collection(bodies)
    return wicks, bodies


def draw_volume(
    ax,
    candles: List[Candle],
    x_offset: int = 0,
    start: int = 0,
    end: Optional[int] = None,
    body_half: Optional[float] = None,
) -> object:
    """Build and attach a volume-bar Collection for ``candles[start:end]``.

    See :func:`draw_candlesticks` for the X-coordinate convention and
    lifecycle contract. Returns the bars artist handle.

    ``body_half`` matches :func:`draw_candlesticks` — pass the same
    value so the volume bars stay aligned with the price bodies.
    """
    from matplotlib.collections import PolyCollection

    if end is None:
        end = len(candles)
    if start < 0:
        start = 0
    if end > len(candles):
        end = len(candles)
    if end <= start:
        return None

    polys: List = []
    colors: List = []
    src_indices: List[int] = []
    for src_i in range(start, end):
        c = candles[src_i]
        if c.is_gap:
            continue
        x = src_i + x_offset
        verts, color = vol_geometry(c, x, body_half=body_half)
        polys.append(verts)
        colors.append(color)
        src_indices.append(src_i)

    if not polys:
        return None

    bars = PolyCollection(
        polys, facecolors=colors, edgecolors=colors,
        linewidths=0.0, zorder=2,
    )
    bars._sc_verts = polys
    bars._sc_colors = colors
    bars._sc_src_indices = src_indices
    ax.add_collection(bars)
    return bars


def draw_session_shading(
    ax,
    candles: List[Candle],
    x_offset: int = 0,
    start: int = 0,
    end: Optional[int] = None,
    pre_color: str = "#4a6fa5",
    post_color: str = "#c07a2e",
    intraday: bool = False,
) -> List[object]:
    """Paint soft vertical bands behind consecutive pre/post-market bars.

    Pre- and post-market sessions get different hues so the user can tell
    morning-before-open and evening-after-close regions apart at a glance.
    Contiguous runs of the *same* session kind collapse into a single
    Rectangle for efficiency.

    Returns a list of artist handles (one per run) so the caller can remove
    them on the next slice refill. Draws at zorder=0 and uses a blended
    transform so the bands always span the full axes height regardless of
    autoscale.

    No-op (returns ``[]``) when no extended bars exist in the slice — the
    common case for daily+ intervals and for intraday data with the
    Extended Hours toggle off.

    When ``intraday`` is True, gap candles (compare-mode timestamp
    placeholders, ``session == "gap"``) contribute to shading via
    wall-clock classification of their timestamp. This keeps the band
    visually continuous on a chart where the *other* ticker has a
    pre/post bar at a slot that this ticker only has a gap for —
    otherwise the per-axis shading would look interleaved with white
    holes. On daily/non-intraday charts the flag is left False so
    midnight-stamped gap timestamps don't get falsely classified as
    "pre" by ``classify_session``.
    """
    from matplotlib.patches import Rectangle
    from matplotlib.transforms import blended_transform_factory

    from .constants import classify_session

    if end is None:
        end = len(candles)
    if start < 0:
        start = 0
    if end > len(candles):
        end = len(candles)
    if end <= start:
        return []

    artists: List[object] = []
    trans = blended_transform_factory(ax.transData, ax.transAxes)
    color_for = {"pre": pre_color, "post": post_color}

    def _shade_session(c: Candle) -> str:
        """Return the session label to use for shading a given candle.

        Real bars contribute their stored ``session``. Gap placeholders
        only contribute when the chart is intraday — in which case the
        wall-clock classification of the gap's timestamp drives the
        band so the visual stays continuous across one-sided missing
        bars in compare mode.
        """
        if c.session == "gap":
            if not intraday:
                return "regular"
            return classify_session(c.date.hour, c.date.minute)
        return c.session

    # Walk the slice, grouping consecutive bars (real or gap-via-clock)
    # that share the same extended-hours session label. Break runs when
    # the session kind changes (e.g. pre→regular→post within a single
    # day yields two separate bands with distinct colors).
    i = start
    while i < end:
        sess = _shade_session(candles[i])
        if sess in color_for:
            j = i
            while j < end and _shade_session(candles[j]) == sess:
                j += 1
            x0 = i + x_offset - 0.5
            x1 = (j - 1) + x_offset + 0.5
            rect = Rectangle(
                (x0, 0), x1 - x0, 1,
                transform=trans,
                facecolor=color_for[sess], edgecolor="none",
                alpha=0.14, zorder=0,
            )
            ax.add_patch(rect)
            artists.append(rect)
            i = j
        else:
            i += 1
    return artists


def setup_price_axes(ax) -> None:
    """One-time setup for a price axes: grid, margins, Y-tick locator.

    Kept separate from :func:`draw_candlesticks` so pan re-renders that only
    replace the candle Collections don't wipe grid / locator / watermark.
    """
    from matplotlib.ticker import MaxNLocator

    ax.grid(True, linestyle="--", alpha=0.3)
    ax.margins(x=0)
    # Drop the bottom-most price tick to avoid overlap with the top-most
    # volume tick on the panel directly below (hspace=0).
    ax.yaxis.set_major_locator(MaxNLocator(prune="lower"))
    # Right-side y-axis (TradingView / Sierra Chart convention).
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")


def setup_indicator_pane_axes(ax, *, min_label_px: int = 28) -> None:
    """One-time setup for an indicator pane axes (RSI, ATR, RVOL, ADX, …).

    Identical to :func:`setup_price_axes` except the y-tick locator is
    *pixel-aware*: it caps the number of ticks based on the pane's
    current pixel height so labels don't overlap on short panes / small
    monitors. ``MaxNLocator(nbins="auto")`` only considers the data
    range, which is fine on the tall price pane but produces 6–8 ticks
    on a 60 px-tall RVOL pane → unreadable on a 13" laptop.

    The pixel-aware locator queries ``ax.bbox.height`` on every
    ``__call__``, so it keeps adapting if the user resizes the window
    or adds/removes panes (which changes each pane's allocated height).
    """
    from matplotlib.ticker import MaxNLocator

    ax.grid(True, linestyle="--", alpha=0.3)
    ax.margins(x=0)

    class _PixelAwareLocator(MaxNLocator):
        """``MaxNLocator`` that caps ``nbins`` by available pixel height.

        At each tick-query, ``nbins`` is set to ``max(2, height_px //
        min_label_px)``. The default 28 px per tick matches the
        rendering at 9 pt labels (~12 px glyph + ~16 px padding) and
        leaves a clear visual gap between adjacent labels.
        """

        def __init__(self, *args, _ax=ax, _min_px=min_label_px, **kwargs):
            super().__init__(*args, **kwargs)
            self._sc_ax = _ax
            self._sc_min_px = _min_px

        def _sc_refresh_nbins(self) -> None:
            try:
                height_px = float(self._sc_ax.bbox.height)
            except Exception:  # noqa: BLE001 - axes may not be rendered yet
                return
            if height_px <= 0:
                return
            n = int(height_px // self._sc_min_px)
            # Always keep at least 2 (so ax has top + bottom anchor)
            # and cap at 9 (matplotlib's default upper bound) so very
            # tall panes don't try to draw 30 ticks.
            self.set_params(nbins=max(2, min(9, n)))

        def __call__(self):  # type: ignore[override]
            self._sc_refresh_nbins()
            return super().__call__()

    ax.yaxis.set_major_locator(_PixelAwareLocator(prune="lower"))
    # Right-side y-axis (TradingView / Sierra Chart convention).
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")


def setup_volume_axes(ax) -> None:
    """One-time setup for a volume axes: grid, margins, Y-formatter, locator."""
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    ax.grid(True, linestyle="--", alpha=0.3)
    ax.margins(x=0)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: fmt_volume(v)))
    # Drop the top-most volume tick so it doesn't collide with the bottom-most
    # price tick on the price chart that sits directly above (hspace=0).
    ax.yaxis.set_major_locator(MaxNLocator(nbins=3, prune="upper"))
    # Right-side y-axis (TradingView / Sierra Chart convention).
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")


def style_axes(ax, theme: dict) -> None:
    """Apply theme colors to an axes' background, ticks, spines, and grid."""
    ax.set_facecolor(theme["ax_bg"])
    ax.tick_params(colors=theme["text"])
    ax.yaxis.label.set_color(theme["text"])
    ax.xaxis.label.set_color(theme["text"])
    ax.title.set_color(theme["text"])
    for spine in ax.spines.values():
        spine.set_color(theme["spine"])
    ax.grid(True, linestyle="--", alpha=0.35, color=theme["grid"])
    # Re-color the in-pane indicator-name label if one was rendered
    # (set by ``indicators.render.render_for_slot``). Theme swaps invoke
    # ``style_axes`` again, which is when this branch fires.
    pane_label = getattr(ax, "_sc_pane_label_artist", None)
    if pane_label is not None:
        try:
            pane_label.set_color(theme["text"])
        except Exception:  # noqa: BLE001
            pass
