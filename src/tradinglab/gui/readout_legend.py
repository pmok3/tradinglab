"""Pure helpers for the in-readout overlay indicator legend.

Historically TradingLab drew overlay indicators (SMA / EMA / Bollinger
…) into a separate opaque Tk pill strip (:mod:`gui.overlay_legend`)
placed *below* the top-left OHLCV readout. That widget overlapped the
readout and had a solid background — both visually and structurally at
odds with the always-on, transparent matplotlib readout above it.

This module computes the **rows** for a TradingView-style legend that
lives *inside* the matplotlib readout offsetbox. As of the
``legend-condensation`` sprint, ONE row per indicator config — even
for multi-output indicators like Bollinger Bands. A multi-output row
carries a list of :class:`OverlaySegment` entries (one per visible
output) so the renderer can lay them out as
``IndicatorName(params) upper <v1> middle <v2> lower <v3>`` with
each band's value in its own colour via ``HPacker``.

The enumeration deliberately INCLUDES hidden configs (greyed) so the
user can re-enable them with a right-click → Show, matching the old
pill strip's affordance.

Visibility resolution (audit ``legend-condensation``):

1. The indicator class declares its **default** visible output set
   via :meth:`BaseIndicator.effective_output_keys(params)` (e.g.
   AVWAP with ``bands="off"`` returns only ``("avwap",)``).
2. The user-flippable per-output ``LineStyle.visible`` flag on the
   config's ``style[key]`` is consulted afterward — a band the user
   hides in the per-indicator dialog drops out of the legend.

Output order in the row matches whatever the indicator returned from
``effective_output_keys`` (canonical visual order, e.g. Bollinger
returns ``("upper", "middle", "lower")``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .overlay_legend import collect_overlay_configs

if TYPE_CHECKING:
    from ..indicators.config import IndicatorConfig, IndicatorManager


@dataclass(frozen=True)
class OverlaySegment:
    """One output of a multi-output indicator inside an overlay legend row.

    * ``output_key`` identifies the line for live-value reads.
    * ``key_label`` is the band name shown beside the value
      (e.g. ``"upper"``, ``"middle"``, ``"lower"``). Empty for
      single-output indicators where the indicator's parenthesised
      label already disambiguates.
    * ``color`` is the resolved colour for this output's value text.
    """

    output_key: str
    key_label: str
    color: str


@dataclass(frozen=True)
class ReadoutLegendRow:
    """One legend row: one overlay indicator config.

    * ``config_id`` identifies the indicator config (used by the
      click hit-test to route right-clicks back to the per-indicator
      dialog + context menu).
    * ``label`` is the row's prefix text — typically
      ``"IndicatorName(param1, name2=val2, ...)"`` (see
      :func:`format_indicator_label`). Rendered in the theme's
      neutral text colour.
    * ``outputs`` is the ordered list of visible output segments.
      Length 1 for single-output indicators; length 2+ for
      multi-output (Bollinger, AVWAP-with-bands, MACD, ...).
    * ``visible`` mirrors ``cfg.visible`` so the renderer can grey /
      strike hidden rows while keeping them re-enable-able.
    """

    config_id: int
    label: str
    outputs: list[OverlaySegment]
    visible: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _factory_for_kind_id(kind_id: str):
    """Return the indicator factory class for ``kind_id`` (or ``None``)."""
    try:
        from ..indicators.config import factory_by_kind_id
    except Exception:  # noqa: BLE001
        return None
    try:
        entry = factory_by_kind_id(kind_id)
    except Exception:  # noqa: BLE001
        return None
    if entry is None:
        return None
    if isinstance(entry, tuple) and len(entry) == 2:
        return entry[1]
    return entry


def _effective_output_keys_for(cfg: IndicatorConfig) -> list[str]:
    """Resolve the visible output keys for ``cfg`` honouring all signals.

    Order:
    1. Indicator class's ``effective_output_keys(params)`` (declares
       which keys are actually rendered for these params).
    2. Filter by per-output ``cfg.style[key].visible`` (user toggle).
    3. Fall back to ``cfg.style`` keys → single synthetic key
       (legacy / styleless indicators).
    """
    factory = _factory_for_kind_id(cfg.kind_id)
    keys: tuple[str, ...] = ()
    if factory is not None:
        method = getattr(factory, "effective_output_keys", None)
        if callable(method):
            try:
                keys = tuple(str(k) for k in method(dict(cfg.params or {})))
            except Exception:  # noqa: BLE001
                keys = ()
    if not keys:
        # Last-resort fallback: keys of the config's own style dict, then
        # a single synthetic key based on the kind_id.
        try:
            keys = tuple(str(k) for k in cfg.style.keys())
        except Exception:  # noqa: BLE001
            keys = ()
        if not keys:
            keys = (str(getattr(cfg, "kind_id", "") or "value"),)
    # Apply per-output user visibility (LineStyle.visible).
    out: list[str] = []
    for k in keys:
        try:
            ls = cfg.style.get(k)
            if ls is not None and getattr(ls, "visible", True) is False:
                continue
        except Exception:  # noqa: BLE001
            pass
        out.append(k)
    return out


def _color_for_key(cfg: IndicatorConfig, key: str, theme_text: str) -> str:
    """Resolve the colour for one output ``key`` of ``cfg``.

    Order: config per-key override → factory ``default_style`` → theme text.
    """
    try:
        ls = cfg.style.get(key)
        color = getattr(ls, "color", None)
        if color:
            return str(color)
    except Exception:  # noqa: BLE001
        pass
    factory = _factory_for_kind_id(cfg.kind_id)
    if factory is not None:
        default_style = getattr(factory, "default_style", None)
        if default_style and hasattr(default_style, "get"):
            try:
                ls = default_style.get(key)
                color = getattr(ls, "color", None)
                if color:
                    return str(color)
            except Exception:  # noqa: BLE001
                pass
    return theme_text


def format_indicator_label(cfg: IndicatorConfig) -> str:
    """Build the ``"DisplayName(p1, name2=v2, ...)"`` prefix for a row.

    If the config's ``display_name`` already contains a parenthesised
    suffix (e.g. ``"SMA(20)"`` — the convention the factories set on
    ``self.name``), it is returned as-is so we don't end up with
    ``"SMA(20)(20)"``. Otherwise we walk the indicator factory's
    ``params_schema`` (declaration order). The FIRST schema-listed
    param with a non-empty value is rendered positionally
    (``"typical"``); every subsequent non-empty param is rendered
    ``name=value`` (``"bands=off"``). Empty / missing params are
    skipped so a default ``anchor_ts=""`` doesn't bloat the label.
    Unknown kind_id → bare ``display_name``.

    When ``display_name`` is empty AND we're falling back to the
    bare ``kind_id``, the kind_id is uppercased for presentation
    (``"sma"`` → ``"SMA"``) — matches the registry's display labels
    and the historical pill-strip presentation.

    Audit ``legend-condensation``.
    """
    raw_display = (cfg.display_name or "").strip()
    if raw_display:
        display = raw_display
    else:
        display = (cfg.kind_id or "").strip().upper() or "indicator"
    # If the indicator's display_name is already a formatted
    # "Name(...)" string, trust it — re-formatting would double up.
    if "(" in display and display.endswith(")"):
        return display

    factory = _factory_for_kind_id(cfg.kind_id)
    if factory is None:
        return display
    schema = getattr(factory, "params_schema", None) or ()

    params = dict(cfg.params or {})
    parts: list[str] = []
    for pdef in schema:
        name = getattr(pdef, "name", None)
        if not name:
            continue
        val = params.get(name)
        if val is None:
            continue
        # Skip empty strings / empty containers — keeps "anchor_ts="
        # out of every AVWAP label.
        if isinstance(val, (str, list, tuple, dict)) and len(val) == 0:
            continue
        # Trim trailing-zero noise on floats: "2.0" → "2".
        if isinstance(val, float):
            txt = f"{val:g}"
        else:
            txt = str(val)
        if not parts:
            parts.append(txt)
        else:
            parts.append(f"{name}={txt}")
    if not parts:
        return display
    return f"{display}({', '.join(parts)})"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_overlay_legend_rows(
    manager: IndicatorManager,
    scope: str,
    interval: str,
    *,
    theme_text: str = "#cccccc",
) -> list[ReadoutLegendRow]:
    """Return the legend rows for overlay indicators on ``(scope, interval)``.

    ONE :class:`ReadoutLegendRow` per overlay indicator config — even
    multi-output ones (Bollinger, AVWAP-with-bands). Each row's
    ``outputs`` list carries one :class:`OverlaySegment` per visible
    output key (the indicator class's ``effective_output_keys``
    filtered by per-output ``style.visible``).

    Hidden configs are included (greyed by the renderer) so the user
    can right-click → Show to re-enable them. The order matches
    :func:`gui.overlay_legend.collect_overlay_configs` (manager
    insertion order).
    """
    rows: list[ReadoutLegendRow] = []
    try:
        configs = collect_overlay_configs(manager, scope, interval)
    except Exception:  # noqa: BLE001
        return rows
    for cfg in configs:
        keys = _effective_output_keys_for(cfg)
        if not keys:
            continue
        multi = len(keys) > 1
        segments: list[OverlaySegment] = [
            OverlaySegment(
                output_key=k,
                key_label=k if multi else "",
                color=_color_for_key(cfg, k, theme_text),
            )
            for k in keys
        ]
        rows.append(
            ReadoutLegendRow(
                config_id=int(cfg.id),
                label=format_indicator_label(cfg),
                outputs=segments,
                visible=bool(getattr(cfg, "visible", True)),
            )
        )
    return rows


__all__ = (
    "OverlaySegment",
    "ReadoutLegendRow",
    "build_overlay_legend_rows",
    "format_indicator_label",
)
