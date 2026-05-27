"""Headless per-trade chart screenshot rendering.

A trade screenshot is the picture in the Strategy Tester Report that
shows *where* a mechanically-fired trade actually got taken on the
chart, with entry / exit / MAE / MFE annotations. It is generated
**fully headless** — no Tk, no pyplot — by composing the live chart's
:mod:`tradinglab.rendering` primitives against a fresh
:class:`matplotlib.figure.Figure` and rasterising the result with
:class:`matplotlib.backends.backend_agg.FigureCanvasAgg`.

Two constraints shape the design:

1.  **Visual parity with the live chart.** A bar drawn here must
    overlap pixel-for-pixel (up to dpi / size) with what
    ``ChartApp._render`` would draw for the same slice of candles —
    we reuse ``draw_candlesticks`` / ``draw_volume`` /
    ``setup_price_axes`` / ``setup_volume_axes`` rather than
    reimplementing them, so palette tweaks, body half-width sizing,
    and session-shading rules all propagate automatically.

2.  **Zero Tk surface.** Workers in :mod:`runner` are off-thread and
    may not touch Tk. ``matplotlib.figure.Figure`` (constructed
    directly, not via ``pyplot.figure``) + ``FigureCanvasAgg`` is
    Tk-free by construction.

Public surface — only :func:`render_trade_screenshot` and the
:class:`ScreenshotSpec` knob bag.

The window selection rule (see Design notes in plan.md):

* Start at ``max(0, entry_index - PRE_BARS)``.
* End at ``min(len(candles), exit_index + POST_BARS)``.
* If the resulting window exceeds ``MAX_BARS`` bars, clip from the
  *left* (preserve the exit) — long-running trades will still show
  the recent context, just less pre-entry runway.

The output filename convention is ``<symbol>_<order_id>_post.png``
(matches :func:`tradinglab.backtest.performance.write_trade_rows_csv`
for downstream tooling consistency).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator

from ..backtest.performance import TradeRow
from ..core.timezones import ET as _ET
from ..entries.model import EntryStrategy
from ..exits.model import ExitStrategy
from ..models import Candle
from ..rendering import (
    draw_candlesticks,
    draw_volume,
    dynamic_body_half,
    setup_price_axes,
    setup_volume_axes,
    style_axes,
)
from ..scanner.model import Condition as _ScannerCondition
from ..scanner.model import FieldRef as _FieldRef
from ..scanner.model import Group as _ScannerGroup

__all__ = [
    "ScreenshotSpec",
    "render_trade_screenshot",
    "select_window",
    "trade_filename",
]


# Colour cycle for indicator overlays. Picked for perceptual distinctness
# on both light and dark themes; readable next to entry/exit greens/reds.
_INDICATOR_COLORS = (
    "#ff7f0e",  # orange
    "#1f77b4",  # blue
    "#9467bd",  # purple
    "#8c564b",  # brown
    "#e377c2",  # pink
    "#17becf",  # cyan
    "#bcbd22",  # olive
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Window selection (per design plan.md §Screenshots).
PRE_BARS_DEFAULT = 30
POST_BARS_DEFAULT = 10
MAX_BARS_DEFAULT = 200

# Output sizing (per design plan.md §Screenshots).
WIDTH_IN_DEFAULT = 14.5         # 1600 px @ 110 dpi
HEIGHT_IN_DEFAULT = 8.2         #  900 px @ 110 dpi
DPI_DEFAULT = 110

# Annotation colours (theme-independent — readable on either backdrop).
ENTRY_LONG_COLOR = "#1bb556"    # green
ENTRY_SHORT_COLOR = "#d8444f"   # red
EXIT_COLOR = "#7d7d7d"          # neutral grey
MAE_COLOR = "#d8444f"           # red dot at low-water mark
MFE_COLOR = "#1bb556"           # green dot at high-water mark
TARGET_COLOR = "#1f77b4"        # blue horizontal target line
ENTRY_GUIDE_COLOR = "#1bb556"   # vertical guide line at entry index
EXIT_GUIDE_COLOR = "#888888"    # vertical guide line at exit index

# Marker sizing — bumped from the previous defaults so the entry / exit
# remain obvious even on dense charts with 200 bars of context.
ENTRY_MARKER_SIZE = 180
EXIT_MARKER_SIZE = 170

# Eastern Time zone — re-exported from ``core.timezones`` (None when tzdata
# is missing). Imported at the top of the module.


# Light theme that mirrors the live chart's default palette closely
# enough for visual parity. Dark mode is selected via ``dark_mode=True``.
# Required keys for ``rendering.style_axes``:
# ``ax_bg``, ``text``, ``spine``, ``grid``.
# ``draw_session_shading`` also reads ``pre_shade`` / ``post_shade``.
_LIGHT_THEME: dict[str, object] = {
    "fig_bg": "#ffffff",
    "ax_bg": "#ffffff",
    "text": "#222222",
    "spine": "#888888",
    "grid": "#dddddd",
    "pre_shade": "#f0f0f0",
    "post_shade": "#e8e8e8",
}

_DARK_THEME: dict[str, object] = {
    "fig_bg": "#1e1e1e",
    "ax_bg": "#1e1e1e",
    "text": "#e8e8e8",
    "spine": "#606060",
    "grid": "#3a3a3a",
    "pre_shade": "#2a2a2a",
    "post_shade": "#262626",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreenshotSpec:
    """Knob bag for :func:`render_trade_screenshot`.

    Default values match the design contract in plan.md; tests
    override these for fast PNGs, headless CI, etc.
    """

    pre_bars: int = PRE_BARS_DEFAULT
    post_bars: int = POST_BARS_DEFAULT
    max_bars: int = MAX_BARS_DEFAULT
    width_in: float = WIDTH_IN_DEFAULT
    height_in: float = HEIGHT_IN_DEFAULT
    dpi: int = DPI_DEFAULT
    dark_mode: bool = False
    draw_volume_pane: bool = True


def trade_filename(symbol: str, order_id: str) -> str:
    """Return the canonical screenshot filename for a trade.

    Format: ``<SYM>_<order_id>_post.png``. The ``_post`` suffix
    mirrors :func:`tradinglab.backtest.performance.write_trade_rows_csv`
    so the screenshots travel with exported CSVs without renaming.

    Falls back to ``unknown`` when no ``order_id`` is available
    (legacy/unattributed trades).
    """
    safe_sym = (symbol or "UNK").strip().replace("/", "_") or "UNK"
    safe_oid = (order_id or "unknown").strip().replace("/", "_") or "unknown"
    return f"{safe_sym}_{safe_oid}_post.png"


def select_window(
    candles: list[Candle],
    entry_index: int,
    exit_index: int,
    *,
    pre_bars: int = PRE_BARS_DEFAULT,
    post_bars: int = POST_BARS_DEFAULT,
    max_bars: int = MAX_BARS_DEFAULT,
) -> tuple[int, int]:
    """Compute ``(start, end)`` slice indices for one trade.

    ``end`` is exclusive (Python slice semantics). The slice
    always *contains* both ``entry_index`` and ``exit_index``
    (clamped to ``[0, len(candles)]``).

    When the natural window exceeds ``max_bars``, we clip from the
    *left* so the exit + post-bars context is preserved.
    """
    if not candles:
        return (0, 0)
    n = len(candles)
    e_in = max(0, min(entry_index, n - 1))
    x_in = max(0, min(exit_index, n - 1))
    if x_in < e_in:
        x_in = e_in
    start = max(0, e_in - max(0, pre_bars))
    end = min(n, x_in + 1 + max(0, post_bars))
    if end - start > max_bars:
        start = end - max_bars
    return (start, end)


def render_trade_screenshot(
    *,
    candles: list[Candle],
    trade_row: TradeRow,
    output_path: str | Path,
    spec: ScreenshotSpec | None = None,
    entry_strategy: EntryStrategy | None = None,
    exit_strategy: ExitStrategy | None = None,
) -> Path:
    """Render one trade's screenshot to ``output_path`` and return the path.

    Composes:

    * the trade's candle window via :func:`select_window`
    * an OHLC pane via :func:`draw_candlesticks`
    * (optional) a volume pane via :func:`draw_volume`
    * entry / exit arrows aligned to bar X-coordinates
    * a red MAE dot at the lowest excursion price (long; highest for
      short) and a green MFE dot at the highest excursion price
    * an optional dashed target line from PreTradeEntry.target
    * (optional) **strategy indicator overlays** when
      ``entry_strategy`` / ``exit_strategy`` are supplied — every
      distinct price-overlay indicator (EMA / SMA / VWAP / Bollinger,
      etc.; oscillator indicators like RSI / MACD are skipped because
      their y-scale doesn't fit the price pane) referenced by the
      strategy condition tree(s) is computed against the full
      ``candles`` series and drawn on the price pane in a distinct
      colour, with a small legend in the upper-left corner.

    Raises ``ValueError`` only when ``candles`` is empty *and* the
    trade refers to it — every other failure mode degrades gracefully
    (missing annotations) so a screenshot still gets produced for
    inspection.
    """
    spec = spec or ScreenshotSpec()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    post = trade_row.post
    entry_index = _index_of_ts(candles, post.entry_ts)
    exit_index = _index_of_ts(candles, post.exit_ts)
    if entry_index < 0 or exit_index < 0:
        raise ValueError(
            f"trade entry/exit timestamps not found in candle window: "
            f"entry_ts={post.entry_ts} exit_ts={post.exit_ts}"
        )

    start, end = select_window(
        candles, entry_index, exit_index,
        pre_bars=spec.pre_bars,
        post_bars=spec.post_bars,
        max_bars=spec.max_bars,
    )

    theme = _DARK_THEME if spec.dark_mode else _LIGHT_THEME
    fig = Figure(
        figsize=(spec.width_in, spec.height_in),
        dpi=spec.dpi,
        facecolor=str(theme["fig_bg"]),
    )
    canvas = FigureCanvasAgg(fig)

    # Axis layout: 4:1 price/volume; price pane only when volume is off.
    # hspace=0 makes the two panes touch (matches the live chart's
    # layout). setup_price_axes / setup_volume_axes already prune the
    # bottom-most price tick AND top-most volume tick so the boundary
    # doesn't show colliding tick labels (see rendering.spec.md audit
    # ``volume-axis-prune-both``). Previously we used hspace=0.04 which
    # left a visible horizontal gap between price and volume panes —
    # users reported this as a regression vs the live chart UI.
    if spec.draw_volume_pane:
        gs = fig.add_gridspec(2, 1, height_ratios=[4, 1], hspace=0)
        ax_price = fig.add_subplot(gs[0])
        ax_volume = fig.add_subplot(gs[1], sharex=ax_price)
    else:
        ax_price = fig.add_subplot(111)
        ax_volume = None

    # Configure axes the same way the live chart does — keeps body
    # widths, grid styling, and tick formatters in sync.
    setup_price_axes(ax_price)
    style_axes(ax_price, theme)
    if ax_volume is not None:
        setup_volume_axes(ax_volume)
        style_axes(ax_volume, theme)

    ax_price.set_xlim(start - 0.5, end - 0.5)
    if ax_volume is not None:
        ax_volume.set_xlim(start - 0.5, end - 0.5)

    n_visible = max(1, end - start)
    body_half = dynamic_body_half(ax_price, n_visible)

    draw_candlesticks(
        ax_price, candles, start=start, end=end, body_half=body_half,
    )
    if ax_volume is not None:
        draw_volume(
            ax_volume, candles, start=start, end=end, body_half=body_half,
        )
        _apply_volume_ylim(ax_volume, candles, start, end)

    # Frame the price pane with a small headroom margin so arrows /
    # dots don't clip the spine.
    lo, hi = _price_range(candles, start, end)
    if math.isfinite(lo) and math.isfinite(hi) and hi > lo:
        pad = (hi - lo) * 0.08
        ax_price.set_ylim(lo - pad, hi + pad)

    # Strategy-indicator overlays on the price pane (before annotations
    # so entry/exit markers sit on top of the lines).
    _draw_indicator_overlays(
        ax_price, candles, start, end,
        entry_strategy=entry_strategy,
        exit_strategy=exit_strategy,
    )

    _annotate_trade(
        ax_price, candles, trade_row, entry_index, exit_index,
    )

    # Datetime x-axis labels — without these the user can't tell when
    # in the timeline a trade actually occurred. Apply to BOTH panes
    # when the volume pane is present: matplotlib's ``sharex`` would
    # otherwise auto-hide the price pane's tick labels, and a user
    # whose volume pane is empty / annotated "Volume unavailable"
    # would see no time labels at all (Bug 1).
    _apply_datetime_xaxis(ax_price, candles, start, end, fontsize=7)
    # ``sharex`` flips ``labelbottom`` off on the upper pane during
    # axis-share setup; force it back on so the labels actually paint.
    ax_price.tick_params(axis="x", labelbottom=True)
    if ax_volume is not None:
        _apply_datetime_xaxis(ax_volume, candles, start, end)

    _draw_title_and_labels(
        fig, ax_price, trade_row, candles, entry_index,
        entry_strategy=entry_strategy,
    )

    fig.subplots_adjust(left=0.06, right=0.96, top=0.94, bottom=0.08)
    canvas.print_png(str(out))
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _normalize_ts_to_seconds(ts: int | float) -> float:
    """Convert an integer epoch timestamp to **seconds** regardless of
    whether the caller supplied seconds or milliseconds.

    Heuristic by magnitude:
    * ``ts >= 1e12`` → milliseconds (would be year 33,658 if seconds —
      effectively impossible). Divide by 1000.
    * ``ts < 1e12``  → seconds (post-2001 if in [1e9, 1e12]).

    This solves the "every screenshot is the same" bug:
    :data:`PostTradeReview.entry_ts` is in **epoch seconds** in the
    strategy_tester evaluator output (see
    :mod:`tradinglab.strategy_tester.evaluator` —
    ``bar_ts`` is documented as UTC epoch seconds), but earlier
    versions of :func:`_index_of_ts` compared against
    ``c.date.timestamp() * 1000.0`` (milliseconds). The exact-match
    branch never hit and the nearest-neighbour fallback always
    returned the earliest candle (because ``|c_ms - ts_seconds|``
    is minimised at the smallest ``c_ms``). Every trade rendered the
    same first window of the dataset.
    """
    return float(ts) / 1000.0 if float(ts) >= 1e12 else float(ts)


def _index_of_ts(candles: list[Candle], ts: int) -> int:
    """Return the index of ``ts`` in ``candles`` (-1 when ``candles``
    is empty).

    ``ts`` may be supplied in either **epoch seconds** (the
    strategy_tester convention) or **epoch milliseconds** (legacy
    journal records). :func:`_normalize_ts_to_seconds` figures out
    which based on magnitude, then comparison happens in seconds
    against ``Candle.date.timestamp()`` directly.

    A bar matches when its timestamp is within ½ second of the
    target. The nearest-neighbour fallback is only used when no bar
    is within ½ second AND the closest bar is within 1 day of the
    target — otherwise we return -1 so the caller can short-circuit
    rendering rather than silently drawing the wrong window.
    """
    if not candles:
        return -1
    target = _normalize_ts_to_seconds(ts)
    # Exact (½-second-tolerant) match.
    for i, c in enumerate(candles):
        if c.is_gap:
            continue
        c_sec = c.date.timestamp()
        if abs(c_sec - target) < 0.5:
            return i
    # Nearest fallback (only useful for tz / epoch-precision drift).
    best_i = -1
    best_delta: float | None = None
    for i, c in enumerate(candles):
        if c.is_gap:
            continue
        delta = abs(c.date.timestamp() - target)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_i = i
    if best_delta is not None and best_delta > 86_400.0:
        # No bar within 24 h of the target — almost certainly a unit
        # mismatch we didn't anticipate. Bail out rather than render
        # the wrong window.
        return -1
    return best_i


def _apply_volume_ylim(
    ax,
    candles: list[Candle],
    start: int,
    end: int,
) -> None:
    """Pin the volume pane's y-limits to ``(0, vmax * 1.1)`` for the visible slice.

    Without this, matplotlib autoscales from the PolyCollection's
    vertices — but when every visible bar has ``volume == 0`` (a
    common yfinance quirk for intraday extended-hours bars, e.g.
    AMD 5m at 04:00–09:30 ET), every polygon's top edge sits at
    ``y=0`` and autoscale collapses the pane to its default
    ``(0, 1)`` range. The y-axis labels then read "0" and "1.0"
    with all bars rendered as zero-height rectangles — the bug
    user-reported on ``AMD_t1772226600_post.png``.

    Mirror the live chart's policy from
    :func:`tradinglab.core.viewport.compute_volume_ylim` —
    ``(0.0, vmax * 1.1)`` — and when ``vmax == 0`` (entire window
    is extended-hours / volume-unavailable), draw an explanatory
    annotation so the user knows the empty pane is intentional,
    not a render bug.
    """
    vmax = 0.0
    for i in range(start, min(end, len(candles))):
        c = candles[i]
        if c.is_gap:
            continue
        v = c.volume
        try:
            vf = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(vf) and vf > vmax:
            vmax = vf
    if vmax > 0.0:
        ax.set_ylim(0.0, vmax * 1.1)
        return
    ax.set_ylim(0.0, 1.0)
    ax.text(
        0.5, 0.5,
        "Volume unavailable for this window\n(extended hours or no data)",
        transform=ax.transAxes,
        ha="center", va="center",
        fontsize=8, color="#888888", alpha=0.85,
    )


# ---------------------------------------------------------------------------
# Indicator overlays
# ---------------------------------------------------------------------------


def _walk_field_refs(node: object) -> list[_FieldRef]:
    """Recursively collect every :class:`FieldRef` in a scanner condition tree.

    Walks both :class:`Group` (with arbitrarily nested children) and
    :class:`Condition` (which contributes its ``left`` FieldRef plus
    any FieldRef-valued operator params, e.g. the right-hand operand
    of an ``ema_cross`` comparison). Mirrors the traversal style used
    by :func:`tradinglab.strategy_tester.evaluator._walk_authored_intervals`.
    """
    out: list[_FieldRef] = []
    if node is None:
        return out
    if isinstance(node, _ScannerGroup):
        for child in node.children:
            out.extend(_walk_field_refs(child))
        return out
    if isinstance(node, _ScannerCondition):
        if node.left is not None and isinstance(node.left, _FieldRef):
            out.append(node.left)
        for v in (node.params or {}).values():
            if isinstance(v, _FieldRef):
                out.append(v)
        return out
    return out


def _collect_overlay_indicators(
    entry_strategy: EntryStrategy | None,
    exit_strategy: ExitStrategy | None,
) -> list[tuple[str, dict, object]]:
    """Return the ordered, deduplicated overlay-indicator instances to draw.

    Each entry is ``(kind_id, params_dict, indicator_instance)``.
    Deduplication is by ``(kind_id, sorted(params))`` — referencing
    ``EMA(8)`` twice across entry+exit yields a single overlay.

    Oscillator-style indicators (``overlay == False`` on the
    factory class, e.g. RSI / MACD / SMI) are filtered out because
    their y-scale (0–100 / centered-zero) doesn't fit on the price
    pane. Lookups go through
    :func:`tradinglab.indicators.factory_by_kind_id` so any indicator
    the user has registered (built-in or plugin) is supported.
    """
    from ..indicators.base import factory_by_kind_id

    seen: set[tuple[str, tuple]] = set()
    out: list[tuple[str, dict, object]] = []
    refs: list[_FieldRef] = []
    if entry_strategy is not None and entry_strategy.trigger is not None:
        refs.extend(_walk_field_refs(entry_strategy.trigger.condition))
    if exit_strategy is not None:
        for leg in exit_strategy.legs:
            if not getattr(leg, "enabled", True):
                continue
            for trig in leg.triggers:
                if not getattr(trig, "enabled", True):
                    continue
                refs.extend(_walk_field_refs(getattr(trig, "condition", None)))
    for ref in refs:
        if ref.kind != "indicator" or not ref.id:
            continue
        params = dict(ref.params or {})
        key = (ref.id, tuple(sorted(params.items())))
        if key in seen:
            continue
        seen.add(key)
        factory_lookup = factory_by_kind_id(ref.id)
        if factory_lookup is None:
            continue
        _name, factory = factory_lookup
        try:
            instance = factory(**params)
        except Exception:  # noqa: BLE001 — broken params shouldn't crash render
            continue
        if not getattr(instance, "overlay", False):
            continue
        out.append((ref.id, params, instance))
    return out


def _draw_indicator_overlays(
    ax,
    candles: list[Candle],
    start: int,
    end: int,
    *,
    entry_strategy: EntryStrategy | None,
    exit_strategy: ExitStrategy | None,
) -> None:
    """Compute and plot the price-overlay indicators on ``ax``.

    Lines are drawn at the bar X-coordinates used by the candlestick
    layer (global bar index). A small legend in the upper-left names
    each line so a viewer can read off ``"EMA(8)"`` vs ``"EMA(3)"``
    without checking the strategy definition.
    """
    if entry_strategy is None and exit_strategy is None:
        return
    overlays = _collect_overlay_indicators(entry_strategy, exit_strategy)
    if not overlays:
        return
    import numpy as np

    legend_handles: list = []
    color_idx = 0
    for _kind_id, _params, instance in overlays:
        try:
            series = instance.compute(candles)
        except Exception:  # noqa: BLE001
            continue
        if not series:
            continue
        for out_key, arr in series.items():
            if arr is None:
                continue
            arr_np = np.asarray(arr, dtype=float)
            if arr_np.size == 0:
                continue
            sl = arr_np[start:end]
            if sl.size == 0:
                continue
            x = np.arange(start, start + sl.size)
            color = _INDICATOR_COLORS[color_idx % len(_INDICATOR_COLORS)]
            color_idx += 1
            label = getattr(instance, "name", _kind_id.upper())
            if len(series) > 1:
                label = f"{label} [{out_key}]"
            line, = ax.plot(
                x, sl,
                color=color,
                linewidth=1.5,
                alpha=0.85,
                zorder=5,
                label=label,
            )
            legend_handles.append(line)
    if legend_handles:
        legend = ax.legend(
            handles=legend_handles,
            loc="upper left",
            fontsize=8,
            framealpha=0.85,
            facecolor="white",
            edgecolor="#cccccc",
        )
        legend.set_zorder(12)


def _price_range(candles: list[Candle], start: int, end: int) -> tuple[float, float]:
    """Return ``(lo, hi)`` over the slice, skipping NaN / gap bars."""
    lo = math.inf
    hi = -math.inf
    for i in range(start, min(end, len(candles))):
        c = candles[i]
        if c.is_gap:
            continue
        if c.low < lo:
            lo = c.low
        if c.high > hi:
            hi = c.high
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return (math.nan, math.nan)
    return (lo, hi)


def _annotate_trade(
    ax,
    candles: list[Candle],
    trade_row: TradeRow,
    entry_index: int,
    exit_index: int,
) -> None:
    """Stamp entry, exit, MAE, MFE, target annotations + guide lines onto ``ax``."""
    post = trade_row.post
    pre = trade_row.pre
    side = (post.side or "").strip().lower()
    is_long = side in ("buy", "long")

    # Entry / exit arrows. Place them OUTSIDE the bar (above for short
    # exits, below for long entries) so they don't obscure the candle.
    entry_y = float(post.entry_price)
    exit_y = float(post.exit_price)
    entry_color = ENTRY_LONG_COLOR if is_long else ENTRY_SHORT_COLOR

    # Vertical guide lines at the entry / exit bar indices. Faint
    # enough not to dominate the chart but bright enough that the
    # eye locks onto the right bar even when the chart is dense.
    ax.axvline(
        x=entry_index,
        color=ENTRY_GUIDE_COLOR,
        linestyle="-",
        linewidth=1.0,
        alpha=0.35,
        zorder=3,
    )
    if exit_index != entry_index:
        ax.axvline(
            x=exit_index,
            color=EXIT_GUIDE_COLOR,
            linestyle="--",
            linewidth=1.0,
            alpha=0.35,
            zorder=3,
        )

    # Entry: triangle pointing up (long) / down (short).
    ax.scatter(
        [entry_index], [entry_y],
        marker=("^" if is_long else "v"),
        s=ENTRY_MARKER_SIZE,
        color=entry_color,
        edgecolors="black",
        linewidths=0.8,
        zorder=10,
        label="Entry",
    )

    # Exit: x marker.
    ax.scatter(
        [exit_index], [exit_y],
        marker="x", s=EXIT_MARKER_SIZE,
        color=EXIT_COLOR,
        linewidths=2.4,
        zorder=10,
        label="Exit",
    )

    # Price labels next to each marker so the user can read the fill
    # prices without zooming in. Use a generous offset + white bbox +
    # arrow leader line so the label never overlaps neighbouring
    # candles (Bug 2). Flip the offset direction near the chart edges
    # so the label stays inside the visible window.
    entry_dx, entry_dy = _annotation_offset(
        ax, entry_index, is_entry=True, is_long=is_long,
    )
    ax.annotate(
        f"Entry ${entry_y:,.2f}",
        xy=(entry_index, entry_y),
        xytext=(entry_dx, entry_dy),
        textcoords="offset points",
        fontsize=9,
        color=entry_color,
        fontweight="bold",
        bbox=dict(
            boxstyle="round,pad=0.3",
            facecolor="white",
            edgecolor=entry_color,
            alpha=0.92,
        ),
        arrowprops=dict(
            arrowstyle="->",
            color=entry_color,
            alpha=0.7,
            connectionstyle="arc3,rad=0.0",
        ),
        zorder=11,
    )
    exit_dx, exit_dy = _annotation_offset(
        ax, exit_index, is_entry=False, is_long=is_long,
    )
    ax.annotate(
        f"Exit ${exit_y:,.2f}",
        xy=(exit_index, exit_y),
        xytext=(exit_dx, exit_dy),
        textcoords="offset points",
        fontsize=9,
        color=EXIT_COLOR,
        fontweight="bold",
        bbox=dict(
            boxstyle="round,pad=0.3",
            facecolor="white",
            edgecolor=EXIT_COLOR,
            alpha=0.92,
        ),
        arrowprops=dict(
            arrowstyle="->",
            color=EXIT_COLOR,
            alpha=0.7,
            connectionstyle="arc3,rad=0.0",
        ),
        zorder=11,
    )

    # MAE / MFE dots: place at the price extreme during the holding
    # period. mae/mfe are dollar P&L absolutes -- divide by abs(qty)
    # to get $/share, then offset from entry in the right direction.
    qty = abs(float(post.quantity)) or 1.0
    mae_per_share = float(post.mae) / qty if qty else 0.0
    mfe_per_share = float(post.mfe) / qty if qty else 0.0

    if is_long:
        mae_y = entry_y - abs(mae_per_share)
        mfe_y = entry_y + abs(mfe_per_share)
    else:
        mae_y = entry_y + abs(mae_per_share)
        mfe_y = entry_y - abs(mfe_per_share)

    # Find the bar (within the holding period) that actually hit the
    # extreme so the dot sits on the right X coordinate.
    mae_index = _find_extreme_bar(
        candles, entry_index, exit_index, is_long=is_long, side="mae",
    )
    mfe_index = _find_extreme_bar(
        candles, entry_index, exit_index, is_long=is_long, side="mfe",
    )

    if mae_index >= 0:
        ax.scatter(
            [mae_index], [mae_y],
            marker="o", s=60,
            facecolors=MAE_COLOR, edgecolors="black", linewidths=0.4,
            zorder=9, label="MAE",
        )
    if mfe_index >= 0:
        ax.scatter(
            [mfe_index], [mfe_y],
            marker="o", s=60,
            facecolors=MFE_COLOR, edgecolors="black", linewidths=0.4,
            zorder=9, label="MFE",
        )

    # Optional target line (only when PreTradeEntry recorded one).
    target = pre.target if pre is not None else None
    if target is not None and math.isfinite(float(target)):
        ax.axhline(
            y=float(target),
            color=TARGET_COLOR,
            linestyle="--",
            linewidth=1.2,
            alpha=0.7,
            zorder=4,
        )


def _annotation_offset(
    ax,
    bar_index: int,
    *,
    is_entry: bool,
    is_long: bool,
) -> tuple[float, float]:
    """Pick an (dx, dy) offset (in points) for an entry/exit price label.

    Default direction places the entry label below+right (for long) or
    above+right (for short) of its marker, and the exit label always
    above+right (so entry and exit labels don't collide when the trade
    is short). When the marker sits in the right-hand 20% of the
    visible window we flip the horizontal direction so the label
    stays inside the chart bounds instead of being clipped.
    """
    try:
        x_lo, x_hi = ax.get_xlim()
    except Exception:  # noqa: BLE001 - axes without xlim set
        x_lo, x_hi = 0.0, max(1.0, float(bar_index) + 1.0)
    span = float(x_hi) - float(x_lo)
    rel = (float(bar_index) - float(x_lo)) / span if span > 0 else 0.5
    # Flip horizontally if too close to the right edge so the label
    # leader-line points leftward instead of off-chart.
    flip_x = rel >= 0.80
    dx = -30.0 if flip_x else 30.0
    if is_entry:
        # Long entries get a label below the up-triangle marker, short
        # entries above the down-triangle marker — keeps the leader
        # arrow pointing into the candle body, not across it.
        dy = -35.0 if is_long else 35.0
    else:
        # Exit always offset opposite from the entry direction so the
        # two labels don't stack on top of each other.
        dy = 35.0 if is_long else -35.0
    return (dx, dy)


def _find_extreme_bar(
    candles: list[Candle],
    entry_index: int,
    exit_index: int,
    *,
    is_long: bool,
    side: str,
) -> int:
    """Return the bar index where MAE or MFE peaked, or -1 on empty slice.

    ``side`` is ``"mae"`` (low-water mark for long, high for short) or
    ``"mfe"`` (high for long, low for short). We use bar low/high
    rather than the closing price because MAE/MFE in
    :mod:`backtest.engine` are tracked off the same low/high values.
    """
    if exit_index < entry_index:
        return -1
    best_i = -1
    best_v = None
    is_low_extreme = (side == "mae" and is_long) or (side == "mfe" and not is_long)
    for i in range(entry_index, min(exit_index + 1, len(candles))):
        c = candles[i]
        if c.is_gap:
            continue
        v = c.low if is_low_extreme else c.high
        if not math.isfinite(v):
            continue
        if best_v is None or (is_low_extreme and v < best_v) or (
            not is_low_extreme and v > best_v
        ):
            best_v = v
            best_i = i
    return best_i


def _draw_title_and_labels(
    fig: Figure,
    ax_price,
    trade_row: TradeRow,
    candles: list[Candle] | None = None,
    entry_index: int = -1,
    *,
    entry_strategy: EntryStrategy | None = None,
) -> None:
    """Stamp a single-line title bar with the trade's key facts.

    The left-aligned title carries the symbol, side, quantity, the
    entry date/time (ET) so the user can identify which trade among
    a busy run they're looking at, and the setup / strategy tag.
    The right-aligned title shows P&L absolute + percent.

    Setup-tag fallback (Bug 3):

    * When ``trade_row.setup_tag`` is set, show ``setup: <tag>`` (and
      additionally ``via <strategy_name>`` when an ``entry_strategy``
      is supplied — the journal tag is user-authored and more
      specific, but the strategy name still tells the reader how the
      trade got fired).
    * When ``setup_tag`` is empty but ``entry_strategy`` is supplied,
      show the strategy name (or its id as a fallback) instead of
      the uninformative ``(no setup)`` placeholder. Mechanical
      strategy_tester runs never write a setup_tag (see CLAUDE.md
      §7.9) so this is the common path.
    * When neither is available, omit the setup segment entirely.
    """
    post = trade_row.post
    side = (post.side or "").strip().lower()
    is_long = side in ("buy", "long")
    side_label = "LONG" if is_long else "SHORT"

    pnl = float(post.pnl)
    pnl_pct = float(post.pnl_pct) * 100.0
    pnl_color = ENTRY_LONG_COLOR if pnl >= 0 else ENTRY_SHORT_COLOR

    setup_tag = (trade_row.setup_tag or "").strip()
    strategy_label = ""
    if entry_strategy is not None:
        strategy_label = (
            (entry_strategy.name or "").strip()
            or (getattr(entry_strategy, "id", "") or "").strip()
        )

    # Entry timestamp annotation. Prefer the entry candle's actual
    # date when available (so the title shows the exact bar time)
    # and fall back to ``post.entry_ts`` otherwise.
    entry_dt_str = ""
    if (
        candles is not None
        and 0 <= entry_index < len(candles)
        and candles[entry_index].date is not None
    ):
        entry_dt_str = _format_et_timestamp(candles[entry_index].date)
    elif post.entry_ts:
        entry_dt_str = _format_et_timestamp_from_ms(int(post.entry_ts))

    parts = [f"{post.symbol}", f"{side_label} {abs(post.quantity):.0f}"]
    if entry_dt_str:
        parts.append(f"@ {entry_dt_str}")
    if setup_tag and strategy_label:
        parts.append(f"setup: {setup_tag}  •  via {strategy_label}")
    elif setup_tag:
        parts.append(f"setup: {setup_tag}")
    elif strategy_label:
        parts.append(strategy_label)
    # else: omit the setup segment entirely (no "(no setup)" noise).
    title = "  •  ".join(parts)
    pnl_str = f"P&L: ${pnl:+,.2f}  ({pnl_pct:+.2f}%)"

    ax_price.set_title(title, loc="left", fontsize=10)
    ax_price.set_title(pnl_str, loc="right", fontsize=10, color=pnl_color)
    fig.suptitle("")  # explicit no-op; prevent default


# ---------------------------------------------------------------------------
# Datetime x-axis helpers
# ---------------------------------------------------------------------------


def _format_et_timestamp(dt: datetime) -> str:
    """Return ``YYYY-MM-DD HH:MM ET`` for a (possibly tz-naive) datetime.

    Naive datetimes are assumed to be UTC (matches Candle convention).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if _ET is not None:
        dt = dt.astimezone(_ET)
        suffix = " ET"
    else:  # pragma: no cover - py without zoneinfo
        dt = dt.astimezone(timezone.utc)
        suffix = " UTC"
    return dt.strftime("%Y-%m-%d %H:%M") + suffix


def _format_et_timestamp_from_ms(ts: int) -> str:
    """Return ``YYYY-MM-DD HH:MM ET`` for an epoch timestamp.

    Despite the historical name, ``ts`` may be supplied in either
    epoch seconds (the strategy_tester convention) or epoch
    milliseconds (legacy live-journal records); the function
    auto-detects by magnitude (``ts >= 1e12`` → ms). See the
    ``_index_of_ts`` docstring above for the same landmine.
    """
    ts_seconds = float(ts) / 1000.0 if float(ts) >= 1e12 else float(ts)
    dt = datetime.fromtimestamp(ts_seconds, tz=timezone.utc)
    return _format_et_timestamp(dt)


def _apply_datetime_xaxis(
    ax,
    candles: list[Candle],
    start: int,
    end: int,
    *,
    fontsize: int = 8,
) -> None:
    """Replace bare integer bar-index x-ticks with datetime labels.

    Picks ``M/D HH:MM`` when the window spans multiple calendar days
    (so the user sees the date) and ``HH:MM`` for intraday-only
    windows (preferred for 5m / 1m intraday runs).
    """
    n = len(candles)
    if n == 0 or end <= start:
        return

    # Decide format granularity by window span (multi-day vs intraday).
    s = max(0, start)
    e = min(n, end)
    first = candles[s].date
    last = candles[e - 1].date
    multi_day = False
    if first is not None and last is not None:
        first_dt = first if first.tzinfo else first.replace(tzinfo=timezone.utc)
        last_dt = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        if _ET is not None:
            first_dt = first_dt.astimezone(_ET)
            last_dt = last_dt.astimezone(_ET)
        multi_day = first_dt.date() != last_dt.date()

    def _fmt(x: float, _pos) -> str:
        # x is a bar index in continuous coords. Round to the nearest
        # actual bar index inside the visible window.
        idx = int(round(x))
        if idx < 0 or idx >= n:
            return ""
        c = candles[idx]
        if c.date is None:
            return ""
        dt = c.date if c.date.tzinfo else c.date.replace(tzinfo=timezone.utc)
        if _ET is not None:
            dt = dt.astimezone(_ET)
        if multi_day:
            return dt.strftime("%m/%d %H:%M")
        return dt.strftime("%H:%M")

    ax.xaxis.set_major_locator(MaxNLocator(nbins=8, integer=True, prune="both"))
    ax.xaxis.set_major_formatter(FuncFormatter(_fmt))
    # Slight rotation keeps multi-day labels readable without
    # eating into the chart height.
    for label in ax.get_xticklabels():
        label.set_rotation(0 if not multi_day else 15)
        label.set_horizontalalignment("center" if not multi_day else "right")
        label.set_fontsize(fontsize)
