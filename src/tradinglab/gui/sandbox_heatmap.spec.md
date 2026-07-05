# gui/sandbox_heatmap.py — Spec

## Purpose
Non-modal pop-out window that renders the sandbox Finviz-style heatmap.
Launched from the Sandbox menu while a session is active, it draws the
S&P 500 as a matplotlib treemap embedded in Tk, recolors on every
replay tick, relays out per session, and lets the owner click a tile to
pull that symbol onto the primary chart. All values come from the
[`SandboxController`](../backtest/replay.spec.md) as of `clock_ts()` via
the pure [`backtest/heatmap.py`](../backtest/heatmap.spec.md) layer. See
[`docs/SANDBOX_HEATMAP.md`](../../../docs/SANDBOX_HEATMAP.md).

## Public API
- `class SandboxHeatmapWindow(tk.Toplevel)`.
  - `__init__(app, controller, *, provider=None, price_source=None, **kwargs)` —
    build the figure/canvas, wire hover + click, do the first layout.
  - `on_replay_tick()` — recolor from the controller; relayout first if
    the session rolled. Called by the window's self-poll when the
    replay clock advances.
  - `refresh()` — full rebuild (layout + colors); used on open and
    universe change.
  - `close()` — tear down canvas + mpl callbacks; idempotent.
- `open_sandbox_heatmap(app, controller, **kwargs) -> SandboxHeatmapWindow | None` —
  Sandbox-menu action. Singleton: focuses the existing window if open,
  else constructs one. No-op when no session is active.
- `tile_at(tiles, x, y) -> HeatmapTile | None` — pure hit-test helper.
- `compute_size_pct(provider, price_source, members, clock, *, shares_at=None)` —
  pure size / percent / approximate-symbol helper used by the window and tests.

## Dependencies
- Internal: [`backtest/heatmap`](../backtest/heatmap.spec.md) (pure
  layer), [`backtest/replay.SandboxController`](../backtest/replay.spec.md)
  (duck-typed via `controller`), [`backtest/heatmap_provider`](../backtest/heatmap_provider.spec.md)
  for classification / historical shares / membership data,
  [`gui/native_theme`](native_theme.spec.md) (dark theming), and an
  injectable price source that defaults to daily bars from disk cache.
- External: `tkinter`, `matplotlib` (`Figure`, `FigureCanvasTkAgg`,
  `Rectangle`), `numpy`.

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
  caches `controller.current_session_date()`; on change it calls
  `heatmap.build_layout(...)`, otherwise each tick only calls
  `heatmap.apply_colors(...)`, updates patch facecolors, and
  `draw_idle()` — no squarify, no jitter within a session.
- **No future leakage** (invariant). Prices feed the pure layer solely
  from `controller.visible_candles_by_symbol` (already clock-bounded)
  plus per-symbol prior closes; `full_candles_by_symbol` beyond the
  clock is never read. `clock_ts()` epoch seconds are normalized before
  any millisecond-timestamp comparison.
- **Historically-scaled cap sizing** (decision 3): shares come from
  yfinance `get_shares_full` snapped to the session date (most-recent
  value ≤ date). When price history is deeper than the shares series
  (~11y), sizing before the series start **carries back the earliest
  known count** (nearest-in-time, not today's) and marks those tiles
  `approx_size` (subtle hatched border) + notes it in the coverage
  label. Size uses **raw** session price × **raw** shares so splits
  self-cancel (split-consistency), capturing buybacks / dilution. Sizes
  are stable within a session and update at each session roll.
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
- **Background shares prime.** On open the window renders instantly with
  cache-only sizes (`provider.peek_shares_at` → un-primed tiles are
  approximate slivers), then a daemon thread runs `provider.prime` for
  the membership and a result-flag + `after` poll (CLAUDE.md §7.15)
  triggers a full refresh when real cap sizes are ready. `get_shares_full`
  is disk-cached, so only the first-ever open pays the fetch.
- **Plain `Toplevel`, no `transient()`.** Non-modal and no
  parent-transient call, so the headless-macOS `transient()` deadlock
  (CLAUDE.md §7.1) does not apply and the smoke check needs no darwin
  skip.

## Invariants
- The window references an active controller only; `end_session`
  triggers `close()`.
- Only symbols with data at or before the clock are colored; missing
  data → neutral tile, never a red/green extreme.
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
  ├─ provider.classification() / provider.date_added()       # cached
  ├─ refresh(): members_asof → build_layout(peek sizes) → apply_colors → draw
  └─ _start_prime (daemon: provider.prime) → poll → refresh with real sizes

_poll_clock (250ms) → clock advanced? → window.on_replay_tick()
  ├─ session / membership rolled? → members_asof(clock) → build_layout(sizes)
  ├─ pcts = {sym: compute_1d_pct(price@clock, prior_close) ...}
  ├─ model = apply_colors(layout, pcts, clock)
  ├─ update patch facecolors + labels + focus outline + badges
  └─ draw_idle()  (debounced)

motion_notify → hit-test → tooltip ; button_press → hit-test → load on chart
```

## Testing
- `tests/unit/gui/test_sandbox_heatmap.py` — pure `tile_at` /
  `compute_size_pct` (exact + carry-back + peek-is-approx); Agg window:
  renders a synthetic universe, filters look-ahead members, hover /
  click hit-test loads the symbol, blind-mode title hides the date,
  empty when no clock.
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
  is as-of-today; share count is historical via `get_shares_full` (~11y),
  with carried-back approximate sizing (flagged) for deeper replays.
- Requires the S&P 500 preloaded with prior-day closes for the replay
  window; a missing-symbol tile renders neutral with a hover note.
- First-ever open fetches `get_shares_full` for the membership on a
  background thread (disk-cached thereafter); a single refresh lands
  when done. Incremental / preload-time priming is future work.

## Recent history
- Implemented: pure `heatmap` layer + `heatmap_provider` + this pop-out
  window, wired to the Sandbox menu (`Market Heatmap…`). Self-polls the
  replay clock (250 ms) and background-primes shares. See
  `docs/SANDBOX_HEATMAP.md`.
