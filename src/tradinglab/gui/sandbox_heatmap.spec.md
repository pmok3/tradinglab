# gui/sandbox_heatmap.py — Spec

## Purpose
Non-modal pop-out window that renders the Finviz-style market heatmap,
in **two modes behind one window**:

* **Replay** — launched from the Sandbox menu while a session is active,
  driven by `controller.clock_ts()`, priced from cached bars via
  `SessionPriceSource`.
* **Live** — launched from View → Live Market Heatmap, driven by the
  wall clock, priced from a streaming quote feed via `QuotePriceSource`
  with the bar path as fallback.

The mode is carried by the **context object**
([`gui/heatmap_context`](heatmap_context.spec.md)), not by branches
through the render path: the window only ever read a handful of
controller attributes, all through `getattr(..., default)`, so an object
exposing the same names is a controller as far as it is concerned.

It draws the S&P 500 as a matplotlib treemap embedded in Tk, recolors on
every tick, relays out per session, and lets the owner click a tile to
pull that symbol onto the primary chart. Geometry and colour come from
the pure [`backtest/heatmap.py`](../backtest/heatmap.spec.md) layer. See
[`docs/SANDBOX_HEATMAP.md`](../../../docs/SANDBOX_HEATMAP.md).

## Public API
- `class SandboxHeatmapWindow(tk.Toplevel)`.
  - `__init__(app, controller, *, provider=None, price_source=None, **kwargs)` —
    build the figure/canvas, wire hover + click, do the first layout.
    With no injected `price_source`, builds a `SessionPriceSource` bound
    to the session's `controller.data_source` + `controller.interval`;
    in live mode additionally resolves and subscribes a quote feed.
  - `on_replay_tick()` — recolor from the context; relayout first if
    the session rolled. Called by the window's self-poll when the
    clock advances.
  - `refresh()` — full rebuild (layout + colors); used on open and
    universe change.
  - `close()` — drop the quote subscription, then tear down canvas + mpl
    callbacks; idempotent.
- `class SessionPriceSource` — clock-bounded `(price, prior_close)`
  provider. `__init__(*, source, interval, loader=None)`;
  `build(symbols, as_of_ts)` parses one session's bars (call it off the
  Tk thread); `__call__(symbol, clock_ts)` is a bisect;
  `dollar_volume_at(symbol, clock_ts)` is the session's cumulative
  traded value up to the clock; `stale_symbols()` / `covered_symbols()`
  report coverage.
- `class QuotePriceSource(book, *, fallback=None, stale_after_s=120.0, clock=time.time)` —
  same `(price, prior_close)` + `dollar_volume_at` contract, backed by a
  [`QuoteBook`](../streaming/quote_book.spec.md). Adds
  `quoted_symbols()` (symbols with a usable `last` **and** a vendor
  timestamp), `untimed_symbols()` (quotes that cannot be aged),
  `stale_symbols()`, `feed_age_s()`.
- `open_sandbox_heatmap(app, controller, **kwargs) -> SandboxHeatmapWindow | None` —
  Sandbox-menu action. Singleton on `app._sandbox_heatmap_win`; focuses
  the existing window if open. No-op when no session is active.
- `open_live_heatmap(app, **kwargs) -> SandboxHeatmapWindow | None` —
  View-menu action. Separate singleton on `app._live_heatmap_win`.
- `tile_at(tiles, x, y) -> HeatmapTile | None` — pure hit-test helper.
- `compute_size_pct(provider, price_source, members, clock, *, shares_at=None, size_basis=SIZE_BASIS, dollar_volume_at=None)` —
  size / percent / approximate-symbol helper used by the window and
  tests. `size` follows `size_basis`; `pct` is 1-Day % regardless.

## Dependencies
- Internal: [`backtest/heatmap`](../backtest/heatmap.spec.md) (pure
  layer), [`backtest/replay.SandboxController`](../backtest/replay.spec.md)
  (duck-typed via `controller`), [`gui/heatmap_context`](heatmap_context.spec.md)
  (replay vs live), [`backtest/heatmap_provider`](../backtest/heatmap_provider.spec.md)
  for classification / historical shares / membership data,
  [`streaming/quotes`](../streaming/quotes.spec.md) +
  [`streaming/quote_book`](../streaming/quote_book.spec.md) (live mode),
  [`gui/native_theme`](native_theme.spec.md) (dark theming), and an
  injectable price source that defaults to bars from disk cache.
