# tradinglab — Spec Index

A one-spec-per-`.py` documentation set. Each spec follows a fixed 9-section layout (Purpose, Public API, Dependencies, Design Decisions, Invariants, Data Flow / Algorithm, Testing, Known limitations / Future work, Recent history) and is detailed enough that the accompanying `.py` could be reconstructed from it.

**Style guide:** see [`SPEC_STYLE.md`](SPEC_STYLE.md) for the canonical layout every spec follows.

**Count: 68 specs (one per `.py` module).**

## Top-level (`tradinglab/`)
| Spec | Covers |
|---|---|
| `__init__.spec.md` | Package init — version, marker. |
| `__main__.spec.md` | `python -m tradinglab` entry point. |
| `app.spec.md` | **`ChartApp`** — Tk+matplotlib main window; mixes in `InteractionMixin`, `WatchlistTabMixin`, `WorkerPoolMixin`. Owns all state (caches, tokens, executor, figure). |
| `constants.spec.md` | `INTERVALS`, `PREPOST_CHOICES`, color palettes, geometry constants. |
| `defaults.spec.md` | **Canonical registry of every user-tweakable default.** `TUNABLES` catalog + `get(key)` accessor; reads validated overrides from `settings.json` once at boot. |
| `models.spec.md` | `Candle` dataclass. |
| `formatting.spec.md` | `fmt_price`, `fmt_volume`, `fmt_delta`, `fmt_pct`. |
| `settings.spec.md` | JSON-backed key/value store (`get`, `set`, path under user home). |
| `disk_cache.spec.md` | Per-ticker+interval JSON cache with metadata. |
| `baskets.spec.md` | Built-in basket loaders — `sp500_symbols`, `qqq_symbols`, `nyse_symbols`, `nasdaq_symbols`; `BUILTIN_BASKETS` / `BUILTIN_BASKET_LABELS` / `BUILTIN_BASKET_REFRESHED_DATES` / `FULL_EXCHANGE_BASKETS`. Pure data, CSV-backed for SP500/NYSE/NASDAQ + hardcoded for QQQ. See [`docs/UNIVERSES.md`](UNIVERSES.md). |
| `rendering.spec.md` | `draw_candlesticks`, `draw_volume`, `draw_session_shading` — pure figure-drawing primitives. |
| `status.spec.md` | Status-bar formatter — composes "N bars / last time / last close" + `● LIVE` prefix; pure-function string builder. |

## `core/` — slice math and series containers
| Spec | Covers |
|---|---|
| `core/__init__.spec.md` | Re-exports. |
| `core/pairing.spec.md` | `apply_pair_filter` — aligns primary/compare candle lists by date, identity-preserving on no-op. |
| `core/series.spec.md` | `SeriesArrays` cache object (aliased as `_SeriesArrays` in `app.py`) — numpy arrays built from `Candle` list; `__init__` legacy path + `from_arrays` classmethod fast path; `build_series_safe` worker-thread builder. |
| `core/viewport.spec.md` | `y_limits_for_slice` — min/max over a slice with padding. |

## `data/` — fetchers and normalization
| Spec | Covers |
|---|---|
| `data/__init__.spec.md` | `DATA_SOURCES` registry. |
| `data/_http.spec.md` | Shared credential-safe HTTP opener — `credentialed_opener()` strips auth on cross-host 30x; `MAX_RESPONSE_BYTES = 8 MB`. |
| `data/base.spec.md` | `DataFetcher` protocol. |
| `data/normalize.spec.md` | `candles_from_dataframe` + `_PREBUILT_ARRAYS` side-channel (identity-paired). |
| `data/parallel.spec.md` | Parallel multi-ticker fetch helper. |
| `data/synthetic_source.spec.md` | Deterministic pseudo-random candles. |
| `data/yfinance_source.spec.md` | `yfinance` wrapper with retry + prepost handling. |
| `data/local_source.spec.md` | BYOD CSV import: `make_local_fetcher`, strict canonical schema, `discover_subsources`. |
| `data/local_export.spec.md` | BYOD CSV export: `write_csv`, `export_entries` (atomic, multi-entry). |

