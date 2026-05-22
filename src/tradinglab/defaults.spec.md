# defaults.py — Spec

## Purpose
Single canonical registry of every user-tweakable default. Replaces inline
literals scattered across `app.py`, `gui/interaction.py`, `core/viewport.py`.
Each tunable has a validator so corrupt `settings.json` can't inject garbage.

## Public API
- `TUNABLES: Tuple[Tunable, ...]` — ordered catalog. Each `Tunable` is a frozen dataclass `(key, default, kind, description, validator, is_user_facing)`.
- `get(key) -> Any` — resolved value (validated override if present in `settings.json`, else built-in default). Raises `KeyError` for unknown keys (catches typos in consumer code).
- `describe(key) -> (default, kind, description)` — for docs/dialogs.
- `reload() -> None` — drop the cache and re-read `settings.json` on next `get()`. Mainly for tests.
- `as_markdown_table() -> str` — render the catalog as a GFM table for `README.md`.
- `user_facing_keys() -> Tuple[str, ...]` — subset of `TUNABLES` keys whose `is_user_facing=True`. Used by `gui/dialogs.py` (Settings dialog) to decide which keys to surface as editable rows.
- `example_payload(*, with_comments: bool = True) -> Dict[str, Any]` — emits a fresh `settings.json`-shaped dict containing every user-facing key set to its default value. With `with_comments=True` the dict is wrapped to round-trip in JSON5 / comment-preserving emitters; with `False` it's a plain JSON-safe dict. Used to scaffold a sane starter `settings.json` from the Settings dialog's "Reset to defaults" button.

## Resolution model
- One-shot resolve at first `get()`: `_load_overrides()` reads `settings.json`,
  validates each known key, drops invalid entries silently. Cached in
  `_resolved` for process lifetime — defaults are not re-read mid-session.
  Restart-to-apply (live-mutation across tight loops / class-definition reads
  is not worth the wiring complexity).
- Validators are per-key `(v) -> (ok, normalized)` callables. `_v_int`
  explicitly rejects `bool` (Python `bool` is an `int` subclass) so an
  int-typed key set to `true` doesn't silently become `1`.

## Catalog (current)
| Key | Type | Default | Description |
|---|---|---|---|
| `display_tz` | str | "" | IANA timezone for intraday timestamps. Empty = ET (market local). |
| `scroll_zoom_invert` | bool | false | Mouse-wheel zoom direction. |
| `theme_overrides` | dict | {} | Per-theme color overrides. |
| `startup_defaults` | dict | {} | Per-key startup overrides (ticker, compare, interval, source, theme). |
| `default_window_bars` | int | 200 | Right-edge default window size (bars). Note: a per-interval default is a known follow-up — at 5m this currently shows ~1 week of data. |
| `full_cache_size` | int | 16 | LRU memory-cache size for fetched (candles, meta) tuples. |
| `hover_throttle_ms` | int | 16 | Coalescing window for hover/crosshair updates. |
| `scroll_zoom_factor_per_step` | float | 1.15 | Per-notch zoom factor. |
| `scroll_zoom_step_clamp` | float | 2.0 | Max \|event.step\| per wheel event. |
| `scroll_zoom_min_bars` | float | 3.0 | Floor on visible-bar count when zooming in. |
| `price_top_pad_frac` | float | 0.12 | Top headroom on price axes (fraction of data span). |
| `price_bot_pad_frac` | float | 0.05 | Bottom padding on price axes. |
| `volume_tod_enabled` | bool | false | Master toggle for time-of-day volume shading on 1d volume bars. |
| `volume_tod_median_lookback_days` | int | 20 | Trading-day lookback for the rolling median full-day volume reference tick. |
| `volume_tod_rth_only` | bool | true | Internal — restrict TOD shading's intraday source to RTH bars (09:30–16:00 ET). |
| `volume_tod_intraday_interval` | str | "5m" | Internal — intraday interval used as the TOD shading source. |
| `indicators` | dict | {} | Persisted per-ticker `IndicatorConfig` payloads keyed by ticker/scope. |
| `custom_indicators_enabled` | bool | false | Allow user-authored indicator factories registered at startup to load. |
| `indicator_last_preset_per_ticker` | dict | {} | Per-ticker `{preset_id, ts}` map used by the Indicator menu to re-apply the last preset on chart load. |
| `show_earnings` | bool | true | Show historical earnings glyphs on the price pane bottom band. |
| `show_dividends` | bool | true | Show historical dividend / corporate-action glyphs on the price pane bottom band. |
| `show_upcoming_events` | bool | true | Render the right-edge forward-earnings badge when a print is within `earnings_window_days`. |
| `earnings_window_days` | int | 10 | Proximity-window radius (trading days) for the pre-trade journal warning and Performance View proximity rollup. |
| `events_source` | str | "yfinance" | Active `EVENT_SOURCES` registry key. Architecture allows future Schwab / Polygon / Alpaca registration; only the registered ones are valid here. |
| `pre_earnings_warn_in_journal` | bool | true | Inline passive notice at the top of `PreTradeFormDialog` when entering within the earnings_window_days proximity. No extra click. |
| `events_fetch_ttl_seconds` | int | 43200 | Internal — disk-cache TTL for `EventBundle`s (default 12h). |
| `events_hover_hit_px` | int | 8 | Internal — hover hit-test radius (pixels) for event glyphs. |
| `local_data` | dict | `{"enabled": false, "roots": []}` | BYOD (Bring Your Own Data) configuration: enabled flag + list of `{"name": str, "path": str}` root entries. Each top-level subfolder of each root becomes a registered source named `<root_name>-<subdir>`. Managed via Tools → Configure Local Data…. See `docs/LOCAL_DATA.md`. |

