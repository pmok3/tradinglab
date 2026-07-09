# gui/events_app.py — Spec

`EventsAppMixin` — the events-overlay glue (historical earnings/dividend
glyphs), extracted from `ChartApp` (mixin-extraction wave-4, AGENTS.md §7.24).
Pure method-bag: no `__init__`, no `super()`; reads/writes state owned by
`ChartApp.__init__`.

## Methods

- `_get_events_view_for_slot(slot) -> EventsView | None` — resolve a **gated**
  events view for the symbol displayed in `slot` (`_slot_symbol(slot)`).
  Sandbox-active → delegates to the controller's `events_visible_for` (honours
  session clock + blind flag); non-sandbox → looks the bundle up in
  `_events_cache` and gates it against `time.time()*1000` with `blind=False`
  (forward earnings inside the window are visible). Returns `None` when no
  bundle is known or gating import fails.
- `_render_event_glyphs_for_slot(slot)` — resolves sandbox blind mode, then
  `self._ensure_renderer().render_event_glyphs_for_slot(slot,
  get_events_view=self._get_events_view_for_slot, theme=self._theme,
  sandbox_blind=...)`.
- `_load_events_async(symbol)` — submit a background `EventBundle` fetch for
  `symbol` on `_fetch_executor` (sibling of `_load_data_async`). Deduped via
  `_events_fetch_inflight`, token-gated via `_events_fetch_token` so a
  superseded load's late callback doesn't overwrite a fresher bundle; on
  success caches into `_events_cache` and calls `_request_redraw_for_events`.
  Marshals back to the Tk thread via `_await_future_on_tk`.
- `_request_redraw_for_events()` — repaint event glyphs for the visible slots
  after a fetch lands (re-renders per slot via `_render_event_glyphs_for_slot`,
  invalidates `_blit_bg`, and schedules a watchlist-tab refresh via
  `_schedule_watchlist_tab_refresh`).

## Dependencies

State on `ChartApp`: `_events_cache` (LRUDict), `_events_fetch_token`,
`_events_fetch_inflight`, `_fetch_executor`, `_sandbox_controller`,
`_panel_state`, `_figure`, `_blit_bg`, `_theme`. Methods on ChartApp / sibling
mixins: `_slot_symbol`, `_await_future_on_tk`, `_ensure_renderer`,
`_schedule_watchlist_tab_refresh` (WatchlistTabMixin). External (in-method
imports): `..events.EVENT_SOURCES`, `..events.gating.events_visible_for`,
`.. import defaults`, `.events_overlay.clear_event_glyph_artists`.

## Callers (remain in ChartApp)

`_load_data` calls `_load_events_async`; `_render` calls
`_render_event_glyphs_for_slot`. Both resolve via inheritance.

## Tests

`tests/unit/test_load_data_prefetch_indicator_refresh.py` monkeypatches
`app._load_events_async`; the smoke suite exercises the events-glyph flow. No
test reads `app.py` source for strings that now live here.