## `indicators/`
| Spec | Covers |
|---|---|
| `indicators/__init__.spec.md` | Registry. |
| `indicators/base.spec.md` | Indicator protocol. |
| `indicators/config.spec.md` | `IndicatorConfig` + `ParamDef` schema; per-pair config storage. |
| `indicators/loader.spec.md` | Built-in indicator registration on package import. |
| `indicators/cache.spec.md` | `IndicatorCache` — identity-keyed LRU (cap 64), key `(id(candles), config_hash)`. |
| `indicators/moving_averages.spec.md` | Unified `MovingAverage` indicator (SMA / EMA / WMA / RMA × Close / Open / High / Low / HL2 / HLC3 / OHLC4); legacy `SMA`/`EMA` kept as hidden back-compat shims. |
| `indicators/rsi.spec.md` | Wilder's RSI. |
| `indicators/atr.spec.md` | Wilder's ATR. |
| `indicators/adx.spec.md` | Wilder's ADX (with +DI / -DI). |
| `indicators/bollinger.spec.md` | Bollinger Bands; SMA/EMA basis selectable via `ma_type`, separate stddev window. |
| `indicators/lrsi.spec.md` | Laguerre RSI. |
| `indicators/smi.spec.md` | Stochastic Momentum Index. |
| `indicators/vwap.spec.md` | Session-anchored VWAP (RTH-anchored). |
| `indicators/avwap.spec.md` | Anchored VWAP — user-clickable bar starts cumulation; optional ±1σ / ±2σ bands. |
| `indicators/rvol.spec.md` | Relative Volume family — Cumulative-Day / Time-of-Day / Simple Rolling, sharing one pane via `pane_group="rvol"`. |
| `indicators/sessions.spec.md` | Shared session helpers (per-day grouping, HH:MM time-of-day key, intraday detection). |
| `indicators/render.spec.md` | Render-side bridge: gap-aware compute → `Line2D` artists on price + lower panes; `PanelIndicatorState` walked by blit / theme swap. |

## `backtest/` — Sandbox bar-replay engine
| Spec | Covers |
|---|---|
| `backtest/__init__.spec.md` | Package overview + public re-exports. |
| `backtest/bars.spec.md` | `BarSeries` + `from_candles` + adapter cache. |
| `backtest/clock.spec.md` | `Clock` master-timeline iterator. |
| `backtest/orders.spec.md` | `Side` / `Order` / `Fill` dataclasses. |
| `backtest/fills.spec.md` | `apply_fills` — pure-function fill model with slippage + commission. |
| `backtest/portfolio.spec.md` | `Position` / `Portfolio` — weighted-avg cost, flip-through-zero realised P/L. |
| `backtest/journal.spec.md` | `PreTradeEntry` / `PostTradeReview` records. |
| `backtest/tags.spec.md` | `TagStore` setup-tag taxonomy. |
| `backtest/deck.spec.md` | `DeckEntry` + date-only deck APIs (`draw_one_date`, `filter_candles_to_session`, `build_eligible_dates`). |
| `backtest/session.spec.md` | `SessionSpec` / `SessionResult` + `ENGINE_VERSION = "sandbox-1d"`. |
| `backtest/engine.spec.md` | `SandboxEngine` — frozen master timeline, idempotent `register_bars`, fixed tick phases (fills → MAE/MFE → mark-to-market), `flatten_all_at_close`. |
| `backtest/persistence.spec.md` | `save_session` / `load_session` — versioned JSON envelope + mirrored screenshots. |
| `backtest/performance.spec.md` | `TradeRow` + `SetupAggregate` + `ProximityAggregate` + `build_trade_rows` / `build_setup_aggregates` / `build_proximity_aggregates` — round-trip pairing + per-setup / per-event-proximity rollups. |
| `backtest/replay.spec.md` | `SandboxController` — open-universe, frozen master timeline, multi-TF daily context, multi-interval intraday display via aggregation, blind / auto-cycle, post-trade memento callback. |
| `backtest/aggregation.spec.md` | `aggregate` + `divides_evenly` — pure-Python session-anchored higher-TF candle derivation from a single primary tick interval. |
| `backtest/actions.spec.md` | `CorporateAction` (input) + `CashAdjustment` / `QuantityAdjustment` (engine-output facts) for the corporate-action tick phase. |

