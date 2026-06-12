"""Pure math for computing y-limits and virtualized render ranges."""
from __future__ import annotations

import numpy as np

from .series import SeriesArrays

RENDER_BUFFER_MULTIPLIER = 3


def y_limits_for_slice(
    series: SeriesArrays, kind: str, start: int, end: int,
    *, log: bool = False,
) -> tuple[float, float] | None:
    """Compute (ymin, ymax) with padding for the visible slice of a series.

    ``kind`` is ``"price"`` (use high/low envelope + 5% pad) or ``"volume"``
    (0 to 1.1 * max). Uses NaN-aware reductions so gap candles (NaN prices)
    are transparently skipped. Returns ``None`` when the slice contains no
    finite data — callers should leave the axis limits as-is in that case
    (matplotlib raises if you hand it NaN/Inf ylims).

    When ``log=True`` (price kind only), padding is applied
    multiplicatively in log space — roughly a 5% fatter decade on each
    side. This prevents the linear pad from producing a negative
    ``ymin`` (illegal on a log axis) and from nudging the limits past a
    decade boundary, which would cause :class:`LogLocator` to round the
    view out to the next whole decade and shrink the candles to half
    the window.
    """
    if kind == "price":
        lows = series.lows[start:end]
        highs = series.highs[start:end]
        if lows.size == 0 or not np.any(np.isfinite(lows)):
            return None
        lo = float(np.nanmin(lows))
        hi = float(np.nanmax(highs))
        # Asymmetric padding: extra top headroom reserves visual space
        # for the always-on top-left OHLCV / %change readout strip
        # (gui/interaction §11.6) so the highest bar never collides with
        # the box. Bottom keeps the original 5% so the chart still feels
        # vertically centered. Tuned to roughly match TradingView's
        # default top headroom. Both fractions are user-overridable via
        # settings.json["price_top_pad_frac"] / "price_bot_pad_frac".
        from .. import defaults as _defaults
        TOP_PAD_FRAC = _defaults.get("price_top_pad_frac")
        BOT_PAD_FRAC = _defaults.get("price_bot_pad_frac")
        if log and lo > 0.0 and hi > 0.0:
            ratio = hi / lo
            top_mult = ratio ** TOP_PAD_FRAC
            bot_mult = ratio ** BOT_PAD_FRAC
            return lo / bot_mult, hi * top_mult
        span = hi - lo
        if span <= 0:
            # Flat slice — fall back to a small absolute pad so axis
            # limits stay well-formed (matplotlib chokes on lo == hi).
            pad = max(hi * 0.01, 1.0)
            return lo - pad, hi + pad
        return lo - span * BOT_PAD_FRAC, hi + span * TOP_PAD_FRAC
    vols = series.volumes[start:end]
    if vols.size == 0:
        return None
    vmax = float(np.nanmax(vols)) or 1.0
    return 0.0, vmax * 1.1


def remap_window_by_time(
    prev_dates,
    prev_xlim: tuple[float, float],
    new_dates,
) -> tuple[int, int] | None:
    """Remap a bar-index xlim from ``prev_dates`` to ``new_dates`` by time.

    Used by ticker-switch reloads: the user is viewing bars
    ``prev_dates[lo:hi]`` and we want the *equivalent calendar window*
    in the freshly-loaded symbol's bar series. Returns the integer
    ``(lo, hi)`` slice in ``new_dates`` whose timestamps best cover
    the source window, or ``None`` if remapping is degenerate
    (insufficient overlap, empty inputs, or invalid xlim).

    Semantics:
      * ``prev_xlim`` is in matplotlib bar-index float space; rounded
        and clamped to ``[0, len(prev_dates) - 1]``.
      * ``lo`` in the new series = greatest index whose date ≤ source
        ``t_lo``, snapping to 0 if the source window starts before all
        new bars.
      * ``hi`` in the new series = greatest index whose date ≤ source
        ``t_hi`` (so the bar AT ``t_hi`` is included), clamped to
        ``len(new_dates) - 1`` if the source extends past the new
        series' end.
      * Result is a half-open ``[lo, hi)`` slice with ``hi > lo``.
        Returns ``None`` when the remapped window has zero or one bar
        (caller should fall back to default windowing).
      * Returns ``None`` when the SOURCE window spans the entire source
        series (``lo_i == 0`` AND ``hi_i == len(prev_dates) - 1``): viewing
        a symbol's whole (often tiny) history is not a deliberate zoom, so
        there is no calendar selection to preserve. This is the IPO /
        short-history guard — a 2-bar source must not crush a long-history
        destination to 2 bars. A proper sub-window (however narrow) is
        still remapped.

    Pure: takes any sequence of comparable timestamps (datetime,
    np.datetime64, etc.) so it's testable without Tk/matplotlib.
    """
    if not prev_dates or not new_dates:
        return None
    try:
        lo_f, hi_f = float(prev_xlim[0]), float(prev_xlim[1])
    except Exception:  # noqa: BLE001
        return None
    if hi_f - lo_f <= 1.5:
        return None
    n_prev = len(prev_dates)
    lo_i = max(0, min(n_prev - 1, int(round(lo_f))))
    hi_i = max(0, min(n_prev - 1, int(round(hi_f))))
    if hi_i <= lo_i:
        return None
    # Intent guard: when the source window spans the ENTIRE source series
    # (first bar through last), the user was NOT zoomed into a sub-window —
    # there is no calendar selection worth carrying onto the new symbol.
    # This is the IPO / very-short-history case (e.g. a 2-bar chart showing
    # all it has): preserving it would crush a long-history destination
    # (e.g. AMD) down to ~2 bars. Returning None makes the caller fall back
    # to its default right-edge window. A deliberate sub-window
    # (``lo_i > 0`` OR ``hi_i < n_prev - 1``), however narrow, is still
    # preserved. See viewport.spec.md.
    if lo_i <= 0 and hi_i >= n_prev - 1:
        return None
    try:
        t_lo = prev_dates[lo_i]
        t_hi = prev_dates[hi_i]
    except Exception:  # noqa: BLE001
        return None
    rmap_lo = -1
    rmap_hi = -1
    for i, d in enumerate(new_dates):
        if d <= t_lo:
            rmap_lo = i
        if d <= t_hi:
            rmap_hi = i
        else:
            break
    if rmap_lo < 0:
        rmap_lo = 0
    if rmap_hi < 0:
        rmap_hi = min(len(new_dates) - 1, rmap_lo)
    if rmap_hi <= rmap_lo:
        return None
    return rmap_lo, rmap_hi + 1


def compute_render_range(
    visible_lo: int, visible_hi: int, n: int,
    min_size: int, max_size: int,
) -> tuple[int, int]:
    """Compute a ``[start, end)`` slice centered on the visible window.

    The rendered range is ``visible_count * RENDER_BUFFER_MULTIPLIER`` wide
    (clamped to ``[min_size, max_size]``), centered on the visible window,
    and clipped to ``[0, n]``.
    """
    visible_lo = max(0, min(n, visible_lo))
    visible_hi = max(visible_lo, min(n, visible_hi))
    span = max(1, visible_hi - visible_lo)
    target = span * RENDER_BUFFER_MULTIPLIER
    target = max(min_size, min(max_size, target))
    if target >= n:
        return 0, n

    center = (visible_lo + visible_hi) // 2
    start = max(0, center - target // 2)
    end = min(n, start + target)
    if end - start < target:
        start = max(0, end - target)
    return start, end

