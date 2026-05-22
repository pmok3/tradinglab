"""Render and hit-test helpers for drawings (Feature C).

Drawings are rendered with matplotlib primitives (``axhline`` in
v1, ``Line2D`` segments + ``Rectangle`` in future versions). The
caller is responsible for tracking the returned artists and
removing them on the next render — the helpers themselves don't
hold any state.

Z-order
-------
:data:`DRAWING_ZORDER` (3.5) sits **above the candles** (price
bodies are zorder 2-3) and **below the indicator overlays**
(``indicators/render.py`` uses ``base + 0.01 * i`` starting at
~4.0). Crosshair and crosshair label artists sit at 10-11 and
remain on top.

Label rendering (regression #C3, 2026-05)
-----------------------------------------
:func:`render_drawings` also paints the drawing's ``label`` (when
non-empty) at the right edge of the axes, vertically aligned with
the line. The label uses the line's own color on a small
muted-background pill so it remains legible against either light
or dark themes. Positioned via :meth:`Axes.get_yaxis_transform`
(data-y, axes-x) so the pill stays glued to the right margin
regardless of pan/zoom. Anchored at ``x=0.998`` (axes fraction) so
it doesn't crowd the y-axis tick labels.

The earlier shipping version stored ``label`` to ``drawings.json``
but never created any text artist — a "ghost feature" the user
could type into and watch persist with no visible result on
chart. Caught by the 2026-05 adversarial review.

Hit-testing
-----------
:func:`pick_drawing` operates in **display coordinates** (pixels),
not data coordinates, so a single tolerance value feels identical
on a penny chart and a mega-cap chart. The tolerance is also
**DPI-scaled** internally: a 5-logical-pixel tolerance becomes ~10
display pixels on a 192-DPI 4K monitor so the click target tracks
the rendered line thickness instead of shrinking by half (audit
``pick-tolerance-dpi``). Returns the closest drawing within
tolerance; ties (rare — two lines on the same price) are resolved
by most-recently-added wins.
"""
from __future__ import annotations

from collections.abc import Sequence

from matplotlib.axes import Axes
from matplotlib.lines import Line2D
from matplotlib.text import Text

from .model import Drawing

# Reference DPI for tolerance scaling. Matches the "logical pixel"
# resolution most desktop UI conventions assume; matplotlib's
# default ``figure.dpi`` is 100 which produces a near-1.0 scale
# factor on legacy displays. HiDPI displays (e.g. 192 / 240) get
# proportionally larger pick targets so the user can actually hit
# the line. Audit ``pick-tolerance-dpi``.
PICK_TOLERANCE_REFERENCE_DPI = 96.0

DRAWING_ZORDER = 3.5
# Labels sit just above the line so their pill background doesn't
# obscure the wick of a candle that happens to graze the line.
DRAWING_LABEL_ZORDER = DRAWING_ZORDER + 0.05

DRAWING_GID_PREFIX = "drawing:"
DRAWING_LABEL_GID_PREFIX = "drawing-label:"

# User-facing style strings → matplotlib linestyle. Kept here (not
# in ``model``) because it's a render-layer concern; ``model``
# only validates the names.
_LINESTYLE_MAP = {
    "solid": "-",
    "dashed": "--",
    "dotted": ":",
    "dashdot": "-.",
}


def _render_label(ax: Axes, d: Drawing) -> Text | None:
    """Paint ``d.label`` as a small pill at the right edge of ``ax``.

    Returns the :class:`~matplotlib.text.Text` artist or ``None``
    if the label is empty / falsy. Caller need not track the
    returned artist: it's owned by ``ax`` and removed by the
    next ``fig.clear()`` along with everything else.
    """
    label = (d.label or "").strip()
    if not label:
        return None
    try:
        txt = ax.text(
            0.998, float(d.price),
            label,
            transform=ax.get_yaxis_transform(),
            ha="right",
            va="bottom",
            fontsize=8,
            color=d.color,
            zorder=DRAWING_LABEL_ZORDER,
            bbox={
                "boxstyle": "round,pad=0.2",
                "facecolor": "white",
                "edgecolor": d.color,
                "alpha": 0.85,
                "linewidth": 0.8,
            },
            clip_on=True,
        )
    except Exception:  # noqa: BLE001
        return None
    try:
        txt.set_gid(f"{DRAWING_LABEL_GID_PREFIX}{d.id}")
    except Exception:  # noqa: BLE001
        pass
    return txt


