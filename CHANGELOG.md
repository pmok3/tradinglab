# Changelog

## [0.4.2] - 2026-07-03

### Added

- **Hover values on the volume and indicator panes.** Just like the price
  pane, moving the crosshair over a bar now shows that bar's values on the
  lower panes: the volume pane reads `Volume <value>` (top-left), and each
  indicator pane (RVOL, RSI, …) shows its value for the bar (top-right, in the
  line's colour). The value never goes blank — off a bar it shows the latest
  bar.
- **Starter indicator presets ship with the app.** A library of ready-made
  presets (Daily Levels, Momentum, Mean Reversion, VWAP + anchor, and more) is
  now installed on first launch and appears under **Indicators → Load Preset**.
  Your own saved presets are never touched, and a deleted starter preset stays
  deleted. **Tools → Restore Default Templates** also restores them.
- **RSI oversold / overbought bands.** RSI now draws dotted reference lines at
  user-configurable levels (default 30 / 70), so overbought and oversold zones
  are visible at a glance. Length defaults to 14.
- **Choose where indicator presets are saved.** The Custom Indicator Builder
  and the indicator-preset menu now let you save to (and load from) a file
  location you pick — so a preset can live in a durable, portable, shareable
  place instead of only the app's internal store.

### Changed

- **Cleaner indicator names on the chart.** Moving-average labels now read
  `MA (EMA, 9, close)` instead of the verbose `MA (EMA, length = 0, source =
  Close)`. Prior-day levels read **Prior Day H/L/C** with abbreviated
  `pd_high` / `pd_low` / `pd_close` values, and a de-selected close no longer
  draws on the chart.
- **Tidier side panel and menus.** The Primary / Compare OHLC price tables were
  removed from the right-side panel (the chart already shows the prices — the
  tables were wasted space). The menu bar was consolidated: Entries / Exits /
  Strategy Tester now live under a single **Strategies** menu, **Download Replay
  Data…** moved to the Sandbox menu, and the Help topic guides are grouped under
  a **Guides** submenu.

### Fixed

- **Typing a ticker is snappy again on a busy chart.** Each keystroke of a
  ticker now composites through the fast blit path instead of re-rendering the
  whole figure, so typing (e.g. `TSLA` after a ratio + a heavy indicator preset)
  no longer lags per letter.
- **The crosshair stays light when indicator readouts are loaded.** The always-
  on top-left readout is now cached per bar instead of being re-rasterised on
  every mouse move, roughly halving the per-frame cost of the crosshair with the
  daily-levels preset loaded.

## [0.4.1] - 2026-06-25

### Changed

- **Your indicator presets are now independent of configuration files.** Saving
  or loading a configuration no longer touches your chart indicators or your
  named presets at all — a configuration is purely a layout / theme / view
  snapshot. Named presets save themselves automatically to their own file and
  survive an app restart, while the active indicators on the chart start clean
  each launch. (Previously indicators were bundled into the config file, which
  is what made the preset-wipe bug below possible.)
- **"Rebase to 100" now follows your view.** The ratio rebase line re-anchors
  its 100 mark to the *leftmost visible bar* and keeps it there as you zoom,
  pan, or drill down — so the chart always reads as relative performance from
  the left edge of what you're actually looking at, not a fixed historical
  point. During a pan the y-axis relabels live (with no snap when you let go).

### Fixed

- **Loading a configuration no longer wipes your saved indicator presets.** A
  configuration saved before this release could carry an (empty) indicator
  section, and loading it used to clear your named-preset library. Configurations
  are now fully decoupled from indicators, and any indicator data in an older
  config file is ignored on load — so your presets are safe.
- **Indicator presets now survive an app restart.** Presets you save via
  Indicators → Save Preset… are written to their own file and restored on the
  next launch, with no need to explicitly Save Configuration.
- **The compare overlay no longer blanks the chart when you drill into today.**
  Drilling into today's session and then turning on Compare could make every
  candle vanish when the compare ticker's intraday data lagged a calendar day
  behind. The chart now keeps today's bars on both sides, so the candles stay
  put.
- **Heikin-Ashi "flat bar" highlights are now visible on bearish candles in
  dark mode.** The flat-bar accent was collapsing into the red candle body,
  making bearish flat bars look like ordinary bars; the highlight is now clearly
  lighter than the body, and the first bar of each flat run is no longer dropped.

## [0.4.0] - 2026-06-19

### Added

- **Ratio charts — type two tickers to chart their ratio.** Type a symbol
  followed by a slash and a second symbol — for example `AMD/NVDA`, `RSP/SPY`,
  `XLF/SPY`, or `SMH/SPY` — and the chart shows the first divided by the second,
  bar for bar. It's the fastest way to read *relative* strength: the chart rises
  when the numerator is outperforming and falls when it's lagging. Ratios work
  anywhere a normal symbol does (main chart, the compare overlay, watchlists),
  and are drawn as ordinary candlesticks. The volume pane is hidden for a ratio
  (a quotient has no meaningful volume). An optional **View → Ratio charts (A/B)
  → Rebase to 100** rescales the line so it reads as relative *performance* from
  the left edge. See the new **Help → Ratio Charts Guide** for the full list of
  useful ratios and the details. A few memorable shorthands are also recognised
  (for example `RSPSPY` is the same as `RSP/SPY`).
- **Connect to Schwab from inside the app.** A new **Tools → Connect to
  Schwab…** dialog walks you through signing in to Schwab in your system browser
  and pasting the redirect URL back — the standard, secure OAuth flow (no
  embedded login window). This replaces the old terminal-only sign-in step.

### Changed

- **Roomier ticker boxes and a compact Compare toggle.** The Ticker and Compare
  display boxes in the toolbar are wider so a ratio symbol like `AMD/NVDA` shows
  in full. The old "Compare mode" checkbox is now a small **`Compare:` `[On/Off]`**
  toggle button sitting just before the compare ticker, which is clearer and
  saves toolbar space.

### Fixed

- **SPY (and other always-loaded symbols) no longer get stuck on yesterday's
  daily bar.** Mid-session, the daily chart builds a synthetic "today" bar from
  intraday data. Symbols that were already warm in memory — SPY being the prime
  example, since it's the default compare ticker — never triggered the intraday
  fetch that feeds it, so their 1-day chart could sit on yesterday while freshly
  opened stocks showed today. The app now fetches the needed intraday data for
  those symbols too, so today's bar appears for everything.

## [0.3.11] - 2026-06-16

### Fixed

- **Saving and loading a configuration now preserves your theme.** If you
  switched to dark mode and saved a config, loading it back used to come up in
  light mode — the saved theme was applied to the colour *overrides* but never
  to the base light/dark mode. The theme now round-trips correctly through
  File → Save/Load Configuration, with no relaunch needed.
- **Your chart view toggles now survive Save/Load Configuration.** Heikin-Ashi
  candles, the key-bar and flat-bar highlights, the time-of-day volume overlay,
  the colour-blind palette, magnetic drawing snap, the ChartStack panel, the UI
  scale, and the worker-pool size were all written to your config file but never
  re-applied when you loaded it — they only took effect on the *next* launch.
  They now restore immediately on load.
- **Indicators and indicator presets are now saved with your configuration.**
  Previously the active indicators on your chart *and* your named indicator
  presets were never written to the config file at all, so File → Save
  Configuration silently dropped them and a later Load restored nothing. They
  are now captured on save and fully restored on load — including re-applying a
  saved preset.

## [0.3.10] - 2026-06-15

### Changed

- **The chart redraws far faster when you have several indicators on.**
  Adding a 5th, 6th, or 7th indicator pane used to make panning, zooming,
  streaming, and ticker switches feel sluggish, because every redraw tore
  down and rebuilt the entire figure from scratch. The app now recognises
  when the *layout* hasn't changed (same panes, same interval, same
  compare on/off) and **reuses the existing chart, repainting only the
  data** — about **80% faster** in the heavy multi-indicator case. Anything
  that genuinely changes the layout (toggling compare, adding/removing a
  pane, changing interval, drilling into a day) still does a full rebuild,
  so nothing looks different — it just gets there quicker. Power-user
  escape hatch: set `paint_topology_preserve` to `false` in settings to
  force the old full-rebuild path.

### Fixed

- **Indicator names on the price pane now follow a dark-mode switch
  immediately.** If you set up overlays in light mode and then switched to
  dark mode, the indicator names stayed black on the chart until you opened
  *Manage Indicators* (which forced a redraw). The theme toggle now recolors
  those names in place right away — visible names take the theme text colour,
  hidden ones stay greyed.
- **Non-volume indicators are snappy again.** A regression in 0.3.9 made
  *every* indicator pane (RSI, ATR, MACD, ADX, …) quietly use the new,
  more expensive Relative-Volume "centered" axis maths — so charts with
  several indicators felt less responsive than before. The centered scale
  is now correctly limited to the RVOL/RRVOL panes it was designed for.