## `strategy_tester/` — Mechanical entry/exit pairing over a universe
| Spec | Covers |
|---|---|
| `strategy_tester/__init__.spec.md` | Package overview + public re-exports (`run`, `TestConfig`, `TestRun`, `RunStatus`, `UniverseSpec`, `UniverseKind`, `DatePreset`, `CostModel`, `AcceptanceToken`, `RunCancelled`). |
| `strategy_tester/acceptance.spec.md` | `AcceptanceToken` — cancellation wrapper around `threading.Event` + `RunCancelled` sentinel. |
| `strategy_tester/model.spec.md` | `TestConfig` / `TestRun` / `UniverseSpec` / `CostModel` / `RunStatus` / `UniverseKind` / `DatePreset` + `validate_config` + `make_run_id` (sha256-12 over canonical_json + engine_version). |
| `strategy_tester/universe.spec.md` | `resolve` + `resolve_preset` + `resolve_watchlist` + `PRESETS` (megacaps / sp500_seed / nasdaq100_seed / dow30_seed). |
| `strategy_tester/evaluator.spec.md` | Headless trigger-evaluation kernel — registry-based dispatch on `EntryTrigger.kind` / `ExitTrigger.kind`; PR-1 wires MARKET/LIMIT/STOP/STOP_LIMIT + eod_kill_switch; unsupported kinds raise `UnsupportedTriggerKind`. |
| `strategy_tester/runner.spec.md` | `run(cfg, *, cancel_token, progress, candles_fetcher, entry_loader, exit_loader, max_workers, today, screenshot_spec) -> RunResult` orchestrator — ThreadPoolExecutor fan-out, per-symbol independent capital, atomic manifest writes, partial results on cancel/error. Screenshots are opt-in via `screenshot_spec`. |
| `strategy_tester/screenshot.spec.md` | Headless per-trade PNG rendering — `render_trade_screenshot` composes `rendering.draw_candlesticks` / `draw_volume` on a `FigureCanvasAgg` (no Tk, no pyplot); annotates entry / exit / MAE / MFE / optional target line. `<run_dir>/screenshots/<SYM>_<order_id>_post.png`. |
| `strategy_tester/report.spec.md` | Whole-Run aggregation kernel — `compute_aggregate` (pure) + `aggregate_run` (disk driver) compute Wilson score CI on win rate, 10K-sample bootstrap CIs on expectancy + profit factor (fixed `rng_seed=1337`), daily-Sharpe / daily-Sortino annualised by `sqrt(252)`, max DD, per-symbol + per-year breakouts, best/worst-month-removed P&L, sample-size banners (N<30 / N<100). Persists `aggregate.json` + `trades.csv` (24-column). |
| `strategy_tester/storage.spec.md` | `run_dir_for` / `save_config` / `save_manifest` / `save_session_result_for_symbol` / `list_runs` / `delete_run` — persists under `%LOCALAPPDATA%/TradingLab/strategy_tests/<run_id>-<iso_ts>/`. |

## `events/` — Earnings & dividends ambient context
| Spec | Covers |
|---|---|
| `events/__init__.spec.md` | Package entry point + `EVENT_SOURCES` registry + conditional provider registration. |
| `events/base.spec.md` | `EarningsRecord`, `DividendRecord`, `EventBundle`, `EventFetcher` protocol. |
| `events/gating.spec.md` | `events_visible_for` + `EventsView` + `ForwardEarningsBadge` — pure sandbox gating + blind-mode redaction. |
| `events/synthetic_events.spec.md` | Deterministic in-memory fetcher for smoke tests / fallback. |
| `events/yfinance_events.spec.md` | yfinance-backed fetcher with column-tolerant normaliser. |
| `events/cache.spec.md` | Disk-backed bundle cache (mirror of `disk_cache`). |
| `events/render.spec.md` | Pure glyph-descriptor builder for the GUI overlay layer. |

## `streaming/`
| Spec | Covers |
|---|---|
| `streaming/__init__.spec.md` | `STREAM_SOURCES` registry. |
| `streaming/base.spec.md` | `StreamSubscription` protocol. |
| `streaming/synthetic.spec.md` | Synthetic tick generator on a background thread. |