- External: `tkinter`, `matplotlib` (`Figure`, `FigureCanvasTkAgg`,
  `Rectangle`), `numpy`.

## Live mode
- **Streams, never polls.** A 500-name map on a REST refresh loop would
  consume the request budget that on-demand chart loads and background
  history depend on, to rebuild a value the quote wire already carries.
  So live mode subscribes once to a
  [`QuoteSource`](../streaming/quotes.spec.md) and paints from a
  coalescing book. With no quote source configured it degrades to
  whatever bars are already cached — still no polling — and says so.
- **Quote resolution happens here**, at the window level, for the same
  reason the shares source does: the tunable is policy, and low-level
  modules should receive an already-chosen provider. A broken or absent
  source is not fatal and not loud — `price_source` stays on the bar
  path and the footer reports which is actually in use, because a live
  map that silently pretends to be streaming is worse than one that
  admits it is reading cache.
- **The subscription follows membership, not the tick.** `set_symbols`
  is called on relayout only; index membership is near-static day to
  day, which is precisely why a subscription beats polling here.
- **Staleness is per tile and is a correctness concern.** In replay
  every symbol is priced at the clock or it is grey. Live, symbols go
  stale independently — a thin name's last print can be 40 minutes old
  while a mega-cap updates every second — and two tiles that render
  identically while one is half an hour old is how a trader acts on a
  price that no longer exists. Stale tiles are dimmed to
  `_STALE_ALPHA`; hover reports the age. Dimming rather than hatching is
  deliberate: hatching already means "approximate size", and both facts
  must be readable at once.
- **A symbol the feed has never delivered is not counted stale by the
  quote source** — it is served entirely by the fallback, whose own
  staleness reporting covers it. Counting both would double-report.
- **"Covered by the feed" is narrower than "has a book entry."**
  `quoted_symbols()` requires a usable `last` *and* a vendor timestamp.
  Without the timestamp the age is unknown, and an unknown age is not a
  fresh one — such a symbol used to fall between the two stale sets (the
  quote source skips it because it cannot age it, and the window
  subtracted it from the bar source's set as "covered"), rendering at
  full opacity while its legs came from a completed daily bar.
  `untimed_symbols()` feeds those into the stale set explicitly.
- **The sandbox universe scope is replay-only.** `_sandbox_universe` is
  app-level state written on session start and cleared on end, so
  applying it live would do exactly what two separate windows exist to
  prevent: starting a scoped replay would silently shrink the *live* map
  to those symbols and ending it would grow it back.
- **Feed health is separate from tile staleness.** A dead socket freezes
  every symbol at once; dimming 500 tiles would bury the one fact that
  matters, so `_feed_status()` reports it in the footer instead.
- **Live and replay are separate singletons.** They show different
  worlds — a historical session under a replay clock versus the tape
  right now — and reusing one window would let a sandbox start quietly
  repurpose a map the trader is reading as live.

## Design Decisions
- **Non-modal pop-out, not a docked pane** (decision 7). A full S&P 500
  map needs space, and the owner protects chart real estate; a
  standalone resizable `Toplevel` (ideal on a second monitor) never
  touches the main layout. Singleton per session; auto-closes on
  `end_session`.
- **Matplotlib `Rectangle` patches on `FigureCanvasTkAgg`** — the
  [`gui/performance_view.py`](performance_view.spec.md) embed pattern.
  No treemap dependency (squarify is vendored in the pure layer);
  `mpl_connect("motion_notify_event")` drives the tooltip and
  `"button_press_event")` drives click hit-testing against tile bboxes.
- **Recolor per bar, relayout per session** (decision 8). The window
  caches `controller.current_session_date()`; on a roll it re-primes the
  price snapshot in the background and **keeps the previous geometry**
  until it lands (relayouting from an empty snapshot would flash an
  equal-weight grid, since every size floors to a sliver); the prime's
  completion poll calls `refresh()`, which does the real relayout. Every
  other tick only calls `heatmap.apply_colors(...)` and
  `_update_colors`, which mutates the retained `Rectangle` / `Text`
  handles in place and `draw_idle()`s — no squarify, no artist churn, no
  jitter within a session. Rebuilding ~500 patches plus labels four
  times a second was the dominant cost of stepping bars.
- **No future leakage** (invariant). The price leg comes from
  **intraday** bars at/before the clock; the base leg from the last
  **completed** daily session via `heatmap.completed_session_closes`.
  These are deliberately different rules — a daily bar is timestamped at
  its open but carries the settled close, so an "at or before the clock"
  lookup on `1d` bars returns the finished day's price from the opening
  print onward. That was a live leak: the map showed the day's answer
  for the whole replay, and (both legs being constant) never changed
  intraday. `SessionPriceSource` restricts its per-session snapshot to
  the clock's own session and refuses to serve a snapshot built for a
  different day, so a mid-rebuild tick renders neutral rather than
  stale-but-plausible. `clock_ts()` epoch seconds are normalized before
  any millisecond-timestamp comparison.
- **Prices come from the session's data source** (audit
  ``sandbox-data-source``). `SessionPriceSource` reads
  `controller.data_source` + `controller.interval`, falling back to the
  app's chart source. A hardcoded vendor meant a Schwab/Alpaca user got
  a silently all-neutral map, and an interval mismatch meant the same.