> `worker_count` is now a registered Tunable again (audit `workers-persisted`). Default `0` = auto-detect via `os.cpu_count()` clamped to `[1, 64]`. Any positive value the user picks in the Settings slider persists to `settings.json` and is reapplied on the next launch. See `gui/workers.spec.md` for the resolution order.

## Consumers
| Tunable | Consumer |
|---|---|
| `display_tz` | `app.py:__init__` → `formatting.format_dt` |
| `scroll_zoom_invert` | `app.py:__init__` → `gui/interaction.py:_on_scroll_zoom` |
| `theme_overrides` | `app.py` theme system |
| `startup_defaults` | `app.py:__init__` → `constants.resolve_startup_defaults` |
| `default_window_bars` | `app.py` `_render`, `_reset_view`, drilldown sizing |
| `full_cache_size` | `app.py` `_FULL_CACHE_MAX` constant |
| `hover_throttle_ms` | `gui/interaction.py` `_HOVER_THROTTLE_MS` |
| `scroll_zoom_factor_per_step` / `_step_clamp` / `_min_bars` | `InteractionMixin._SCROLL_ZOOM_*` class attrs |
| `price_top_pad_frac` / `price_bot_pad_frac` | `core/viewport.py:y_limits_for_slice` |
| `volume_tod_enabled` / `volume_tod_median_lookback_days` / `volume_tod_rth_only` / `volume_tod_intraday_interval` | `app.py:_render_volume_tod_for_slot` → `gui/volume_tod_overlay.py` |
| `indicators` / `custom_indicators_enabled` / `indicator_last_preset_per_ticker` | `indicators/store.py`, `gui/indicator_menu.py`, `gui/indicator_dialog.py` |
| `show_earnings` / `show_dividends` / `show_upcoming_events` / `earnings_window_days` / `events_source` / `pre_earnings_warn_in_journal` / `events_fetch_ttl_seconds` / `events_hover_hit_px` | `app.py:_load_events_async`, `events/render.py`, `gui/events_overlay.py`, `gui/sandbox_dialog.py:PreTradeFormDialog`, `gui/performance_view.py`, `gui/watchlist_tab.py`, `events/cache.py`, `gui/interaction.py:_check_event_glyph_hit` |
| `local_data` | `data/__init__.py:register_local_sources`, `gui/local_data_dialog.py` |
| `watchlist_poll_interval_sec` / `watchlist_poll_offhours_multiplier` | `gui/watchlist_tab.py:_watchlist_poll_effective_delay_ms`, `_start_watchlist_poll_loop`, `_watchlist_poll_tick`. Recurring background poll that re-runs `_preload_watchlist` + `_preload_watchlist_daily` so transient yfinance failures self-heal. Set interval to 0 to disable. Off-hours (outside 09:30–16:00 ET weekdays) multiplies the interval. Floor of 5 seconds in `_watchlist_poll_effective_delay_ms` defends against misconfiguration. |

## Adding a new tunable
1. Append a `Tunable(...)` to the `TUNABLES` tuple.
2. Replace the inline literal in the consumer with `defaults.get("key")`.
3. Update the catalog table above and `README.md`.
4. If user-facing, add a row in the Settings dialog (`gui/dialogs.py`).