## `watchlists/`
| Spec | Covers |
|---|---|
| `watchlists/__init__.spec.md` | Re-exports. |
| `watchlists/manager.spec.md` | `WatchlistManager` — CRUD over named lists + pin APIs (`MAX_PINNED=5`, `pinned_names/pin/unpin/reorder_pins`). |
| `watchlists/storage.spec.md` | JSON persistence (schema v2: `{version, watchlists, pinned}`) + import/export. |

## `gui/`
| Spec | Covers |
|---|---|
| `gui/__init__.spec.md` | Subpackage marker; avoids `tradinglab.app` back-imports. |
| `gui/color_palette.spec.md` | Per-scope indicator colour palette + assignment helpers; deterministic next-colour selection per (scope, kind). |
| `gui/menu_theme.spec.md` | Classic `tk.Menu` palette helper; recursively themes menus and appends the U+203A cascade-chevron workaround for Windows dark mode. |
| `gui/dialogs.spec.md` | `_SettingsDialog` (live preview, cancel-revert) + `_WatchlistDialog` (CRUD + pin column + import/export). |
| `gui/interaction.spec.md` | `InteractionMixin` — pan (blit), zoom (rubber band), hover, crosshair, click-to-type, Y-autoscale. |
| `gui/watchlist_tab.spec.md` | `WatchlistTabMixin` — nested-notebook with one sub-tab per pinned watchlist (cap 5); snapshot-driven Treeview per sub-tab, click-to-sort (per-tab), debounced repaint. |
| `gui/workers.spec.md` | `WorkerPoolMixin` — `ThreadPoolExecutor` lifecycle + live swap. |
| `gui/indicator_dialog.spec.md` | Modeless "Manage Indicators…" Toplevel — singleton, manager-subscribed, debounced live commit, scope checkboxes preserve drilldown, unknown-kind rows read-only. Supports `restricted_to_config_id` mode for single-row reuse by `per_indicator_dialog`. |
| `gui/per_indicator_dialog.spec.md` | Per-indicator settings popup spawned by double-clicking an overlay-legend row — singleton-per-`config_id`, reuses `IndicatorDialog._build_row` widgets, auto-closes on remove / clear / preset_load. |
| `gui/sandbox_dialog.spec.md` | `SandboxStartDialog` (open-universe start, eligibility-aware, blind ⇒ auto-cycle) + `PreTradeFormDialog` (mandatory thesis + positive size). |
| `gui/sandbox_panel.spec.md` | `SandboxPanel` sidebar — clock / cash / positions / focus / Buy-Sell / Next-bar / End-session; dumb panel, smart controller. |
| `gui/sandbox_review_dialog.spec.md` | `PostTradeReviewDialog` (mandatory non-empty, X-button refused) + `TagsEditorDialog` (wholesale Replace on OK). |
| `gui/performance_view.spec.md` | `PerformanceView` Toplevel — sortable trade table + per-setup aggregates; read-only, UTC timestamps. |
| `gui/local_data_dialog.spec.md` | BYOD: `Configure Local Data…` Toplevel — enabled toggle + roots list + Save-and-Close paradigm. |
| `gui/export_cache_dialog.spec.md` | BYOD: `Export Bars to CSV…` Toplevel — Treeview + checkbox column, Select All/None, atomic export. |
| `gui/universe_prepare_dialog.spec.md` | `UniversePrepareDialog` Toplevel — 4-basket picker (SP500/QQQ/NYSE/NASDAQ) + watchlist option, reactive ETA/size estimate, amber survivorship banner for full-exchange baskets, Stop-safe-to-resume cancel paradigm, fundamental-filter prepass. See [`docs/UNIVERSES.md`](UNIVERSES.md). |
| `gui/strategy_tab.spec.md` | `StrategyTab` notebook tab — full Configure → Running → Result UX loop for the Strategy Tester. Entry/exit pickers, 3-mode universe picker (Symbols/Watchlist/Preset), date-range preset, advanced cost model, Run / Stop, headline metrics + per-symbol & per-year Treeviews. Runs the strategy_tester kernel on a daemon thread; loads `aggregate.json` via `report.load_aggregate`. Survivorship + sample-size banners. |