def render_drawings(
    ax: Axes,
    drawings: Sequence[Drawing],
) -> list[Line2D]:
    """Draw every hline in ``drawings`` on ``ax``.

    Returns the :class:`~matplotlib.lines.Line2D` artists in the
    same order so the caller can :meth:`Line2D.remove` them on the
    next render. Each artist carries a ``gid`` of the form
    ``"drawing:<uuid>"`` so :meth:`Axes.findobj` searches and pick
    events can recover the originating drawing id. ``picker=True``
    is set so matplotlib's pick events fire for future drag
    support; the primary hit-test path is :func:`pick_drawing`,
    not the pick event.

    Each drawing's optional ``label`` is rendered as a small pill
    at the right edge of ``ax`` via :func:`_render_label`. The
    label :class:`~matplotlib.text.Text` artist is **not** included
    in the returned list (callers only ever needed the line
    artists for hit-test gid lookup); the figure's normal
    ``fig.clear()`` between renders removes the labels along with
    every other axes artist.

    Errors building a single artist are swallowed so one bad
    drawing (e.g. NaN price after a manual edit) does not blank
    the rest of the chart.
    """
    artists: list[Line2D] = []
    for d in drawings:
        if d.kind != "hline":
            continue
        try:
            line = ax.axhline(
                y=float(d.price),
                color=d.color,
                linewidth=float(d.width),
                linestyle=_LINESTYLE_MAP.get(d.style, "-"),
                zorder=DRAWING_ZORDER,
                picker=True,
            )
            try:
                line.set_gid(f"{DRAWING_GID_PREFIX}{d.id}")
            except Exception:  # noqa: BLE001
                pass
            artists.append(line)
        except Exception:  # noqa: BLE001
            continue
        # Label rendering is independent of line creation success
        # so a malformed label doesn't blank the line, and a
        # malformed line wouldn't have an axes to label anyway
        # (we'd have continue'd above).
        try:
            _render_label(ax, d)
        except Exception:  # noqa: BLE001
            pass
    return artists


def clear_drawing_artists(ax: Axes) -> int:
    """Remove every drawing-related artist from ``ax``.

    Locates artists by their ``gid`` (lines tagged with
    :data:`DRAWING_GID_PREFIX`, label pills with
    :data:`DRAWING_LABEL_GID_PREFIX`) and calls ``artist.remove()``
    on each. Returns the number of artists removed.

    Used by the drawings-only fast-path repaint in
    :meth:`tradinglab.app.ChartApp._repaint_drawings_only`, which
    swaps out drawing artists between full ``_render`` cycles
    without re-running candles / indicators / volume (audit
    ``redraw-overlay-perf``).

    Errors removing a single artist are swallowed so one stuck
    artist doesn't leave the axes in a half-cleared state.
    """
    removed = 0
    try:
        # ``ax.lines`` and ``ax.texts`` are list-like; iterate over
        # a snapshot so ``artist.remove()`` doesn't corrupt the
        # iteration.
        line_snapshot = list(getattr(ax, "lines", ()))
        text_snapshot = list(getattr(ax, "texts", ()))
    except Exception:  # noqa: BLE001
        return 0
    for line in line_snapshot:
        try:
            gid = line.get_gid()
        except Exception:  # noqa: BLE001
            continue
        if gid and isinstance(gid, str) and gid.startswith(DRAWING_GID_PREFIX):
            try:
                line.remove()
                removed += 1
            except Exception:  # noqa: BLE001
                pass
    for text in text_snapshot:
        try:
            gid = text.get_gid()
        except Exception:  # noqa: BLE001
            continue
        if gid and isinstance(gid, str) and gid.startswith(DRAWING_LABEL_GID_PREFIX):
            try:
                text.remove()
                removed += 1
            except Exception:  # noqa: BLE001
                pass
    return removed


