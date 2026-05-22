# `gui/overlay_legend.py` — per-axes overlay legend with eye-toggles

## Purpose

Horizontal strip inside each price panel, just below the OHLCV
readout. Each row: small color swatch, `display_name`, and an
eye-button toggling the config's `visible` flag through
`IndicatorManager`. Hidden configs render with `○` (vs `●`) so
they can be re-enabled with one click.

## Public API

- `OverlayLegend(master, *, manager, theme, on_row_dblclick=None,
  on_row_context_menu=None)` — `ttk.Frame` subclass. Construct
  once per `kind == "price"` axes (primary + compare).
  - `on_row_dblclick(config_id) -> None` — fires on row /
    swatch / label double-click. Wired to
    `_open_per_indicator_dialog(config_id, slot)`. Eye-button is
    NOT bound to double-click (would fire toggle + popup).
  - `on_row_context_menu(config_id, x_root, y_root) -> None` —
    fires on `<Button-3>`. Wired to
    `_show_legend_context_menu(cfg_id, slot, x, y)` for
    `Edit Settings… / Change Color… / Duplicate / Hide / Remove`.
  - When callbacks wired, row / swatch / label use `hand2` cursor.
- `OverlayLegend.refresh(overlay_configs: List[IndicatorConfig])`
  rebuilds rows; empty list hides the strip via `place_forget`.
- `OverlayLegend.reposition_for_axes(ax, canvas_widget)` — anchors
  below the axes' top-left with `_OHLCV_CLEARANCE_PX` (28 px)
  vertical offset. `ax=None` hides. Silent no-op when canvas not
  laid out (`winfo_height() <= 1`).
- `OverlayLegend.apply_theme(theme: dict)` — updates swatch
  outline on light↔dark flip.
- `collect_overlay_configs(manager, scope, interval)` — module
  helper returning overlay-class configs for `(scope, interval)`.
  Does **NOT** filter by `cfg.visible` (hidden overlays must
  remain in the legend to allow re-enable).

## Wiring

- `ChartApp.__init__` constructs one `OverlayLegend` per slot
  (`"primary"`, `"compare"`) into `self._overlay_legends`.
  `self._overlay_legend` is a back-compat alias for primary.
  Callbacks captured per-slot via default-arg closure to preserve
  slot through the popup / menu paths.
- `_refresh_overlay_legend` (from `_render`) drives both legends:
  primary scope `"main"`; compare scope `"compare"`, gated by
  `compare_var.get()`.
- `_reposition_overlay_legends` (from `_refresh_overlay_legend`
  and `InteractionMixin._on_draw_event`) calls
  `legend.reposition_for_axes(ax, canvas)`. Draw-event
  repositioning follows compare-toggles / resizes / theme switches.
- `_apply_theme` iterates `_overlay_legends.values()` for
  swatch-outline theme flip.

## Coordinate model

Matplotlib display = bottom-up; Tk = top-down. Conversion in
`reposition_for_axes`:

```
x = bbox.x0 + _LEFT_INSET_PX
y = canvas_h - bbox.y1 + _OHLCV_CLEARANCE_PX
```

`bbox = ax.get_window_extent()`,
`canvas_h = canvas_widget.winfo_height()`.

## Design decisions

- **Tk overlay vs matplotlib artists**: native `ttk.Button`s get
  proper hover / click / focus semantics without manual hit-tests.
- **Horizontal layout**: rows pack `side=tk.LEFT` (TradingView-
  style pill strip).
- **Per-axes anchoring**: one legend per `kind == "price"` axes
  so compare-panel users can toggle compare-scope overlays.
- **Auto-place gating**: `_anchor_ax is None` → fall back to
  `place(relx=1.0, anchor="ne", x=-8, y=8)` for legacy tests +
  smoke check that construct without anchoring.
- **Toggle path**: `manager.update(cfg_id, visible=...)` flips
  the flag, fires manager's redraw subscriber; next `_render`
  rebuilds artists. Persistence via existing manager save sub.