## `preload/` — Sandbox universe preload pipeline
| Spec | Covers |
|---|---|
| `preload/service.spec.md` | `preload_universe` — pure-logic batch fetch with L1/disk/network ladder, retry budget, inter-op rate-limit, cancellation contract; injectable fetcher/cache/sleep so it tests headless. |
| `preload/manifest.spec.md` | `UniverseManifest` JSON sidecar + `build_from_loaded(previous=...)` interval-union semantics + `coverage_for_date` (off-Tk-thread for N > 500). |
| `preload/fundamental_filter.spec.md` | Optional prepass filter on min volume / min close / max close / lookback days. |

## Cross-cutting architectural notes
- **Mixin rules**: no `__init__`, no `super()`. All state in `ChartApp.__init__`.
- **Token gating**: every fetch and stream carries a monotonically incrementing token; stale callbacks are dropped. `_fetch_token` is bumped in both `_load_data` and `_next_bar_fetch_tick` (the poll-tick async path).
- **Identity preservation**: `apply_pair_filter` returns the input list when no-op, `_PREBUILT_ARRAYS` verifies `(candles_ref, arrays)` identity on pop, `_series_cache` verifies `sa._candles is candles`. All three protect streaming's in-place tick updates.
- **X coordinate = global candle index + x_offset**: slicing never renumbers bars; pan/zoom state stays consistent with virtualized slice rendering.
- **`figure.clear()` is called only from `_render`**; blit machinery depends on this contract.
- **`_preserve_xlim_on_render`** flag is sticky by design: survives theme/compare/scale toggles and stream ticks; explicitly reset by `_reset_view` and `_do_scheduled_reload`.
- **`_slide_xlim_to_right_edge`** is a one-shot companion flag consumed-and-cleared at the top of every `_render`; re-asserted by `_next_bar_fetch_tick` when the user was glued to the right edge, so poll-driven updates appear live without clobbering zoom.
- **Bar-close aligned polling**: the pure helper `_compute_fetch_delay_ms` anchors on last-bar timestamp + interval + 5s grace (respects session-aligned 1h/4h bars), postpones past market-closed windows (weekends/overnight), respects `prepost_var`. Up to 2 retries at 5s spacing when the provider hasn't yet published the expected bar.
- **Async poll fetch**: `_next_bar_fetch_tick` offloads the provider call to `_fetch_executor`; the result hands back to `_load_data` via the one-shot `_prefetched_raw` slot on the main thread. User-triggered loads remain synchronous.
- **Notebook tab labels reflect tickers**: Primary/Compare tabs show the active `ticker_var` (e.g. `AMD`/`SPY`); `_refresh_tab_labels` is called after every successful load and after bad-ticker revert.
- **Companion-interval prefetch**: every successful `_load_data` fires background prefetches for 5m + 1d on both primary and compare tickers (the two most-used intervals). Switching between them is a cache hit, no provider round-trip. Backed by generic `_ensure_prefetched` + shared `_prefetch_inflight` (cap 4) + stale-overwrite guard + LRU promotion. `_full_cache` capacity 8 → 16.

