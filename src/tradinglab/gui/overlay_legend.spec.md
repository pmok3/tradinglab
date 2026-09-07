# `gui/overlay_legend.py` — retired compatibility module

Last updated: 2026-09-07

## Status

The floating Tk `OverlayLegend` class is retired and no longer exists.
The runtime legend is transparent matplotlib `TextArea` rows rendered inside
the top-left readout offsetbox — see `gui/readout_legend.py` and
`InteractionMixin._build_readout_indicator_rows` /
`_update_readout` / `_maybe_handle_readout_legend_click`.

`ChartApp.__init__` no longer constructs `OverlayLegend`:
`self._overlay_legend is None` and `self._overlay_legends == {}`. The
`_refresh_overlay_legend` / `_reposition_overlay_legends` /
`_on_theme_changed` paths short-circuit to no-ops on the empty dict.

## Compatibility helper

- `collect_overlay_configs(manager, scope, interval)` — returns
  overlay-class configs for `(scope, interval)`. Does **NOT** filter by
  `cfg.visible` (hidden overlays must remain enumerated so they can be
  re-enabled). Consumed by `readout_legend.build_overlay_legend_rows`.

The helper is implemented in `gui.readout_legend` and re-exported here for
source compatibility with older integrations. New code should import it from
`readout_legend`; this module has no Tk or matplotlib runtime dependencies.
