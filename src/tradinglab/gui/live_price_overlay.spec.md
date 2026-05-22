# gui/live_price_overlay.py — Spec

## Purpose
Owns the dotted horizontal "live quote" line at the current price, plus its right-edge label, for every slot in `_panel_state`. Mirrors `exits_overlay.py` / `entries_overlay.py` in lifecycle: rebuild on every `_render`, mutate in place on every `_refresh_view_after_tick`.

The overlay does **not** look up the latest price itself — the caller (`ChartApp`) resolves the price via `resolve_price(symbol, last_stream_price, panel_state_slot)` and passes it in. Keeps the renderer pure and the price-source policy in one place.

## Public API
- `class LivePriceOverlay`
  - `LivePriceOverlay(*, enabled: bool = True)` — single instance owned by `ChartApp`.
  - `redraw(*, ax_by_slot, price_by_slot, color, label_suffix="", label_bg=None, label_fg=None, label_edge=None)` — rebuild artists for every slot. Drops the previous pass's Python refs (`figure.clear()` already removed the matplotlib artists). When `label_bg` / `label_fg` / `label_edge` are all provided, the right-edge label renders as a TradingView-style boxed badge (round bbox patch); when any is `None`, the legacy unboxed plain-text label is rendered.
  - `update_in_place(slot, price, *, label_suffix="") -> bool` — mutate `line.set_ydata` + `label` position/text without re-rendering. Returns `True` if the artist was mutated, `False` if there's nothing to update (no artist for slot, non-finite price, or mutation raised). Handles both `Text` (legacy) and `Annotation` (boxed) labels — boxed labels move via `label.xy = (x, p)`, plain text labels move via `set_position`.
  - `apply_theme(*, line_color, label_bg, label_fg, label_edge)` — recolour every existing artist's line + label bbox + label text without rebuilding. Called by `gui.theme_controller.ThemeController._apply_overlay_artists` when the light/dark mode flips.
  - `set_enabled(enabled)` / `enabled` property — toggle the overlay off when (e.g.) sandbox-blind mode hides current price.
  - `slot_count` / `get_artists(slot)` — testing helpers.
  - `clear()` / `close()` — drop Python refs.
- `format_price(price) -> str` — module-level label formatter. 3 decimals for `|price| < 1`, otherwise 2 decimals with thousands separator. Returns empty string for None / non-finite.
- `resolve_price(symbol, *, last_stream_price, panel_state_slot) -> Optional[float]` — newest-wins resolver. Stream tick first, then last non-gap candle close. Pure function; takes plain dicts so tests don't need a `ChartApp`.

## Dependencies
- Internal: none. The module is self-contained; `ChartApp` wires it into `_render` / `_refresh_view_after_tick`.
- External: `matplotlib.axes`, `matplotlib.lines`, `matplotlib.text`, `matplotlib.transforms.blended_transform_factory`.

## Design Decisions
- **Stateless w.r.t. price source.** The renderer accepts `price_by_slot: Dict[str, Optional[float]]` rather than a callback or `ChartApp` handle. Decouples the renderer from `app.py` so the unit tests use plain dicts and pure matplotlib `Agg` axes.
- **Neutral color, dotted style, never direction-coded.** The live-price line is informational — it shows "where is price right now" — not a trading level. So it must not look like a stop/target line. Caller passes `theme["text"]`; the renderer never green/red-codes.
- **Right-edge label is a TradingView-style boxed badge.** Built via `ax.annotate(..., bbox=dict(boxstyle="round,pad=0.30", fc=theme["tooltip_bg"], ec=theme["spine"], alpha=1.0, linewidth=0.8))` so the price reads as the same "current value pill" idiom as the cursor crosshair price label (`gui/interaction.py::_build_hover_artists`). Mirrors the cursor's `xytext=(3, 0) textcoords="offset points"` offset so the badge doesn't visually crash into the right spine. The legacy unboxed `ax.text(...)` rendering is still available when callers omit the `label_bg` / `label_fg` / `label_edge` params (used by older tests).
- **Right-edge label uses `blended_transform_factory(ax.transAxes, ax.transData)`.** Mirrors `exits_overlay._draw_one`. The label sticks at axes-x=1.0 (right edge) at data-y=price even as xlim shifts during pan / zoom / streaming. The label is ` price` (leading space) so it doesn't visually butt against the price axis spine.
- **Z-order 3 (line) / 4 (label).** Below exits/entries overlay lines (z=4) so user-placed levels read on top of the cursor. Below crosshair (z=10/11). Above grid (z<3).
- **Sub-dollar prices get three decimals.** Penny stocks (e.g. SPRT @ 0.075) need finer resolution than $1+ tickers; the label format switches at `|price| < 1`. Otherwise `f"{p:,.2f}"` with thousands separator for readability on (e.g.) NVDA @ 1,250.50.
- **Non-finite suppresses the line.** None / NaN / inf input ⇒ no axhline drawn for that slot. Without this guard the very first render before any candles arrived would paint a `nan`-positioned line that matplotlib silently places off-axes.
- **`update_in_place` is the fast path.** `line.set_ydata([p, p])` + `label.set_position((1.0, p))` + `label.set_text(...)` updates without touching axes layout. The caller schedules `canvas.draw_idle()` once after all per-tick updates; we don't poke `canvas` here.
- **Gap candles are skipped in `resolve_price`.** `Bars.from_candles` includes gap candles (no trade) with NaN close. `resolve_price` walks `candles[::-1]` skipping `is_gap=True` so the line lands at the last real trade rather than NaN.
- **Symbol normalisation in `resolve_price`.** Stream-tick keys are uppercased on insert; `resolve_price` mirrors with `.strip().upper()` so the caller doesn't need to.
- **Per-slot scope only.** Drilldown re-uses `_panel_state["primary"]` (see `drilldown.py:555`), so it's covered automatically. No separate drilldown slot key.

## Invariants
- `redraw` clears the artist map before rebuilding ⇒ no stale refs after a `_render` cycle.
- `update_in_place(slot, price)` is a no-op when `redraw` has not been called for that slot, so the per-tick path is safe to call before the first `_render`.
- Calling `redraw` with `enabled=False` clears the artist map and draws nothing.
- The label's x position stays at axes-1.0 across `update_in_place` calls. Only the y (data coord) is updated.
- `format_price` never raises on weird inputs (None, "", non-numeric strings) — always returns a string (possibly empty).

