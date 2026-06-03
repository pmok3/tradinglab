# TradingLab — Project Specification

A desktop candlestick charting application for equities, built in Python with
Tkinter and matplotlib. This document is the full specification of the
project as it currently stands and captures every non-obvious design decision
that shaped it. It is intended as a complete rebuild guide: someone reading
it top-to-bottom, with no access to the existing source, should be able to
recreate the app with the same behavior and the same internal structure.

## Table of contents

1. [Scope & goals](#1-scope--goals)
2. [Repository layout](#2-repository-layout)
3. [Data model](#3-data-model)
4. [Data sources](#4-data-sources)
5. [Live streaming](#5-live-streaming)
6. [Rendering pipeline](#6-rendering-pipeline)
7. [Compare mode](#7-compare-mode)
8. [Theming](#8-theming)
9. [Background fetching, caching, and polling](#9-background-fetching-caching-and-polling)
10. [Numpy cache: `SeriesArrays`](#10-numpy-cache-seriesarrays)
11. [Hover tooltip & blitting](#11-hover-tooltip--blitting)
12. [Click-to-type ticker entry](#12-click-to-type-ticker-entry)
13. [Status bar](#13-status-bar)
14. [Event flow (end-to-end)](#14-event-flow-end-to-end)
15. [Top-level design decisions & rationale](#15-top-level-design-decisions--rationale)
16. [Testing strategy](#16-testing-strategy)
17. [Dependencies](#17-dependencies)
18. [Extension points](#18-extension-points)
19. [Known gotchas / pitfalls](#19-known-gotchas--pitfalls)
20. [Glossary](#20-glossary)
21. [Phase roadmap](#21-phase-roadmap)
22. [Backtest kernel architecture](#22-backtest-kernel-architecture)
23. [Sandbox Phase-1 limitations](#23-sandbox-phase-1-limitations)
24. [Indicators — cross-cutting notes](#24-indicators--cross-cutting-notes)

---

---

## 1. Scope & goals

### 1.1 What the app is

A single-window desktop app that displays OHLCV candlestick charts for
individual tickers and supports side-by-side comparison of two tickers. Data
comes from pluggable sources (yfinance for live data, a deterministic
synthetic generator for offline testing, and a synthetic live-streaming
source). The app is intended to scale to multi-year 1-minute data (hundreds
of thousands of bars) and to support **simulated** real-time tick-level
updates on the rightmost bar via `SyntheticStreamSource` (deterministic,
offline). Real market-tick streaming is not implemented.

### 1.2 Feature set

- Candlestick + volume panels per ticker, green bull / red bear, with wicks.
- Single-chart mode and compare mode (two stacked panels, shared X axis).
- Data source selector (yfinance / synthetic / synthetic-stream).
- Interval selector: `1m 2m 5m 15m 30m 1h 1d 1wk 1mo`.
- Bar count selector ("Bars") — how many bars to display; does not trigger
  a re-fetch.
- Extended-hours toggle (pre/post market) with colored session-shading
  bands, intraday intervals only.
- Dark mode toggle.
- Live streaming in single *and* compare mode (synthetic stream
  implementation; architecture is ready for real providers).
- Hover tooltip with precise hit-testing on candle bodies/wicks and volume
  bars; dotted crosshair overlay at cursor for candle-by-candle inspection.
  OHLCV text in tooltip.
- Right-side data tables per ticker (newest-at-top, capped at 300 rows)
  **plus a third Watchlist tab** showing Ticker / Last / Change / Change %
  with bull/bear row coloring that tracks the active theme. Change columns
  are pinned to the 1d aggregation regardless of the chart interval.
- Custom watchlists with a modal manager (New / Rename / Delete / Set
  Active, per-ticker Add / Remove / reorder, JSON Import / Export). The
  active watchlist feeds the Ticker and Compare Comboboxes *and* the
  Watchlist tab. Persisted to `<cache_dir>/watchlists.json`.
- **Settings** dialog (toolbar → Settings…) exposing the fetch-worker
  thread count. Change is applied live (the executor is swapped without
  killing in-flight fetches) and persisted across runs.
- Click-to-type ticker entry — left-click a chart, type a symbol, Enter to
  load. Preview ticker shown as grey text in the center of the chart.
- Left-drag to pan (X only), right-drag to draw a zoom rectangle.
- Reset-view button.
- In-memory + disk caching so the app opens to the last-known data
  instantly, then refreshes in the background.
- Bad-ticker rejection: revert the StringVar if fetch fails, show an error
  in the status bar.

### 1.3 Explicit non-goals

- No broker integration, no orders.
- Technical indicators are now part of the shipped surface — see
  `src/tradinglab/indicators/`. (Previously listed here as a non-goal.)
- No multi-window / tabbed multi-chart UI — always one window, one or two
  panels.
- No trade history, no portfolio view.

---

## 2. Repository layout

```
tradinglab.py                   # one-line launcher shim
src/tradinglab/
    __init__.py                    # re-exports ChartApp, main
    __main__.py                    # `python -m tradinglab` entry
    app.py                         # ChartApp (the whole GUI)
    constants.py                   # colors, themes, interval maps, small helpers
    defaults.py                    # TUNABLES catalog + startup defaults registry
    settings.py                    # JSON-backed user settings store
    models.py                      # Candle dataclass
    formatting.py                  # fmt_volume, format_dt, fmt_price, ...
    rendering.py                   # matplotlib drawing primitives
    disk_cache.py                  # pickle-based on-disk durable log (no TTL)
    status.py                      # status-bar string composer (pure)
    core/                          # slice math + series containers (headless)
        __init__.py
        pairing.py                 # apply_pair_filter (compare-mode alignment)
        series.py                  # SeriesArrays (numpy view of candles)
        viewport.py                # y_limits_for_slice
    data/                          # historical data sources
        __init__.py
        base.py                    # DataFetcher, DATA_SOURCES, register_source
        normalize.py               # candles_from_dataframe + arrays side-channel
        parallel.py                # fetch_chunks_parallel
        yfinance_source.py
        synthetic_source.py
    streaming/                     # live streaming sources
        __init__.py
        base.py                    # StreamSource Protocol, STREAM_SOURCES
        synthetic.py               # SyntheticStreamSource (offline simulator)
    indicators/                    # technical indicators (compute + render)
        __init__.py
        base.py                    # Indicator protocol, registry
        config.py                  # IndicatorConfig + ParamDef schema
        loader.py                  # built-in registration on import
        cache.py                   # IndicatorCache (LRU-64, identity-keyed)
        render.py                  # render_for_slot, compute_layout, PanelIndicatorState
        ma_kernels.py              # SMA / EMA pure-numpy kernels
        wilder.py                  # Wilder's smoothing helper
        moving_averages.py         # SMA / EMA indicator (ma_type ParamDef)
        rsi.py                     # Wilder's RSI
        atr.py                     # Wilder's ATR
        adx.py                     # Wilder's ADX (+DI / -DI)
        bollinger.py               # Bollinger Bands (SMA/EMA basis)
        lrsi.py                    # Laguerre RSI
        smi.py                     # Stochastic Momentum Index
        vwap.py                    # Session-anchored VWAP
    watchlists/
        __init__.py
        storage.py                 # JSON load_all/save_all, normalize_tickers
        manager.py                 # WatchlistManager CRUD + pin APIs
    gui/                           # Tk-coupled mixins + dialogs (no main loop)
        __init__.py
        color_palette.py           # thin wrapper around native OS color chooser
        dialogs.py                 # _SettingsDialog + _WatchlistDialog
        interaction.py             # InteractionMixin (pan/zoom/hover/crosshair)
        watchlist_tab.py           # WatchlistTabMixin (pinned sub-tabs)
        workers.py                 # WorkerPoolMixin (executor lifecycle)
        indicator_dialog.py        # Manage Indicators… modeless Toplevel
        sandbox_dialog.py          # Sandbox start + pre-trade form
        sandbox_panel.py           # Sandbox sidebar
        sandbox_review_dialog.py   # Post-trade review + tags editor
        performance_view.py        # Performance Toplevel (sortable trade table)
    backtest/                      # Sandbox bar-replay engine (headless kernel)
        __init__.py                # public re-exports
        bars.py                    # BarSeries + adapter cache
        clock.py                   # Clock master-timeline iterator
        orders.py                  # Side / Order / Fill dataclasses
        fills.py                   # apply_fills (pure-function fill model)
        portfolio.py               # Position / Portfolio
        journal.py                 # PreTradeEntry / PostTradeReview
        tags.py                    # TagStore setup-tag taxonomy
        deck.py                    # DeckEntry + date-only deck APIs
        session.py                 # SessionSpec / SessionResult, ENGINE_VERSION
        engine.py                  # SandboxEngine (frozen master timeline)
        persistence.py             # save_session / load_session + screenshots
        performance.py             # build_trade_rows / build_setup_aggregates
        aggregation.py             # session-anchored higher-TF derivation
        replay.py                  # SandboxController (SOLE Tk-coupled module)
spec.md                            # this document
```

Python's package-over-module precedence makes both `python tradinglab.py`
and `python -m tradinglab` work. The shim is for users who don't know
about `-m`.

**Package boundaries (added during the modularization pass):**
Each of `data/`, `streaming/`, `indicators/`, `watchlists/` is a plug-in
extension point. Every package exposes a `base.py` with a Protocol + a
registry dict + a `register_*()` helper; concrete implementations live
in sibling modules and self-register at import time via the package
`__init__.py`. To add a new source/indicator/etc., drop a file into the
right package and either add an import line to `__init__.py` or call
the registry helper imperatively. No change to `app.py` is required —
the app consumes the registry dicts only.

**What's *not* broken up**: `app.py` remains monolithic (~3,400 lines).
`ChartApp` spans GUI construction, event handlers, streaming lifecycle,
render orchestration, and blit-overlay state; chopping those apart
would introduce cross-cutting state boundaries harder to reason about
than a single class. The `_SettingsDialog` and `_WatchlistDialog`
modal classes live at the bottom of the same file for the same reason —
they're tightly coupled to `ChartApp` internals. Indicators currently
have **no UI wiring** (they're a data/compute layer that future
iterations can surface through the toolbar). **Watchlists** *are*
fully wired (toolbar button, modal dialog, dedicated Notebook tab).

---

## 3. Data model

### 3.1 `Candle` (`models.py`)

```python
@dataclass
class Candle:
    date: datetime
    open: float
    high: float
    low:  float
    close: float
    volume: int
    session: str = "regular"   # "pre" | "regular" | "post" | "gap"
```

Properties:
- `is_bull` — `close >= self.open`.
- `is_extended` — `session in ("pre", "post")`. Must **not** include `"gap"`.
- `is_gap` — `session == "gap"`.

Factory:
- `Candle.gap(date)` — placeholder for compare-mode timestamp alignment.
  Prices are NaN, volume is zero.

`Candle` is a plain dataclass (mutable). The streaming tick path relies on
in-place mutation, so do not switch to `frozen=True`.

### 3.2 Session classification

`classify_session(hour, minute)` in `constants.py` tags a wall-clock time
(US Eastern) with a session label:

- `"pre"`     — before 09:30
- `"regular"` — 09:30 – 16:00 (inclusive-exclusive)
- `"post"`    — 16:00 – 20:00
- anything else (overnight) folds into `"pre"` (i.e., next-day pre-market)

Assumes timestamps arrive in exchange-local time (yfinance returns
tz-aware Eastern timestamps for US equities by default).

---

## 4. Data sources

### 4.1 Contract (`tradinglab/data/base.py`)

```python
DataFetcher = Callable[[ticker: str, interval: str], Optional[List[Candle]]]
DATA_SOURCES: Dict[str, DataFetcher]
register_source(name: str, fetcher: DataFetcher) -> None
```

The fetcher is synchronous. The app calls it from a background thread; see
section 9 ("Background fetching"). Each concrete provider lives in its
own module (`yfinance_source.py`, `synthetic_source.py`, ...) and is
registered at package-import time by `tradinglab/data/__init__.py`.

### 4.2 Built-ins

- **`yfinance`** (`fetch_live_data`) — uses yfinance's `Ticker().history()`.
  Intraday intervals request `prepost=True` so pre/post bars are
  included; each candle's `session` is set from `classify_session` on its
  timestamp. Import is inside the function (lazy: the app starts even if
  yfinance is missing). **Split / dividend caveat:** all OHLC are split-
  and dividend-adjusted (yfinance default `auto_adjust=True`). Backtests
  spanning a corporate action see continuous prices, NOT actual realised
  cash.
- **`synthetic`** (`fetch_synthetic_data`) — deterministic log-normal
  random walk seeded by `hash((ticker, interval))` so a given ticker
  always draws the same series. Intraday generates 04:00 → 20:00 ET bars
  on weekdays, with extended-hours volume ≈ 15% of RTH.
- **`synthetic-stream`** (`fetch_synthetic_stream_bootstrap`) — wraps
  `fetch_synthetic_data` and **truncates** any bar whose start is at or
  past the current interval boundary. That guarantees the live stream
  can open a fresh in-progress bar at the boundary without colliding
  with a pre-existing seeded bar at the same timestamp.

### 4.3 Intervals & yfinance period limits

`INTERVAL_PERIODS` maps interval → yfinance period string:

```
1m→7d   2m→60d   5m→60d   15m→60d   30m→60d
1h→730d   1d→2y   1wk→10y   1mo→max
```

These mirror yfinance's per-interval caps (1m is limited to 7 days, etc.).

---

## 5. Live streaming

### 5.1 `StreamSource` protocol (`tradinglab/streaming/base.py`)

```python
class StreamSource(Protocol):
    def subscribe(
        self, ticker: str, interval: str,
        on_event: Callable[[kind, Candle], None],
    ) -> Callable[[], None]: ...
```

Event kinds:

- `"tick"` — the in-progress bar's OHLC/volume changed. `bar.date` equals
  the current rightmost bar's timestamp. Consumer **replaces** its
  rightmost bar.
- `"rollover"` — a new bar has opened. `bar.date` is strictly greater
  than the previous bar's date. Consumer **appends**.

Callbacks run on the source's own thread. Consumers marshal to the UI
thread via a queue. The returned `unsubscribe` is idempotent and returns
without waiting for callbacks to drain, so consumers must be resilient to
one trailing event.

### 5.2 `SyntheticStreamSource`

> **`SyntheticStreamSource` is a deterministic offline simulator, not a real
> market feed.** See `src/tradinglab/streaming/synthetic.spec.md` for
> the warning banner and the in-progress-bar invariants.

One daemon thread per subscription. Wakes every `tick_period` (0.5s),
advances a log-normal walk on the in-progress bar's close (σ ≈ 0.15%),
updates high/low envelope, accumulates a small volume slug, emits a
`"tick"`. When wall-clock crosses a new interval boundary, emits a
`"rollover"` at the new boundary with open = previous close.

Seeded by `hash((ticker, interval, "stream"))`. Same ticker in multiple
subscriptions gets the same walk — convenient for reproducible compare
tests.

The source emits an **initial `"rollover"`** at subscribe time for the
current interval boundary. This is the handshake: the consumer now knows
the in-progress bar exists.

### 5.3 Registry

```python
STREAM_SOURCES: Dict[str, StreamSource] = {
    "synthetic-stream": SyntheticStreamSource(),
}
```

The app consults this on every `_start_stream_if_applicable` to decide
whether the currently-selected data source has a live streamer.

### 5.4 App-side lifecycle (per-slot)

State kept on `ChartApp`:

```python
self._stream_queue: queue.Queue        # maxsize=200
self._stream_token: int                # monotonically incremented
self._stream_subs: Dict[str, Dict[str, object]]
#   {slot_name -> {"unsub": Callable, "ctx": (src, ticker, interval)}}
self._stream_active: bool
self._stream_drain_job: Optional[str]  # Tk after-id
```

Slot names: `"primary"` and `"compare"`.

**Start preconditions** (computed by `_start_stream_if_applicable`):
- Source has an entry in `STREAM_SOURCES`.
- Interval is intraday.
- Raw history is already cached in `_full_cache` for the slot.
- In compare mode, **both** slots' raw must be cached
  (see "Both-live-or-neither" below).

**Subscribe is transactional.** Build the new `subs` dict into a local
`new_subs`; iterate slots and call `source.subscribe(...)`. On exception,
unwind every already-created entry by calling its `unsub()` and bump
`_stream_token` so any events already enqueued are staled-out. Only
commit `self._stream_subs = new_subs` on success.

**Stop**: iterate `_stream_subs`, call each `unsub()`, clear the dict,
bump `_stream_token`.

**Token as a generation id**: every reconfig (source/ticker/interval
change, compare toggle, prepost toggle) bumps the token. Workers stamp
events with the token at subscribe time; the drain loop discards
mismatched events. This is how late events from a just-cancelled
subscription are rejected.

### 5.5 Event tuple & the drain loop

Event tuple (enqueued by workers):
```
(token, slot, src, ticker, interval, kind, bar)
```

The slot is at index 1 and the kind at index 5 — deliberate, since the
drain loop needs both to coalesce and dispatch.

`_drain_stream_queue` runs on Tk main every 50ms (~20 Hz).

Coalescing: drain all pending events, collapse to at most one `"tick"`
per `(token, slot)`, **always keep every** `"rollover"` (ticks are
idempotent; losing a rollover would desync bar indexing).

Queue-full behavior: when the UI stalls and the queue fills, prefer to
drop the oldest tick. Only drop a rollover as a last resort if the
backlog is entirely rollovers.

### 5.6 Applying ticks vs rollovers

**Tick** (`_apply_stream_tick`):
- Look up raw via `_full_cache[(src, ticker, interval)]`.
- If `raw[-1].date == bar.date`: replace `raw[-1]` in place (mutate) so
  references held by derived lists (aligned compare view) see the
  update via object identity — `_align_pair` stores the SAME Candle
  objects, not copies.
- Otherwise: treat defensively as rollover.
- Call `_refresh_view_after_tick(slot)` — invalidates that slot's
  numpy `_SeriesArrays` cache and redraws only that slot.

**Rollover** (`_apply_stream_rollover`) — three-way comparison on the
bar timestamp vs the current last raw bar:

| Comparison                    | Action                         |
| ----------------------------- | ------------------------------ |
| `bar.date < last.date`        | drop (stale)                   |
| `bar.date == last.date`       | upsert — replace in place      |
| `bar.date > last.date`        | append                         |

The equal-timestamp case matters because the stream's subscribe emits an
immediate bootstrap rollover; if history already has a bar at that
boundary it would otherwise leave stale OHLCV.

On append, call `_refresh_view_after_append()` — re-derives
`_apply_pair_filter_and_align(primary_raw, compare_raw)`, rewires both
slots' candle list references, recomputes `_global_right_edge`, updates
per-slot offsets, pins xlim via shift (if the user was pinned to the
right edge), invalidates render range via `_ensure_rendered_for_view`,
and redraws.

### 5.7 Both-live-or-neither in compare mode

Rule: in compare mode, stream **both** slots or **neither**.

Rationale: `_next_bar_fetch_tick` (the event-driven refresh, §9.3) is
suppressed whenever `_stream_active` is True. If we streamed only one
side, the other side would freeze forever — the scheduler can't cover
it. Rather than introduce a second refresh class, the policy is to
gate streaming on mutual eligibility.

In practice: if compare mode is on and either raw is not cached (fetch
still in flight), we keep streaming **off** until the fetch resolves;
the next-bar scheduler covers the gap. Once both raws exist, we start
both subscriptions in one transaction.

### 5.8 View refresh on rollover without losing pan/zoom

Do **not** call full `_render()` on rollover — it calls
`_apply_view_window` which resets xlim to the last N bars, snapping the
user's zoom.

Instead:
1. Append to raw.
2. Re-run pair-filter + alignment.
3. Rewire `_panel_state[slot]["candles"]` and `_ax_candle_map[ax][0]`
   so downstream code sees the new list.
4. Invalidate `_series_cache.pop(id(old_list), None)`.
5. Recompute `_global_right_edge = max(n_primary, n_compare, 1)`.
6. For each slot, compute new offset = `right_edge - len(slot)`; update
   `_panel_state[slot]["offset"]` and `_ax_candle_map` entries.
7. If the user was pinned to the right edge (xlim_hi ≈ old_right_edge -
   0.5), shift xlim by +1 on all shared-X axes to track the new bar.
8. Call `_ensure_rendered_for_view(slot)` for each slot (important —
   offset shifts the viewport-to-local-index mapping, so the render
   window may need to move even if xlim didn't change).
9. `_redraw_streaming_slot(slot)` — force a slice rebuild and autoscale.

---

## 6. Rendering pipeline

### 6.1 Drawing primitives (`rendering.py`)

`draw_candlesticks(ax, candles, x_offset, start, end)` and
`draw_volume(ax, candles, x_offset, start, end)` return the artist
handles they created. Neither calls `ax.clear()`; neither sets xlim.
Both build a single `LineCollection` (wicks) and `PolyCollection`
(bodies / volume bars) per call — drawing 3 Collections is dramatically
cheaper than drawing 5000 individual patches.

Per-bar color handled via `to_rgba(base, alpha)`:
- Bulls get `BULL_COLOR` (#26a69a), bears get `BEAR_COLOR` (#ef5350).
- Extended-hours bars use alpha 0.45; regular bars use 1.0 (price) or
  0.7 (volume).
- Gap bars (`c.is_gap`) are skipped entirely — no wick, no body — so
  they appear as empty X slots.

`draw_session_shading(ax, candles, ..., intraday=False, pre_color, post_color)`
paints soft vertical bands (alpha 0.14) behind contiguous runs of same-
session bars. Uses `blended_transform_factory(ax.transData, ax.transAxes)`
so bands always span the full axes height regardless of Y autoscale.
Two colors — cool blue for pre, warm amber for post.

When `intraday=True`, **gap candles contribute to shading via wall-clock
`classify_session`** of their timestamp. This keeps pre/post bands
visually continuous on compare-mode charts where the partner ticker has
no pre/post bar at that slot. When `intraday=False`, gap timestamps at
midnight would falsely classify as "pre", so we skip them.

`setup_price_axes(ax)` / `setup_volume_axes(ax)` do one-time styling
(grid, margins, Y-tick locators with `prune="lower"` on price and
`prune="upper"` on volume so the bottom-most price tick doesn't collide
with the top-most volume tick at `hspace=0`).

`style_axes(ax, theme)` applies theme colors to axes backgrounds, ticks,
spines, and the grid — kept separate from draw functions so theme
changes don't require a re-render.

### 6.2 X coordinates — integer indices, not dates

Bars are drawn at integer positions, not matplotlib datetimes. This
eliminates visual gaps on weekends and holidays without any extra
work. Wick/body X is `i + x_offset` where `i` is the global candle
index inside the slice and `x_offset` right-aligns shorter series in
compare mode (see §7.2).

### 6.3 Virtualized rendering

Rendering 500k bars is too slow to do on every pan/zoom. The renderer
is **virtualized**: at any time only a window of bars around the visible
range is drawn.

Per-slot state lives in `self._panel_state[slot]`:
```python
{
    "candles":      ref to primary/compare candles list,
    "offset":       int,
    "price_ax":     Axes,
    "vol_ax":       Axes,
    "ind_axes":     List[Axes],         # lower-pane indicator axes
    "ind_scope":    str,                # "main" | "compare" | "drilldown"
    "ind_state":    PanelIndicatorState, # walked by blit / theme swap
    "render_start": int,   # local indices, [start, end) end-exclusive
    "render_end":   int,
    "price_wicks":  Artist | None,
    "price_bodies": Artist | None,
    "vol_bars":     Artist | None,
    "price_shades": List[Artist],
    "vol_shades":   List[Artist],
}
```

`_compute_render_range(visible_span, n)`:
- target = clamp(visible_span × 3, 500, 60000).
- If `target >= n` → `(0, n)` (whole series).
- Else center window around visible; clamp right edge; **no** "force
  cover visible" logic (an earlier such branch caused bugs at extreme
  zoom-out and was removed).

`_ensure_rendered_for_view(slot)`:
- Short-circuit when `render_start == 0 and render_end == n`.
- Safe-zone check: visible must sit inside the render window by at
  least half the buffer, or be flush against the data edge. Otherwise
  refill.

`_draw_slice(slot, new_start, new_end)`:
- Tear down old Collections + shade rectangles via `_safe_remove`.
- Call `draw_candlesticks`, `draw_volume`, `draw_session_shading` for
  the new slice.
- **Invalidate `_blit_bg = None`** — the cached background holds the
  previous Collections, restoring it after a slice refill would show
  stale bars.
- Store new handles back into `_panel_state[slot]`.

### 6.4 Autoscale + pan + zoom

Left-drag pans X; right-drag draws a zoom rectangle. We deliberately
**do not** use matplotlib's `Axes.start_pan`/`drag_pan`/`end_pan`
because `start_pan` snapshots press-time ylim and `drag_pan` restores
that ylim every frame — fighting our per-frame Y autoscale and
producing visible jitter on the active axis.

Custom pan:
- On button press: record `press_xlim` and axes pixel width.
- On drag: `dx_data = dx_px * xrange / ax_width_px`, then
  `ax.set_xlim(...)` directly on the relevant axes.
- Pan redraw is throttled to 60fps via `_schedule_pan_redraw` (16ms
  coalesce).

Y autoscale:
- `_y_limits_for_slice(series, kind, start, end)` uses
  `np.nanmin`/`np.nanmax` (gaps have NaN prices) and returns
  `(None, None)` on all-NaN slices; callers then skip `set_ylim`.
- On every `xlim_changed` and every pan frame, recompute Y to fit
  visible bars only.

Reset view: call `_apply_view_window` with default bar count.

---

## 7. Compare mode

### 7.1 Layout

`self.fig.subfigures(2, 1, hspace=0)` — top subfigure holds primary's
price+volume, bottom holds compare's. Each subfigure uses a 2-row
GridSpec with `height_ratios=[3, 1]` and `hspace=0`.

The second panel's `price_ax` is created with `sharex=self.ax`; the two
volume axes share X with their price axis → all four axes are
transitively X-linked, so pan/zoom on any one reflows all of them.

`subplots_adjust(top=0.98, bottom≈0.04, left=0.09, right=0.99)` —
`left=0.09` is required on Windows to keep volume tick labels like
`100.0M` from clipping.

### 7.2 Right-alignment (unequal lengths)

Given `n_primary = 200` and `n_compare = 350`, compare has more bars.
Both series should right-align at the same X coordinate (the "now"
edge).

```
right_edge      = max(n_primary, n_compare, 1)
primary_offset  = right_edge - n_primary
compare_offset  = right_edge - n_compare
```

Each slot passes its offset into `draw_*(..., x_offset=...)`. The
shared xlim is `(right_edge - bars - 0.5, right_edge - 0.5)`.

`_ax_candle_map[ax] = (candles_list, kind, x_offset)` — a 3-tuple. All
consumers (hover, autoscale, render-range) must unpack 3 values.

Candle index from X: `idx = round(xdata - offset)`.

### 7.3 Timestamp alignment with gaps

When both tickers exist but have different session coverage (e.g. one
has pre/post bars and the other doesn't), right-alignment alone will
not line up bars by time. Fix: **`_align_pair(primary, compare)`**
builds a shared timeline as the sorted union of both sides' timestamps
**clipped to the overlapping date range**, then emits for each
timestamp the matched bar or `Candle.gap(date)`. Output lists are
equal-length.

Key property: real bars are passed through by reference — the aligned
list contains the SAME `Candle` objects as the source raw. This is why
mutating a raw bar in place during streaming propagates to the aligned
view without any rebuild. Do not introduce copies here.

Pipeline order: `_apply_pair_filter(primary_raw, compare_raw, want_ext)`
runs first — coordinates the extended-hours toggle by dropping `pre/post`
bars on both sides if either lacks them. Only then does `_align_pair`
run. This keeps the filter's fast-path (pass-through when nothing is
tagged extended) working.

Single-chart mode skips alignment entirely — gaps would be meaningless
without a partner.

### 7.4 Zero-flicker layout on compare-ticker change

Naively, changing the compare ticker with no cache for the new symbol
goes: render (empty compare) → layout collapses to single → fetch
completes → layout flips back to compare. Visible flicker.

Fix: `_load_data` and `_on_fetch_done` both check a `pending_primary` /
`pending_compare` gate. While either is in flight, skip rendering
entirely and display "Loading XYZ..." in the status bar. Whichever
fetch arrives last triggers a single atomic render.

### 7.5 Default compare preloading

SPY is preloaded into `_full_cache` on startup via
`_preload_default_compare` (background thread). That way toggling
compare on for the first time is instant.

---

## 8. Theming

Two dicts in `constants.py`: `LIGHT_THEME` and `DARK_THEME`. Each holds
about 16 named colors: window/frame/axes backgrounds, text, grid, spine,
tree row backgrounds, tooltip, watermark, pre-shade, post-shade.

`ttk` on Windows needs the `"clam"` theme as a base for custom colors
to take effect. `_apply_theme` restyles ttk.Style for `TFrame`,
`TLabel`, `TCheckbutton`, `TButton`, `TNotebook`, `Treeview`, `TEntry`,
`TSpinbox`, `TCombobox`. The `Treeview.Heading` hover/pressed state
must be pinned explicitly via `style.map` — otherwise dark-mode headers
turn light-grey on hover.

Combobox popdown listboxes are a special case. `option_add` only affects
future widgets, so on re-theme we walk existing comboboxes via
`_iter_comboboxes` and reconfigure their popdown listbox directly:

```python
popdown = cb.tk.call("ttk::combobox::PopdownWindow", cb)
cb.tk.call(f"{popdown}.f.l", "configure",
           "-background", ..., "-foreground", ..., ...)
```

Combobox states that must be covered for all transitions: `readonly`,
`focus`, `active`, `pressed`, `disabled`, `!disabled`. Also set
`lightcolor` and `darkcolor` to kill 3-D bevels.

Theme toggle does **not** re-render:
- `_apply_theme` restyles axes in place by looping `_ax_candle_map`
  and calling `style_axes(ax, theme)`.
- Watermark text artists are tracked in `_watermark_artists` and
  recolored directly.
- Session-shading bands are torn down and redrawn only because their
  color lives in the Rectangle — cheap.

---

## 9. Background fetching, caching, and polling

### 9.1 Fetch executor

`ThreadPoolExecutor(max_workers=N, thread_name_prefix="fetch")` where
**N is user-configurable** (toolbar → Settings…). Default is
`os.cpu_count()` clamped into `[ChartApp._WORKER_COUNT_MIN=1,
_WORKER_COUNT_MAX=64]`; the persisted value in
`<cache_dir>/settings.json` (key `worker_count`) overrides it on every
subsequent launch.

The pool is shared across:

- primary + compare historical fetches (up to 2 in flight),
- `_disk_load_async` probes (§9.2),
- watchlist current-interval preload (one task per ticker),
- watchlist daily preload (one task per ticker, used by the Watchlist
  tab's Change/Change % columns).

At cold start with an N-ticker watchlist the fan-out is roughly
`2 + 2N` concurrent tasks. Four workers serialize that into several
waves; sizing to `os.cpu_count()` collapses it to ~2 on an 8-core box
and ~1 on modern 16+ core desktops. The workers spend ~95% of their
time in `socket.recv` (GIL released), so oversubscribing modestly past
the core count has no contention cost but diminishing returns above
~16–32 depending on provider rate limits — hence the 64 ceiling.

**Live resize.** `ChartApp._apply_worker_count(n)` swaps in a fresh
executor with the new `max_workers` and calls
`old.shutdown(wait=False, cancel_futures=False)` on the previous one.
In-flight tasks continue on the old pool; they still hold a reference
to `self` and marshal results back via `self.after(0, ...)`, so
dropping them mid-flight would lose the exact UI updates the user is
waiting on. New submissions go to the replacement pool. The change is
persisted via `tradinglab.settings.set("worker_count", n)` before
returning.

**Generation token.** `_fetch_token` (incremented on every
`_load_data`) stales late results. Workers call the fetcher, then
`self.after(0, self._on_fetch_done, future, token, ...)`. `self.after`
is thread-safe in Tk. `_on_fetch_done` drops the result if
`token != self._fetch_token`.

**Worker-side series prebuild.** Both `_fetch_async` and
`_disk_load_async` build the candles' `_SeriesArrays` (see §10) on the
worker thread **before** resolving their Future. The main-thread
completion handler seeds `_series_cache[id(data)] = series` so the
upcoming `_render()` avoids a synchronous `np.fromiter` pass. For a
6-month intraday history (~8,000 bars) this saves ~20–80 ms of Python-
level iteration on the main thread at each data arrival. `_SeriesArrays`
only populates numpy arrays + stashes the `format_date` callable in its
constructor — the callable is Tk-read-only and is invoked later from
`tooltip_text` on the main thread, so constructing the object off-thread
is safe.

**Initial-load race.** The first `_load_data` and
`_preload_default_compare` calls are scheduled via `self.after(0, ...)`
rather than called directly at the end of `__init__`. Their async-disk
workers post results back with `self.after(0, ...)`; if mainloop has
not yet started when the worker fires (~20ms), Tk raises `RuntimeError:
main thread is not in main loop`. Deferring the kickoff by one tick
guarantees mainloop is running before any worker callback fires.

Shutdown: `WM_DELETE_WINDOW` → `_on_close` → `_stop_stream` +
`after_cancel` on `_stream_drain_job`, `_poll_job` (event-driven
next-bar fetch, see §9.3), and `_watchlist_tab_refresh_job` (debounced
tab repaint, §9.2) + `_fetch_executor.shutdown(wait=False)`. In
headless smoke tests where `update()` is called instead of
`mainloop()`, late Future callbacks can raise `RuntimeError: main
thread is not in main loop`; this is harmless teardown noise.

### 9.2 Cache layers

- **In-memory `_full_cache`**:
  `OrderedDict[(src, ticker, interval), List[Candle]]`. Bounded via
  `_cache_full(key, candles)` — LRU eviction at the cap. `move_to_end`
  on every read to preserve LRU order. Used for primary/compare chart
  data.
- **Disk**: pickle files under `%LOCALAPPDATA%\tradinglab\` (Windows) or
  `~/.cache/tradinglab/`. **No TTL is enforced by the disk cache itself**
  — `disk_cache.py` is a durable log of every `(source, ticker, interval)`
  fetch result, since sealed OHLCV bars are immutable facts. Freshness is
  caller-enforced via `ChartApp._cache_is_stale` (session-aware: respects
  whether the market has opened since the last bar). `load` returns
  `(candles, is_fresh)`; stale results are still returned for instant-on
  startup, the caller just kicks off a background refresh.
- **`IndicatorCache`** (`indicators/cache.py`): identity-keyed LRU, capacity
  64. Key is `(id(candles), config_hash)` where `config_hash` is a stable
  SHA-1 of the indicator's `kind_id` + sorted compute-affecting params.
  Indicator results are bound to candle list identity, so any in-place
  mutation (streaming tick / sandbox advance) requires explicit
  invalidation by the caller — the cache cannot detect mutations.
- **`_watchlist_snapshot`**: `Dict[str, {"last", "chg", "pct"}]`. A
  derived, display-ready cache keyed by ticker. Populated **by the
  preload workers** the instant their fetch (or their fresh disk-cache
  read) lands — no duplicate disk I/O, the worker already has the data
  in hand. The main-thread `_populate_watchlist_tab` reads only from
  this dict (+ falls through to `_full_cache` for tickers that happen
  to be loaded as primary/compare). Consequence: every Watchlist-tab
  repaint is ~78 µs of pure dict lookups with zero disk I/O, even when
  the tab is being driven at N Hz by the debounced refresh scheduler
  while streaming. Plain dict, not `OrderedDict` — concurrent writes
  from different worker threads and reads from the main thread are
  atomic in CPython.
- **`_series_cache`**: `Dict[int, _SeriesArrays]` keyed by
  `id(candles)`. See §10 for the full story. Invalidation
  (`_invalidate_series_cache`) short-circuits when the live-id set
  hasn't changed since the previous call — important because `_render`
  (and therefore the invalidation hook) fires at up to 20 Hz during
  streaming.
- **`_PREBUILT_ARRAYS`** stash (in `data/normalize.py`): keyed by
  `id(candles)`, populated by `candles_from_dataframe`, drained by
  `_build_series_safe`. Capped at `_PREBUILT_ARRAYS_MAX = 32` with
  FIFO eviction as defense-in-depth — the normal pop-and-consume
  protocol runs in milliseconds per fetch, but the cap prevents
  unbounded growth if the consumer pathway ever regresses.

**Two-phase load (`_load_data`).**
`pickle.load` for a fat intraday history is 10–50 ms — enough to
stutter the UI when called on the Tk main thread. The load pipeline is
therefore phase-split:

1. **Phase 1 (main thread, synchronous).** `_load_one` does an
   in-memory LRU hit test only. If both primary and compare (when
   active) are memory-resident, jump straight to Phase 2.
2. **Phase 2 (fan-in).** Apply the pair filter + alignment, render, and
   spawn a network refresh if the data is stale.

When Phase 1 misses on either side, an async disk read is submitted to
`_fetch_executor` via `_disk_load_async`. The worker calls
`disk_cache.load` + `_build_series_safe` and resolves with
`(candles, is_fresh, series)`. A main-thread callback caches the data,
seeds `_series_cache`, and — once both sides' disk probes have
completed — runs Phase 2. The network fetch is then spawned from
Phase 2 exactly as before.

**Debounced Watchlist-tab repaint.**
`_schedule_watchlist_tab_refresh` coalesces the N-worker completion
storm at cold start. Without it, N preload workers each marshal an
`after(0, self._populate_watchlist_tab)` callback; each repaint used
to fan out N pairs of disk reads → O(N²) pickle.loads on the main
thread at startup. The debouncer uses a pending `after(60, …)` handle;
repeated schedule calls within the window no-op. The 60 ms budget is
imperceptible and gives several workers time to publish their
snapshot rows before the first paint.

**Cache pollution risk**: smoke tests that stub `DATA_SOURCES["yfinance"]`
write fake data to the disk cache. Future cold starts then load stale
fake data for 5 min. Manual remediation: delete the file. A cleaner
solution (`TRADINGLAB_NO_CACHE=1` env guard) is possible but not
currently implemented.

### 9.3 Freshness scheduling (event-driven, not periodic)

Historical data is kept fresh by **computing when the next bar is
expected and scheduling a single `after()` for exactly that moment**,
rather than polling on a fixed clock. This cut request rate against
yfinance by roughly 10–1000× depending on interval (e.g., 5m chart:
~1,440 req/day → ~78 req/day per ticker; 1d chart: ~1,440 → ~1) while
removing an entire class of "why didn't my chart update?" confusion.

`_schedule_next_bar_fetch()` computes the scheduling target as:

```
next_bar_time = last_bar.date + interval + grace
```

where `grace` absorbs publication latency — 30 s for intraday, 5 min
for daily/weekly, 10 min for monthly. The resulting delay is clamped
to `_MIN_POLL_BACKOFF_MS = 30_000` so a stale last-bar timestamp can't
produce a runaway wake-up loop. `datetime.timestamp()` handles both
naive (synthetic) and tz-aware (yfinance US/Eastern) timestamps
correctly, and `after()` handles multi-day delays fine (weekend /
holiday waits are just very large `ms` arguments).

`_next_bar_fetch_tick()` clears **only** the active primary/compare
keys from `_full_cache` (preserving watchlist preload entries) and
calls `_load_data`. The next successful render re-arms the scheduler
via `_load_data_phase2` → `_schedule_next_bar_fetch`, and
`_on_fetch_done` does the same once a network fetch lands. Any active
stream suppresses scheduling — streaming already delivers bars in
real time and the scheduler would just duplicate work.

**Preserve pan across refresh.** When a next-bar tick fires,
`_user_has_panned_x` (`xlim_hi < right_edge - 0.5 - 1`) decides
whether `_render` should restore the panned viewport rather than
snap to the latest N bars. The flag (`_preserve_xlim_on_render`) is
set in `_next_bar_fetch_tick` and read at the top of `_render`,
which captures the current xlim BEFORE any `fig.clear()` wipes it.
After the normal render completes (including `_apply_view_window`'s
default snap), it overrides xlim with the captured value **shifted
by the change in global right edge** (`delta = new_right_edge -
old_right_edge`). Because X is index-based and right-aligned, every
bar's X coordinate shifts by exactly `delta` when new bars append —
adding `delta` to both xlim endpoints keeps the same historical
bars under the user's cursor. Then it re-runs
`_ensure_rendered_for_view` per slot (the viewport-to-local-index
mapping has changed) and Y-autoscales to the restored slice.

The flag is **not** cleared at the end of `_render`. A single
refresh cycle can trigger two renders: first synchronously from
`_load_data` (disk cache hit with stale data) and again from
`_on_fetch_done` when the background fetch lands. If the flag were
cleared on the first render the second would snap back to the right
edge — the exact bug this mechanism exists to prevent. Instead, the
flag persists and is re-evaluated at each tick via
`_user_has_panned_x`, so it self-clears naturally as soon as the
user pans back to the right. Explicit clears live in
`_do_scheduled_reload` (any user-initiated reload supersedes preserve
state) and `_reset_view` (the Reset View button is an explicit "take
me back to the latest bars" action).

The raw cache still refreshes normally; only the visual snap is
suppressed. So "Reset View" and subsequent pans see the new bars.

---

## 10. Numpy cache: `SeriesArrays`

Hover hit-tests, autoscale, and view-window queries all need fast
random access to OHLCV. We derive a numpy-backed view of each candle
list. The class lives at `core.series.SeriesArrays`; `app.py` keeps
the old `_SeriesArrays` name as a module-level alias for back-compat
with smoke tests and existing call sites.

```python
class SeriesArrays:  # aliased as _SeriesArrays in app.py
    opens, highs, lows, closes, volumes: np.ndarray  # 1-D
    _candles: List[Candle]
    def tooltip_text(self, i: int) -> str: ...   # lazy, dict-cached
```

Built on first access per candle list, keyed by `id(candles)` in
`self._series_cache`. Tooltip strings are **lazy** (`get_tooltip(i)` with
a dict cache) because eagerly building f-strings for 500k candles adds
~1s to startup with no tooltip to show.

`_invalidate_series_cache` prunes entries whose id is no longer held
by `self.candles`, `self.compare_candles`, or any `_full_cache` value.

Invariant: when a candle list reference is replaced (e.g. after
re-aligning in streaming), the caller **must** `pop(id(old_list))`
before swapping in the new list. Otherwise a stale numpy view leaks
until the next invalidation.

---

## 11. Hover tooltip & blitting

### 11.1 Hit-testing

On `motion_notify_event`:
1. `ax = event.inaxes`; look up `(candles, kind, offset)` from
   `_ax_candle_map`.
2. `idx = int(round(event.xdata - offset))`. Clamp to valid range.
3. Gate on `render_start <= idx < render_end` — during pan lag, if the
   cursor enters a not-yet-drawn region, hide the tooltip instead of
   showing stale data.
4. Gate on `candles[idx].is_gap` — no tooltip for placeholder bars.
5. Body hit: `abs(event.xdata - (idx + offset)) <= 0.3` AND either
   price: `c.low <= ydata <= c.high`, or volume: `0 <= ydata <= c.volume`.

### 11.2 Blitting

On every `draw_event`, capture `self._blit_bg = canvas.copy_from_bbox(fig.bbox)`.
Hover annotation is `set_animated(True)` so it's excluded from the
snapshot. `_show_hover` does `restore_region → draw_artist → blit`
instead of a full `draw_idle()`. Annotation is reused — only `xy`/`text`/
`position` updated on move; recreated only when the `ax` changes.

`_blit_bg` is invalidated to None after any slice refill (§6.3).

### 11.3 Direction flipping

Using figure-pixel coords: `rx = event.x / fig_w`, `ry = event.y / fig_h`.
Flip left (tooltip on left of cursor) when `rx >= 0.8`, flip down (below
cursor) when `ry >= 0.6`. This keeps the tooltip on-screen.

### 11.4 Crosshair overlay

Visual aid for candle-by-candle inspection. Dotted `axvline` + `axhline`
on every chart axes, all animated so they're excluded from the blit
background snapshot. Shown whenever the cursor is inside any chart
axes — even over gap placeholders, between candles, or outside the
rendered slice — because the user's question is "where is my cursor?",
not "am I on a valid bar?". Hidden immediately on `axes_leave_event`,
`figure_leave_event`, and while panning/zoom-dragging.

- **Vertical line**: shown on every chart axes (in compare mode: all
  four — primary price, primary volume, compare price, compare volume).
  Because axvline is clipped per-axes, the horizontal gap between the
  two subfigures naturally has no line — the crosshair visually spans
  both charts but not the section separating them.
- **Horizontal line**: shown only on the axes the cursor is currently
  in. Y scales differ between price and volume subplots, and between
  the primary/compare panels, so a horizontal line elsewhere would be
  at a meaningless Y value.

Rendering is centralized in `_blit_overlays`: restore the blit
background, `draw_artist` each visible vline + the one visible hline
+ the hover annotation (if visible), single `blit(fig.bbox)`. Both
`_show_hover` and `_hide_hover` route through it so the crosshair and
tooltip composite in a single frame rather than fighting each other.

Artists are rebuilt at the end of every `_render` call because
`fig.clear()` on topology change destroys the axes (and thus their
children). On same-topology re-renders we explicitly `remove()` the
previous generation first to keep the tracking dict clean.

**Cursor-cache revival across re-renders.** A rebuild ends with the
new artists `visible=False` and `_crosshair_current_ax = None` — so
without a subsequent `motion_notify_event` they stay hidden. If the
cursor hasn't moved (user reading data while a next-bar fetch tick
fires and re-renders) that produces the visible symptom "crosshairdisappears after a bit
of inactivity". Fix: `_on_mouse_move` caches `(event.x, event.y)` in
`self._last_cursor_px` whenever the cursor is inside a chart; the
cache is cleared by `_hide_overlays`. At the end of `_render`, after
rebuilding the crosshair artists, if the cache is populated and the
cursor's pixel position still lands inside one of the new axes
(`ax.bbox.contains(px, py)`), `_update_crosshair_pixels` is called
to re-position and re-show the crosshair. `transData.inverted()`
handles the (often changed) data-coord mapping correctly.

---

## 12. Click-to-type ticker entry

Left-click a chart: begin typing mode for that slot. The slot is stored
as `_typing_target` ∈ `{"primary", "compare"}`. Keystrokes accumulate in
`_typing_buffer` (alnum + `._-`). A large grey `ax.text(0.5, 0.5, buffer, ...)`
is rendered in the center of the chart as the preview. Enter commits
(calls `_schedule_reload(delay_ms=0)`), Esc cancels.

If the user starts typing with no chart clicked, we default to
`"primary"` and set `_last_clicked_slot = "primary"` for continuity.

Distinguishing click-to-type from pan-start: compare `event.x`/`event.y`
at release to press — a move under ~3px is a click; greater is a drag.

### Bad-ticker rejection

Failed fetches revert the StringVar to the last **confirmed** value
(`_confirmed_primary_ticker` / `_confirmed_compare_ticker`). Status bar
shows e.g. `'XYZW' not found (yfinance).` A ticker is "confirmed" only
after a successful fetch lands.

---

## 13. Status bar

One-line `tk.StringVar` at the bottom of the window.

- Normal state: bar count, last bar timestamp, last close.
- Compare mode: "N slots / M real bars" (slot count includes gap
  placeholders; real count excludes them).
- Loading: `"Loading XYZ..."` — suppresses render until fetches
  resolve.
- Streaming: prefix `"● LIVE  "` when `self._stream_active` is True.
  Terminal must allow UTF-8; tests set `PYTHONIOENCODING=utf-8`.

---

## 14. Event flow (end-to-end)

A typical "change compare ticker" flow, illustrating all the subsystems:

```
User types new ticker in compare slot, presses Enter
  → _schedule_reload(delay_ms=0) — bypasses the 700ms debounce
  → _load_data()
      _stream_token += 1           # stale any in-flight stream events
      _stop_stream()               # unwind all subs, bump token again
      look up primary & compare in _full_cache
        primary present, compare absent
      pending_compare = True; status = "Loading COMPARE..."
      _spawn_fetch("compare", source, new_ticker, interval)
        (background thread)
      RETURN WITHOUT RENDERING     # zero-flicker gate
  (worker finishes)
  → self.after(0, _on_fetch_done, future, token, slot, ...)
      token check: OK
      store in _full_cache (with LRU eviction if needed)
      disk_cache.save(...)
      pending_compare = False
      _confirmed_compare_ticker = new_ticker
      _apply_pair_filter_and_align(primary_raw, compare_raw)
      _render()                     # single atomic render
      _update_status()
      _start_stream_if_applicable()
        both slots cached, intraday, source has streamer → subscribe both
        commits new _stream_subs = {"primary": ..., "compare": ...}
        _stream_active = True
  (stream worker thread emits ticks at 2Hz)
  → _enqueue_stream_event(token, slot, src, ticker, interval, kind, bar)
  → _drain_stream_queue (20Hz)
      coalesce ticks per (token, slot), dispatch rollovers eagerly
      _apply_stream_tick / _apply_stream_rollover
      _refresh_view_after_{tick|append} → _redraw_streaming_slot
```

---

## 15. Top-level design decisions & rationale

This section exists because most of the interesting choices came from
hard-won lessons during development, not from first-principles design.

### 15.1 `@dataclass` (mutable) Candle, not `frozen`

The streaming tick path mutates `raw[-1]` in place so that the aligned
compare view (which holds the same object by reference) sees the update
without any rebuild. Freezing the dataclass would break this.

### 15.2 Integer X axis, not datetimes

Weekends/holidays produce visible gaps if we use matplotlib dates.
Integer indices eliminate those with no extra code. The cost is that
human-meaningful X tick labels are non-trivial; we sidestep the problem
entirely by showing dates in the status bar, hover tooltip, and the
right-side tables instead.

### 15.3 Virtualized rendering over downsampling

Earlier the app downsampled when the bar count exceeded a threshold.
Downsampling corrupts hover (tooltip shows an aggregated bar the user
didn't click on) and makes OHLC visually inaccurate at common zoom
levels. Virtualization keeps the full bar dataset visible and scales
to 500k+ bars.

### 15.4 3-tuple in `_ax_candle_map`

`(candles, kind, x_offset)`. Consumers (hover, autoscale, render range)
all need the offset to convert between X data coords and global candle
indices. Storing it once per axes avoids threading it through every
call.

### 15.5 Custom pan, not matplotlib's

`Axes.start_pan` captures press-time ylim and `drag_pan` restores it
every frame, fighting our per-frame Y autoscale and producing visible
jitter. Rolling our own X-only pan is cleaner than trying to subclass
and work around matplotlib's behavior.

### 15.6 Both-live-or-neither in compare mode

Streaming suppresses the event-driven next-bar fetch. Partial streaming
would leave the non-streaming side frozen. Simplest correct policy is
to stream both or neither; the next-bar scheduler covers any brief
"neither" gap while waiting for the second side's history to load.

### 15.7 Generation-token pattern everywhere

`_fetch_token` for background fetches, `_stream_token` for streaming.
Any reconfig bumps the token; stale events are discarded on the main
thread in a single check. Removes the need for per-subscription cancel
handshakes.

### 15.8 Coalesce ticks, keep rollovers

Ticks are idempotent — losing a tick just means a slightly-delayed
visual update. Rollovers are not — losing a rollover leaves a bar
undersized and desyncs indexing. The drain loop's coalesce logic
encodes this asymmetry.

### 15.9 Equal-timestamp rollover = upsert

The stream's subscribe emits an immediate bootstrap rollover at the
current interval boundary. If history already has a bar there, the
naive "drop older-or-equal" rule would leave stale OHLCV. Upserting
(in-place replace) handles the boundary cleanly.

### 15.10 Transactional stream subscribe

If the second of N `source.subscribe(...)` calls raises, we'd otherwise
leak the first subscription and have half-committed state. Build a
local dict, on exception unwind every already-created entry and bump
the token to invalidate any events that slipped through. Only commit
the new dict on full success.

### 15.11 Render-range recompute on offset shift

When one side appends a new bar and the other doesn't, `_global_right_edge`
stays at the max, so that one slot's offset changes. The viewport-to-
local-index mapping shifts. `_redraw_streaming_slot` alone doesn't catch
this — it only forces a rebuild at the *current* render range.
`_ensure_rendered_for_view` has to re-evaluate the window.

### 15.12 Session shading uses blended transform + wall-clock fallback

Using `blended_transform_factory(ax.transData, ax.transAxes)` means
bands span full axes height without needing to know the Y range.
Gap candles on intraday charts falling back to wall-clock session
keeps the band continuous when the partner ticker has no bar at that
slot — otherwise pre/post shading would have visible white holes on
sparsely-covered series.

### 15.13 `is_extended` excludes gaps

This is a bug fix, not an aesthetic choice. When gaps were first added,
`is_extended` was `session != "regular"` which included gaps and
corrupted `_apply_pair_filter`'s "does either side have extended bars?"
check. Now `is_extended` is strict: `session in ("pre", "post")`.

### 15.14 Session-shading bands span across gap slots via wall-clock

Same motivation as 15.12 — pre/post visual continuity matters more than
strict correctness for placeholder bars.

### 15.15 Watermark stacking bug required explicit artist cleanup

`self._watermark_artists = []` drops references but doesn't remove
Artists from their axes. Each re-render was adding another alpha-0.6
watermark on top of the old ones, visibly darkening the color. Must
`_safe_remove(wm)` before clearing the list.

### 15.16 Why in-memory LRU cache over `functools.lru_cache`

`lru_cache` can't be sized dynamically, can't be LRU-walked (we want
`move_to_end` on read), and holds strong references to self. An
`OrderedDict` is the right primitive here.

### 15.17 Preserve pan across periodic refresh

A 60s background poll clearing the cache and triggering a re-render
was snapping the user's xlim back to the last N bars mid-analysis.
Rather than suppress the poll entirely when panned (which would leave
the raw cache stale so that "Reset View" showed old data), a one-shot
`_preserve_xlim_on_render` flag set by `_poll_tick` lets `_render`
capture and restore the viewport, shifted by the right-edge delta so
the same historical bars stay under the cursor. User-initiated reloads
clear the flag explicitly — "load fresh ticker" always snaps.

### 15.18 Raising `OrderedDict[...]` cache cap would trade memory for
multi-ticker responsiveness

Currently capped at 8. Most users look at 2-3 tickers; 8 gives room
for exploration. If memory becomes a problem (multi-year 1m data is
large), reducing to 4 is safe.

### 15.19 What is (and isn't) parallelizable

Matplotlib artist construction, `canvas.draw()`, `copy_from_bbox`, and
`canvas.blit()` are **not** thread-safe, so the render itself is fixed
to the Tk main thread. Tkinter is also single-threaded. Parallelism
wins are therefore limited to IO and numpy prep that can be hoisted
off the main thread:

- **`_fetch_async` and `_disk_load_async`** run on the
  user-configurable `_fetch_executor` (see §9.1). Default sizing is
  `os.cpu_count()` clamped to `[1, 64]`; persisted via
  `settings.json` and changeable live through the Settings dialog.
  Both paths build `_SeriesArrays` before resolving, so the main-thread
  `_series()` lookup in `_render` is a cache hit.
- **Vectorized normalization** (`data/normalize.py:candles_from_dataframe`).
  Provider fetchers (yfinance, future Polygon) extract all OHLCV columns
  via a single `.to_numpy()` each and construct candles in one tight
  Python loop. ~14× faster than the previous `df.iterrows()` path on
  5k-bar intraday fetches (empirically 193ms → 13.6ms on ARM64). Runs on
  the fetch worker, so the speedup lowers worker latency and doesn't
  block the main thread either way.
- **Arrays side-channel** (lazy-candle optimization).
  `candles_from_dataframe` stashes its extracted numpy arrays in a
  module-level dict keyed by `id(candles)`. The worker-side
  `_build_series_safe` immediately pops the entry and hands the arrays
  to `_SeriesArrays.from_arrays`, skipping five redundant `np.fromiter`
  passes over the candle list. Stash lifetime is milliseconds — stash
  on fetch worker, pop on same fetch worker before the list escapes
  into long-term caches — so no memory-leak risk.
- **Watchlist preload + snapshot publish** (`ChartApp._preload_watchlist`
  and `_preload_watchlist_daily`). Fires one task per watchlist ticker
  to `_fetch_executor` at startup. Workers write through to
  `disk_cache`, then compute `last` / `chg` / `pct` **on the worker
  thread** and publish them into `_watchlist_snapshot` (plain dict,
  atomic CPython writes). The main-thread `_populate_watchlist_tab`
  then renders a debounced repaint with zero disk I/O — without this
  offload, N workers would each trigger a paint that did N pickle.load
  calls on the Tk thread (O(N²)). Best-effort: network or import
  failures are swallowed.
- **`fetch_chunks_parallel`** (`data/parallel.py`). Scaffolding helper
  that splits a single logical fetch into N independent sub-requests
  (e.g., monthly chunks of 1-minute bars for a Polygon-style provider)
  and concatenates the results. Not currently used by yfinance (whose
  `period` parameter already batches the whole range server-side), but
  documented as the canonical pattern for a new chunked-I/O source.
- **Disk cache saves** (`disk_cache.save` inside the fetch worker)
  have always been off-thread.
- **Live streaming** runs one daemon thread per subscription.

Deliberately left serial (cost already sub-millisecond, thread-submit
overhead dominates, or GIL-bound CPU work with no numpy path):

- Pair filter + alignment (`_apply_pair_filter_and_align`).
- Per-axes Y-autoscale (`_autoscale_y_to_view`).
- Tooltip text formatting (lazy on hover).
- Splitting *one* normalize call across threads: the `Candle(...)`
  constructor loop is pure Python → GIL-bound → no speedup from threads,
  and a process pool's pickle overhead dominates for fetch sizes
  <100k bars. Vectorization + the arrays side-channel already remove
  the redundant work; further splitting would regress.

The one remaining low-hanging win would be **speculative buffer
pre-slicing during pan-idle**: run the virtualization slicer on the
adjacent buffer so crossing the buffer edge has zero main-thread
filter/gap work. Matplotlib Collection construction still has to
happen on the main thread, but the numpy slicing and gap classification
could be pre-done. Not implemented — the current buffer already covers
~3× viewport so edge-crossings are rare during typical use.

---

## 16. Testing strategy

All tests are **smoke tests** in a `_smoke_*.py` file at the repo root
(created on demand, deleted after). Key patterns:

- `MPLBACKEND=Agg` so no window opens.
- `PYTHONIOENCODING=utf-8` so the LIVE `●` char doesn't break Windows
  stdout.
- Stub `DATA_SOURCES["yfinance"]` with a deterministic fake — but
  remember it writes to disk cache! Clean up the pickle after or use
  a data source that doesn't hit the disk cache.
- `app = ChartApp(); app.update()` loops in place of `app.mainloop()`.
- Preseed `_full_cache[(src, ticker, interval)] = list(...)` to avoid
  real fetches.
- For streaming: loop `app.update(); time.sleep(0.05)` for 2–3 seconds
  and watch `raw[-1].close` mutate.
- Clean shutdown: `app._on_close()`. Ignore any `RuntimeError: main
  thread is not in main loop` from late Future callbacks — harmless
  teardown noise in headless harnesses.

No pytest, no unittest — smoke tests are run directly as scripts. The
test harness stays intentionally simple because the interesting
failures are all integration-level.

---

## 17. Dependencies

- Python 3.12+ (tested on 3.12 ARM64 Windows).
- `matplotlib` (3.10+) — pulls in numpy.
- `yfinance` (1.3+) — only needed for real data; import is lazy.
- `tkinter` — stdlib.

No other runtime deps. Install: `pip install matplotlib yfinance`.

---

## 18. Extension points

Each extension point lives in its own package — see §2 for the layout.
The pattern is identical across all four: `base.py` defines the
protocol + a module-level registry dict + a `register_*()` helper;
the package `__init__.py` imports the built-in implementations so
they self-register at import time. Adding a new plug-in is a single
file + one import line.

### 18.1 Adding a data source

1. Create `tradinglab/data/foo_source.py` exporting a function
   ``fetch_foo(ticker: str, interval: str) -> Optional[List[Candle]]``.
   For intraday, request pre/post from the provider if available and
   tag `session` via `classify_session(dt.hour, dt.minute)`.
2. In `tradinglab/data/__init__.py`, add::

       from .foo_source import fetch_foo
       register_source("foo", fetch_foo)

3. The source-selector combobox picks it up automatically on next launch.

### 18.2 Adding a streaming source

1. Create `tradinglab/streaming/foo.py` with a class implementing
   the `StreamSource` protocol. Spawn your own thread; emit
   `("rollover", initial_bar)` on subscribe, then alternating
   `("tick", bar)` and `("rollover", new_bar)` at interval boundaries.
   Return an idempotent `unsubscribe`.
2. In `tradinglab/streaming/__init__.py`::

       from .foo import FooStreamSource
       register_stream("foo-stream", FooStreamSource())

3. Add a matching entry in `tradinglab/data/` for the history
   bootstrap — typically call your REST endpoint for the last N bars
   and **truncate to strictly before the current interval boundary** so
   the stream's initial rollover doesn't collide with a seeded bar at
   the same timestamp.

### 18.3 Adding a technical indicator

The indicator pipeline is fully wired end-to-end (compute → cache →
render → menu UI → preset persistence). Built-ins live in
`tradinglab/indicators/` (RSI, ATR, ADX, SMA/EMA, Bollinger Bands,
LRSI, SMI, VWAP). Adding a new one:

1. Create `tradinglab/indicators/my_indicator.py` exposing
   `kind_id: str`, `kind_version: int`, `params_schema: List[ParamDef]`,
   `default_style: dict`, and a `compute(candles, params) ->
   Dict[str, np.ndarray]` callable. Output arrays must be the same length
   as `candles`; pad with `NaN` where undefined or across data gaps.
2. Register in `indicators/loader.py` (or call `register_indicator`
   imperatively at import time).
3. The Manage Indicators dialog auto-discovers it; no `app.py` edits.

Render wiring (live):

- `indicators/render.py:compute_layout(state, ind_axes)` reads each
  registered config's `pane` ("price" overlay vs. lower pane) and
  resizes the lower-pane GridSpec dynamically per render.
- `indicators/render.py:render_for_slot(scope, candles, configs,
  price_ax, vol_ax, pane_axes, state)` runs each config's `compute`
  through `IndicatorCache`, plots the resulting `Line2D` artists (or
  fills, for Bollinger Bands), and stores them on `PanelIndicatorState`
  so blit / theme-swap can walk them without re-plotting.
- Compute is thread-safe (no Tk, no matplotlib in `compute()`); the
  render-time call is the main-thread coupling point.
- `IndicatorCache` (LRU-64, key `(id(candles), config_hash)`) avoids
  recompute across pure pan / zoom / theme toggles — see §9.2.

### 18.3.1 Indicator reorder UX

Shipped: drag-to-reorder + keyboard fallback in the Manage Indicators
dialog. Mechanism:

- **Drag handle**: a leading `≡` cell on each row grabs and reorders
  the row via mouse drag. Drop reorders the underlying
  `IndicatorManager` config list and triggers a debounced re-render.
- **Keyboard fallback**: `Alt+↑` / `Alt+↓` when a row has focus moves
  it up / down by one position. Same code path as drag-drop.
- **Overlay layering**: render order is encoded in matplotlib zorder as
  `zorder = 4 + 0.01 * position_index`. The 0.01 step gives ~100
  indicator slots before the next integer band; the +4 base sits above
  candles (zorder 2) and below crosshair / tooltip (zorder 5+).
- **Per-scope ordering**: each scope (`main` / `compare` / `drilldown`)
  has its own ordered list, persisted via the indicator preset menu.

### 18.4 Custom watchlists (fully wired)

`tradinglab/watchlists/` provides storage + a `WatchlistManager`
CRUD API. `WatchlistManager` uses a **dirty-flag + explicit-save**
model with **no observer channel**: mutations set an internal
`is_dirty` flag, the host UI is responsible for calling
`_rebuild_watchlist_subtabs()` after any pin / unpin / reorder /
ticker-edit, and persistence happens only when the user invokes
File → Save Watchlists. Pin APIs: `pinned_names()`, `pin(name)`,
`unpin(name)`, `reorder_pins(names)`; the cap is `MAX_PINNED = 5`
(enforced by `pin`). Ticker normalization (strip / upper-case /
dedupe, preserving order) is factored into `storage.normalize_tickers`
and reused by `manager.create`, `manager.set_tickers`, and
`storage.import_from_file`.

**Wiring:**

- **Toolbar "Watchlists…" button** opens `_WatchlistDialog`, a modal
  with a left-hand list of watchlists (New / Rename / Delete / Set
  Active / Import / Export JSON) and a right-hand tickers editor
  (text entry with Add, Remove, ↑/↓ reorder).
- **Active watchlist tickers feed both Comboboxes** (Ticker, Compare)
  *and* the dedicated Watchlist tab on the right-side Notebook.
- **Third Notebook tab ("Watchlist")** displays Ticker / Last /
  Change / Change %, one row per ticker in active-watchlist order.
  **Change columns are pinned to the 1d interval** regardless of the
  chart's current interval (so the figure matches what brokers show
  day-to-day). Rows are colored with the theme's bull/bear palette —
  dark-mode matches the candle colors exactly, not a stale light-mode
  residue.
- **Snapshot-driven repaint.** `_populate_watchlist_tab` reads from
  `_watchlist_snapshot` (§9.2) only; zero disk I/O on the Tk thread.
  Repaints are debounced to a single `after(60, …)` callback per
  worker-completion storm.
- **Background preload**: `_preload_watchlist` (current interval,
  for Combobox / chart prewarming) + `_preload_watchlist_daily`
  (pins the Change columns). See §15.19 for the concurrency story.
- **Event-driven freshness** (§9.3) keeps the Watchlist tab current
  without periodic polling.

Adding a future feature (e.g., right-click "Compare with" from the
tab, or drag-reorder within the tab) consumes the existing
`WatchlistManager` API — no changes to `storage.py` needed.

### 18.5 Adding a new interval

1. `INTRADAY_INTERVALS` (in `constants.py`) if it's sub-daily.
2. `INTERVAL_PERIODS` with an appropriate yfinance period string.
3. Add to the `Interval` ttk Combobox's values.

### 18.6 Adding timezones / non-US markets

`classify_session` hardcodes US Eastern 04:00/09:30/16:00/20:00. For
other exchanges, parameterize it by `(exchange, interval)` → session
boundaries. Note that yfinance returns timestamps already in the
exchange's local tz, so no conversion should be needed — just the
boundary constants change.

### 18.7 Adding a new user setting

`tradinglab/settings.py` is a tiny JSON store at
`<cache_dir>/settings.json` with four module-level functions: `load()`,
`save(dict)`, `get(key, default)`, `set(key, value)`. Writes are
atomic (`.tmp` + `os.replace`) and `set` preserves unknown keys on
round-trip, so older and newer app versions can coexist against the
same file without clobbering each other's settings.

To add a new setting:

1. Pick a stable snake-case key (e.g., `"poll_grace_ms"`).
2. Resolve it on construction: `value = settings.get("poll_grace_ms",
   <default>)`, validating/clamping as appropriate.
3. If the setting is live-changeable, expose it through
   `_SettingsDialog` (see `_open_settings_dialog` → `_SettingsDialog`
   in `app.py`) — the dialog uses a standard Spinbox / Combobox +
   Apply-button pattern. Persist via `settings.set(key, value)` before
   applying.

The current worker-count setting is the reference implementation:
`_WORKER_COUNT_MIN`, `_WORKER_COUNT_MAX`, `_clamp_worker_count`,
`_resolve_worker_count`, `_apply_worker_count` on `ChartApp`, plus a
row in `_SettingsDialog`.

---

## 19. Known gotchas / pitfalls

- **Don't add `frozen=True` to `Candle`** — breaks streaming in-place
  mutation.
- **Don't copy bars in `_align_pair`** — `_series_cache` and aligned
  views rely on object identity with the raw list.
- **Don't `ax.clear()` in draw primitives** — wipes grid, locators,
  watermark, shading, and the blit background.
- **Don't call full `_render()` on rollover** — snaps the user's pan.
  (For the event-driven next-bar fetch we can't avoid `_render`; see
  the `_preserve_xlim_on_render` flag in §9.3 instead.)
- **Don't forget to invalidate `_blit_bg = None`** after any slice
  refill — stale bars will flash on hover otherwise.
- **Don't forget `_series_cache.pop(id(old_list))`** when swapping a
  candle list — stale numpy arrays will be returned indefinitely.
- **Don't stream one side in compare mode** — the event-driven
  next-bar fetch is suppressed during streaming and the other side
  would freeze.
- **Don't use matplotlib's `drag_pan`** — ylim-snap per frame fights
  autoscale and produces jitter.
- **Mind the disk cache on smoke tests** — stubbing `DATA_SOURCES["yfinance"]`
  writes fake data that persists for 5 min.
- **Ticks fire from a worker thread** — never touch Tk widgets or
  matplotlib artists outside the main thread; enqueue and drain.
- **Event tuple index positions matter** — `(token, slot, src,
  ticker, interval, kind, bar)`. Multiple code paths index by
  position; if you add a field, do a full audit of event consumers.

---

## 20. Glossary

- **Slot** — panel slot in `_panel_state`. Values: `"primary"` and
  `"compare"`. The drilldown view re-uses the `primary` slot rather
  than introducing a third one.
- **Scope** — *indicator* scope, addressing where an `IndicatorConfig`
  applies. Values: `"main"`, `"compare"`, `"drilldown"`. Slot ↔ scope
  mapping: slot `primary` ↔ scope `main`; slot `compare` ↔ scope
  `compare`; the `drilldown` scope is registered but not currently
  rendered (its configs survive a drilldown round-trip but the render
  pipeline still draws the `main` scope).
- **Raw** — the unfiltered, un-aligned candle list as stored in
  `_full_cache`. Canonical state.
- **Filtered / aligned** — derived view stored in `self.candles` /
  `self.compare_candles`, produced by `_apply_pair_filter_and_align`.
- **Tick** — intra-bar OHLC/volume update to the in-progress (rightmost)
  bar.
- **Rollover** — new bar boundary; a new in-progress bar opens.
- **Gap candle** — `Candle.gap(date)` placeholder inserted during
  compare-mode timestamp alignment; renders as empty space.
- **Offset** — per-slot `x_offset` used for right-alignment in compare
  mode.
- **Right edge** — `_global_right_edge = max(n_primary, n_compare, 1)`,
  the stable X coordinate at which the newest bar of the longest
  series sits.
- **Token** — monotonic generation id used to discard stale fetch /
  stream events after reconfigure.
- **Identity preservation** — the property that an in-place mutation of
  a list (e.g. `visible_candles_by_symbol[sym].append(bar)` on a
  streaming tick or sandbox advance) keeps `id(list)` stable, so
  caches keyed by object identity remain valid. Both `_series_cache`
  (keyed by `id(candles)`) and `IndicatorCache` (keyed by
  `(id(candles), config_hash)`) rely on this. Why it matters:
  identity-keyed caches cannot detect an in-place mutation, so
  invalidation must happen explicitly on each tick / rollover (e.g.
  `_invalidate_focused_panels`, `_series_cache.pop(id(old_list))` on
  list swap). Conversely, *replacing* the list (re-slice, copy,
  re-assign) silently invalidates every cached entry — the
  `check_b14_sandbox_visible_list_identity_stable` smoke locks this
  in.
- **Memento (pattern)** — a snapshot object that captures another
  object's state for later restoration without exposing its internals.
  Used by `backtest/replay.SandboxMemento`: `start_session` calls
  `SandboxMemento.capture(app)` to snapshot pre-sandbox app state
  (`_primary` / `_compare` / `candles` lists, the relevant Tk
  StringVars, `_drilldown_day`); `end_session` calls `memento.restore`
  exactly once to put the app back. Centralizing the captured fields
  makes the pre/post contract testable and prevents ad-hoc restore
  paths from dropping state on the floor.
- **MAE / MFE** — Maximum Adverse Excursion / Maximum Favorable
  Excursion. Per-trade analytics tracked by `SandboxEngine` over the
  holding period of an open position by rolling the cursor against
  each bar's high/low. `MAE` is the worst point against the trade
  (largest unrealized loss); `MFE` is the best point in favour
  (largest unrealized gain). Stored on `PostTradeReview` as both
  dollar values (`mae`, `mfe`) and signed percentages of entry
  (`mae_pct`, `mfe_pct`); `performance.build_trade_rows` exposes them
  in the Performance View.
- **RTH** — Regular Trading Hours (09:30–16:00 ET for US equities), as
  opposed to extended hours (pre-market 04:00–09:30, after-hours
  16:00–20:00). The Extended Hours toggle gates session-shading bands
  and pair-mode alignment (`core/pairing` falls back to RTH on both
  sides if either side lacks pre/post data); `backtest/aggregation`
  session-anchors higher-timeframe buckets to the first RTH bar so
  `5m → 1h` produces `[09:30, 10:30)` rather than the UTC-aligned
  `[09:00, 10:00)`.
- **TRF** — Trade Reporting Facility, the FINRA tape that aggregates
  off-exchange (dark-pool / ATS) prints. Relevant in
  `data/normalize.py`: Yahoo's chart API often reports `NaN` or `0`
  volume for extended-hours bars because their volume aggregation
  excludes the TRF tape. Volumes are coerced via
  `np.nan_to_num(..., nan=0.0).astype(np.int64)` so a `NaN` doesn't
  crash the per-row loop.
- **R-multiple** — trade outcome expressed as a multiple of initial
  risk (1R = the dollar risk taken on entry, typically `entry − stop`
  for a long). A win that earns 2× the risked amount is "+2R". The
  Phase 1 backtest engine deliberately does not compute R-multiples
  because it has no stops (market-orders-only); `backtest/journal`,
  `backtest/performance`, and `gui/performance_view` all flag this as
  a Phase 2 deferral once orders carry stop prices.

---

## 21. Phase roadmap

> **Banner:** Everything in this section is non-binding planning. Items
> marked SHIPPED have moved into the body of the spec; items still here
> are aspirational. For the live indicator render pipeline see
> [§18.3](#183-adding-a-technical-indicator) and the reorder UX in
> [§18.3.1](#1831-indicator-reorder-ux).

The codebase is organized around two phases. Phase 1 (manual sandbox
replay) is substantially complete. Phase 2 (automated batch
backtesting) is deliberately deferred — the kernel data layout was
locked in Phase 1 specifically so Phase 2 wouldn't need a re-port.

### Phase 1 — Manual sandbox replay (substantially complete)

- **1a — kernel data layout**: per-field-ndarray `BarSeries`, `Clock`,
  `Order` / `Fill`, `Portfolio`, `apply_fills`. Locked so Phase 2 can
  walk the same arrays.
- **1b — engine + journal**: `SandboxEngine` three-phase tick (fills →
  MAE/MFE → MTM), `PreTradeEntry` / `PostTradeReview`, deterministic
  `SessionResult` round-trip.
- **1c — Tk-coupled controller**: `SandboxController`, post-trade
  review modal, setup-tag taxonomy, screenshot capture.
- **1c-redux — open universe**: master clock anchored on a single
  reference symbol; tickers join mid-session via `register_ticker`
  without extending the timeline.
- **1d — sessions + performance + UX polish**: `save_session` /
  `load_session`, Performance View aggregates (`build_trade_rows`,
  `build_setup_aggregates`), blind mode, auto-cycle through eligible
  dates, multi-timeframe daily-context display, full-session xlim
  pre-allocation.
- **1 indicators** (✓ SHIPPED): SMA, EMA, RSI, ATR, ADX, Bollinger
  Bands, LRSI, SMI, VWAP — all registered with `kind_id` /
  `kind_version` / `params_schema` / `default_style`. Per-pair config
  managed by `IndicatorManager`. Render pipeline (`render_for_slot` +
  dynamic `compute_layout`), Manage Indicators dialog, preset
  Save/Load/Delete cascades in the Indicators menu, drag-to-reorder
  with `Alt+↑/↓` keyboard fallback, native OS colour chooser, and the
  `BollingerBandsEMA` / `ATRSMA` fold into a `ma_type` ParamDef are
  all in. See §18.3 / §18.3.1.

### Phase 2 — Automated backtest engine (deferred)

- **Performance View export bundle** (✓ SHIPPED): equity-curve chart
  with toggleable MTM + closed-trade-realized lines; `Export CSV…`
  writes a portable journal bundle (CSV + sibling
  `<stem>_screenshots/` mirror so pre/post PNGs travel with the
  CSV); `Copy to clipboard` exports a TSV. See
  `gui/performance_view.spec.md` and `backtest/performance.spec.md`.
- **Anchored VWAP** (✓ SHIPPED): user-clickable bar to start the
  cumulation, optional ±1σ / ±2σ bands, works on every interval
  (1m → 1mo). Anchor is set via the indicator dialog's "Pick
  Anchor…" button (one-shot armed click capture); pre/post-market
  clicks snap forward to the next regular bar. See
  `indicators/avwap.spec.md`.
- **Relative Volume family** (✓ SHIPPED): three flavours sharing one
  lower pane (`pane_group="rvol"`) — Cumulative-Day, Per-Bar
  Time-of-Day, Simple Rolling. HH:MM keying for cross-session
  comparisons (correct under half-days / DST), mean-or-median
  aggregator, configurable session filter, partial-warmup support,
  and reference dashes at 1.0 / `threshold_warn` /
  `threshold_extreme`. Time-of-day variants are intraday-only and
  appear with a `(needs intraday)` annotation in the dialog kind
  dropdown when the chart is on a daily-or-higher interval. See
  `indicators/rvol.spec.md`.
- Stop / limit / bracket / OCO orders; intra-bar fill paths;
  volume-aware partial fills.
- Automated `on_bar` strategy hook + batch `run_to_completion` runner
  (kernel is already importable headless — no Tk, no matplotlib).
- R-multiple, payoff ratio, drawdown, Sharpe / Sortino columns once
  orders carry stops.
- `SessionResult` schema migrator (cross-version load is currently a
  hard `ValueError`).

---

## 22. Backtest kernel architecture

The `backtest/` package is structured as a **headless kernel** that the
Tk-coupled controller drives. Three invariants are enforced by code
review and locked in by smoke checks:

1. **No Tk, no matplotlib in kernel modules.** Every `backtest/` module
   except `replay.py` imports only stdlib + numpy. This is what makes
   the kernel reusable from a future batch `run_to_completion` runner
   (Phase 2) without bringing the GUI along.
2. **`replay.py` is the SOLE Tk-coupled module in the package and must
   never be auto-imported.** `backtest/__init__.py` re-exports kernel
   symbols only. `replay.SandboxController` is imported lazily by
   `app.py` when the user opens Sandbox → Start session…; importing
   `tradinglab.backtest` from a headless test does not pull
   `tkinter`.
3. **`SandboxController` marshals between kernel and GUI.** It owns the
   `SandboxEngine`, the master `Clock`, the open-universe ticker
   registry, the post-trade memento callback wiring, and the chart-side
   integrations (drilldown day lock, compare panel routing,
   focus-list updates). Kernel → GUI events are pushed through
   `app.after(0, …)`; GUI → kernel calls are synchronous on the main
   thread.

---

## 23. Sandbox Phase-1 limitations

A condensed checklist of what **Phase 1 sandbox replay is and is not**.
Pin this above any sandbox feature request.

- Market orders only — no stop, limit, bracket, OCO, trailing, or TIF.
- Full-size fills at the next bar's `open`.
- No commission, spread, or slippage by default (configurable per
  session, but unmodelled in the kernel beyond a flat per-fill add-on).
- No margin, leverage, or PDT model — buying power = cash.
- Shorts allowed via negative qty; no borrow cost.
- No Sharpe / drawdown / R-multiple metrics yet — the equity curve is
  exposed for external compute.
- Blind mode does **not** hide time-of-day (only the calendar date).
- Auto-flatten falls back to `avg_cost` for symbols missing a close
  price at session end.
- Single account, USD only, no FX conversion.

---

## 24. Indicators — cross-cutting notes

Trader-relevant invariants that apply to every indicator regardless of
kind:

- **Bar-close evaluated.** Indicators read the candle's sealed
  `close`; in-progress (rightmost) bars produce values that update
  with each tick but are not "final" until rollover.
- **NaN-padded across data gaps.** `Candle.gap(date)` placeholders
  inserted by `_align_pair` produce `NaN` values from `compute()`; the
  render layer skips them, leaving visual breaks aligned with the
  candle gaps.
- **NOT session-aware EXCEPT for VWAP.** RSI, ATR, ADX, MAs,
  Bollinger Bands, LRSI, SMI all roll across the pre-/regular-/post-
  session boundary as if the bars were contiguous. Only `vwap.py`
  re-anchors to the first RTH bar of each session (see `core/pairing`
  for the RTH definition).
- **Recomputed deterministically.** No look-ahead in sandbox replay:
  the engine slices `candles[:k+1]` per tick before handing them to
  `compute()`, so an indicator at bar `k` cannot see bar `k+1`.
- **Caching is identity-keyed.** `IndicatorCache` (LRU-64, key
  `(id(candles), config_hash)`) means in-place tick mutation requires
  explicit invalidation by the caller.

