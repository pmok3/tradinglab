"""Chart overlay: time-of-day shading on 1d volume bars.

Visualises **where the current time-of-day sits within each historical
trading session's volume profile**. The user wants to see, at a glance,
whether today's session-so-far volume is heavier, lighter, or roughly
typical for the wall-clock minute they're standing in — without reading
a number off an indicator.

Visual contract (plan.md decisions 1–18 for this feature)
---------------------------------------------------------

For every visible **1d** bar in the slice, we paint two extra collections
on top of the existing volume bar (rendered by
:func:`tradinglab.rendering.draw_volume`):

1. **Outline envelope** — a hollow rectangle at the same x-width as the
   volume bar, with height = the bar's full-day volume. Drawn in the
   bar's own bull/bear hue, run through
   :func:`tradinglab.rendering.darker_shade` so it reads as a
   "same-colour-but-darker" frame around the bar (decision 17).
2. **Realized solid fill** — a filled rectangle at the same x-width,
   height = the bar's full-day volume × *(realized minutes / RTH
   minutes)*, using each day's intraday 5-minute bars to compute the
   exact realized fraction at the reference time-of-day. Same colour as
   the volume bar's normal fill.

Behavioural rules (decisions 4–12, 14–15):

* **Reference time-of-day** comes from the sandbox replay clock when a
  sandbox session is active, else wall-clock (decision 4).
* **RTH only** — the cumulative ignores pre- and post-market bars
  (decision 5). The fraction-of-day denominator is the 09:30→16:00 ET
  span (390 minutes; half-day sessions latch when their actual close is
  reached — decision 11).
* **Pre-9:30 ET** (wall-clock path) — suppress entirely. Bars render
  with no outline / no fill overlay, identical to feature-off
  (decision 6). The sandbox-mid-replay-pre-open case is decision 12:
  full outline, 0 % fill (the bar is the envelope only).
* **Post-16:00 ET** — latch "session complete": outline matches the
  solid fill exactly (100 % filled), so a closed past day reads as a
  conventional fully-solid bar (decision 7).
* **Missing intraday data** for a given day — render the day's volume
  bar fully solid with no outline (degrade to feature-off look,
  decision 8).
* **Median tick** — a thin neutral horizontal line at *median(full-day
  volume over the prior N RTH days)*, drawn across each visible bar
  at the median height. Lets the trader compare each day's full-day
  envelope against the rolling median without a numeric readout
  (decision 14, with N from the ``volume_tod_median_lookback_days``
  tunable, default 20 — decision 15). Style: ``axis_text``-coloured,
  thin (decision 18). Drawn once per visible bar when enough lookback
  is available.

Pure-functional surface
-----------------------

This module mirrors the
:mod:`tradinglab.gui.events_overlay` pattern: pure-functional
:func:`compute_volume_tod_patches` (math) + :func:`draw_volume_tod_patches`
(matplotlib artist build) + :func:`clear_volume_tod_artists` (teardown).
No Tk state, no class instance, no module-level cache; the caller
(``ChartApp._render_volume_tod_for_slot``) owns the artist refs so they
can be torn down between renders.

Determinism
-----------

This is a visual-only overlay. Nothing it computes lands in
:class:`SessionResult`, the journal, the engine, or any persisted state.
A user can flip ``volume_tod_enabled`` mid-session and the sandbox
output stays byte-identical — locked in by ``check_b68`` in the smoke
gate.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import (
    Any,
)

from matplotlib.axes import Axes
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.colors import to_rgba

from .. import constants as _constants
from ..core.timezones import get_et
from ..models import Candle
from ..rendering import _BODY_HALF, darker_shade

# Z-order: just above the volume bars (zorder 2 in rendering.draw_volume)
# but below the volume-pane grid spine (5). Outline frame at 2.6 sits
# above the solid fill at 2.5 so the frame's strokes paint on top.
_ZORDER_SOLID_FILL = 2.5
_ZORDER_OUTLINE = 2.6
_ZORDER_MEDIAN_TICK = 2.8

# Alpha used for the outline (envelope) frame. Solid fill uses the bar's
# native alpha. Outline alpha is intentionally higher than the bar to
# make the envelope read crisply against the bar's fill.
_OUTLINE_ALPHA = 0.90
_OUTLINE_LINEWIDTH = 0.9

# Median-tick visual style. Neutral grey via theme["axis_text"] (decision
# 18). Slightly translucent so it sits gently over the bar.
_MEDIAN_TICK_ALPHA = 0.65
_MEDIAN_TICK_LINEWIDTH = 1.0
_MEDIAN_TICK_HALF_WIDTH = _BODY_HALF  # matches the bar's x-extent

# RTH window in ET minutes-of-day (09:30 → 16:00 = 390 minutes).
_RTH_OPEN_MIN = 9 * 60 + 30   # 570
_RTH_CLOSE_MIN = 16 * 60      # 960
_RTH_SPAN_MIN = _RTH_CLOSE_MIN - _RTH_OPEN_MIN  # 390


def _candle_date_key(c: Candle) -> date | None:
    """Return the day-resolution key matching :mod:`events.render`'s convention.

    Uses **UTC date** for tz-aware datetimes (yfinance disk pickles
    carry ``America/New_York``-aware datetimes; the daily bar at
    midnight ET = 05:00 UTC stays on the trading day's UTC date) and
    the naive date as-is otherwise. This is the same rule
    :func:`tradinglab.events.render._bar_index_for_ts` uses, so a
    daily bar and an intraday bar on the same trading day share the
    same key regardless of which side carries tzinfo.

    The narrow window where UTC and ET disagree (00:00–05:00 UTC =
    19:00–00:00 prev-day ET) is post-market and filtered by the
    ``rth_only`` filter anyway, so the day-boundary mismatch is
    unobservable in v1.
    """
    d = c.date
    tz = getattr(d, "tzinfo", None)
    if tz is not None:
        try:
            d = d.astimezone(timezone.utc)
        except Exception:  # noqa: BLE001
            d = d.replace(tzinfo=None)
    return d.date()


def _candle_et_minute_of_day(c: Candle) -> int | None:
    """Return ``hour*60 + minute`` in ET for one candle's bar-open time.

    yfinance disk pickles preserve ``America/New_York``-aware datetimes,
    so we convert to ET when ``tzinfo`` is present. Tz-naive datetimes
    are assumed to be in ET wall-clock (the codebase convention) and
    consumed as-is.
    """
    d = c.date
    tz = getattr(d, "tzinfo", None)
    if tz is not None:
        et = get_et()
        if et is not None:
            try:
                d = d.astimezone(et)
            except Exception:  # noqa: BLE001
                d = d.replace(tzinfo=None)
        else:
            d = d.replace(tzinfo=None)
    return d.hour * 60 + d.minute


def _epoch_ms_to_et_minute(epoch_ms: int) -> int | None:
    """Convert UTC epoch-ms to an ET minute-of-day (0..1439).

    Returns ``None`` when ``ZoneInfo`` / tzdata isn't available — the
    caller treats this as a "can't compute time-of-day reference"
    signal and the overlay degrades to feature-off.
    """
    et = get_et()
    if et is None:
        return None
    try:
        dt = datetime.fromtimestamp(epoch_ms / 1000.0, et)
    except Exception:  # noqa: BLE001
        return None
    return dt.hour * 60 + dt.minute


def _bar_base_color(c: Candle) -> tuple[float, float, float, float]:
    """Return the bar's native body fill colour (mirrors vol_geometry).

    Replicates the colour resolution in
    :func:`tradinglab.rendering.vol_geometry` so the overlay's solid
    fill matches the underlying ``draw_volume`` bar exactly.
    """
    base = _constants.BULL_COLOR if c.is_bull else _constants.BEAR_COLOR
    extended_alpha = 0.45 if c.is_extended else 1.0
    return to_rgba(base, 0.7 * extended_alpha)


@dataclass
class VolumeTodPatch:
    """Per-1d-bar geometry + metadata produced by the math layer.

    ``bar_index`` is the index into the slot's candle list (matches the
    convention used by :class:`tradinglab.gui.events_overlay`). The
    consumer adds ``offset`` from ``_panel_state[slot]['offset']`` at
    draw time so the X coordinate aligns with the volume bar.

    ``outline_height`` and ``filled_height`` are both in the same units
    as ``Candle.volume`` (raw shares-traded), so the overlay aligns
    pixel-for-pixel with the underlying ``draw_volume`` bar regardless
    of the volume axis's autoscale state.

    ``has_intraday`` is False when the day's 5m bars are missing — the
    consumer renders the bar fully solid in that case (decision 8).

    ``is_session_pre_open`` is True when the reference time-of-day for
    this bar is before 09:30 ET (sandbox-rewind case, decision 12). The
    consumer paints outline only, zero fill.

    ``median_height`` is the prior-N-day median full-day volume for the
    rolling tick (decision 14). Zero means "not enough history" and the
    consumer skips the tick for this bar.
    """
    bar_index: int
    full_day_volume: float
    outline_height: float
    filled_height: float
    has_intraday: bool
    is_session_pre_open: bool
    base_color: tuple[float, float, float, float]
    median_height: float = 0.0


@dataclass
class VolumeTodArtists:
    """Render output: per-collection artist refs.

    Stashed on ``panel_state[slot]['vol_tod_artists']`` so the next
    ``_reset_slot_artists`` can clear them in one pass.
    """
    artists: list[Any] = field(default_factory=list)
    patches: list[VolumeTodPatch] = field(default_factory=list)


def clear_volume_tod_artists(artists: Sequence[Any]) -> None:
    """Remove each artist from its axes; ignore detached / removed ones.

    Mirrors :func:`tradinglab.gui.events_overlay.clear_event_glyph_artists`.
    Idempotent — safe to call on an already-cleared list.
    """
    for a in artists:
        try:
            a.remove()
        except Exception:  # noqa: BLE001
            pass


def _group_intraday_by_et_date(
    intraday: Sequence[Candle], *, rth_only: bool,
) -> dict[date, list[tuple[int, float]]]:
    """Build ``{et_date: [(minute_of_day, volume), ...]}``.

    Lists are kept in chronological order (matches the input order,
    which the data layer already guarantees). Pre/post bars are
    filtered when ``rth_only`` is True; "gap" placeholder bars are
    always filtered.
    """
    out: dict[date, list[tuple[int, float]]] = {}
    for c in intraday:
        if c.is_gap:
            continue
        if rth_only and c.session != "regular":
            continue
        key = _candle_date_key(c)
        if key is None:
            continue
        m = _candle_et_minute_of_day(c)
        if m is None:
            continue
        out.setdefault(key, []).append((int(m), float(c.volume)))
    return out


def _realized_at_tod(
    day_bars: Sequence[tuple[int, float]], cutoff_minute: int,
) -> tuple[float, float]:
    """Return ``(realized_vol, full_day_vol)`` for one trading day.

    ``cutoff_minute`` is the reference time-of-day in ET minutes-of-day.
    A 5m bar with start-minute ``m`` is counted toward ``realized`` iff
    ``m < cutoff_minute`` — i.e., the bar must have STARTED strictly
    before the reference time. This matches the "volume so far up to
    10:00am" semantics: at exactly 10:00am, the 10:00–10:05 bar hasn't
    started accumulating yet, so it's excluded. At 10:00:01am the same
    bar is partially recorded but the discrete 5m representation only
    surfaces it after it seals at 10:05am.

    ``full_day_vol`` is the sum across the whole day's filtered list
    (RTH or all, depending on the upstream filter).
    """
    realized = 0.0
    total = 0.0
    for m, v in day_bars:
        total += v
        if m < cutoff_minute:
            realized += v
    return (realized, total)


def _compute_median_tick_height(
    candle_full_day_volumes: Sequence[float],
    window_end_idx: int,
    lookback: int,
) -> float:
    """Median of the previous ``lookback`` valid (>0) daily volumes.

    Window is ``[max(0, window_end_idx - lookback), window_end_idx)`` —
    strictly before the current bar to avoid look-ahead. Returns 0.0
    when the window has fewer than ``lookback // 2`` valid entries
    (a soft floor that keeps the tick stable across cold-start renders;
    the alternative — showing a wildly noisy median on day 3 of history
    — fails the "calmer-than-numeric-readout" goal of the feature).
    """
    if lookback <= 0 or window_end_idx <= 0:
        return 0.0
    start = max(0, window_end_idx - lookback)
    window = candle_full_day_volumes[start:window_end_idx]
    valid = [v for v in window if v and v > 0.0]
    min_valid = max(1, lookback // 2)
    if len(valid) < min_valid:
        return 0.0
    valid_sorted = sorted(valid)
    n = len(valid_sorted)
    if n % 2 == 1:
        return float(valid_sorted[n // 2])
    return float((valid_sorted[n // 2 - 1] + valid_sorted[n // 2]) / 2.0)


def compute_volume_tod_patches(
    candles: Sequence[Candle],
    intraday: Sequence[Candle],
    *,
    now_ms: int,
    slice_start: int,
    slice_end: int,
    rth_only: bool = True,
    median_lookback_days: int = 20,
    sandbox_active: bool = False,
) -> list[VolumeTodPatch]:
    """Build one :class:`VolumeTodPatch` per non-gap daily bar in slice.

    The math layer is purely functional — no Tk, no matplotlib, no app
    state. It accepts:

    candles
        The full daily candle list for the slot. ``slice_start`` /
        ``slice_end`` index into it. We need the full list (not just
        the slice) because the median-tick lookback may reach
        ``median_lookback_days`` bars to the LEFT of ``slice_start``.
    intraday
        The 5-minute candle list for the same symbol, ideally covering
        every visible day. Missing days degrade to ``has_intraday=False``.
    now_ms
        UTC epoch-ms reference time. Sandbox clock when active, else
        ``time.time() * 1000``.
    slice_start, slice_end
        ``[slice_start, slice_end)`` is the visible range; one patch
        per non-gap bar inside it.
    rth_only
        Restrict the intraday source to RTH bars (decision 5).
    median_lookback_days
        Trading-day lookback for the rolling median full-day tick
        (decision 15).
    sandbox_active
        True when the chart is in sandbox mode. Distinguishes decision
        6 (live wall-clock pre-open → suppress entirely → ``has_intraday
        = False``) from decision 12 (sandbox-rewind pre-open → full
        envelope, 0 % fill → ``is_session_pre_open = True``).

    Returns
    -------
    A list of :class:`VolumeTodPatch`. May be empty (no visible bars,
    or unable to resolve the ET clock).
    """
    if slice_end <= slice_start:
        return []
    if slice_start < 0:
        slice_start = 0
    if slice_end > len(candles):
        slice_end = len(candles)
    ref_minute = _epoch_ms_to_et_minute(int(now_ms))
    if ref_minute is None:
        return []

    # Precompute the per-day intraday lookup once. Cheap to do here;
    # callers cache the input at a higher level via `app._full_cache`.
    by_day = _group_intraday_by_et_date(intraday, rth_only=rth_only)

    # Full-day volume array (parallel to ``candles``) so the median
    # tick can scan backwards without reissuing the grouping pass.
    full_day_vols: list[float] = []
    for c in candles:
        if c.is_gap:
            full_day_vols.append(0.0)
        else:
            full_day_vols.append(float(c.volume))

    pre_open = ref_minute < _RTH_OPEN_MIN
    post_close = ref_minute >= _RTH_CLOSE_MIN
    # Clamp the cutoff into the RTH window. Pre-open => 0 (nothing
    # realized yet). Post-close => RTH_CLOSE so the strict-less-than
    # comparison in _realized_at_tod catches the final 15:55 bar.
    if pre_open:
        cutoff = 0  # nothing in RTH has start-minute < 0
    elif post_close:
        cutoff = _RTH_CLOSE_MIN  # all 78 RTH bars satisfy m < 960
    else:
        cutoff = int(ref_minute)

    out: list[VolumeTodPatch] = []
    for idx in range(slice_start, slice_end):
        c = candles[idx]
        if c.is_gap:
            continue
        bar_date = _candle_date_key(c)
        day_bars = by_day.get(bar_date) if bar_date is not None else None
        base_color = _bar_base_color(c)
        full_day = float(c.volume)

        median_h = _compute_median_tick_height(
            full_day_vols, idx, median_lookback_days,
        )

        if pre_open and not sandbox_active:
            # Decision 6: live wall-clock pre-open → suppress entirely.
            # ``has_intraday=False`` makes the consumer skip the overlay,
            # leaving the default ``draw_volume`` bar untouched.
            out.append(VolumeTodPatch(
                bar_index=idx, full_day_volume=full_day,
                outline_height=0.0, filled_height=0.0,
                has_intraday=False, is_session_pre_open=True,
                base_color=base_color, median_height=median_h,
            ))
            continue

        if not day_bars:
            # Decision 8: missing intraday for this day → render fully
            # solid (no outline). The consumer falls back to the default
            # bar appearance for this index.
            out.append(VolumeTodPatch(
                bar_index=idx, full_day_volume=full_day,
                outline_height=0.0, filled_height=0.0,
                has_intraday=False, is_session_pre_open=False,
                base_color=base_color, median_height=median_h,
            ))
            continue

        # Decision 12: sandbox + pre-open → full outline, 0 % fill.
        if pre_open and sandbox_active:
            out.append(VolumeTodPatch(
                bar_index=idx, full_day_volume=full_day,
                outline_height=full_day, filled_height=0.0,
                has_intraday=True, is_session_pre_open=True,
                base_color=base_color, median_height=median_h,
            ))
            continue

        realized, intraday_total = _realized_at_tod(day_bars, cutoff)
        # Use candle.volume as the authoritative envelope height (some
        # providers' 5m sums don't match 1d to the last share). Scale
        # by intraday fraction.
        if post_close:
            # Decision 7: outline matches solid (fully filled).
            filled = full_day
            outline = full_day
        else:
            if intraday_total > 0.0:
                frac = max(0.0, min(1.0, realized / intraday_total))
            else:
                frac = 0.0
            filled = full_day * frac
            outline = full_day

        out.append(VolumeTodPatch(
            bar_index=idx, full_day_volume=full_day,
            outline_height=outline, filled_height=filled,
            has_intraday=True, is_session_pre_open=False,
            base_color=base_color, median_height=median_h,
        ))

    return out


def draw_volume_tod_patches(
    ax_v: Axes,
    patches: Sequence[VolumeTodPatch],
    *,
    offset: int,
    theme: Mapping[str, Any],
    dark_mode: bool,
    show_median_tick: bool = True,
) -> VolumeTodArtists:
    """Project ``patches`` into matplotlib artists on the volume axes.

    Three collections are added:

    * **Solid-fill** :class:`PolyCollection` — realized portion of each
      bar at its native bull/bear hue (matches ``draw_volume``).
    * **Outline** :class:`PolyCollection` — full-day envelope frame at
      the same hue run through :func:`darker_shade`, no fill.
    * **Median tick** :class:`LineCollection` — neutral horizontal
      reference line at the rolling-median full-day height (decision
      14/18).

    Patches with ``has_intraday=False`` contribute neither a solid nor
    an outline (the underlying ``draw_volume`` bar shows through
    untouched). Patches with ``is_session_pre_open=True`` AND
    ``has_intraday=True`` (sandbox-rewind path) contribute only the
    outline. The median tick is drawn for every patch whose
    ``median_height > 0`` regardless of fill state.
    """
    out = VolumeTodArtists(patches=list(patches))
    if ax_v is None or not patches:
        return out

    solid_verts: list[Any] = []
    solid_colors: list[Any] = []
    outline_verts: list[Any] = []
    outline_colors: list[Any] = []
    median_segments: list[Any] = []

    median_color = _color_from_theme(theme)

    for p in patches:
        if not p.has_intraday:
            continue
        x_center = float(p.bar_index + offset)
        x0 = x_center - _BODY_HALF
        x1 = x_center + _BODY_HALF
        if p.filled_height > 0.0:
            solid_verts.append((
                (x0, 0.0), (x0, p.filled_height),
                (x1, p.filled_height), (x1, 0.0),
            ))
            solid_colors.append(p.base_color)
        if p.outline_height > 0.0:
            outline_verts.append((
                (x0, 0.0), (x0, p.outline_height),
                (x1, p.outline_height), (x1, 0.0),
            ))
            outline_colors.append(
                darker_shade(p.base_color, dark_mode=dark_mode)
            )

    # Median tick segments — drawn for every patch with a valid median,
    # whether or not it has intraday (decision 14: reference is the
    # rolling median, independent of the per-bar fill semantics).
    if show_median_tick:
        for p in patches:
            if p.median_height <= 0.0:
                continue
            x_center = float(p.bar_index + offset)
            x0 = x_center - _MEDIAN_TICK_HALF_WIDTH
            x1 = x_center + _MEDIAN_TICK_HALF_WIDTH
            median_segments.append((
                (x0, p.median_height), (x1, p.median_height),
            ))

    if solid_verts:
        try:
            solids = PolyCollection(
                solid_verts, facecolors=solid_colors,
                edgecolors=solid_colors, linewidths=0.0,
                zorder=_ZORDER_SOLID_FILL,
            )
            ax_v.add_collection(solids)
            out.artists.append(solids)
        except Exception:  # noqa: BLE001 — overlay must never block render
            pass
    if outline_verts:
        try:
            outlines = PolyCollection(
                outline_verts, facecolors="none",
                edgecolors=[_with_alpha(c, _OUTLINE_ALPHA)
                            for c in outline_colors],
                linewidths=_OUTLINE_LINEWIDTH,
                zorder=_ZORDER_OUTLINE,
            )
            ax_v.add_collection(outlines)
            out.artists.append(outlines)
        except Exception:  # noqa: BLE001
            pass
    if median_segments:
        try:
            ticks = LineCollection(
                median_segments,
                colors=[_with_alpha(median_color, _MEDIAN_TICK_ALPHA)]
                       * len(median_segments),
                linewidths=_MEDIAN_TICK_LINEWIDTH,
                zorder=_ZORDER_MEDIAN_TICK,
            )
            ax_v.add_collection(ticks)
            out.artists.append(ticks)
        except Exception:  # noqa: BLE001
            pass

    return out


def _color_from_theme(theme: Mapping[str, Any]) -> tuple[float, float, float, float]:
    """Resolve a neutral text-like colour from the active theme."""
    if isinstance(theme, dict):
        for key in ("axis_text", "spine", "text"):
            v = theme.get(key)
            if isinstance(v, str) and v:
                try:
                    return to_rgba(v)
                except Exception:  # noqa: BLE001
                    continue
    return to_rgba("#7d8794")


def _with_alpha(
    rgba: tuple[float, float, float, float], alpha: float,
) -> tuple[float, float, float, float]:
    """Return ``rgba`` with its alpha channel replaced."""
    return (rgba[0], rgba[1], rgba[2], float(alpha))


def patches_should_suppress_default_fill(
    patches: Iterable[VolumeTodPatch],
) -> dict[int, bool]:
    """Return ``{bar_index: True}`` for bars whose default volume fill
    must be hidden so the overlay's solid+outline can paint cleanly.

    Used by :meth:`ChartApp._render_volume_tod_for_slot` to mutate the
    underlying ``vol_bars`` PolyCollection's per-bar facecolor to
    transparent for any bar that the overlay is providing its own
    solid fill for. Suppressing only the affected indices preserves
    bars where the overlay degrades (missing intraday → keep the
    default solid bar).
    """
    out: dict[int, bool] = {}
    for p in patches:
        if p.has_intraday and not p.is_session_pre_open:
            out[int(p.bar_index)] = True
        elif p.has_intraday and p.is_session_pre_open:
            # Sandbox-rewind: overlay shows outline only, 0 % fill.
            # Suppress the default bar so the empty envelope reads.
            out[int(p.bar_index)] = True
    return out


__all__ = (
    "VolumeTodPatch",
    "VolumeTodArtists",
    "compute_volume_tod_patches",
    "draw_volume_tod_patches",
    "clear_volume_tod_artists",
    "patches_should_suppress_default_fill",
)
