# `chartstack/panel.py` — Top-level Tk container for the mini-chart strip

## Purpose

Owns the single shared `Figure` + `FigureCanvasTkAgg` and partitions N
stacked `Axes` across N `CardWidget` slots. `ChartApp` constructs one of
these via composition (`owner=self`); no mixin — `app.py` already has 11.

## Public API

- `ChartStackPanel(master, *, owner=None, geometry_store=None)`.
- `refresh()` — re-resolve bindings, invalidate stale per-slot caches when
  the binding changed, redraw placeholders, and call `card.controller.start()`
  on every non-empty slot for first-paint fetch. Also calls
  `controller.start_stream(self._subscription_registry)` after the fetch kick.
- `apply_card_stash(slot_index, token, symbol, candles)` — receive bars from
  the worker-inbox drain, populate the per-slot `CardSeriesCache`, render
  via `draw_card_sparkline`. **Token-gated**: payloads whose `token` is
  older than the controller's current token, or whose `symbol` no longer
  matches the slot's binding, are silently dropped (slot was re-bound while
  the fetch was in flight). Empty bars → placeholder + `mark_error()`.
- `apply_stream_event(slot_index, token, kind, bar)` — single entry point
  for stream events from `gui/polling.py:_drain_stream_queue`'s
  `"card:N"`-slot branch. Token-gated. `kind == "tick"` →
  `series_cache.upsert_tick`; `kind == "rollover"` →
  `series_cache.append_rollover` (capped at
  `chartstack.sparkline_bar_count`). Schedules a coalesced flush via
  `_schedule_idle_flush()` so many ticks within one Tk idle slice collapse
  to one flush.
- `subscription_registry` (read-only) — one `SubscriptionRegistry` per panel.
- `apply_theme(theme)` — recolor. Accepts a palette **dict** (the primary
  path — `ChartApp._apply_theme` cascades the already-resolved palette so
  user `theme_overrides` flow through), a legacy `str` (`"dark"`/`"light"`,
  routed through `constants.resolve_theme`), or `None` (light-mode default).
  Beyond figure patch + axes facecolors, every card slot is marked dirty
  and an idle flush is scheduled so the next render bakes the theme's `text`
  color into the symbol / placeholder text artists (otherwise `ax.clear()`
  resets them to matplotlib's default black). Skips the right-aligned %chg
  label (direction-encoding bull/bear/flat colour is preserved). Resolved
  palette is stored on `self._theme_palette` and forwarded into all
  subsequent `draw_card_*` calls so colors persist across binding swaps,
  sparkline refreshes, and sandbox lockstep flushes.
- `set_visible(visible)` / `is_visible()`.
- `demote_to(promoted_symbol, demoted_symbol)` — same-slot demote: rebinds
  the card currently bound to `promoted_symbol` to `demoted_symbol` and
  kicks a fresh fetch. No-op when the promoted card is unfindable, symbols
  match, or either argument is empty.
- `cards`, `figure`, `canvas` — read-only properties for tests.
- `on_card_promote` setter — callback fired with the card's symbol when its
  axes is left-clicked. Right-click and clicks outside any axes are ignored.

### Manual-pin API

- `pin_symbol(symbol)` — append to pinned list (deduped by `str(symbol)`);
  `refresh()`. No-op on `None` or repeat.
- `unpin_symbol(symbol)` — remove by stringified match; `refresh()` only on
  state change.
- `clear_manual_pins()` — wipe and re-resolve.
- `get_manual_pins() -> tuple[object, ...]` — read-only snapshot.

### Sandbox lockstep

- `attach_sandbox(sandbox)` — snapshot pin list, `stop_stream()` every card
  (no live feeds during sandbox), `sandbox.register_card_subscriber(self._on_sandbox_tick)`.
  Fires one initial `_on_sandbox_tick`. Idempotent against same sandbox;
  swaps to a new one if attached.
- `detach_sandbox()` — release subscription, restore pre-attach pin
  snapshot (session pins don't leak to live mode), `refresh()`. Idempotent.
- `_on_sandbox_tick()` — reads `sandbox.visible_candles_by_symbol` for each
  bound card, snapshots into per-slot `CardSeriesCache` (via
  `cache.invalidate()` + per-bar `append_rollover`), marks slot dirty. Uses
  `sandbox.is_active()` as auto-detach signal: `end_session` fires the
  subscriber with `active=False` → `detach_sandbox()` and bail.

- `destroy()` — disconnect mpl connects, release sandbox + stream
  subscriptions, cancel pending `after_idle` flush, tear down figure.

## Owner contract (read-only)

- `owner._watchlist_snapshot` — placeholder symbols when empty.
- `owner._fetch_executor` — submit point for `CardController.start`.
- `owner._worker_inbox` — sink for `("card_stash", payload)` items.
- `owner._chartstack` — `_drain_worker_inbox` reads this to dispatch stash
  payloads back to `apply_card_stash` (panel does not self-register;
  `app.py` sets the attribute).
- `owner.source_var` / `owner.interval_var` — read on Tk thread inside
  `CardController.start`.
- `owner._stream_queue` — sink for `(token, "card:N", src, ticker,
  interval, kind, bar)` events. `_drain_stream_queue` dispatches
  `"card:"`-prefixed slot strings to `apply_stream_event`.

Sandbox attachment goes through `attach_sandbox` (called from
`gui/sandbox_menu.py` after `SandboxController.start_session`). The panel
never mutates owner state.

## Design decisions

- **One Figure, N Axes.** Five canvases would multiply `_blit_bg` /
  `_on_draw_event` participants and complicate existing focus paths.
- **Per-slot `CardSeriesCache`** (not symbol-keyed) — rebinding invalidates
  via `set_binding`; slot-keying ties cache lifetime to visual card
  lifecycle. Capped at `chartstack.sparkline_bar_count` (default 60).
- Placeholder symbols `["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"][:N]` for
  the wireframe-out-of-the-box experience.
- **Click-to-promote**: `mpl_connect("button_press_event", ...)` with
  axes-level hit-test (`event.inaxes is card.ax`). Left button only;
  right-click reserved for the context menu.
- **`apply_card_stash` is the single integration point** for the worker-inbox
  drain. Token gating lives here so stale payloads can be dropped without
  coordinating with the controller.
- **Per-card-bbox blitting pipeline**: `mpl_connect("draw_event")`
  snapshots each card's bbox after every full draw; `apply_stream_event`
  mutates the per-slot cache + schedules `after_idle` flush;
  `_flush_dirty_cards()` for each dirty slot restores bbox bg, redraws
  sparkline artists, `canvas.blit(ax.bbox)` (falls back to `draw_idle()`
  if no bg is cached, e.g. right after a binding swap). The bbox cache is
  invalidated on binding change, theme change, card stash, demote, destroy.
- `_resolve()` threads `tuple(self._manual_pins)` into `resolve_bindings`
  so HYBRID mode gives pins priority slot allocation.
- `_render_card_sparkline` threads M4 toggles
  (`chartstack.show_vwap`/`show_pmh_pml`/`show_last_candles`/
  `volume_stroke_encoding`) into `draw_card_sparkline` and applies per-card
  border tints via `set_card_tint`. Bars carry `session` info from
  `_bar_from_event_bar` / `_bars_from_candles` so the render layer can
  wash pre/post-market runs and anchor VWAP at the RTH boundary. Passes
  `halted_at=card.controller.halt_index` so halted symbols get the grey +
  vertical-bar treatment (see `render.spec.md`).