- **Event markers load faster on long histories.** Building the
  earnings/dividend/split markers scaled with *bars × events*, which
  dragged on multi-year intraday charts. It now scales linearly, with no
  change to which markers appear or where.

## [0.3.9] - 2026-06-12

### Changed

- **Relative Volume panes now read at a glance.** The RVOL and RRVOL panes
  default to a new **centered scale**: the **1.0 "average" line sits in the
  middle of the pane**, 0 is pinned to the bottom, and the busiest bar
  autoscales to the top. Previously, on a plain 0–8 scale a normal ~1× day
  was squashed near the floor and hard to read against the occasional spike.
  The pane keeps a **5× floor** so the 2× and 5× reference lines stay put on
  calm days, and only the top half re-scales when a genuine 5×+ spike shows
  up. You can still switch any RVOL/RRVOL pane to **Log** or **Linear** from
  *Manage Indicators → RVOL → Y-axis scale* — and the old "Log scale"
  checkbox is now folded into that selector (existing setups keep working).

### Fixed

- **Relative Volume log-scale labels are readable in dark mode.** When a
  RVOL pane was set to a logarithmic scale, the small in-between axis labels
  (2, 3, 4, 6 …) stayed black and vanished against the dark background. They
  now follow the theme like every other axis label.
- **Typing a ticker after viewing a brand-new IPO no longer shrinks the
  chart to 2 candles.** A freshly-IPO'd name (e.g. one showing only a
  pre-IPO reference bar plus its first trading day) has just ~1 day of
  history. The app deliberately keeps the same time window when you switch
  symbols, but for a 2-bar IPO that "window" is ~1 day — so switching to a
  liquid name like AMD was carrying that single day across and showing only
  ~2 candles. The app now recognises that viewing a symbol's *entire* (tiny)
  history isn't a deliberate zoom, and opens the new ticker at its normal
  default view. Carrying a real zoom (e.g. a specific week you panned to)
  across symbols still works exactly as before.

## [0.3.8] - 2026-06-11

### Added

- **Anchored VWAP now keeps a separate anchor for each symbol.**
  Previously a single Anchored VWAP used the *same* anchor date for every
  ticker — so the anchor you picked on one stock was wrongly forced onto
  the compare ticker and onto whatever symbol you switched to next. Now
  each symbol remembers its own anchor: pick an anchor on AAPL and another
  on the SPY compare pane, and each draws from its own point. A symbol you
  haven't anchored yet shows **"Not set"** and draws no line (instead of
  silently anchoring to the first bar) until you pick one. A new **"Apply
  anchor to all symbols"** checkbox keeps the old one-anchor-for-everything
  behaviour when you want it (e.g. anchoring every chart to the same
  macro event). Existing saved Anchored VWAPs keep working — they load as
  "apply to all symbols" so nothing changes unless you opt into per-symbol.
- **Relative Volume panes can switch to a logarithmic scale.** When you
  stack a smooth RVOL (e.g. Cumulative) and a spiky one (Time-of-Day) in
  the same pane, a big opening spike used to squash the calmer line into
  an unreadable sliver. A new **"Log scale"** option in the RVOL settings
  (off by default) plots the pane on a log axis so both stay legible while
  the full spike remains visible.

### Changed

- **The app is snappier — especially with many indicators, long histories,
  and during live streaming.** A performance pass made the chart, scanner,
  and Strategy Tester noticeably more responsive:
  - **Live charts stay smooth while streaming.** Each incoming price tick
    now updates the chart with a lightweight repaint instead of redrawing
    the whole figure, cutting per-tick drawing work by roughly **5×** — the
    chart no longer stutters when quotes arrive quickly.
  - **Indicators and scans compute faster.** The visual Conditions builder
    (shared by the Scanner, Entries, and Exits), the built-in indicators
    (RSI, ATR, MACD, Bollinger Bands, VWAP, Anchored VWAP, ADX, RVOL /
    RRVOL), and the candle / volume drawing were rewritten to do far less
    repeated work, so live charts, scans, and Strategy Tester runs over
    long histories and multi-indicator setups finish quicker.
  - These are **speed-only** changes — every indicator value, signal,
    screenshot, and journal number is identical to before (pinned by
    equivalence tests).
- **Indicators update live as you edit them by default.** Tweaking an
  indicator's settings now reflects on the chart immediately rather than
  waiting behind a separate "Apply" step.
- **Switching ticker, interval, or pre/post-market now snaps the view to
  the latest bars.** An explicit chart change no longer reuses the previous
  interval's zoom window (which could leave you parked months in the past
  with data off to the right).

### Fixed

- **Relative Volume panes with two RVOL indicators now show both
  correctly.** When RVOL Cumulative and RVOL Time-of-Day shared one pane:
  the pane auto-scaled to only one of them (clipping the other's spikes);
  hovering showed only one indicator's value; and clicking the pane label
  to edit always opened the first one. Now the pane fits **both** series,
  the hover readout lists **every** indicator's value at the cursor, and
  each indicator name in the pane label is individually clickable so you
  edit the one you actually clicked.
- **No more phantom current-day bar.** A data-provider quirk could insert a
  bar with no real price (blank OHLC) for the current session before any
  trade printed — showing as an invisible gap behind a stray volume bar.
  These bars are now dropped on load and never cached, and a corrupt cached
  bar self-heals on next read. Daily charts with a recently-missing trading
  day also re-fetch once to fill the hole.
- **"Pick Anchor" no longer leaves dialogs stuck on the taskbar.** Choosing
  an Anchored VWAP anchor now fully hides the open indicator dialogs while
  you click the chart, then restores them — instead of minimising them to
  the taskbar where they grabbed focus.
- **Colour picker OK button is no longer clipped** off the bottom of the
  themed colour chooser.

## [0.3.7] - 2026-06-08

### Added

- **TradingLab now warns you instead of silently doing nothing when a
  strategy can't work on the chosen interval.** Some indicators are
  *intraday-only* — **VWAP**, the cumulative / time-of-day modes of
  **RVOL** / **RRVOL**, and **Prior Day High/Low** — because they're
  anchored to the trading session. On a **daily / weekly** chart each bar
  *is* a whole session, so these indicators have no value and any rule
  that reads them (e.g. *close > VWAP*) can never become true. Previously
  a strategy built around one of them just produced **zero trades / zero
  signals** with no explanation. Now the app checks for this mismatch and
  shows a clear popup that names the offending indicator, in three places:
  - **Strategy Tester** — clicking **Run** on a daily/weekly interval with
    such an indicator is blocked up front (no more staring at an empty
    0-trade result wondering why).
  - **Arming an entry** (Entries tab) — arming a strategy that can never
    fire (an intraday-only indicator pinned to a daily/weekly rule) is
    blocked with an explanation.
  - **Sandbox bar-replay** — if the sandbox session was loaded with only
    daily data, arming an entry that needs finer (intraday) bars — or uses
    an intraday-only indicator on those daily bars — is blocked, since the
    session simply can't feed it. (A plain *market* entry, which fires on
    the bar regardless of interval, is never blocked.)

  Strategies that are fine on their interval are unaffected — e.g. a 5-minute
  VWAP strategy still arms and runs normally.

### Fixed

- **Strategy Tester "Recent runs" now lists newest first.** The list was
  sorted by an internal folder name (a configuration fingerprint), so runs
  appeared in an effectively random order rather than by time. They are now
  ordered by their **Started** timestamp, newest at the top.

## [0.3.6] - 2026-06-08

### Added

- **"Show: Mine | Templates | All" filter declutters the strategy
  lists.** TradingLab seeds ~21 entry and ~22 exit *starter templates*
  into your library on first run. Those starters are handy, but they
  buried your own saved strategies in three places: the **Entries** tab,
  the **Exits → Edit Strategies** dialog, and the **Strategy Tester**'s
  entry/exit pickers. Each of those now has a **Show** filter that opens
  on **Mine** — your own strategies only — so the bundled starters stay
  out of the way until you switch the filter to **Templates** (browse the
  starters) or **All** (everything). Details:
  - A strategy counts as a *bundled template* only if it's one of the
    originals shipped with the app. The moment you **Load** or
    **Duplicate** a template it becomes *your* strategy and shows under
    **Mine** — so customizing a starter never makes it disappear.
  - The filter is **display-only**: it never changes which strategies are
    saved, armed, evaluated, or run. In the Strategy Tester your selected
    entry/exit is preserved even if you flip the filter to a view that
    hides it.
  - It resets to **Mine** each time you open the app / dialog (it is not a
    saved setting), and each segment shows a live count
    (`Mine (n)` / `Templates (n)` / `All (n)`).

### Fixed

- Corrected an internal architecture spec that had drifted from the code
  (the `ChartApp` mixin list in `app.spec.md`) and added a regression
  test so it can't silently drift again. No user-visible behavior change.

## [0.3.5] - 2026-06-06

### Changed