## Smoke test coverage
~123 `check_*` functions in `tests/smoke/test_smoke_full.py` cover: import, init, render (plain/compare/log), fetch + disk fallback, cache staleness, executor lifecycle, streaming dispatch + token gating, hover/crosshair, click-to-type, watchlist tab, notebook, dialogs, xlim preservation across compare toggle, pan stability, disk-cache persistence, the indicator render pipeline, sandbox replay end-to-end, multi-interval / multi-TF context, and post-trade review enforcement. Per-check details now live under [Recent changes](#recent-changes-chronological) below.

## Recent changes (chronological)

Newest at the bottom. One bullet per `check_dN`. See the named spec for the full design rationale.

- **check_d7 — glued-edge slide on poll tick** — `_next_bar_fetch_tick` slides the viewport forward when the user was pinned to the right edge. See `app.spec.md`.
- **check_d8 — bar-close aligned scheduler** — `_compute_fetch_delay_ms` schedules the next fetch on `last_bar + interval + grace`, respecting session-aligned 1h/4h bars. See `app.spec.md`.
- **check_d9 — poll retry cadence** — up to 2 retries at 5 s spacing when the provider hasn't yet published the expected bar. See `app.spec.md`.
- **check_d10 — async poll offload** — `_next_bar_fetch_tick` dispatches the provider call to `_fetch_executor`; result returns to `_load_data` via the one-shot `_prefetched_raw` slot. See `app.spec.md`.
- **check_d11 — ticker-labeled notebook tabs** — primary / compare tabs reflect their `ticker_var` after every load. See `gui/watchlist_tab.spec.md`.
- **check_d12 — companion-interval prefetch** — every successful load prefetches 5m + 1d for both tickers. See `app.spec.md`.
- **check_d13 — pinned watchlist sub-tabs** — nested notebook with one sub-tab per pinned watchlist; cap 5. See `gui/watchlist_tab.spec.md`.
- **check_d14 — customizable theme overrides** — merge / allow-list filter; `set/clear/replace_theme_overrides` round-trip via `settings.json`; corrupt-reload normalization. See `settings.spec.md`.
- **check_d15 — pin-triggered preload** — pinning a fresh list submits `_preload_one_last` / `_preload_one_daily` and populates `_watchlist_snapshot` without `_load_data`. See `app.spec.md`.
- **check_d16 — startup defaults** — `BUILTIN_STARTUP_DEFAULTS`, `STARTUP_DEFAULT_KEYS`, `resolve_startup_defaults` per-key validation; sparse `settings.json` round-trip. See `defaults.spec.md`.
- **check_d17 — 1d → 5m drilldown** — double-click a 1d candle to switch to 5m for that day; gates exclude non-1d intervals, gap candles, and the compare panel. See `app.spec.md`.
- **check_d18 — display timezone** — `formatting.format_dt` converts intraday ET datetimes to a user-selected IANA zone; passes through on bad / empty zones. See `formatting.spec.md`.
- **check_d19 — Reset view → 1d** — Reset View clears `_preserve_xlim_on_render`, switches to 1d, snaps to right-edge bar window. See `app.spec.md`.
- **check_d20 — drilldown day persists across ticker change** — `_drilldown_day` + `_reload_preserving_drilldown` keep the calendar day in view across watchlist double-click; explicit interval/source/pre-post changes clear the lock. See `app.spec.md`.
- **check_d21 — Space cycles active watchlist** — Space advances the last-clicked panel's ticker through the active pinned watchlist (modulo wrap); slot routing mirrors watchlist double-click. See `app.spec.md`.
- **check_d24 — N7 async user-load** — `_load_data_async` offloads user-triggered reloads to `_fetch_executor`; cache-hit fast path remains synchronous. See `app.spec.md`.
- **check_d25 — mouse-wheel zoom** — TradingView-style cursor-anchored scroll-wheel zoom; floor at 3-bar width; clamped against high-DPI trackpads. See `gui/interaction.spec.md`.
- **check_d26 — scroll-zoom invert setting** — `settings.json["scroll_zoom_invert"]` flips the wheel direction (macOS natural-scroll). See `settings.spec.md`.
- **check_d27 — floating crosshair price label** — TradingView-style "current value" badge pinned to the left spine of every y-axis under blit. See `gui/interaction.spec.md`.
- **check_d28 — top-left OHLCV / %change readout** — always-on data strip via `AnchoredOffsetbox` + `HPacker`; bull/bear-coloured `+X.XX%` segment. See `gui/interaction.spec.md`.
- **check_d29 — price axes top headroom** — asymmetric padding (`BOT_PAD_FRAC=0.05`, `TOP_PAD_FRAC=0.12`) so the top-left readout never collides with the highest bar. See `core/viewport.spec.md`.
- **check_d30 — drilldown ylim race fix** — `_load_data` skips its `after_idle` deferred-render fast path when `_preserve_xlim_on_render` is armed. See `app.spec.md`.
- **check_d31 — post-pan blit-bg invalidation** — `_pan_end` invalidates `_blit_bg` to avoid the candle-less snapshot from the synchronous `canvas.draw()` during pan setup. See `gui/interaction.spec.md`.
- **check_d32 / check_d33 — regression-test infrastructure** — pixel-sanity helpers (`_count_candle_pixels`, `_assert_canvas_has_candles`) catch blank-screen regressions at the pixel level via `canvas.buffer_rgba()`. d32 runs an interaction-sequence matrix; d33 asserts `_blit_overlays()` never reduces candle pixel count. See `app.spec.md`.
- **check_d34 — compare-toggle drilldown ylim** — `_on_compare_toggle` calls `_autoscale_y_to_visible()` after `_render()` (cache-hit and cache-miss paths) so enabling compare during a drilldown doesn't leave compare misaligned. See `app.spec.md`.
- **check_d56 — EMA seeding alignment (TradingView/TA-Lib)** — `EMA.compute` and `ma_kernels.ema` both NaN-pad indices `0..length-2` and seed at `length-1` with the SMA of the first `length` closes (NOT pandas-style seed-at-index-0). `kind_version` bumped 1→2 to invalidate cached computes. Also fixes ATR(`ma_type=EMA`) and Bollinger(`ma_type=EMA`) which route through the shared kernel. See `indicators/moving_averages.spec.md` and `indicators/ma_kernels.spec.md`.
- **check_d57 — Performance View export bundle** — equity-curve chart with toggleable MTM + closed-trade-realized lines (`ax.step(where='post')`); `Export CSV…` writes a portable journal bundle (CSV + sibling `<stem>_screenshots/` mirror so pre/post PNGs travel with the CSV); `Copy to clipboard` exports TSV with header. Realized series anchored at `spec.starting_cash` (NOT `equity_curve[0]`). Screenshot-filename fallback `close-NNNN_post.png` for unattributed closes. See `backtest/performance.spec.md` and `gui/performance_view.spec.md`.
- **check_b60 — events fetcher protocol + registry** — `tradinglab.events.EVENT_SOURCES` mirrors `data.DATA_SOURCES`: yfinance + synthetic registered at import; `register_event_source` is idempotent on identical input; synthetic bundle is byte-deterministic per ticker. See `events/__init__.spec.md` + `events/base.spec.md`.
- **check_b61 — engine corporate-action phase** — engine tick phase 2.5 (between MAE/MFE roll and mark-to-market): cash dividend credits `Portfolio.cash`, stock split rescales `Position.quantity` AND inverse-rescales `Position.avg_cost` (cost basis preserved); idempotent `register_corporate_actions` returns 0 on identical re-register; `ENGINE_VERSION` stays `"sandbox-1d"`. See `backtest/engine.spec.md` + `backtest/actions.spec.md`.
- **check_b62 — events blind redaction** — `events_visible_for(blind=True)` returns past records fully, forward records empty, and only `ForwardEarningsBadge(trading_days_until, when)` — a dataclass-locked surface with no absolute-ts leak. Forward earnings actuals are NaN-masked defence-in-depth even in non-blind mode. See `events/gating.spec.md`.
- **check_b63 — master timeline frozen across events** — registering corporate actions whose ts is mid-bar OR out-of-timeline never extends or mutates `Clock.timeline`; arrays are byte-equal before/after both register and `run_to_completion`. See `backtest/engine.spec.md` Recent history.
- **check_b64 — save/load proximity + adjustments round-trip** — `SessionResult.cash_adjustments` / `quantity_adjustments` and `PreTradeEntry`'s 6 new proximity fields round-trip to/from JSON byte-identically; legacy saves (missing the additive fields) load cleanly with empty lists / zero defaults so `ENGINE_VERSION` stays `"sandbox-1d"`. See `backtest/session.spec.md`.
- **check_b65 — events disk cache + merge_bundle** — cache `save` / `load` is keyed by `(source, ticker)`, atomic-write via `os.replace`, honors `TRADINGLAB_CACHE_DIR`; `merge_bundle` is "new wins on overlapping ts" (lets providers revise forward estimates) and returns sorted output. See `events/cache.spec.md`.
- **check_b66 — auto-cycle clears in-flight events** — `SandboxController._events_fetch_token` bumps on every `prefetch_events_for` call so stale post-cycle bundles can't bleed into the new session. See `backtest/replay.spec.md`.
- **check_b67 — provider drift does not perturb SessionResult** — identical bars + identical scripted orders + identical registered `CorporateAction` list produces a byte-identical `SessionResult` JSON regardless of which event bundle the controller would have fetched. Events are ambient context; only the *applied* `CashAdjustment` / `QuantityAdjustment` records affect persistence. See `backtest/session.spec.md` + `events/__init__.spec.md`.
