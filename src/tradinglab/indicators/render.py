"""Render-side helpers for indicators.

Tk-thread / matplotlib-coupled. Pure compute lives in :mod:`base`,
:mod:`moving_averages`, :mod:`rsi`, :mod:`bollinger`. This module
bridges :class:`IndicatorManager` + :class:`IndicatorCache` to the
matplotlib figure: it computes (cached) values, materializes
``Line2D`` artists onto a slot's price axis (overlays) and per-config
lower panes (non-overlays), and exposes a state object the app can
walk during fast paths (pan/zoom blit, streaming tick, theme swap).

The compute call is gap-aware via ``gap_mask`` — when the slot's
candles list has been gap-padded for compare-mode alignment, the
helper computes on the non-gap subset and NaN-pads the result back to
the full length so x positions line up with the rendered candles.

A thin wrapper around :func:`tradinglab.indicators.base.factory_by_kind_id`
is exposed here as :func:`factory_by_kind_id` (returning JUST the
factory, not the ``(display_name, factory)`` tuple the base form
uses). The wrapper is the canonical seam every call site in this
module goes through; treating the base's tuple return as a class
historically silently degraded — the wrapper makes that contract
explicit and logs at WARNING if a future regression breaks the
return-shape contract.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..models import Candle
from ._palette import FALLBACK_GRAY
from .base import compute_via_bars
from .base import factory_by_kind_id as _factory_by_kind_id_raw

_LOG = logging.getLogger(__name__)


#: Output-kind → matplotlib ``drawstyle`` keyword mapping. Extend
#: here when adding a new line-shape kind to the Indicator output
#: protocol; callers use :func:`_drawstyle_for_output_kind`.
_DRAWSTYLE_BY_OUTPUT_KIND: dict[str, str] = {
    "stair_line": "steps-post",
    # "line" / unknown → default smooth line.
}


def _drawstyle_for_output_kind(kind: str | None) -> str:
    """Return the matplotlib ``drawstyle`` for an indicator output kind.

    Falls back to ``"default"`` for ``None`` / unknown kinds — keeps
    a single source of truth for the price-pane and lower-pane
    rendering paths (which both materialize ``Line2D`` artists).
    """
    return _DRAWSTYLE_BY_OUTPUT_KIND.get(kind or "", "default")


def factory_by_kind_id(kind_id):
    """Return just the factory class for ``kind_id`` or ``None``.

    Wraps :func:`tradinglab.indicators.base.factory_by_kind_id`,
    which is typed to return ``Optional[Tuple[str, IndicatorFactory]]``.
    The wrapper extracts the factory half of the tuple so every caller
    in this module can treat the return as a class.

    A broken contract from the base (returns something other than a
    2-tuple or ``None``) is logged at WARNING and treated as "unknown
    indicator" — the call site already handles that gracefully by
    skipping the config. Logging at WARNING ensures a future
    regression doesn't silently degrade to "no indicators render"
    with zero diagnostic.
    """
    pair = _factory_by_kind_id_raw(kind_id)
    if pair is None:
        return None
    if not isinstance(pair, tuple) or len(pair) < 2:
        _LOG.warning(
            "factory_by_kind_id(%r) returned non-tuple %r — expected "
            "(display_name, factory). Treating as unknown indicator.",
            kind_id, pair,
        )
        return None
    return pair[1]
from .cache import IndicatorCache, config_hash  # noqa: E402
from .config import IndicatorConfig, IndicatorManager, effective_pane_group  # noqa: E402

# --- Layout -------------------------------------------------------------

#: Historic price-pane unit weight relative to the volume pane (3:1).
#: Preserves the pre-indicator visual when no indicator panes exist.
PRICE_UNIT: float = 3.0
#: Volume pane unit weight (the reference unit of ``height_ratios``).
VOLUME_UNIT: float = 1.0
#: Indicator pane unit weight — same height as volume by default.
INDICATOR_UNIT: float = 1.0
#: Minimum vertical pixels we'll allow a stacked indicator pane to take.
#: Used only as a *can_add_more* gate; ratios themselves stay weight-based
#: so the historic 3:1 price:volume look is preserved on the n=0 path.
MIN_LOWER_PANE_HEIGHT_PX: int = 80
#: Minimum fraction of the figure height the price pane keeps even
#: when many lower panes are stacked.
PRICE_FLOOR_FRAC: float = 0.40


def compute_layout(
    num_lower_panes: int,
    fig_height_in: float,
    dpi: float = 100.0,
) -> tuple[list[float], bool]:
    """Return ``(height_ratios, can_add_more)`` for one slot.

    ``num_lower_panes`` is ``1`` (volume) + the count of applicable
    non-overlay indicators. ``height_ratios`` always has length
    ``1 + num_lower_panes`` (price first).

    The base scheme is weight-based so the historic price:volume
    proportion (``PRICE_UNIT : VOLUME_UNIT == 3 : 1``) is preserved
    exactly when no indicator panes are present. Each indicator pane
    adds another ``INDICATOR_UNIT`` slot. ``can_add_more`` flips to
    False once the projected price share with one *more* indicator
    pane would drop below :data:`PRICE_FLOOR_FRAC`.
    """
    n = max(1, int(num_lower_panes))
    n_ind = n - 1  # excluding the volume pane.
    ratios: list[float] = (
        [PRICE_UNIT, VOLUME_UNIT] + [INDICATOR_UNIT] * n_ind
    )
    total = sum(ratios)
    next_total = total + INDICATOR_UNIT
    can_add_more = (PRICE_UNIT / next_total) >= PRICE_FLOOR_FRAC
    # Sanity: very tiny figures shouldn't allow more panes either.
    fig_h_px = max(1.0, float(fig_height_in) * float(dpi))
    if (PRICE_UNIT / next_total) * fig_h_px < (PRICE_FLOOR_FRAC * fig_h_px):
        can_add_more = False
    if (INDICATOR_UNIT / next_total) * fig_h_px < MIN_LOWER_PANE_HEIGHT_PX:
        can_add_more = False
    return ratios, can_add_more


# --- Per-slot artist state ----------------------------------------------

@dataclass
class PanelIndicatorState:
    """Live mapping of indicator config-ids → matplotlib artists.

    Recreated on every full ``_render`` because ``fig.clear()`` kills
    the underlying axes. Mutated in place by :func:`render_for_slot`
    and the streaming-tick fast path.
    """

    # config_id -> {output_key: Line2D} on the price axis (overlays).
    overlay_lines: dict[int, dict[str, Any]] = field(default_factory=dict)
    # config_id -> Axes (lower pane) for non-overlay indicators.
    panes: dict[int, Any] = field(default_factory=dict)
    # config_id -> {output_key: Line2D} on the per-config lower pane.
    pane_lines: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Snapshot of which config_ids appeared on this slot the last time
    # we rendered. Used to detect stale lines that need removing.
    last_config_ids: tuple[int, ...] = ()

    def all_artists(self) -> list[Any]:
        """Flat list of every Line2D currently held — for blit-anim sets."""
        out: list[Any] = []
        for lines in self.overlay_lines.values():
            out.extend(lines.values())
        for lines in self.pane_lines.values():
            out.extend(lines.values())
        return out

    def clear(self) -> None:
        for lines in self.overlay_lines.values():
            for ln in lines.values():
                _safe_remove_line(ln)
        for lines in self.pane_lines.values():
            for ln in lines.values():
                _safe_remove_line(ln)
        self.overlay_lines.clear()
        self.pane_lines.clear()
        # Note: `panes` axes are owned by the figure via gridspec;
        # _render rebuilds them. We don't remove them here.
        self.panes.clear()
        self.last_config_ids = ()


def _safe_remove_line(ln: Any) -> None:
    try:
        ln.remove()
    except Exception:  # noqa: BLE001
        pass


# --- Compute + render helper -------------------------------------------

def applicable_non_overlay_configs(
    manager: IndicatorManager, scope: str, interval: str,
) -> list[IndicatorConfig]:
    """Return non-overlay configs that should render in this slot."""
    out: list[IndicatorConfig] = []
    for cfg in manager.applicable(scope, interval):
        if cfg.unknown:
            continue
        cls = factory_by_kind_id(cfg.kind_id)
        if cls is None:
            continue
        if not bool(getattr(cls, "overlay", True)):
            out.append(cfg)
    return out


def applicable_pane_groups(
    manager: IndicatorManager, scope: str, interval: str,
) -> list[list[IndicatorConfig]]:
    """Group non-overlay configs by ``pane_group`` for shared-pane rendering.

    Each entry in the returned list is one **lower pane** worth of
    configs:

    * Configs with ``pane_group == ""`` get a singleton list (legacy
      one-config-per-pane behaviour preserved).
    * Configs sharing the same non-empty ``pane_group`` collapse into
      one entry. The first occurrence in manager order fixes the
      pane's position; later configs in the same group are appended
      to that entry (drawn as additional lines on the same axis).

    The total number of lower panes the slot needs is therefore
    ``1 (volume) + len(applicable_pane_groups(...))``.
    """
    groups: list[list[IndicatorConfig]] = []
    by_key: dict[str, list[IndicatorConfig]] = {}
    for cfg in applicable_non_overlay_configs(manager, scope, interval):
        # Params-aware: the unified RVOL / RRVOL indicators toggle
        # between "rvol" and "rvol_z" pane groups based on z_score.
        # ``effective_pane_group`` resolves the live pane group from
        # ``factory.pane_group_for(params)`` ⇒ ``cfg.pane_group`` ⇒
        # class attribute, so a stale persisted value can't pin a
        # z-score config to the wrong pane.
        key = effective_pane_group(cfg)
        if not key:
            groups.append([cfg])
            continue
        bucket = by_key.get(key)
        if bucket is None:
            bucket = [cfg]
            by_key[key] = bucket
            groups.append(bucket)
        else:
            bucket.append(cfg)
    return groups


def applicable_overlay_configs(
    manager: IndicatorManager, scope: str, interval: str,
) -> list[IndicatorConfig]:
    """Configs for ``(scope, interval)`` whose kind is an overlay.

    Skips configs whose ``kind_id`` no longer maps to a registered
    factory (typically: a saved config references an indicator that
    was removed from the codebase). Skips at DEBUG so a "missing
    plugin" doesn't pollute the status bar but still surfaces in the
    log file when diagnosed.
    """
    out: list[IndicatorConfig] = []
    for cfg in manager.applicable(scope, interval):
        if cfg.unknown:
            continue
        cls = factory_by_kind_id(cfg.kind_id)
        if cls is None:
            _LOG.debug(
                "applicable_overlay_configs: dropping config %r (kind_id=%r) — "
                "no factory registered for that kind",
                cfg.display_name, cfg.kind_id,
            )
            continue
        if bool(getattr(cls, "overlay", True)):
            out.append(cfg)
    return out


def _build_gap_mask(candles: Sequence[Candle]) -> np.ndarray | None:
    """Return a boolean mask of gap entries, or None if no gaps."""
    mask = np.fromiter(
        (bool(getattr(c, "is_gap", False)) for c in candles),
        dtype=bool, count=len(candles),
    )
    if not mask.any():
        return None
    return mask


def _resolve_reference_levels(
    cfg: IndicatorConfig,
    factory: Any,
) -> tuple[float, ...]:
    """Resolve the reference-axhline levels for a pane indicator.

    Source of truth, in order:

    1. Per-instance levels: build an indicator instance from
       ``cfg.params`` and read its ``reference_levels`` attribute.
       This lets indicators expose user-tunable thresholds (e.g.
       LRSI's ``oversold`` / ``overbought`` params or a
       ``show_reference_lines`` toggle) without each indicator
       having to plumb the levels through the render layer manually.
    2. Class attribute ``reference_levels`` — for indicators with
       fixed levels (SMI's ±40/0, ADX's 25).
    3. Empty tuple — no axhlines.

    The returned tuple is ALWAYS finite floats; non-finite or
    non-numeric values are skipped. Order is preserved so the
    caller can compare tuples for change detection.
    """
    # Try the instance first. If construction fails for any reason
    # we silently fall back to the class attribute — the indicator
    # itself may already be unhappy with these params, in which case
    # ``_compute_for_config`` will also bail and the pane stays
    # empty (no levels drawn either way).
    raw: Any = None
    if factory is not None:
        try:
            inst = factory(**cfg.params)
        except Exception:  # noqa: BLE001
            inst = None
        if inst is not None:
            raw = getattr(inst, "reference_levels", None)
        if raw is None:
            raw = getattr(factory, "reference_levels", None)
    if not raw:
        return ()
    out: list[float] = []
    for v in raw:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(f):
            continue
        out.append(f)
    return tuple(out)


def _compute_for_config(
    cfg: IndicatorConfig,
    candles: Sequence[Candle],
    gap_mask: np.ndarray | None,
    cache: IndicatorCache,
) -> dict[str, np.ndarray] | None:
    """Return cached/freshly-computed output dict; NaN-padded for gaps.

    Returns ``None`` if the indicator class is missing (unknown
    placeholder) or a compute exception was raised.
    """
    cls = factory_by_kind_id(cfg.kind_id)
    if cls is None:
        return None
    try:
        ind = cls(**cfg.params)
    except Exception:  # noqa: BLE001
        return None
    n_full = len(candles)
    if gap_mask is None or not gap_mask.any():
        h = config_hash(cfg.kind_id, cfg.params)
        try:
            bars = cache.bars_for(list(candles)) if not isinstance(candles, list) \
                else cache.bars_for(candles)
            # Hot path — route through the incremental hook so
            # appended-only growth (sandbox tick, stream rollover) can
            # extend O(k) via ``inc_step`` instead of recomputing the
            # full kernel each render. Non-incremental indicators fall
            # through to a full ``compute_via_bars`` internally.
            return cache.get_or_compute_incremental(candles, h, ind, bars)
        except Exception:  # noqa: BLE001
            return None
    # Gap-aware path: compute on the non-gap subset, NaN-pad back.
    # Cache key includes a gap fingerprint so a non-compare render
    # later doesn't mistakenly reuse the padded result. Incremental
    # protocol does NOT apply here — the non-gap mask can vary between
    # renders (compare-mode alignment) so ``inc_step`` over the
    # nongap-subset has no stable ``prev_len`` semantics.
    nongap_candles = [c for c, g in zip(candles, gap_mask, strict=False) if not g]
    if not nongap_candles:
        return None
    raw_h = config_hash(
        cfg.kind_id,
        {**cfg.params, "_gapfp": int(gap_mask.tobytes().__hash__() & 0xFFFFFFFF)},
    )
    try:
        nongap_bars = cache.bars_for(nongap_candles)
        raw_out = cache.get_or_compute(
            candles, raw_h, lambda: compute_via_bars(ind, nongap_bars),
        )
    except Exception:  # noqa: BLE001
        return None
    nongap_idx = np.flatnonzero(~gap_mask)
    padded: dict[str, np.ndarray] = {}
    for key, arr in raw_out.items():
        a = np.asarray(arr, dtype=float)
        if a.shape[0] != nongap_idx.shape[0]:
            # Shape mismatch: skip rather than crash.
            continue
        full = np.full(n_full, np.nan, dtype=float)
        full[nongap_idx] = a
        padded[key] = full
    return padded


def _resolve_style(cfg: IndicatorConfig, output_key: str) -> tuple[str, float]:
    """Return ``(color_hex, line_width)`` for one output of one config."""
    cls = factory_by_kind_id(cfg.kind_id)
    default = {}
    if cls is not None:
        default = dict(getattr(cls, "default_style", {}) or {})
    cfg_style: dict[str, Any] = dict(getattr(cfg, "style", {}) or {})
    spec = cfg_style.get(output_key) or default.get(output_key)
    if spec is None:
        return "#1f77b4", 1.2
    color = getattr(spec, "color", None) or "#1f77b4"
    width = float(getattr(spec, "width", 1.2) or 1.2)
    return color, width


def _draw_histogram(
    ax_lower: Any,
    existing: dict[str, Any],
    key: str,
    x: np.ndarray,
    arr: np.ndarray,
    cls: Any,
    cfg: IndicatorConfig,
    *,
    zorder: int = 3,
) -> None:
    """Render a 4-color histogram output as vertical segments.

    Each finite bar becomes a vertical line segment from ``(x_i, 0)``
    to ``(x_i, arr_i)``. Color is picked from
    ``cls.histogram_palette`` using
    :func:`tradinglab.indicators.macd.classify_histogram` (or any
    classifier returning 0..3 for valid bars, -1 for NaN). The
    drawn artist is a ``LineCollection`` stashed back in
    ``existing[key]``. The y values are pinned on the artist as
    ``_sc_y_data`` so :func:`autoscale_pane_y` and hover readouts can
    access them like a Line2D's ``get_ydata()``.

    If ``existing[key]`` already holds a ``LineCollection``, segments
    and colors are updated in place (no re-allocation, no flicker).
    Any non-LineCollection artist there (e.g. a stale Line2D from a
    prior render kind) is removed first.
    """
    from matplotlib.collections import LineCollection

    # Local import to avoid a top-level dependency on macd from render.
    from .macd import classify_histogram

    palette = tuple(getattr(cls, "histogram_palette", ()) or ())
    if len(palette) < 4:
        palette = ("#26a69a", "#b2dfdb", "#ffcdd2", "#ef5350")
    # Optional per-output user color override (b42 honeycomb picker).
    # We only honor it as the "rising-above-zero" anchor; the other
    # three classes derive from the default palette to keep the
    # 4-class TradingView contrast intact.
    cfg_style: dict[str, Any] = dict(getattr(cfg, "style", {}) or {})
    spec = cfg_style.get(key)
    if spec is not None:
        override = getattr(spec, "color", None)
        if override:
            palette = (override, palette[1], palette[2], palette[3])

    finite_mask = np.isfinite(arr)
    classes = classify_histogram(arr)
    segments = []
    colors = []
    for i in range(arr.size):
        if not finite_mask[i]:
            continue
        c = int(classes[i])
        if c < 0:
            continue
        y_val = float(arr[i])
        segments.append([(float(x[i]), 0.0), (float(x[i]), y_val)])
        colors.append(palette[c])

    prior = existing.get(key)
    if prior is not None and not isinstance(prior, LineCollection):
        _safe_remove_line(prior)
        prior = None

    if prior is None:
        lc = LineCollection(
            segments, colors=colors, linewidths=1.5, zorder=zorder,
        )
        try:
            lc.set_label(cfg.display_name)
        except Exception:  # noqa: BLE001
            pass
        try:
            ax_lower.add_collection(lc)
        except Exception:  # noqa: BLE001
            return
        existing[key] = lc
    else:
        lc = prior
        try:
            lc.set_segments(segments)
            lc.set_colors(colors)
            lc.set_visible(True)
            lc.set_zorder(zorder)
        except Exception:  # noqa: BLE001
            pass
    # Stash the full y-array (NaN preserved at warmup) so autoscale and
    # hover readouts can read it back like Line2D.get_ydata().
    try:
        lc._sc_y_data = np.asarray(arr, dtype=float)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def render_for_slot(
    *,
    price_ax: Any,
    pane_axes: Sequence[Any],
    candles: Sequence[Candle],
    offset: int,
    manager: IndicatorManager,
    cache: IndicatorCache,
    interval: str,
    scope: str,
    state: PanelIndicatorState,
) -> None:
    """Compute + render every applicable indicator for one slot.

    ``pane_axes`` must be one Axes per applicable **pane group** —
    see :func:`applicable_pane_groups` — in the same order. Configs
    sharing a non-empty ``pane_group`` are rendered onto a single
    shared Axes (multiple lines, one set of reference dashes).

    Mutates ``state`` in place. Removes Line2Ds for configs that no
    longer apply.
    """
    overlays = applicable_overlay_configs(manager, scope, interval)
    pane_groups = applicable_pane_groups(manager, scope, interval)
    panes: list[IndicatorConfig] = [c for grp in pane_groups for c in grp]
    n = len(candles)
    gap_mask = _build_gap_mask(candles) if n else None
    x = np.arange(n, dtype=float) + float(offset) if n else np.zeros(0)

    current_ids = {c.id for c in overlays} | {c.id for c in panes}

    # Tear down any line whose config disappeared.
    for cid in list(state.overlay_lines):
        if cid not in current_ids:
            for ln in state.overlay_lines[cid].values():
                _safe_remove_line(ln)
            state.overlay_lines.pop(cid, None)
    for cid in list(state.pane_lines):
        if cid not in current_ids:
            for ln in state.pane_lines[cid].values():
                _safe_remove_line(ln)
            state.pane_lines.pop(cid, None)
            state.panes.pop(cid, None)

    # Overlays on the price axis. ``zorder`` is set to ``4 + i * 0.01``
    # so reordering the manager list flips the visual stacking of
    # overlapping lines without forcing a full Line2D recreate. (Pure
    # constant zorder=4 left late-added lines stranded on top even
    # after the user reordered them down.)
    for i, cfg in enumerate(overlays):
        out = _compute_for_config(cfg, candles, gap_mask, cache)
        existing = state.overlay_lines.setdefault(cfg.id, {})
        if out is None:
            for ln in existing.values():
                _safe_remove_line(ln)
            state.overlay_lines.pop(cfg.id, None)
            continue
        z = 4.0 + 0.01 * i
        cls_ov = factory_by_kind_id(cfg.kind_id)
        out_kinds_ov: dict[str, str] = (
            dict(getattr(cls_ov, "output_kinds", {}) or {}) if cls_ov else {}
        )
        for key, arr in out.items():
            if not bool(cfg.visible):
                # Hide existing line if any.
                ln = existing.get(key)
                if ln is not None:
                    ln.set_visible(False)
                continue
            color, width = _resolve_style(cfg, key)
            kind_ov = out_kinds_ov.get(key, "line")
            drawstyle = _drawstyle_for_output_kind(kind_ov)
            ln = existing.get(key)
            if ln is None:
                (ln,) = price_ax.plot(
                    x, arr, color=color, linewidth=width,
                    label=cfg.display_name, zorder=z,
                    drawstyle=drawstyle,
                )
                existing[key] = ln
            else:
                # If the kind has changed (e.g. user swapped indicators
                # into the same slot), update drawstyle live.
                try:
                    if ln.get_drawstyle() != drawstyle:
                        ln.set_drawstyle(drawstyle)
                except Exception:  # noqa: BLE001
                    pass
                ln.set_data(x, arr)
                ln.set_color(color)
                ln.set_linewidth(width)
                ln.set_zorder(z)
                ln.set_visible(True)

    # Non-overlay panes — iterated by group so multiple configs can
    # share one Axes (``pane_group`` field). Each group consumes one
    # axes from ``pane_axes`` (caller must size accordingly).
    for ax_lower, group in zip(pane_axes, pane_groups, strict=False):
        # In-pane label: "RVOL ToD(20)  •  RVOL Cum(20)" pinned to
        # the upper-left in axes coords. Lets the user identify what
        # the pane is showing without expanding the side legend.
        # Only configs that are visible contribute, so toggling a
        # config off removes it from the label too.
        visible_label_cfgs = [
            cfg for cfg in group
            if bool(cfg.visible) and (cfg.display_name or cfg.kind_id)
        ]
        label_parts = [(cfg.display_name or cfg.kind_id) for cfg in visible_label_cfgs]
        label_text = "  \u2022  ".join(label_parts)
        label_config_ids = tuple(int(cfg.id) for cfg in visible_label_cfgs)
        existing_label = getattr(ax_lower, "_sc_pane_label_artist", None)
        if label_text:
            if existing_label is None:
                try:
                    # Inherit theme color from the axis label artist that
                    # ``style_axes`` already coloured. Falls back to a
                    # neutral grey if for some reason we're rendering
                    # before any theme has been applied.
                    try:
                        text_color = ax_lower.yaxis.label.get_color() or FALLBACK_GRAY
                    except Exception:  # noqa: BLE001
                        text_color = FALLBACK_GRAY
                    artist = ax_lower.text(
                        0.005, 0.97, label_text,
                        transform=ax_lower.transAxes,
                        ha="left", va="top",
                        fontsize=8,
                        color=text_color,
                        alpha=0.85,
                        zorder=10,
                        clip_on=False,
                    )
                    try:
                        artist.set_picker(True)
                        artist._sc_pane_label_config_ids = label_config_ids
                        artist._sc_pane_label_scope = scope
                    except Exception:  # noqa: BLE001
                        pass
                    ax_lower._sc_pane_label_artist = artist
                except Exception:  # noqa: BLE001
                    pass
            else:
                try:
                    if existing_label.get_text() != label_text:
                        existing_label.set_text(label_text)
                    existing_label.set_picker(True)
                    existing_label._sc_pane_label_config_ids = label_config_ids
                    existing_label._sc_pane_label_scope = scope
                    existing_label.set_visible(True)
                except Exception:  # noqa: BLE001
                    pass
        else:
            if existing_label is not None:
                try:
                    existing_label._sc_pane_label_config_ids = ()
                    existing_label.set_visible(False)
                except Exception:  # noqa: BLE001
                    pass

        # Reference levels: union (deduped, ordered) across every
        # config in this group, so a shared pane gets one set of
        # dashes covering the strictest thresholds requested.
        ref_levels: tuple[float, ...] = ()
        seen: set = set()
        merged: list[float] = []
        for cfg in group:
            factory = factory_by_kind_id(cfg.kind_id)
            for lvl in _resolve_reference_levels(cfg, factory) or ():
                key = round(float(lvl), 6)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(float(lvl))
        ref_levels = tuple(merged)

        prev = getattr(ax_lower, "_sc_ref_levels_drawn", None)
        if ref_levels:
            if tuple(prev or ()) != ref_levels:
                for ln in getattr(ax_lower, "_sc_ref_level_lines", []) or []:
                    _safe_remove_line(ln)
                new_lines = []
                for lvl in ref_levels:
                    try:
                        new_lines.append(ax_lower.axhline(
                            float(lvl),
                            color=FALLBACK_GRAY,
                            linewidth=0.7,
                            linestyle="--",
                            alpha=0.6,
                            zorder=1,
                        ))
                    except Exception:  # noqa: BLE001
                        pass
                ax_lower._sc_ref_level_lines = new_lines
                ax_lower._sc_ref_levels_drawn = ref_levels
        else:
            if prev:
                for ln in getattr(ax_lower, "_sc_ref_level_lines", []) or []:
                    _safe_remove_line(ln)
                ax_lower._sc_ref_level_lines = []
                ax_lower._sc_ref_levels_drawn = ()

        # Render each config's lines onto the shared axes.
        for cfg in group:
            state.panes[cfg.id] = ax_lower
            out = _compute_for_config(cfg, candles, gap_mask, cache)
            existing = state.pane_lines.setdefault(cfg.id, {})
            if out is None:
                for ln in existing.values():
                    _safe_remove_line(ln)
                state.pane_lines.pop(cfg.id, None)
                continue
            cls = factory_by_kind_id(cfg.kind_id)
            out_kinds: dict[str, str] = (
                dict(getattr(cls, "output_kinds", {}) or {}) if cls else {}
            )
            for key, arr in out.items():
                if not bool(cfg.visible):
                    ln = existing.get(key)
                    if ln is not None:
                        ln.set_visible(False)
                    continue
                kind = out_kinds.get(key, "line")
                if kind == "histogram":
                    _draw_histogram(
                        ax_lower, existing, key, x, arr, cls, cfg, zorder=3,
                    )
                else:
                    color, width = _resolve_style(cfg, key)
                    drawstyle = _drawstyle_for_output_kind(kind)
                    ln = existing.get(key)
                    if ln is None:
                        (ln,) = ax_lower.plot(
                            x, arr, color=color, linewidth=width,
                            label=cfg.display_name, zorder=4,
                            drawstyle=drawstyle,
                        )
                        existing[key] = ln
                    else:
                        # Histogram → line transition (e.g. user swapped
                        # kinds in the dialog): drop the prior artist.
                        if not hasattr(ln, "set_data"):
                            _safe_remove_line(ln)
                            (ln,) = ax_lower.plot(
                                x, arr, color=color, linewidth=width,
                                label=cfg.display_name, zorder=4,
                                drawstyle=drawstyle,
                            )
                            existing[key] = ln
                        else:
                            try:
                                if ln.get_drawstyle() != drawstyle:
                                    ln.set_drawstyle(drawstyle)
                            except Exception:  # noqa: BLE001
                                pass
                            ln.set_data(x, arr)
                            ln.set_color(color)
                            ln.set_linewidth(width)
                            ln.set_visible(True)

    state.last_config_ids = tuple(c.id for c in overlays) + tuple(
        c.id for c in panes
    )


def autoscale_pane_y(ax_lower: Any, lines: Iterable[Any], lo: int, hi: int) -> None:
    """Fit a pane's Y to the visible portion of its lines.

    ``lo``/``hi`` are integer x-data indices. If no usable data is in
    range, leaves the existing ylim alone.

    Histogram artists (``LineCollection`` with a ``_sc_y_data`` array)
    are read via that attribute so the underlying y values participate
    in autoscale just like Line2D ``get_ydata()``.
    """
    mins: list[float] = []
    maxs: list[float] = []
    for ln in lines:
        y = getattr(ln, "_sc_y_data", None)
        if y is None:
            try:
                y = ln.get_ydata()
            except Exception:  # noqa: BLE001
                continue
        if y is None:
            continue
        arr = np.asarray(y, dtype=float)
        if arr.size == 0:
            continue
        a = max(0, int(lo))
        b = min(arr.size, int(hi))
        if b <= a:
            continue
        seg = arr[a:b]
        seg = seg[np.isfinite(seg)]
        if seg.size == 0:
            continue
        mins.append(float(seg.min()))
        maxs.append(float(seg.max()))
    if not mins:
        return
    lo_y = min(mins)
    hi_y = max(maxs)
    if hi_y <= lo_y:
        hi_y = lo_y + 1.0
    pad = 0.05 * (hi_y - lo_y)
    try:
        ax_lower.set_ylim(lo_y - pad, hi_y + pad)
    except Exception:  # noqa: BLE001
        pass