- **Color-blind-safe (Okabe-Ito) palette now recolors the *entire* UI —
  live, with no relaunch.** Previously, turning on **Settings → "Use
  color-blind-safe palette (Okabe-Ito)"** only swapped the main chart's
  candle colors; many other red/green surfaces stayed teal-green /
  coral-red, which defeated the purpose for a color-blind user. This
  release audits every *directional* color in the app (anything that
  encodes up/down, bull/bear, gain/loss, profit/loss) and routes it
  through a single source of truth so the toggle reaches all of it at
  once. Now flipping the setting immediately recolors, with no restart:
  - the **watchlist row background shading** and row text (the headline
    fix — these were still red/green);
  - the **Primary / Compare OHLC tables**;
  - the **MACD histogram** (all four momentum classes);
  - **Prior Day High / Low** reference lines;
  - the hover **readout % change**;
  - the **ChartStack** SPY/QQQ/VXX mini-cards;
  - the Heikin-Ashi flat-bar hatching and the time-of-day volume overlay;
  - **Strategy Tester** trade screenshots (entry/exit/MAE/MFE markers)
    and the sandbox post-trade **P/L badge**.

  Under the color-blind palette these all become **orange (bullish) /
  sky-blue (bearish)** instead of green/red, while preserving each
  theme's carefully tuned tint (light pastels stay pale, dark tints stay
  muted). The default green/red palette is byte-for-byte unchanged.
  Status colors (error/warning/info) are intentionally left alone — they
  are a separate concern from the bull/bear distinction.

### Added

- **Watchlist column width is now a saved setting.** The width of the
  right-hand watchlist / OHLC / scanner column is no longer reset to a
  fixed golden-ratio default on every launch. Drag the divider to the
  width you like, then **File → Save Configuration**; that width is
  written to your configuration file and restored by **File → Load
  Configuration** in future sessions (stored under
  `layout.notebook_width_px`). No new dialog — it follows the same
  drag-then-save flow as the rest of the layout. Configurations saved
  before this release (which have no stored width) simply fall back to
  the previous golden-ratio default.

### Fixed

- **Compare mode no longer shows a one-day gap on daily charts.** For
  some tickers (e.g. MU), turning on compare mode against another symbol
  rendered today's daily bar detached from yesterday's — a blank slot
  appeared between the last two bars on one chart, and a blank
  "tomorrow" slot on the other. Root cause: mid-session, today's daily
  bar is synthesized from intraday data and carries the *session-open*
  timestamp (e.g. 09:30 ET), while the compared symbol's same-day bar
  came from the data provider stamped at midnight. The compare-mode
  aligner keyed bars by their exact timestamp, so those two same-day
  bars landed in different slots. Daily (and weekly / monthly) charts now
  align bars by **calendar date**, so today snaps into a single shared
  slot on both panels. Intraday compare alignment is unchanged
  (sub-day bars are still matched by exact timestamp).

### Removed

- **Removed the "Update endpoint override" field from Settings.** This
  power-user text box (for pointing the update checker at a fork or
  self-hosted release manifest) is not something an end user should need
  to touch, so it no longer clutters the Settings dialog. The underlying
  capability is unchanged — the update endpoint still falls back to the
  built-in GitHub Releases URL (or the `TRADINGLAB_UPDATE_URL`
  environment variable), and the `update_check_url` setting can still be
  edited directly in `settings.json` by anyone who genuinely needs it.

## [0.3.4] - 2026-06-05

### Added