- **Bar timestamps go through `_candle_epoch`, not bare `.timestamp()`.**
  A tz-naive date resolves through the *machine's* zone, while
  `backtest.bars._candle_ts_epoch` (which builds the clock these are
  compared against) treats naive as UTC. On a host east of UTC the bare
  call shifts the series earlier and the bisect returns a bar from after
  the clock — the same look-ahead leak, reintroduced by the user's
  locale. Synthetic sources emit naive dates.
- **`build` invalidates before it publishes.** The reset zeroes the day
  bounds and clears the snapshot *before* storing the new session's
  bounds, so a Tk-thread lookup interleaving with the prime thread can
  never pass the guard for the new session while reading the old one's
  entries (auto-cycle draws a random date, so "old" may be in the new
  session's future).
- **A forced re-prime arriving mid-flight is queued, not dropped.**
  Parsing takes seconds; an auto-cycle roll landing in that window used
  to be discarded, leaving the new session with a snapshot that answers
  nothing until the *next* roll.
- **The fast path refreshes everything the full redraw did implicitly.**
  `_update_colors` clears the focus outline from the previously-focused
  tile (focus changes mid-session via click-to-chart) and re-reads
  position badges (opening / closing a position doesn't relayout).
  Badge `Text` artists are created for every labelled tile — empty when
  flat — so the fast path can show / hide them with `set_text`.
- **Universe-scoped membership.** When the session has a prepared
  universe (`app._sandbox_universe`), members are narrowed to it:
  rendering 500 tiles when 80 have bars produces a mostly-grey map whose
  readable tiles are the ones that can't be traded. No universe → full
  point-in-time membership (legacy behaviour).
- **Tile-area basis is user-selectable** (`heatmap_size_basis`, and a
  "Size by" combobox in the window). Market cap is the default and
  Finviz parity, but it gives most of the pixels to a few mega-caps;
  dollar volume weights by where money is trading and — crucially —
  needs no share count, no filings and no split reconciliation, so it
  keeps working for non-filers, pre-XBRL replays and thinly-covered
  names where cap sizing degrades; equal weight makes the map pure
  breadth. Dollar volume is accumulated **up to the clock** from the
  session's own bars, so it inherits the same no-future-leakage rule as
  colour — summing the whole session would be the in-progress-bar leak
  in the size channel. A symbol with no intraday coverage has no dollar
  volume and is flagged rather than given an invented one. Changing
  basis relayouts (geometry changes) and writes the choice back to the
  setting.
- **Coverage footer is computed, not asserted.** The label counts
  members / priced tiles / prior-close fallbacks / approximate sizes and
  names the source, rather than restating a fixed caveat string.
- **Historically-scaled cap sizing** (decision 3): shares come from
  the injected `shares_data_source` provider (SEC EDGAR) snapped
  point-in-time to the session date (most-recent value already *filed*). When price history is deeper than the shares series
  (~11y), sizing before the series start **carries back the earliest
  known count** (nearest-in-time, not today's) and marks those tiles
  `approx_size` (subtle hatched border) + notes it in the coverage
  label. The count is taken from `provider.basis_shares_at`, i.e.
  already lifted onto the price series' basis by `split_factor_after`
  when that series is back-adjusted (`data.quality.is_split_adjusted`,
  passed as `HeatmapProvider.price_split_adjusted`) — multiplying a
  back-adjusted price by an as-reported count under-sized every
  post-replay-date splitter by its cumulative split ratio (`heatmap`
  Invariant 7). Buybacks / dilution are still captured. Sizes are stable
  within a session and update at each session roll.
- **Point-in-time membership + coverage label** (v1 survivorship
  stance, decision 1). The universe is filtered through
  `heatmap.members_asof(clock)` (current members with `Date added` ≤
  clock), so look-ahead names never render; membership is re-evaluated
  at the clock, so an add/remove crossing triggers a relayout like a
  session roll. A footer label quantifies coverage (members shown ·
  removed names unavailable · symbols missing bars). Members resolve by
  CIK / name, not bare ticker.
- **1-Day % color via Finviz palette** (decisions 4, 5, 11):
  `finviz_hex` fixed ±3% buckets; tile label color chosen by
  `text_color_for` (luminance). Ticker + % shown only when a tile is
  large enough; smaller tiles show ticker only; tiny tiles rely on
  hover.
- **Click-to-chart + sandbox highlights** (decision 10). Clicking a
  tile routes through the controller's focus/register path so the
  symbol loads on the primary chart at the current clock. The
  currently-charted ticker's tile is outlined; open positions
  (`controller.positions_snapshot()`) are badged — position side uses a
  badge, never tile color, which is already spent on %.
- **Blind-mode compliance** (decision 9). When `controller.blind`, the
  title reads "Replay Bar N", the tooltip omits date and absolute index
  level, and no timeframe label leaks the era; tickers, sectors, and %
  stay.
- **Dark-mode theming** (CLAUDE.md §7.31). Figure + axes facecolor and
  group-header text come from the active theme; a matplotlib canvas is
  not swept by the ttk `ThemeController`, so facecolors are set
  explicitly. Any classic Tk chrome is themed via `gui/native_theme`.
- **Clock self-poll (no tick coupling).** While open, the window polls
  `controller.clock_ts()` every 250 ms and calls `on_replay_tick()` only
  when the clock advanced — so it self-updates without hooking the
  controller / panel tick path, and rapid Right-arrow stepping coalesces
  to ~4 redraws/sec (the poll doubles as the debounce). Stops on `close()`.
- **Background shares prime + price snapshot.** On open the window
  renders instantly with cache-only sizes (`provider.peek_shares_at` →
  un-primed tiles are approximate slivers) and neutral tiles, then a
  daemon thread runs `provider.prime` **and**
  `SessionPriceSource.build`; a result-flag + `after` poll
  (CLAUDE.md §7.15) triggers a full refresh when real sizes and colors
  are ready. Bar parsing is O(symbols × bars) and must never run on the
  Tk thread — it used to run per tile per 250 ms tick, re-reading and
  re-parsing every symbol's cache file each time. A session roll
  re-primes with `force=True`.
- **Plain `Toplevel`, no `transient()`.** Non-modal and no
  parent-transient call, so the headless-macOS `transient()` deadlock
  (CLAUDE.md §7.1) does not apply and the smoke check needs no darwin
  skip.

## Invariants
- The window references an active controller only; `end_session`
  triggers `close()`.
- **No value is read from any bar after the clock.** The price leg stops
  at the clock within the clock's own session; the base leg only reads
  daily bars whose session date is *strictly before* it. Pinned by
  `tests/unit/gui/test_heatmap_no_lookahead.py`, including the
  metamorphic case (deleting every bar after the clock changes nothing).
- Only symbols with data at or before the clock are colored; missing
  data → neutral tile, never a red/green extreme.
- A price snapshot is only served for the session it was built for.
- Under blind mode, no calendar date or absolute index level appears
  anywhere in the window.
- No member with `Date added` > the replay clock is rendered
  (point-in-time filter); the coverage label reflects members shown vs.
  those missing data.
- `close()` unbinds every mpl callback and is safe to call twice.

## Data Flow / Algorithm
```text
Sandbox menu → open_sandbox_heatmap(app, controller)
  ├─ singleton? focus existing : construct SandboxHeatmapWindow
  ├─ SessionPriceSource(source=controller.data_source,
  │                     interval=controller.interval)
  ├─ provider.classification() / provider.date_added()       # cached
  ├─ refresh(): members_asof ∩ universe → build_layout(peek sizes)
  │             → apply_colors → _draw
  └─ _start_prime (daemon: provider.prime + prices.build) → poll → refresh

_poll_clock (250ms) → clock advanced? → window.on_replay_tick()
  ├─ session rolled? → re-prime (force); keep geometry until it lands
  ├─ pcts = {sym: compute_1d_pct(*prices(sym, clock)) ...}
  │          price = intraday bar ≤ clock, in the clock's session
  │          base  = last COMPLETED daily session close
  ├─ model = apply_colors(layout, pcts, clock)
  ├─ _update_colors: set_facecolor + label text on retained artists
  └─ draw_idle()  (debounced)

motion_notify → hit-test → tooltip ; button_press → hit-test → load on chart
```

## Testing
- `tests/unit/gui/test_heatmap_live_quotes.py` — `QuotePriceSource`:
  quote legs win, no-quote falls through to cached bars, a
  half-populated quote borrows only the missing leg, the clock argument
  is inert, a raising book degrades; dollar volume from the
  consolidated session total with fallback; staleness threshold, a
  quote with no vendor timestamp is not called stale, a never-delivered
  symbol is not double-reported, feed age; `_fmt_age`.
- `tests/unit/gui/test_sandbox_heatmap.py` — pure `tile_at` /
  `compute_size_pct` (exact + carry-back + peek-is-approx); Agg window:
  renders a synthetic universe, filters look-ahead members, hover /
  click hit-test loads the symbol, blind-mode title hides the date,
  empty when no clock; per-tick fast path moves (not accumulates) the
  focus outline, tracks position badges, and re-issues a queued prime.
- `tests/unit/gui/test_heatmap_no_lookahead.py` — the no-future-leakage
  contract: intraday price leg advances with the clock, daily base leg
  excludes the in-progress session, daily-only fallback uses two
  completed sessions, tz-naive bars read as UTC (not machine-local),
  a new session invalidates the previous snapshot, and the metamorphic
  "future bars change nothing" property.
- `tests/unit/gui/test_sandbox_source_choice.py` — the map prices from
  `controller.data_source` / `controller.interval` and falls back to the
  chart source.
- `tests/smoke/test_smoke_full.py::check_g3_sandbox_heatmap` — enters
  sandbox, opens the heatmap, advances a bar, asserts the map refreshes
  and leaks no date under blind mode. No macOS skip needed (no
  `transient()`).

## Known limitations / Future work
- v1 is S&P 500 + 1-Day % only; no RS coloring, extra timeframes,
  sector-strength aggregates, industry-drill zoom, colorblind palette,
  or full-market map — all v2 (see
  [`docs/SANDBOX_HEATMAP.md`](../../../docs/SANDBOX_HEATMAP.md)).
- A footer label surfaces the fidelity caveats + coverage: membership
  is point-in-time via the `Date added` filter (look-ahead removed) with
  a labeled survivorship residual (removed names absent); classification
  is as-of-today; share count is historical via SEC EDGAR (~2009+),
  with carried-back approximate sizing (flagged) for deeper replays.
- Requires the S&P 500 preloaded with prior-day closes for the replay
  window; a missing-symbol tile renders neutral with a hover note.
- First-ever open fetches shares for the membership on a
  background thread (disk-cached thereafter); a single refresh lands
  when done. Incremental / preload-time priming is future work.

## Recent history
- Fixed a **future-data leak** in the price path: the daily-bar
  "last close ≤ clock" lookup returned the in-progress session's settled
  close, so the map showed the finished day's move from the opening bar
  and never changed intraday. Prices now come from a
  `SessionPriceSource` (intraday leg at the clock, base leg from the
  last completed daily session), parsed once per session on the prime
  thread instead of per tile per tick. Same change: the map reads the
  session's pinned data source / interval instead of hardcoded
  `yfinance` `1d`, recolors by mutating retained artists, scopes members
  to the session universe, computes the coverage footer, and shows the
  clock in ET.
- Implemented: pure `heatmap` layer + `heatmap_provider` + this pop-out
  window, wired to the Sandbox menu (`Market Heatmap…`). Self-polls the
  replay clock (250 ms) and background-primes shares. See
  `docs/SANDBOX_HEATMAP.md`.
