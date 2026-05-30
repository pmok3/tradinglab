# `gui/overlay_legend.py` — overlay-config enumeration helper (legend retired)

## Status

**The floating Tk `OverlayLegend` pill is RETIRED at runtime.** It has
been replaced by transparent matplotlib `TextArea` rows rendered inside
the top-left readout offsetbox — see `gui/readout_legend.py` (pure
enumeration) and `InteractionMixin._build_readout_indicator_rows` /
`_update_readout` (rendering + live hover values) /
`_maybe_handle_readout_legend_click` (click routing). The new legend has
a transparent background (never overlaps the OHLCV strip) and shows
`NAME value` per visible overlay at the hovered bar.

`ChartApp.__init__` no longer constructs `OverlayLegend`:
`self._overlay_legend is None` and `self._overlay_legends == {}`. The
`_refresh_overlay_legend` / `_reposition_overlay_legends` /
`_on_theme_changed` paths short-circuit to no-ops on the empty dict.

The `OverlayLegend` class itself is **kept importable** (still unit-tested
in `tests/unit/gui/test_overlay_legend.py`) but is not wired into the
running app. Only `collect_overlay_configs` is reused at runtime (by
`gui/readout_legend.py`).

## Reused module helper

- `collect_overlay_configs(manager, scope, interval)` — returns
  overlay-class configs for `(scope, interval)`. Does **NOT** filter by
  `cfg.visible` (hidden overlays must remain enumerated so they can be
  re-enabled). Consumed by `readout_legend.build_overlay_legend_rows`.

## Dormant class API (no longer wired)

The `OverlayLegend` `ttk.Frame` (construct/refresh/reposition/apply_theme/
eye-toggle) remains as documented history below; it is retained for test
coverage and potential reuse but is not instantiated by the app.

- `OverlayLegend(master, *, manager, theme, on_row_dblclick=None,
  on_row_context_menu=None)` — `ttk.Frame` subclass.
- `OverlayLegend.refresh(overlay_configs)` rebuilds rows; empty list hides.
- `OverlayLegend.reposition_for_axes(ax, canvas_widget)`.
- `OverlayLegend.apply_theme(theme)`.