- **ChartStack now defaults to a fixed SPY / QQQ / VXX preset, editable
  via a dedicated settings popup.** The mini-chart sidebar (ChartStack)
  previously filled its cards from a "hybrid" mix of your open
  positions, watchlist, and scanner results. It now ships with a
  fixed, predictable layout out of the box — **SPY on top, QQQ in the
  middle, VXX on the bottom** — the broad-market reference trio most
  useful as an at-a-glance market read. To change which symbols appear
  (or how many, if you've increased the card count), open
  **View → ChartStack → Settings…**: a small popup with one text box
  per slot. Type a ticker into each, click **Save**, and the cards
  re-bind immediately; **Reset to Defaults** restores SPY/QQQ/VXX;
  **Cancel** discards your edits. Your picks persist across restarts
  (stored in `settings.json` under `chartstack.fixed_preset_symbols`).
  A new `FIXED_PRESET` binding mode backs this — it binds each slot to
  your chosen symbol verbatim and, unlike the old hybrid mode, never
  silently substitutes your open positions or watchlist. Existing users
  who had explicitly set a different ChartStack binding mode keep their
  choice; open the Settings popup and Save to switch to the preset.

### Changed

- **View menu: ChartStack is now a sub-menu (cascade), matching
  Heikin-Ashi.** Instead of two separate top-level View entries
  (`ChartStack` toggle + `ChartStack Settings…`), there is a single
  **ChartStack** sub-menu containing **Show ChartStack** (the
  show/hide toggle, still bound to `Ctrl+``) and **Settings…** (the
  preset editor). This keeps related controls grouped together and
  tidies the View menu.

### Fixed

- **Toggling ChartStack no longer resizes the watchlist.** Previously,
  showing or hiding the ChartStack sidebar would snap the right-hand
  watchlist / OHLC / scanner column to roughly half the screen,
  especially if you had resized or maximised the window since launch.
  Root cause: the toggle computed the new pane layout from the
  *startup* window width rather than the *current* width, so on a
  wider-than-startup window the watchlist boundary landed in the wrong
  place. The toggle now measures the watchlist's actual current edge
  and holds it fixed — only the main chart resizes (by exactly the
  220-pixel ChartStack column width) to make room for, or reclaim space
  from, the three mini-charts. The watchlist stays exactly where you
  left it. Verified pixel-for-pixel: the watchlist column does not move
  by a single pixel across a toggle.

## [0.3.3] - 2026-06-04

### Added

- **View → Heatmap menu entry — one-click hand-off to the Finviz
  S&P 500 sector heatmap.** Adds a new `Heatmap` item to the View
  menu (positioned just below the existing `ChartStack` toggle).
  Clicking it opens the Finviz sector performance treemap
  (https://finviz.com/map.ashx?t=sec) in your default web browser
  — TradingLab itself doesn't pop a new window, and there's no
  intermediate variant-selection dialog to click through. The
  sector view (~11 squares, one per S&P sector with intraday
  performance shading) was chosen over the per-stock view (~500
  tiny squares) because it's more useful as a "where is the money
  rotating right now" glance during an active trading session;
  the per-stock view is a single URL flip away
  (`t=sec_all`) if a future iteration wants to expose both. No new
  bundled dependencies — the implementation is a thin wrapper
  around stdlib `webbrowser.open` (~45 LOC of code + ~200 LOC of
  tests). If your machine has no default browser configured
  (locked-down Windows profile, headless session, or an
  `Exception` raised inside `webbrowser`), the feature degrades to
  a `messagebox` dialog containing the URL so you can copy-paste
  it manually rather than silently doing nothing. Mirrors the
  existing `Help → View Online Docs` pattern.

## [0.3.2] - 2026-06-03

### Added

- **Themed Win-ChooseColor look-alike replaces the OS chooser for
  indicator color selection.** New `ThemedColorChooser` (in
  `gui.color_palette`) mirrors the Win32 ChooseColor layout
  (8×6 Basic colors grid + 8×2 Custom colors grid + H×S pad +
  L slider + H/S/L + R/G/B spinboxes + hex entry + Color|Solid
  preview + Add to Custom Colors / OK / Cancel) but its chrome
  follows the app's light/dark theme. Closes the gap left by the
  legacy `tkinter.colorchooser.askcolor` (a Win32 `COMMDLG` that
  never adopted Windows dark mode). Custom colors persist as
  `%LOCALAPPDATA%\TradingLab\custom_colors.json` (16-slot list,
  atomic JSON writes, corrupt-file safe). Same `pick_color`
  public signature — every caller (`ChartApp._legend_pick_color`,
  `IndicatorDialog._on_pick_color_for_output`) picks up the new
  behavior transparently. (audit `themed-color-chooser`)

### Fixed

- **Entries tab: "Load template…" button aligned with the rest of
  the toolbar.** Was offset 8 px to the right because its `padx`
  was `(12, 0)` instead of `(4, 0)` like every other button on
  bars 1 and 2. Pure visual fix — no behavior change. (audit
  `entries-tab-load-template-alignment`)

All notable changes to this project will be documented here. Format roughly follows [Keep a Changelog](https://keepachangelog.com/).

## [0.3.1] - 2026-06-02

### Fixed

- **Color picker no longer stuck in light mode in dark themes** when
  opened from a child dialog (e.g. IndicatorDialog). Root cause:
  `gui/native_theme.current_theme(owner)` only inspected
  `owner._theme_ctrl` directly; only `ChartApp` installs a
  `_theme_ctrl`, so intermediate dialogs caused the lookup to fall
  through to `LIGHT_THEME`. Fixed by walking the Tk `master` chain
  (with a `winfo_toplevel()` fallback) until an ancestor with
  `_theme_ctrl` is found. Every dialog using `current_theme(self)`
  benefits transparently. (audit `color-picker-theme-walks-master-chain`)
- **Strategy Tester: 1d / 1wk / 1mo strategies no longer emit zero
  trades.** Root cause: `runner._filter_rth_only` was dropping 100%
  of daily candles (timestamped 00:00 ET = outside the 09:30-16:00
  RTH window) AND `arm_window` + `require_market_open` evaluator
  gates were blocking every daily bar. Now bypassed when
  `is_intraday(interval) is False`. After fix: 5y MSFT 3/8 EMA
  cross produces 70 closed trades / 141 fills (was 0).
  (audit `daily-rth-bypass`)

### Changed

- **AVWAP legend prefix shows only the anchor point** — the one
  "important detail" for an anchored indicator. Format:
  - blank anchor → bare `Anchored VWAP` (no parens)
  - date-only anchor → `Anchored VWAP(2025-09-15)`
  - intraday anchor → `Anchored VWAP(2025-09-15 09:30)`
    (`T` → space, zero seconds dropped; non-zero seconds preserved)

  `price_source` and `bands` rendering knobs no longer appear in the
  label. New `BaseIndicator.legend_label(display_name, params)`
  classmethod hook lets future indicators suppress similarly noisy
  schema-walker output. (audit `avwap-anchor-only-label`)
- **Indicator legend rows consolidated** — multi-output indicators
  (Bollinger Bands, AVWAP-with-bands, Keltner, Donchian) now render
  as a single row of the form
  `BB(20) upper 421.50 middle 418.20 lower 414.90` with each band's
  value in its own color. Per-output `style.visible=False` now also
  hides bands from the legend. AVWAP with bands disabled went from
  5 noisy rows to 1 clean row. New
  `BaseIndicator.effective_output_keys(params)` classmethod lets
  indicators declare which outputs are visible per-params + their
  canonical top-down chart order. (audit `legend-condensation`)
- **Color picker shows Advanced HSV + Swatches side-by-side** —
  the historical view-toggle radio is gone; both panes are
  permanently visible. Hex entry + preview swatch moved under the
  swatches column ("final pick" affordances grouped together).
  Dialog widened 440×420 → 760×420. (audit `color-picker-side-by-side`)

### Tests

- **+62 invariant meta-tests across 7 files** that catch entire
  classes of future regressions at PR time. Covers: dark-theme
  coverage for every Toplevel subclass; no hardcoded color
  literals in classic Tk widget constructors; spec-md coverage
  (every `.py` has a colocated `.spec.md`); ChartApp MRO
  invariants (mixins have no `__init__`, `tk.Tk` is last);
  TriggerKind dispatch completeness (entries + exits); ZoneInfo
  consolidation; protect_combobox_wheel on every BaseModalDialog;
  indicator schema (`scannable_outputs` ⊆ `default_style`,
  `effective_output_keys` ⊆ `default_style`, `kind_id` unique, no
  required ctor args); no debug-statement leaks; no hardcoded
  user paths; no raw `open(w/a)`; JsonObjectStore adoption; no
  mixin → mixin imports. Each contract has an allowlist with
  documented grandfathered cases and a self-test that detects
  stale entries.

### Internal

- **4 spec.md sync passes** keeping AGENTS.md / CLAUDE.md and
  per-module `*.spec.md` in step with the legend-condensation +
  AVWAP-label + color-picker + theme-walk-up sprints.

## [0.1.2] - 2026-05-23

### Added

- **Strategy Tester — new top-level "Strategy" notebook tab.** Pair
  a saved Entry strategy with a saved Exit strategy and replay it
  mechanically over a chosen universe + date range. Produces a Run
  on disk with per-symbol JSON results, an aggregate Report
  (Wilson + bootstrap CIs, daily Sharpe / Sortino, max drawdown,
  per-symbol + per-year breakouts, sample-size banners),
  per-trade annotated screenshots, a 24-column trades.csv, and
  optional self-contained HTML + multi-page PDF exports. Recent
  Runs sidebar at the bottom of the Configure pane lets you reload
  prior runs without re-execution. See `docs/STRATEGY_TESTER.md`
  for the walkthrough and `docs/strategy_tester/metrics.md` for the
  plain-English metrics glossary.

### Changed

- **Consolidated `SMA` and `EMA` indicators into a single "Moving
  Average" entry** in the Add Indicator menu. Pick from `SMA / EMA /
  WMA / RMA` in a Type dropdown plus a new Source dropdown (`Close /
  Open / High / Low / HL2 / HLC3 / OHLC4`), so one indicator now
  covers four kernels × seven source fields. Default colours match
  the legacy classes (SMA = blue, EMA = orange). The dropdown
  remembers the last MA type you picked for the rest of the app
  session. Existing saved presets, drawings, and chart layouts that
  used `SMA` or `EMA` migrate automatically at load — colour,
  length, and per-interval visibility you customised are all
  preserved. See `indicators/moving_averages.spec.md` and
  `docs/indicators/ma.md`.

### Fixed

- Smoke tests no longer crash CPython 3.11/3.12 at exit on Linux
  via `Tcl_AsyncDelete: async handler deleted by the wrong thread`.
  The session `app` fixture now drains pending Tk `Variable.__del__`
  on the main thread after `_on_close()`, and per-test Tk roots
  in `test_smoke_strategy.py` do the same after `root.destroy()`.

## [0.1.1] - 2026-05-22

### Added (0.1.1 cycle)

- **`View → Volume time-of-day shading (1d bars)` discoverability toggle.**
  Mirrors the Settings checkbox; rendering remains off by default.
  Settings selection and menu state stay in sync. See
  `gui/volume_tod_overlay.spec.md`. Audit
  `volume-tod-view-menu-toggle`.
- **Daily today-bar upsampling.** On the 1d chart, the most recent
  daily bar is now upsampled from the active intraday cache (1m > 2m
  > 5m > … > 1h), so live mid-session OHLCV matches what you see on
  the 5m chart. `_full_cache` stays truthful — the synth bar lives
  only in `_primary_raw`/`_compare_raw`. Audit
  `daily-today-upsample`. See `data/today_upsample.spec.md`.
- **Click pane-indicator labels to edit params.** Indicators with
  their own pane (RSI, MACD, ATR, RRVOL, volume…) now respond to
  B1 (open params popup) and B3 (context menu) on the pane label —
  matching the existing UX for overlay indicators on the price chart.
  Audit `pane-indicator-label-click`.
- **`A` / `B` / `D` event marker glyphs.** Earnings AMC, Earnings
  BMO, and Dividend events are now rendered as bold letter glyphs
  (`A`, `B`, `D`) instead of squares/dots. Hover metadata and
  cleanup logic preserved. See `events/*.spec.md`. Audit
  `event-letter-markers`.

### Fixed (0.1.1 cycle)

- **Watchlist Change/% anchored to prior session close.** Live mode
  no longer shows "yesterday vs. day-before-yesterday" when yfinance
  hasn't emitted today's daily bar; intraday `last` − prior
  completed daily close is the new anchor. Current-day partial
  daily bars are excluded as anchors. Daily fallback is tagged and
  overwritten by intraday refresh. Sandbox behaviour preserved.
  Audit `watchlist-prior-close-anchor`.
- **Indicators refresh on poll-fetch arrival during live session.**
  When a 5m (or other) poll-fetch lands fresh bars, the indicator
  cache is now invalidated for the affected slot so SMA / EMA /
  derived arrays repaint instead of returning stale fingerprint-
  matched results. Audit `indicator-poll-refresh`.
- **RRVOL line renders on interval switch (5m).** When the user
  flips to a sub-1d interval, RRVOL's compare-symbol candles arrive
  async; previously the slot rendered empty until the user toggled
  any other indicator. The slot now re-renders when the reference
  data lands. Audit `rrvol-interval-switch`.
- **HA "Highlight flat candles" toggle unlocked.** The menu entry
  is always clickable (no longer greyed out when HA mode is off).
  Render gates on HA mode AND the flat-toggle, so toggling either
  off correctly hides the highlights. Audit
  `ha-flat-toggle-unlocked`.
- **Watchlists + Entries tabs: complete dark-mode coverage.**
  Second-pass sweep — `TFrame`, `TNotebook`, Treeview body chrome,
  context-menu active row, picker `Listbox` focus/border (Watchlists);
  root/toolbar `TFrame`, toolbar `TButton`s, `TPanedwindow`s,
  `TLabelframe`s, strategy `Treeview`, `TScrollbar`, audit/stats
  `tk.Text` focus rings (Entries). Audit `ttk-container-dark-v2`.
- **Cascade menu arrows visible in dark mode.** Append a Unicode
  chevron (`›` U+203A) to every cascade label so it renders in the
  menu's `fg` colour. The native Win32 cascade indicator on Windows
  draws via `DrawFrameControl` → `GetSysColor(COLOR_MENUTEXT)`,
  which ignores Tk color options; the workaround paints a light
  chevron via Tk's text rendering instead. Always-on, idempotent.
  Audits `menu-disabled-fg`, `menu-cascade-unicode-chevron`.
- **Startup window restored to 90%-of-screen.** A saved
  small/cramped `main` geometry was previously accepted unconditionally
  and overrode the percent fallback. `geometry_store` now rejects
  too-small saved main geometry, falls back to centered 90%×90%,
  and exposes `startup_width_pct` / `startup_height_pct` tunables.
  Audit `startup-window-percent`.



### Added

- **Schwab credentials can now be configured ahead of OAuth landing.**
  `File → Configure Credentials…` now surfaces the SCHWAB_APP_KEY /
  SCHWAB_APP_SECRET / SCHWAB_REDIRECT_URI fields **unconditionally**
  (no longer gated on `data.schwab_source.SCHWAB_REGISTRATION_ENABLED`).
  Users wiring up the Schwab integration can stash credentials now;
  the data layer still keeps `register_source("schwab", ...)` gated
  until the OAuth plumbing lands. Predecessor audit
  `schwab-credentials-gated` retired in favour of
  `schwab-credentials-always-on`. See `gui/credentials_dialog.spec.md`.
- **Scanner rank-by picker now exposes every registered indicator.**
  The `Rank by:` combobox in each Scanner sub-tab previously showed
  a hand-curated 7-item list. It now shows the curated head plus
  every scannable builtin / indicator from
  `tradinglab.scanner.fields.all_fields()` — one entry per
  `(indicator, output_key)` pair — so power users can rank a
  candidate list by any indicator output without leaving the
  dialog. Multi-output indicators (Bollinger / ADX / SMI) each
  contribute one preset per output. Audit ID
  `scanner-rank-presets-all-indicators`. See `gui/scanner_tab.spec.md`.

### Fixed

- **CI lint + smoke pipelines restored to green.** The `ruff>=0.4`
  pin in `pyproject.toml` was unbounded and CI auto-resolved to
  ruff 0.15.13, which promoted ~3,890 `UP006` / `UP035` / `UP045` /
  `UP037` / `UP007` violations to errors plus surfaced ~150 long-
  dormant `E701` / `E702` / `E741` style hits. Sprint: (a) pinned
  ruff to `>=0.15,<0.16` so the toolchain is reproducible, (b) ran
  `ruff --fix --unsafe-fixes` to modernize typing across 150+ source
  + test files (`Dict[X]` → `dict[X]`, `Optional[X]` → `X | None`,
  `Union[X, Y]` → `X | Y`, quoted annotations unquoted), (c) added
  `UP035` and `B017` to the `tests/**` per-file-ignores, (d) added
  `E701` / `E702` / `E741` to the global `ignore` list (these are
  codebase style conventions, not bugs — `l = low` in OHLC contexts
  and one-line `cur_h = np.nan; cur_l = np.nan` inits), (e) added
  the missing `logger = logging.getLogger(__name__)` to `app.py`
  (two `logger.exception` calls in `_redraw_live_price_overlay` /
  `_update_live_price_overlay_for_slot` were latent
  `NameError`-bait), (f) restored two pruned typing imports in
  `gui/interaction.py` by rewriting `List[int]` / `List[Any]` to
  `list[int]` / `list[object]`. Smoke-test fixes: refreshed
  `.pkl` → `.jsonl` sentinel filename pins in
  `tests/smoke/test_smoke_full.py` (3 sites) and
  `tests/smoke/conftest.py` for the C1 security migration from
  pickle to JSON cache; widened the d40 cache-isolation assertion
  to honour the `TRADINGLAB_DATA_DIR > TRADINGLAB_CACHE_DIR`
  precedence used by `release.yml`; updated the d42 indicator scope
  picker pin to `{'main', 'drilldown'}` (matching the new
  `DEFAULT_SCOPES` so 1d-added indicators carry forward into
  drill-down by default); made the `test_field_ref_picker_reflow`
  block-editor tests xvfb-robust by stubbing `winfo_width()` rather
  than relying on `geometry()` taking effect under headless Linux.
  Audit `ci-red-sprint-2026-05-22`.
- **Volume y-axis no longer renders a `0` tick label.** The volume
  pane's locator now uses `prune="lower"` so the bottom tick is
  omitted; zero-volume bars remain visually obvious as a flat
  baseline without the `0` label colliding with whatever indicator
  pane lives directly underneath. Audit `volume-axis-zero-tick`.
- **Documentation viewer now follows dark/light theme.** The
  built-in doc viewer (`gui/doc_viewer.py`) previously cached its
  palette at construction time and never repainted on theme toggle,
  so the markdown body, search bar, and TOC stayed light-mode-only.
  `_build_layout` now tags every `tk.Frame` / `tk.Label` with the
  palette slot it consumes, and a new `_apply_theme()` method
  walks them on every theme switch (including singleton re-opens).
  Audit `doc-viewer-live-theme`.
- **Manage Indicators dialog labels and icons follow dark theme.**
  Extended `_apply_theme` in `gui/indicator_dialog.py` to walk
  `tk.Label` widgets and re-tint their bg/fg from the active
  palette. The help-icon "ⓘ" label keeps its blue accent via the
  new `_preserve_fg=True` tag so it stays recognisable across
  both themes. Audit `indicator-dialog-labels-theme`.
- **Export Bars to CSV is now a single zip file.** `File →
  Export Bars to CSV…` (also reachable from Tools) writes a single
  `tradinglab-export-YYYY-MM-DD.zip` (default name editable in the
  Save As… picker) containing per-source CSVs at arcname
  `<SOURCE>/<TICKER>_<INTERVAL>.csv` — saves disk space versus the
  prior folder dump and produces a single file users can share.
  Audit `csv-export-zip`. See `data/local_export.spec.md`.
- **Local data sources accept zip archives as roots.** The BYOD
  `File → Configure Local Data…` browser now offers a "folder vs.
  zip" choice; either root shape feeds the same source-discovery
  pipeline. Inside the zip, each top-level directory becomes one
  registered source (named `<root-name>-<subdir>`), and the
  fetcher reads CSVs directly from the archive without unzipping.
  Round-trip with the new zip-export is now fully sealed (export
  → share → import without unzipping). Audit `local-source-zip`.
  See `data/local_source.spec.md`, `gui/local_data_dialog.spec.md`,
  `docs/LOCAL_DATA.md`.
- **Entries / Watchlist tabs honour dark theme.** Added
  `TLabelframe`, `TLabelframe.Label`, `TPanedwindow`, `Sash`,
  `TScrollbar`, and `TSpinbox` to `build_ttk_style_spec` —
  previously these ttk widget classes fell back to OS-default
  light-grey palette under dark mode, leaving the Entries tab's
  `Strategies` / `Audit (tail)` / `Stats` frames + the Watchlist
  scrollbar unthemed. Audit `ttk-container-dark`.
- **"Highlight Flat HA Candles" menu entry default is now OFF and
  no longer renders blurry under dark mode.** The default for
  `highlight_ha_flat` was flipped from `True` to `False` so
  first-launch users see plain HA candles without the cross-hatched
  overlay. Separately, `gui/theme_controller._apply_menubar_theme`
  now sets `disabledforeground=theme["text_disabled"]` on the
  menubar and every cascade, replacing the Windows-default
  etched/embossed disabled-text style (which looked blurry against
  the dark window background) with a clean GitHub-muted grey
  (`#8b949e` light, `#6e7681` dark). Audits `ha-flat-default-off`
  and `menu-disabled-fg`.
- **Theme Editor gains a Save and Close / Cancel pair.** The
  Theme Editor (`File → Theme…`) previously had a single Close
  button — accidental ESC / window-close still committed any
  in-flight palette edits because every pick applies live.
  `__init__` now `deepcopy`s `_theme_overrides` + snapshots
  `dark_var`; the footer is **[Reset all] … [Save and Close]
  [Cancel]**; ESC and window-close route to Cancel which
  re-applies the snapshot via `replace_theme_overrides`. Audit
  `theme-editor-save-cancel`.
- **Settings dialog primary button renamed to "Save and Close".**
  The Settings dialog `OK` button (`gui/dialogs.py`) is now
  `Save and Close` to match the dialog-button-paradigm sweep used
  elsewhere (Watchlists, Configure Local Data, Configure
  Credentials, Manage Indicators). Behaviour unchanged. Audit
  `dialog-button-paradigms`.
- **Hover price badge keeps 2-decimal precision on price panes.**
  The matplotlib log-axis `_fmt_price` formatter trimmed trailing
  zeros so `$172.50` rendered as `$172.5` (and other tickers'
  hover badges lost their second decimal too). `gui/interaction.py
  ::_format_price_for_label` is now kind-aware via
  `_ax_candle_map.get(ax)`: price axes force `f"{v:,.2f}"`,
  volume axes keep the major formatter's `format_data_short` so
  on-tick `1.2M` / `987K` parity is preserved. Audit
  `hover-price-2-decimals`.
- **Ctrl+H / Alt+H drawing placement now fires even when the cursor
  cache is stale.** `_on_alt_h_placement` previously no-op'd when
  `_last_cursor_px` was `None` (the user hadn't moved the mouse over
  the chart since the most recent re-render). New
  `_resolve_cursor_px_fallback` helper translates
  `winfo_pointerxy()` into matplotlib figure pixels and feeds the
  axes-under-cursor lookup so the line lands at the current pointer
  position regardless of motion-event history. See `app.spec.md`
  §Horizontal-line drawings.
- **Alt+H no longer opens the Help menu.** The Help menubar cascade
  is now added with `underline=-1`, suppressing Tk's default
  first-letter Alt mnemonic on Windows. Alt+H is freed up for
  `_on_alt_h_placement` (matching the original spec intent), and
  the keystroke is now bound on the root via `bind_all("<Alt-h>")`
  + `<Alt-H>` alongside the existing Ctrl+H bindings. Audit ID
  `help-menu-alt-h-no-mnemonic`. See `gui/help_menu.spec.md`.

### Security

Comprehensive security-audit remediation. All 14 findings from the
spawned `claude-opus-4.7-xhigh` audit agent were addressed and
locked in by ~70 new unit tests across 8 new test files.

- **C1 — Pickle → JSON candle + event caches.** `disk_cache.py` and
  `events/cache.py` no longer use `pickle`. Each candle file is now
  a JSON Lines stream (`<source>__<ticker>__<interval>.jsonl`) and
  each event bundle is a single JSON object (`<source>__<ticker>.json`,
  carrying `"schema": 1`). NaN gap candles round-trip as `null` ↔
  `math.nan` so the format is strict-JSON valid. `pickle.load` is
  arbitrary-code-execution by design and any same-user-writable
  `.pkl` (malware, support-handoff cache, tampered backup) would
  have executed on the next chart open. The new format parses with
  zero code execution.
- **C1 — One-shot legacy-pickle purge.** `paths._purge_legacy_pickle_caches`
  unlinks every `*.pkl` in the cache root and `events/` subdir on
  first launch after the upgrade. Symlinks are unlinked (not
  followed) so a planted `link → /etc/shadow` cannot trick the
  purge into deleting something outside the cache. The two legacy
  filesystem migrations in `paths.py` also gained symlink guards
  for the same reason.
- **H1 — Indicator loader docstring + spec.** The user-supplied
  Python indicator loader (`indicators/loader.py`) had a docstring
  that read like a sandbox claim. Rewritten end-to-end as
  "defense-in-depth, NOT a security sandbox; treat user indicator
  modules as fully-privileged code". Matching update to
  `indicators/loader.spec.md`.
- **H2 — Polygon `?apiKey=…` query param → `Authorization: Bearer`
  header.** `data/polygon_source.py::_http_get_aggs` now sets the
  Bearer header instead of inlining the key into every URL. The
  legacy URL pattern leaked the key into any diagnostics bundle,
  any crash dump, and (worst case) into HTTP server access logs of
  any 30x-redirect target. Tests in
  `tests/unit/data/test_polygon_bearer.py` pin this.
- **H3 / L2 — `_update_check.py` HTTP hardening.**
  `_fetch_release_info` now (a) caps the response body at
  `64 * 1024` bytes via `resp.read(64*1024)`, defending against a
  malicious or compromised release-info endpoint streaming
  unbounded JSON to OOM the chart, and (b) short-circuits to
  `None` unless `urlparse(url).scheme` is exactly `http` or `https`,
  blocking `file://`, `gopher://`, `ftp://` and other stdlib
  `urlopen` schemes that would be content-policy-bypass surface if
  an attacker could control the env var.
- **I4 — Shared credential-safe HTTP opener.** New module
  `data/_http.py` exposes `credentialed_opener()` (lazy singleton)
  and `MAX_RESPONSE_BYTES = 8 * 1024 * 1024`. The opener installs
  `_StripCredentialsOnRedirect` which removes
  `Authorization`/`apca`/`key`/`secret`/`token`-shaped headers on
  cross-host 30x redirects (same-host redirects keep headers so
  path-only vendor redirects don't break auth). Adopted by
  `data/polygon_source.py`, `data/alpaca_source.py`,
  `data/schwab_auth.py::_post_token`, and
  `streaming/schwab.py::fetch_streamer_info`. Tests in
  `tests/unit/data/test_http_redirect_strip.py` lock the behaviour.
- **M1 — DPAPI entropy threaded through `pOptionalEntropy`.**
  `_dpapi.py` previously passed `_ENTROPY_DESC` as `szDataDescr`
  (second arg — UI metadata DPAPI ignores). The new code passes it
  as `pOptionalEntropy` (third arg — actively mixed into key
  derivation). Descriptor bumped `v1 → v2` so any pre-fix blob
  fails to decrypt; users are surfaced a `decrypt_error` warning
  via `status_log.warn` and re-enter credentials once.
- **M2 — Status log + diagnostics bundle redactor.** New
  `diagnostics.redact_log_line(line)` with three regexes
  (`_BEARER_RE`, `_BASIC_RE`, `_SECRET_URL_RE`) and a `_read_and_redact`
  helper. `status.py::_emit` calls the redactor BEFORE writing to
  any of the four sinks (Tk bar, in-memory history, daily
  on-disk log, stdout). The diagnostic bundle now redacts both
  `logs/` and `crashes/` (was only logs before) via
  `zf.writestr(..., _read_and_redact(p))`. Bundle README updated to
  honestly state what the redactor catches and what it does not.
- **M3 — `os.system` → `subprocess.Popen` for "Open log file"
  action.** `status._on_open_log` now uses
  `subprocess.Popen([cmd, str(path)], stdout=DEVNULL, stderr=DEVNULL,
  close_fds=True)` so any future code path that put user-controlled
  text into the path cannot become a shell-injection surface.
- **M4 — OAuth `state` CSRF check on Schwab login.**
  `data/schwab_login.py::build_authorize_url` gained an optional
  `state=` kwarg; new `extract_state()` helper; `main()` now
  generates `secrets.token_urlsafe(24)` per attempt and rejects the
  token exchange via `secrets.compare_digest` if the echoed state
  mismatches. Mitigates the classic OAuth login-CSRF where an
  attacker tricks the operator into pasting an attacker-initiated
  code that binds the attacker's broker account to the operator's
  local token cache.
- **M5 — Shared opener adopted by Alpaca + Schwab + Schwab streamer.**
  See I4 above; tracked separately because the M5 finding
  enumerated each affected vendor.
- **L3 — Response cap on `tools/refresh_exchange_lists.py`.**
  `_http_get` now caps `r.read(_MAX_FEED_BYTES)` at 16 MB.
  Real NASDAQ Trader feeds are ~200 KB; a malicious or compromised
  feed cannot OOM the snapshot-refresh CLI.
- **L4 — CSV injection prefix-quote in
  `tools/refresh_exchange_lists.py`.** New `_safe_csv_cell()`
  prefixes a single quote when a cell's leading character is one of
  `("=", "+", "-", "@", "\t", "\r")`. Applied to all four columns
  (`Symbol`, `Name`, `Exchange`, `SnapshotDate`) so a hostile vendor
  name like `=cmd|'/c calc'!A1` cannot fire as a formula when the
  CSV is opened in Excel/LibreOffice.
- **L5 — `prime_environment_from_dpapi` returns explicit sentinel.**
  Was `bool`; now returns one of `"loaded" / "missing" /
  "dpapi_unavailable" / "decrypt_error" / "io_error" /
  "import_error"`. `app.py::main()` captures the sentinel and
  surfaces `decrypt_error` / `io_error` via `status_log.warn(...)`
  so the user sees a clear, actionable message after the entropy
  bump or after a corrupted blob (rather than silently launching
  with no broker creds available).

New / updated specs: `data/_http.spec.md` (new),
`disk_cache.spec.md`, `events/cache.spec.md`, `paths.spec.md`,
`_dpapi.spec.md`, `_update_check.spec.md`,
`data/polygon_source.spec.md`, `data/alpaca_source.spec.md`,
`data/schwab_auth.spec.md`, `data/schwab_login.spec.md`,
`streaming/schwab.spec.md`, `status.spec.md`,
`diagnostics.spec.md`, `gui/credentials_dialog.spec.md`,
`indicators/loader.spec.md`. `docs/SPEC_INDEX.md` updated to add
the new `data/_http.spec.md` row.

### Added
- **Startup self-heal for yfinance's `tkr-tz.db`.** Concurrent
  access to yfinance's tiny ticker→timezone SQLite cache from a
  parallel Python process (e.g. pytest running while the live app
  is open) corrupts the file, after which yfinance returns the
  misleading `Ticker '...' not found` for every uncached symbol —
  manifested in this codebase as the bug where space-cycling to a
  ticker without a local `.jsonl` disk cache (INTC in the
  shipped default watchlist) appeared to "do nothing". `ChartApp.
  __init__` now wipes `platformdirs.user_cache_dir("py-yfinance")/
  tkr-tz.db` (and its `-journal` / `-wal` / `-shm` sidecars) on
  every launch via the new `paths.wipe_yfinance_timezone_cache()`.
  The file rebuilds in 5–10 cheap HTTP round-trips on first use,
  so launch latency is unchanged in steady state. `cookies.db`
  (session reuse) is deliberately left alone. Symlink-safe (a
  planted `tkr-tz.db → /etc/shadow` is unlinked, not followed).
  9 new unit tests in `tests/unit/test_paths_wipe_yfinance.py`.
- **Watchlist live poll loop.** Watchlist tickers (e.g. INTC in the
  default `(AMD, NVDA, INTC, AAPL, MSFT)` pin) now self-heal after a
  transient yfinance fetch failure. A recurring background tick
  re-runs `_preload_watchlist` + `_preload_watchlist_daily` every
  `watchlist_poll_interval_sec` seconds (default **60**). Outside US
  regular trading hours (09:30–16:00 ET, weekdays) the effective
  interval is multiplied by `watchlist_poll_offhours_multiplier`
  (default **5×**, so 5 minutes off-hours). The tick is sandbox-aware
  — it skips the preload body during a replay session but still
  re-arms so polling resumes immediately on sandbox exit. Set
  `watchlist_poll_interval_sec` to 0 to disable. A floor of 5 seconds
  on the effective delay defends against misconfiguration causing
  tight-loop spam. Cache-fresh tickers short-circuit at zero HTTP
  cost so a fully-cached watchlist during RTH costs nothing per tick.
  - Companion fix: **orphan-snapshot recovery.** When the disk-cache
    is fresh (so the existing cache-miss check skips re-fetch) BUT
    the in-memory `_watchlist_snapshot` row is missing `last` /
    `change_1d` / `pct_1d` (e.g. after sandbox exit cleared the
    snapshot, or an earlier worker fetched bars but the dict write
    was lost), `_preload_watchlist` and `_preload_watchlist_daily`
    now rebuild the missing fields directly from the cached series
    (`cached[-1].close` and `cached[-2].close`) and trigger a
    repaint. Previously the row sat empty forever until the user
    switched the chart to that ticker.
  - Two new tunables in `defaults.py`: `watchlist_poll_interval_sec`
    (int, 0–3600, default 60) and `watchlist_poll_offhours_multiplier`
    (float, 1.0–60.0, default 5.0).
  - 22 new unit tests in `tests/unit/gui/test_watchlist_poll.py`
    cover the RTH boundary detection, off-hours throttle math, 5s
    floor, idempotent arming, sandbox-skip-but-rearm, and both
    orphan-recovery branches.
- **Full-exchange universe baskets — NYSE & NASDAQ.** Sandbox →
  Prepare Universe Data… now offers two new built-in baskets
  alongside the existing S&P 500 and Nasdaq-100 / QQQ:
  **NYSE — all common stocks** (~2,088 symbols, Big Board /
  `Exchange='N'` only) and **NASDAQ — all common stocks**
  (~2,894). Snapshots ship as `tools/{nyse,nasdaq}.csv` with a
  canonical 4-column schema (`Symbol,Name,Exchange,SnapshotDate`);
  curation (drop preferreds, warrants, units, rights, ETFs, test
  issues, deficient / bankrupt names) happens at snapshot-build time
  inside a new CLI `tools/refresh_exchange_lists.py` that fetches
  NASDAQ Trader's `nasdaqlisted.txt` + `otherlisted.txt`, applies
  filters, and patches `NYSE_LAST_REFRESHED` / `NASDAQ_LAST_REFRESHED`
  constants in `baskets.py` in place. **Dialog UX**: radios are now
  grouped into three LabelFrames (Index constituents / Full exchange
  listings / Custom watchlist); each radio shows the snapshot date
  and approximate symbol count; a reactive *Estimated: ~N symbols ·
  intervals · ≈wall-time · disk-size* line below the interval
  selectors recomputes on every form change; an **amber survivorship
  banner** appears under the NYSE/NASDAQ radios warning that the
  snapshots are point-in-time (companies that delisted before the
  snapshot date are missing from past-anchored replays). The Cancel
  button is renamed **Stop (safe to resume)** with a matching status
  message — pressing it preserves all bars already on disk and unions
  the partial run with any prior manifest so a re-Start picks up
  exactly where it stopped (via the disk-cache short-circuit). New
  user guide `docs/UNIVERSES.md`; `docs/ONBOARDING.md` updated to
  point to it.
- **`baskets.py` public API extended.** New `nyse_symbols()` /
  `nasdaq_symbols()` loaders, new `NYSE_LAST_REFRESHED` /
  `NASDAQ_LAST_REFRESHED` date constants, new
  `BUILTIN_BASKET_REFRESHED_DATES` map for per-radio refresh-date
  rendering, and new `FULL_EXCHANGE_BASKETS` frozenset (used by the
  dialog to gate the survivorship banner — future fourth/fifth
  full-exchange baskets get the treatment automatically). Shared
  `_load_symbols_csv` helper centralises CSV parsing across the three
  CSV-backed loaders.

### Fixed
- **ChartStack toggle no longer steals from the watchlist.** When
  enabling the ChartStack panel, the notebook (watchlist / Sandbox /
  OHLC / entries / exits tab strip) used to shrink by ~30 % on first
  toggle and wildly drift across sessions because the layout was
  driven by a persisted-drag `geometry_store.restore_sash` default
  that defaulted the chart to 70 % of the remaining width. The toggle
  now uses the same hardcoded layout as startup via the new
  `constants.compute_main_paned_sashes(main_w, chartstack_visible=...)`
  helper: notebook width is pinned at
  `max(280, main_w - int(main_w * CHART_PANE_STARTUP_RATIO))` regardless
  of ChartStack state; the chart absorbs the chartstack column's
  pixels. Geometry-store sash persistence for both the
  `main_paned_3pane` and `main_paned_2pane` keys is bypassed
  end-to-end (consistent with the existing "wide-on-launch" 2-pane
  behaviour). Mid-session drags still work — they just don't survive a
  restart. 14 new tests in `tests/unit/test_main_paned_layout.py`
  pin the layout math at the four common monitor sizes (1280, 1920,
  2560, 3840) plus narrow-window defensive clamps.
- **`preload/service.py` rate-limit gap on the happy path.** The
  per-symbol rate-limit sleep previously only fired *between retries*
  inside `_run_one`. A sequence of N first-try successes back-to-back
  fired N HTTP requests with zero inter-op delay — fine at SP500
  scale, but at full-exchange scale (~5,000 unbroken requests) this
  would cliff into yfinance's CDN throttle. The outer loop in
  `preload_universe` now calls `sleep_fn(cancel_event, rate_limit_s)`
  after every `_run_one` return whose status is `"fetched"`. Cache
  hits (`"l1_hit"` / `"disk_hit"`) and failed/cancelled outcomes
  still incur zero post-op sleep — the gate is fetched-only by
  design.
- **`preload/manifest.py::build_from_loaded` no longer destructively
  overwrites prior manifests.** The function now accepts an optional
  `previous: Optional[UniverseManifest] = None` kwarg; when supplied,
  per-symbol interval sets are unioned with the prior run's and
  prior-only symbols are carried forward unchanged. The dialog call
  site loads the existing manifest for the UID and threads it
  through. Without this, re-running with a smaller interval set
  (e.g. `5m` after a previous `5m+1d` run) silently dropped the
  on-disk bars from manifest coverage even though the pickles were
  still present, making strict-offline gating reject symbols that
  were actually loaded. Pass `previous=None` to opt out (e.g. tools
  that intentionally rebuild from scratch).
- **`preload/manifest.py::coverage_for_date` Tk-thread warning
  documented.** The function performs O(N) pickle deserialisation;
  at full-exchange scale (2,000+ symbols) this takes 5–15 s warm /
  30–60 s cold and would freeze the Tk thread. The docstring + spec
  now explicitly state that callers must dispatch off-thread (worker
  + `after()` poller) for N > 500. No behavioural change.

- **BYOD (Bring Your Own Data) — local CSV data source.** Two
  symmetric flows let users round-trip the app's normalized OHLCV bars
  through CSV files on disk. **Import**: `Tools → Configure Local
  Data…` opens a dialog where the user enables BYOD and lists one or
  more "roots" (folders). Each top-level subfolder of each root
  becomes a registered data source named `<root_name>-<subfolder>`
  that appears in the toolbar source selector alongside the built-in
  sources (`yfinance`, `polygon`, etc.). Built-in source names are
  reserved and cannot be shadowed. **Export**: `Tools → Export Bars
  to CSV…` opens a `Treeview` of every `(source, ticker, interval)`
  tuple in the disk cache with a checkbox column, Select All / Select
  None toggles, and a destination folder picker; writes
  `<dest>/<SOURCE>/<TICKER>_<INTERVAL>.csv` atomically via temp file
  + `os.replace`. **Schema**: strict canonical lowercase
  `timestamp,open,high,low,close,volume`; ISO-8601 timestamps with
  explicit timezone offset required (naive timestamps rejected with
  status-bar error linking to `docs/LOCAL_DATA.md`); duplicate
  timestamps drop with warning, rows sort ascending. **Cache
  semantics**: BYOD-registered sources are opted out of the on-disk
  pickle cache (via new `disk_cache.mark_no_persist`) so the user's
  CSVs are the only persistent storage — no stale pickles accumulate
  alongside them. BYOD sources still participate in the in-memory LRU
  for performance. **Root name validation**: alphanumerics +
  underscores only (no hyphens — reserved for combobox separator).
  **Settings**: persisted under `local_data` key as
  `{"enabled": bool, "roots": [{"name": str, "path": str}, ...]}`.
  New modules: `data/local_source.py` (strict CSV parser + fetcher
  factory + discovery), `data/local_export.py` (atomic writer +
  multi-entry export), `gui/local_data_dialog.py` (configure
  dialog), `gui/export_cache_dialog.py` (export dialog). New helper
  `disk_cache.list_entries()` enumerates cache tuples for the
  exporter. New user-facing guide `docs/LOCAL_DATA.md`. Tools-menu
  integration via `gui/menu_builder.py` + `gui/help_menu.py` mixin
  callbacks. Wired through `app.py::_refresh_data_source_combobox` +
  `gui/toolbar_controller.py::set_sources` so the dialog Save
  refreshes the combobox immediately. 125 new unit tests across 6
  test files. See `docs/LOCAL_DATA.md` for end-to-end workflow.

- **Volume time-of-day shading (1d volume bars).** Opt-in visual
  overlay (`volume_tod_enabled`, default OFF) that helps the trader
  compare today's session-so-far volume against historical sessions
  at the same minute-of-day, without a numeric indicator. Each 1d
  volume bar gains a darker outline envelope at the bar's full-day
  height and a solid fill scaled by *minutes elapsed in the day / RTH
  span*, computed from per-day 5-minute intraday data. The reference
  time-of-day comes from the sandbox replay clock when a sandbox
  session is active, else wall-clock; sandbox-rewind pre-open shows
  the empty envelope (decision 12), live wall-clock pre-open
  suppresses the overlay entirely (decision 6), post-close latches to
  fully-filled (decision 7), and missing intraday data degrades the
  bar to the feature-off look (decision 8). A neutral median tick at
  the rolling 20-day full-day-volume median (configurable via
  `volume_tod_median_lookback_days`) anchors visual comparison
  (decisions 14/15/18). RTH-only in v1 (decision 5). New module
  `gui/volume_tod_overlay.py` (pure-functional math + draw layer,
  mirrors the `gui/events_overlay.py` pattern); new
  `rendering.darker_shade` sibling of `brighter_shade` produces the
  envelope's same-hue-darker frame colour. Wired into `ChartApp` via
  `set_volume_tod_enabled`, `_now_ms_for_slot`,
  `_render_volume_tod_for_slot`, and friends; surfaced in the
  Settings dialog as a checkbox at row 5 with live-preview +
  cancel-revert (rows below shifted +1). Visual-only — nothing leaks
  into `SessionResult`, journal, or engine state, so flipping the
  toggle mid-session leaves engine output byte-identical. Smoke
  check `check_b68_volume_tod_shading` covers math correctness, RTH
  filter, all 7 decision branches, median-tick soft floor, default
  OFF, settings round-trip, and engine determinism; 16 new unit
  tests in `tests/unit/test_volume_tod_overlay.py`.
- **Earnings & dividends ambient context.**New `tradinglab/events/`
  subpackage adds historical earnings prints (with EPS estimate /
  actual / surprise) and dividend history (cash, special, spin-off,
  splits) as a sandbox ambient layer. Glyphs render at the bottom edge
  of the price pane (TradingView-style; mixed
  `transData`/`transAxes`) on past events; forward earnings show as
  an absolute date in normal mode and a relative "T-N trading days"
  badge in blind mode. yfinance + deterministic synthetic providers;
  per-(source, ticker) disk cache. Engine gains a corporate-action
  tick phase (between MAE/MFE roll and mark-to-market) that credits
  cash dividends to open long positions, applies stock-split quantity
  rescales (with inverse `avg_cost` rescale to preserve cost basis),
  and persists the applied facts as new additive `cash_adjustments` /
  `quantity_adjustments` lists on `SessionResult` (engine version
  stays `"sandbox-1d"`). `PreTradeEntry` gains 6 additive proximity
  fields populated at submit-order time; the Performance View grows
  a per-event-proximity rollup table via
  `backtest.performance.build_proximity_aggregates`. Default
  `earnings_window_days = 10`. Smoke checks `check_b60`–`check_b67`
  lock in the protocol + registry, engine phase, blind redaction,
  master-timeline-frozen invariant, save/load round-trip, disk cache,
  cycle token bump, and provider-drift determinism. The replay-layer
  events prefetch routes future completion through the existing
  `_await_future_on_tk` helper (NOT `fut.add_done_callback` +
  `app.after` from a worker thread — that pattern is documented in
  `app.spec.md` as `tk.createcommand` unsafe on this Python/Tk build).
- **Versioning + release infrastructure.** Single source of truth at `src/tradinglab/_version.py`; `pyproject.toml` reads it dynamically via `[tool.setuptools.dynamic]`. New `__version__`, `version_string()` re-exported from `tradinglab`. CLI gains `--version` / `-V` and `--help` / `-h` flags. Window title now displays `TradingLab v<version>`. New `tools/bump_version.py` script (`patch` / `minor` / `major` / explicit / `--show`) updates the version file and prepends a CHANGELOG stub. New `_build_info.py` (gitignored) is generated by the build script and embeds git commit + build date into release artifacts via `_version.version_string()`.
- **Standalone Windows packaging.** Hand-tuned `TradingLab.spec` (deterministic PyInstaller config) bundles the entry-strategy templates + config samples + `.env.example`, prunes unused matplotlib backends, and produces a windowed `TradingLab.exe`. New `tools/build_exe.ps1` orchestrates a clean-venv build (pip install runtime deps + PyInstaller, embed git metadata, run PyInstaller, smoke-test the exe via `--version`, zip as `TradingLab-<version>-win64.zip`). Frozen-bundle-aware path resolution via new `tradinglab._resources.resource_path()` helper; `gui/entries_tab.py` updated to use it so templates resolve in both source and frozen modes.
- **GitHub Actions release workflow.** `.github/workflows/release.yml` triggers on `v*.*.*` tag pushes (or manual dispatch), runs the build script on `windows-latest`, uploads the zip as a workflow artifact, and publishes it to GitHub Releases via `softprops/action-gh-release@v2`.

### Added
- Compare-toggle drill-down ylim safety net (`check_d34`): `_on_compare_toggle` now calls `_autoscale_y_to_visible()` after `_render()` in both cache-hit and cache-miss paths, mirroring `_pan_end`'s click behavior. Prevents the compare panel from loading with a stale Y axis after enabling compare while a primary drill-down is active.
- Pixel-level regression infrastructure (`check_d32`/`check_d33`) — read `canvas.buffer_rgba()` to count bull/bear candle pixels across an interaction matrix, catching blank-screen regressions at the pixel level.
- Pan-end blit-bg invalidation (`check_d31`) — `_pan_end` clears `_blit_bg` so a candle-less snapshot captured during `_pan_setup_blit` can't be restored by the next hover.
- Top-left always-on OHLCV / %change readout strip (`check_d28`) — TradingView-style data strip per price axes, follows cursor or falls back to the latest non-gap bar.
- Floating value label on horizontal crosshair (`check_d27`) — pinned to the LEFT spine of every price + volume axes, formatted via the axis's installed formatter.
- Mouse-wheel zoom (`check_d25`) — cursor-anchored, TradingView-style; user-configurable invert via `settings.json["scroll_zoom_invert"]` (`check_d26`).
- Drill-down day persists across ticker change (`check_d20`).
- Reset view → 1d (`check_d19`).
- Display timezone setting (`check_d18`).
- 1d→5m drill-down on double-click (`check_d17`), including compare-panel drill-down.
- Customizable theme overrides + startup defaults (`check_d14` / `check_d16`).
- Pinned watchlist sub-tabs with parallel preload (`check_d13` / `check_d15`).
- Companion-interval prefetch (`check_d12`).
- Async user-load offload to dedicated `_fetch_executor` (`check_d24`).
- H1/H2/H3/H5/H6/M2/M4 perf optimizations.

### Project structure
- Migrated to `src/` layout
- Smoke tests moved under `tests/smoke/`
- `pyproject.toml`, `.gitignore`, GitHub Actions CI added

## [0.1.0] - Initial development

Early prototype; see commit history.
