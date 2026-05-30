"""Pure helpers for the in-readout overlay indicator legend.

Historically TradingLab drew overlay indicators (SMA / EMA / Bollinger
…) into a separate opaque Tk pill strip (:mod:`gui.overlay_legend`)
placed *below* the top-left OHLCV readout. That widget overlapped the
readout and had a solid background — both visually and structurally at
odds with the always-on, transparent matplotlib readout above it.

This module computes the **rows** for a TradingView-style legend that
lives *inside* the matplotlib readout offsetbox: one line per overlay
output, ``NAME  value`` with the output's own colour, name on hover
updating live exactly like the OHLCV strip. The actual matplotlib
``TextArea`` construction + hover-value plumbing lives in
:class:`gui.interaction.InteractionMixin`; everything here is a pure
function of the indicator manager + theme so it can be unit-tested
without Tk or matplotlib.

The enumeration deliberately INCLUDES hidden configs (greyed) so the
user can re-enable them with a right-click → Show, matching the old
pill strip's affordance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .overlay_legend import collect_overlay_configs

if TYPE_CHECKING:
    from ..indicators.config import IndicatorConfig, IndicatorManager


@dataclass(frozen=True)
class ReadoutLegendRow:
    """One legend row: a single output of one overlay indicator config.

    * ``config_id`` / ``output_key`` identify the line for value reads
      and for routing right-click / double-click gestures back to the
      per-indicator dialog + context menu.
    * ``label`` is the display text (``display_name`` for single-output
      indicators, ``"display_name key"`` for multi-output ones).
    * ``color`` is the resolved swatch / text colour for the output.
    * ``visible`` mirrors ``cfg.visible`` so the renderer can grey /
      strike hidden rows while keeping them re-enable-able.
    """

    config_id: int
    output_key: str
    label: str
    color: str
    visible: bool


def _output_keys_for(cfg: IndicatorConfig) -> list[str]:
    """Return the ordered output keys for ``cfg``.

    Prefers the factory's ``default_style`` keys (the canonical output
    set the renderer styles each line by). Falls back to the keys of
    the config's own ``style`` override, and finally to a single
    synthetic key equal to ``kind_id`` so an indicator with no declared
    style still yields exactly one row.
    """
    try:
        from ..indicators.config import factory_by_kind_id

        entry = factory_by_kind_id(cfg.kind_id)
        if entry is not None:
            _name, factory = entry
            default_style = getattr(factory, "default_style", None)
            if default_style and hasattr(default_style, "keys"):
                keys = [str(k) for k in default_style.keys()]
                if keys:
                    return keys
    except Exception:  # noqa: BLE001
        pass
    try:
        keys = [str(k) for k in cfg.style.keys()]
        if keys:
            return keys
    except Exception:  # noqa: BLE001
        pass
    return [str(getattr(cfg, "kind_id", "") or "value")]


def _color_for_key(cfg: IndicatorConfig, key: str, theme_text: str) -> str:
    """Resolve the colour for one output ``key`` of ``cfg``.

    Order: the config's per-key style override → the factory's per-key
    ``default_style`` colour → the theme's neutral text colour.
    """
    try:
        ls = cfg.style.get(key)
        color = getattr(ls, "color", None)
        if color:
            return str(color)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..indicators.config import factory_by_kind_id

        entry = factory_by_kind_id(cfg.kind_id)
        if entry is not None:
            _name, factory = entry
            default_style = getattr(factory, "default_style", None)
            if default_style and hasattr(default_style, "get"):
                ls = default_style.get(key)
                color = getattr(ls, "color", None)
                if color:
                    return str(color)
    except Exception:  # noqa: BLE001
        pass
    return theme_text


def build_overlay_legend_rows(
    manager: IndicatorManager,
    scope: str,
    interval: str,
    *,
    theme_text: str = "#cccccc",
) -> list[ReadoutLegendRow]:
    """Return the legend rows for overlay indicators on ``(scope, interval)``.

    One :class:`ReadoutLegendRow` per output key of every overlay
    config — including hidden ones (so they stay re-enable-able). The
    order matches :func:`gui.overlay_legend.collect_overlay_configs`
    (manager insertion order), with multi-output indicators expanded in
    their ``default_style`` key order.
    """
    rows: list[ReadoutLegendRow] = []
    try:
        configs = collect_overlay_configs(manager, scope, interval)
    except Exception:  # noqa: BLE001
        return rows
    for cfg in configs:
        keys = _output_keys_for(cfg)
        multi = len(keys) > 1
        name = cfg.display_name or cfg.kind_id or (keys[0] if keys else "")
        visible = bool(getattr(cfg, "visible", True))
        for key in keys:
            label = f"{name} {key}" if multi else name
            color = _color_for_key(cfg, key, theme_text)
            rows.append(
                ReadoutLegendRow(
                    config_id=int(cfg.id),
                    output_key=key,
                    label=str(label),
                    color=str(color),
                    visible=visible,
                )
            )
    return rows


__all__ = ("ReadoutLegendRow", "build_overlay_legend_rows")
