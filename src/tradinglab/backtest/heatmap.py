"""Pure metric + geometry layer for the sandbox heatmap.

Turns candles, per-symbol classification, and a replay-clock timestamp
into a laid-out, colored :class:`HeatmapModel` — a Finviz-style
sector -> industry treemap sized by historically-scaled market cap and
colored by 1-Day percent change. Contains **no Tk and no matplotlib**
so every rule is headless-testable; the window in
``gui/sandbox_heatmap.py`` renders the model this module returns.

See ``backtest/heatmap.spec.md`` and ``docs/SANDBOX_HEATMAP.md`` for the
design rationale (the eleven v1 decisions).
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from ..models import Candle

__all__ = (
    "Classification",
    "HeatmapTile",
    "HeatmapLayout",
    "HeatmapModel",
    "members_asof",
    "build_layout",
    "apply_colors",
    "compute_1d_pct",
    "scaled_cap",
    "price_at_or_before",
    "squarify",
    "finviz_hex",
    "relative_luminance",
    "text_color_for",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Group label for symbols with missing / empty classification.
UNCLASSIFIED = "Unclassified"

#: What tile area encodes (surfaced on the layout for the UI legend).
SIZE_BASIS = "historical_market_cap"

#: Floor applied to a tile's size so squarify never sees a zero area
#: (a symbol with unknown size renders as a negligible sliver, honestly).
_MIN_TILE_SIZE = 1.0

# Finviz-style diverging palette (dark theme): red <-> neutral <-> green.
_NEUTRAL_HEX = "#414554"
_RED_HEX = "#f63538"
_GREEN_HEX = "#30cc5f"

#: Fixed number of color buckets per side (Finviz shows ~+-1/+-2/+-3 steps).
_BUCKETS_PER_SIDE = 3

# Timestamps at or beyond this are milliseconds, not seconds (year ~33658
# in seconds), so divide by 1000 — mirrors CLAUDE.md 7.7's normalizer.
_MS_THRESHOLD = 1e12


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Classification:
    """Per-symbol *static* metadata (sector / industry).

    Share counts are time-varying and are NOT stored here — they reach
    the geometry layer already snapped, via ``size_by_symbol``.
    """

    sector: str
    industry: str


@dataclass(frozen=True)
class HeatmapTile:
    """One symbol's rectangle in the treemap.

    Geometry (``x/y/w/h``) is normalized to the unit square ``[0, 1]``.
    ``pct`` / ``fill`` are the post-color attributes filled by
    :func:`apply_colors`; they are ``None`` / ``""`` on a bare layout.
    """

    symbol: str
    sector: str
    industry: str
    size: float
    approx_size: bool
    x: float
    y: float
    w: float
    h: float
    pct: float | None = None
    fill: str = ""


@dataclass(frozen=True)
class HeatmapLayout:
    """Geometry-only treemap (no color yet).

    ``sector_bounds`` maps ``sector -> (x, y, w, h)`` and
    ``industry_bounds`` maps ``(sector, industry) -> (x, y, w, h)`` so
    the window can draw group headers / borders.
    """

    tiles: tuple[HeatmapTile, ...]
    sector_bounds: Mapping[str, tuple[float, float, float, float]]
    industry_bounds: Mapping[tuple[str, str], tuple[float, float, float, float]]
    size_basis: str = SIZE_BASIS


@dataclass(frozen=True)
class HeatmapModel:
    """A colored layout — the render-ready output."""

    tiles: tuple[HeatmapTile, ...]
    as_of_ts: int
    timeframe: str = "1D"
    clip_pct: float = 3.0
    universe_id: str = ""


# ---------------------------------------------------------------------------
# Membership (point-in-time via Date added)
# ---------------------------------------------------------------------------


def members_asof(
    date_added_by_symbol: Mapping[str, int | None],
    as_of_ts: int,
) -> tuple[str, ...]:
    """Return current members whose ``Date added`` <= ``as_of_ts``.

    Removes look-ahead names (added after the replay clock). ``as_of_ts``
    and the mapping values are UTC epoch **seconds** (the caller converts
    ``sp500.csv`` ``Date added`` dates). A ``None`` date is treated as
    "unknown -> include" (never drop a real current member for a missing
    date). The ``Date added == as_of_ts`` boundary is inclusive.
    Insertion order is preserved for determinism.
    """
    cutoff = _to_seconds(as_of_ts)
    out: list[str] = []
    for sym, added in date_added_by_symbol.items():
        if added is None:
            out.append(sym)
            continue
        if _to_seconds(added) <= cutoff:
            out.append(sym)
    return tuple(out)


# ---------------------------------------------------------------------------
# Layout (geometry) — per session roll
# ---------------------------------------------------------------------------


def build_layout(
    *,
    symbols: Iterable[str],
    size_by_symbol: Mapping[str, float],
    classification: Mapping[str, Classification],
    approx_size_symbols: Iterable[str] = frozenset(),
) -> HeatmapLayout:
    """Group symbols sector -> industry and squarify into a unit square.

    ``size_by_symbol`` is the historically-scaled cap proxy (raw shares x
    raw price). Missing / non-positive sizes are floored to
    ``_MIN_TILE_SIZE`` so every symbol still gets a (tiny) tile — the
    "every input symbol appears in exactly one tile" invariant. Tiles for
    symbols in ``approx_size_symbols`` get ``approx_size=True``.
    """
    approx = set(approx_size_symbols)

    def size_of(sym: str) -> float:
        raw = size_by_symbol.get(sym)
        try:
            val = float(raw)
        except (TypeError, ValueError):
            val = 0.0
        if math.isnan(val) or val <= 0.0:
            val = 0.0
        return max(val, _MIN_TILE_SIZE)

    # Group: sector -> industry -> [symbols], preserving first-seen order
    # inside the buckets (final ordering is by size below).
    grouped: OrderedDict[str, OrderedDict[str, list[str]]] = OrderedDict()
    for sym in symbols:
        cls = classification.get(sym)
        sector = cls.sector if (cls and cls.sector) else UNCLASSIFIED
        industry = cls.industry if (cls and cls.industry) else UNCLASSIFIED
        grouped.setdefault(sector, OrderedDict()).setdefault(industry, []).append(sym)

    # Sector totals, ordered largest-first (ties -> name, for determinism).
    sector_totals = {
        sec: sum(size_of(s) for ind in inds.values() for s in ind)
        for sec, inds in grouped.items()
    }
    sectors_sorted = sorted(grouped, key=lambda s: (-sector_totals[s], s))
    sector_rects = squarify(
        [sector_totals[s] for s in sectors_sorted], 0.0, 0.0, 1.0, 1.0
    )

    tiles: list[HeatmapTile] = []
    sector_bounds: dict[str, tuple[float, float, float, float]] = {}
    industry_bounds: dict[tuple[str, str], tuple[float, float, float, float]] = {}

    for sector, s_rect in zip(sectors_sorted, sector_rects, strict=True):
        sector_bounds[sector] = s_rect
        inds = grouped[sector]
        ind_totals = {ind: sum(size_of(s) for s in syms) for ind, syms in inds.items()}
        inds_sorted = sorted(inds, key=lambda i: (-ind_totals[i], i))
        ind_rects = squarify(
            [ind_totals[i] for i in inds_sorted], s_rect[0], s_rect[1], s_rect[2], s_rect[3]
        )
        for industry, i_rect in zip(inds_sorted, ind_rects, strict=True):
            industry_bounds[(sector, industry)] = i_rect
            syms_sorted = sorted(inds[industry], key=lambda s: (-size_of(s), s))
            sym_rects = squarify(
                [size_of(s) for s in syms_sorted], i_rect[0], i_rect[1], i_rect[2], i_rect[3]
            )
            for sym, (x, y, w, h) in zip(syms_sorted, sym_rects, strict=True):
                tiles.append(
                    HeatmapTile(
                        symbol=sym,
                        sector=sector,
                        industry=industry,
                        size=size_of(sym),
                        approx_size=sym in approx,
                        x=x,
                        y=y,
                        w=w,
                        h=h,
                    )
                )

    return HeatmapLayout(
        tiles=tuple(tiles),
        sector_bounds=sector_bounds,
        industry_bounds=industry_bounds,
        size_basis=SIZE_BASIS,
    )


# ---------------------------------------------------------------------------
# Coloring — per bar
# ---------------------------------------------------------------------------


def apply_colors(
    layout: HeatmapLayout,
    *,
    pct_by_symbol: Mapping[str, float | None],
    as_of_ts: int,
    clip_pct: float = 3.0,
    timeframe: str = "1D",
    universe_id: str = "",
) -> HeatmapModel:
    """Attach ``pct`` + Finviz ``fill`` to each tile; return a new model.

    Never mutates ``layout`` (frozen tiles are replaced into a fresh
    tuple). A missing / ``None`` pct maps to the neutral fill, never a
    red/green extreme.
    """
    colored: list[HeatmapTile] = []
    for tile in layout.tiles:
        pct = pct_by_symbol.get(tile.symbol)
        if isinstance(pct, float) and math.isnan(pct):
            pct = None
        colored.append(replace(tile, pct=pct, fill=finviz_hex(pct, clip_pct)))
    return HeatmapModel(
        tiles=tuple(colored),
        as_of_ts=int(_to_seconds(as_of_ts)),
        timeframe=timeframe,
        clip_pct=clip_pct,
        universe_id=universe_id,
    )


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------


def compute_1d_pct(price_at_clock: float | None, prior_close: float | None) -> float | None:
    """1-Day percent change: ``(price - prior_close) / prior_close * 100``.

    Returns ``None`` when either input is missing / NaN or the prior
    close is zero.
    """
    if price_at_clock is None or prior_close is None:
        return None
    try:
        price = float(price_at_clock)
        base = float(prior_close)
    except (TypeError, ValueError):
        return None
    if math.isnan(price) or math.isnan(base) or base == 0.0:
        return None
    return (price - base) / base * 100.0


def scaled_cap(shares: float | None, price: float | None) -> float:
    """Historically-scaled cap = ``shares * price``.

    Caller must pass **raw** (as-reported) shares with **raw**
    (unadjusted) price so splits self-cancel (spec Invariant 7). Missing
    / NaN inputs -> ``0.0`` (the layout floors it to a sliver).
    """
    if shares is None or price is None:
        return 0.0
    try:
        s = float(shares)
        p = float(price)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(s) or math.isnan(p) or s < 0.0 or p < 0.0:
        return 0.0
    return s * p


def price_at_or_before(candles: Sequence[Candle], as_of_ts: int) -> float | None:
    """Return the close of the last candle at or before ``as_of_ts``.

    Enforces the no-future-leakage boundary at the price-lookup site:
    never returns a close from a candle after the clock. ``candles`` are
    assumed ascending by ``date``; NaN-close (gap) bars are skipped.
    ``as_of_ts`` is normalized (ms -> s) so callers may pass either unit.
    """
    cutoff = _to_seconds(as_of_ts)
    last: float | None = None
    for c in candles:
        try:
            cts = c.date.timestamp()
        except (AttributeError, ValueError, OverflowError, OSError):
            continue
        if cts > cutoff:
            break
        close = c.close
        if close is None or (isinstance(close, float) and math.isnan(close)):
            continue
        last = float(close)
    return last


# ---------------------------------------------------------------------------
# Squarified treemap (vendored — Bruls et al., no external dependency)
# ---------------------------------------------------------------------------


def squarify(
    values: Sequence[float],
    x: float,
    y: float,
    w: float,
    h: float,
) -> list[tuple[float, float, float, float]]:
    """Pack ``values`` into ``[x, y, w, h]`` as a squarified treemap.

    Returns one ``(x, y, w, h)`` rectangle per input value, **in input
    order**. Areas are proportional to the values and tile the parent
    exactly. The caller pre-sorts values descending for good aspect
    ratios. All values must be positive.
    """
    if not values or w <= 0.0 or h <= 0.0:
        return [(x, y, 0.0, 0.0) for _ in values]
    normed = _normalize_sizes(values, w, h)
    rects = _squarify(normed, x, y, w, h)
    # Clamp to the parent bounds: the squarify recursion can overshoot by
    # float noise when tile magnitudes vary enormously (e.g. a floored
    # unknown-size tile beside trillion-dollar caps). Clamping guarantees
    # every rect ⊆ the parent (Invariant 1) at a sub-epsilon area cost.
    return [_clamp_rect(r, x, y, w, h) for r in rects]


def _normalize_sizes(sizes: Sequence[float], dx: float, dy: float) -> list[float]:
    total = float(sum(sizes))
    if total <= 0.0:
        return [0.0 for _ in sizes]
    area = dx * dy
    return [float(s) * area / total for s in sizes]


def _clamp_rect(
    rect: tuple[float, float, float, float],
    px: float,
    py: float,
    pw: float,
    ph: float,
) -> tuple[float, float, float, float]:
    """Clamp ``rect`` to the parent ``[px, px+pw] × [py, py+ph]``."""
    rx, ry, rw, rh = rect
    px2, py2 = px + pw, py + ph
    cx = min(max(rx, px), px2)
    cy = min(max(ry, py), py2)
    cw = max(0.0, min(rx + rw, px2) - cx)
    ch = max(0.0, min(ry + rh, py2) - cy)
    return (cx, cy, cw, ch)


def _layout_row(sizes: Sequence[float], x: float, y: float, dy: float) -> list[tuple]:
    width = sum(sizes) / dy
    rects = []
    cy = y
    for s in sizes:
        rects.append((x, cy, width, s / width))
        cy += s / width
    return rects


def _layout_col(sizes: Sequence[float], x: float, y: float, dx: float) -> list[tuple]:
    height = sum(sizes) / dx
    rects = []
    cx = x
    for s in sizes:
        rects.append((cx, y, s / height, height))
        cx += s / height
    return rects


def _layout(sizes: Sequence[float], x: float, y: float, dx: float, dy: float) -> list[tuple]:
    return _layout_row(sizes, x, y, dy) if dx >= dy else _layout_col(sizes, x, y, dx)


def _leftover(sizes: Sequence[float], x: float, y: float, dx: float, dy: float) -> tuple:
    covered = sum(sizes)
    if dx >= dy:
        width = covered / dy
        return (x + width, y, dx - width, dy)
    height = covered / dx
    return (x, y + height, dx, dy - height)


def _worst_ratio(sizes: Sequence[float], x: float, y: float, dx: float, dy: float) -> float:
    return max(
        max(rw / rh, rh / rw)
        for (_rx, _ry, rw, rh) in _layout(sizes, x, y, dx, dy)
        if rw > 0.0 and rh > 0.0
    )


def _squarify(sizes: Sequence[float], x: float, y: float, dx: float, dy: float) -> list[tuple]:
    sizes = [float(s) for s in sizes]
    if not sizes:
        return []
    if len(sizes) == 1:
        return _layout(sizes, x, y, dx, dy)
    i = 1
    while i < len(sizes) and _worst_ratio(sizes[:i], x, y, dx, dy) >= _worst_ratio(
        sizes[: i + 1], x, y, dx, dy
    ):
        i += 1
    current = sizes[:i]
    remaining = sizes[i:]
    lx, ly, ldx, ldy = _leftover(current, x, y, dx, dy)
    return _layout(current, x, y, dx, dy) + _squarify(remaining, lx, ly, ldx, ldy)


# ---------------------------------------------------------------------------
# Color
# ---------------------------------------------------------------------------


def finviz_hex(pct: float | None, clip_pct: float = 3.0) -> str:
    """Map a percent change to a bucketed Finviz-style red/green hex.

    Fixed diverging scale clipped at ``+-clip_pct``, symmetric about 0
    with ``_BUCKETS_PER_SIDE`` steps per side. ``None`` / NaN -> neutral.
    """
    if pct is None:
        return _NEUTRAL_HEX
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return _NEUTRAL_HEX
    if math.isnan(p) or clip_pct <= 0.0:
        return _NEUTRAL_HEX
    p = max(-clip_pct, min(clip_pct, p))
    step = clip_pct / _BUCKETS_PER_SIDE
    level = round(p / step)  # -_BUCKETS_PER_SIDE .. +_BUCKETS_PER_SIDE
    if level == 0:
        return _NEUTRAL_HEX
    frac = abs(level) / _BUCKETS_PER_SIDE
    anchor = _GREEN_HEX if level > 0 else _RED_HEX
    return _lerp_hex(_NEUTRAL_HEX, anchor, frac)


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of an ``#rrggbb`` color, in ``[0, 1]``."""
    r, g, b = (v / 255.0 for v in _hex_to_rgb(hex_color))

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def text_color_for(fill_hex: str) -> str:
    """Pick black or white label text for legibility on ``fill_hex``."""
    return "#000000" if relative_luminance(fill_hex) > 0.4 else "#ffffff"


# ---------------------------------------------------------------------------
# Internal color / time utilities
# ---------------------------------------------------------------------------


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp_hex(a: str, b: str, t: float) -> str:
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    t = max(0.0, min(1.0, t))
    r = round(ar + (br - ar) * t)
    g = round(ag + (bg - ag) * t)
    bl = round(ab + (bb - ab) * t)
    return f"#{r:02x}{g:02x}{bl:02x}"


def _to_seconds(ts: float) -> float:
    """Normalize an epoch timestamp to seconds (ms -> s by magnitude)."""
    t = float(ts)
    return t / 1000.0 if t >= _MS_THRESHOLD else t