def pick_drawing(
    drawings: Sequence[Drawing],
    ax: Axes,
    x_disp: float,
    y_disp: float,
    *,
    tol_px: float = 5.0,
) -> Drawing | None:
    """Return the drawing closest to display point ``(x_disp, y_disp)``
    within ``tol_px``, or ``None``.

    ``x_disp`` is currently unused for ``kind="hline"`` (lines
    span the full x-extent) but the parameter is kept in the
    signature so future ``kind="rect"`` and ``kind="trend"`` can
    use the same call shape without churn.

    ``tol_px`` is interpreted as **logical pixels at
    :data:`PICK_TOLERANCE_REFERENCE_DPI` (96 DPI)**; the function
    scales the threshold by ``fig.dpi / 96.0`` so the click target
    keeps pace with the rendered line thickness on HiDPI / Retina
    / 4K displays. The scale factor has a floor of 1.0 so legacy
    displays don't lose tolerance below the caller's request.
    Audit ``pick-tolerance-dpi``.

    Closest-wins; ties are resolved by most-recently-added (last
    in the input list). ``ax`` is required so we can convert each
    drawing's data-coord ``price`` to display pixels.
    """
    if not drawings:
        return None
    try:
        transform = ax.transData
    except Exception:  # noqa: BLE001
        return None
    try:
        dpi = float(ax.figure.dpi)
    except (AttributeError, TypeError, ValueError):
        dpi = PICK_TOLERANCE_REFERENCE_DPI
    scale = max(1.0, dpi / PICK_TOLERANCE_REFERENCE_DPI)
    effective_tol = tol_px * scale
    best: tuple[float, int, Drawing] | None = None
    for idx, d in enumerate(drawings):
        if d.kind != "hline":
            continue
        try:
            _, y_d = transform.transform((0.0, float(d.price)))
        except Exception:  # noqa: BLE001
            continue
        dist = abs(float(y_disp) - float(y_d))
        if dist > effective_tol:
            continue
        # idx tiebreaker: prefer larger idx (most recently added).
        if best is None or dist < best[0] or (
                dist == best[0] and idx > best[1]):
            best = (dist, idx, d)
    return best[2] if best else None


def drawing_id_from_gid(gid: str | None) -> str | None:
    """Extract the drawing id from an artist's ``gid``.

    Returns ``None`` if ``gid`` isn't a drawing gid. Used by the
    matplotlib-pick-event fallback path (kept for future drag
    support). Label artists are tagged with
    ``DRAWING_LABEL_GID_PREFIX`` instead — call this with their
    gid to retrieve the underlying drawing id, the prefix is
    stripped transparently.
    """
    if not gid or not isinstance(gid, str):
        return None
    if gid.startswith(DRAWING_GID_PREFIX):
        return gid[len(DRAWING_GID_PREFIX):] or None
    if gid.startswith(DRAWING_LABEL_GID_PREFIX):
        return gid[len(DRAWING_LABEL_GID_PREFIX):] or None
    return None


__all__ = [
    "DRAWING_GID_PREFIX",
    "DRAWING_LABEL_GID_PREFIX",
    "DRAWING_LABEL_ZORDER",
    "DRAWING_ZORDER",
    "clear_drawing_artists",
    "PICK_TOLERANCE_REFERENCE_DPI",
    "drawing_id_from_gid",
    "pick_drawing",
    "render_drawings",
]
