# Onboarding — TradingLab

Welcome! This guide walks you from "fresh clone" to "comfortable user".
It assumes you've never opened the app before and covers exactly what
you see on the screen, in the order you'll see it.

If you're impatient, skip to **[Five-minute quickstart](#five-minute-quickstart)**.

---

## Table of contents

1. [Install](#install)
2. [Five-minute quickstart](#five-minute-quickstart)
3. [Persistence model](#persistence-model)
4. [The window, one piece at a time](#the-window-one-piece-at-a-time)
5. [Loading your first chart](#loading-your-first-chart)
6. [Indicators](#indicators)
7. [Display modes (View menu)](#display-modes-view-menu)
8. [Mouse and keyboard cheat-sheet](#mouse-and-keyboard-cheat-sheet)
9. [Watchlists](#watchlists)
10. [Compare mode](#compare-mode)
11. [Drill-down zoom (1d → 5m)](#drill-down-zoom-1d--5m)
12. [Sandbox bar-replay](#sandbox-bar-replay)
13. [Scanner](#scanner)
14. [Configuration files](#configuration-files)
15. [Themes and visual tweaks](#themes-and-visual-tweaks)
16. [Where your data lives](#where-your-data-lives)
17. [Troubleshooting](#troubleshooting)
18. [Where to go next](#where-to-go-next)

---

## Install

```bash
git clone https://github.com/pmok3/tradinglab.git
cd tradinglab
pip install -e .[dev]
python -m tradinglab
```

Or, after install, just:

```bash
tradinglab
```

Requirements: Python 3.10+, an internet connection (for live yfinance
data), and a display (Tkinter is a desktop GUI). No database, no API
key, no signup.

---

## Five-minute quickstart

1. Launch the app: `python -m tradinglab`.
2. **Type a ticker** while the chart has focus (click anywhere on the
   chart first if it doesn't already — the *Ticker:* readout in the
   toolbar is a display-only label, not an input). Try `AMD`. Press
   **Enter** to commit. The chart loads.
3. **Scroll the mouse wheel** over the chart — scroll DOWN to zoom IN,
   UP to zoom OUT. The bar under your cursor stays anchored.
4. **Click and drag** to pan. **Double-click** any 1d candle to drill
   down into 5-minute bars for that day.
5. **File → Load Configuration…** and pick `config/example_config.json`
   to see a sample setup.
6. **Watchlists → Load Watchlists…** and pick `config/example_watchlists.json`
   to populate the *Watchlist* tab with sample lists.

That's the core loop. Everything else is optional polish.

---

## Persistence model

TradingLab follows an **explicit-save** model — like a text editor,
not a database. Watchlists, settings, and sandbox state are persisted
only when the user (or a smoke check) calls a save method explicitly.

What this means in practice:

- Fresh launches start empty (or from whatever JSON file you load via
  `File → Load Configuration…` / `Watchlists → Load Watchlists…`).
- Edits live in memory. The window title shows `*` (trailing
  asterisk) when there
  are unsaved changes.
- Nothing auto-writes back to disk. To persist, use **File → Save…**
  (writes to the loaded path) or **File → Save … As** (or
  **Watchlists → Save Watchlists** for the watchlist set).
- The disk *cache* (fetched candles) is separate — it's auto-managed
  for performance, but it isn't your data.

Want auto-load on launch? Put your config path in
`startup_defaults` and load it once; subsequent launches read from it.

---

## The window, one piece at a time

When the app opens, you'll see (top-down, roughly):

- **Menu bar** — `File` (Load/Save Configuration, Load/Save Watchlists), `Indicators` (add indicators per scope, save/load presets, clear all), `Sandbox` (bar-replay sessions, save/load, performance review), `View` (Heikin-Ashi Candles, Highlight Flat HA Candles, Highlight Key Bars, ChartStack, Theme…), `Tools` (Configure Credentials…, Download Replay Data…, Status History…, Reveal Data Folder, Restore Default Templates…), `Help` (About, Getting Started…, Keyboard Shortcuts…, Documentation Library…, View Online Docs, Reveal Data Folder, Check for Updates…, Export Diagnostic Bundle…, Reset & Quit (purge data folder)…).
- **Toolbar** — left side shows the *Ticker:* and *Compare:* readouts
  (display-only labels; click on the chart and type to change them
  — see [Loading your first chart](#loading-your-first-chart)) plus
  the *Source* and *Interval* dropdowns and a *Pre/Post* checkbox;
  right side has the **Reset view (R)**, **Settings (Ctrl+,)**, and
  **Watchlists (Ctrl+L)** buttons. The theme toggle lives in the
  *Settings* dialog (and on the *View* menu), not on the toolbar.
- **Notebook tabs** — *Chart*, *Table*, *Watchlist*, *Scanner*. The
  Chart tab is selected by default; *Scanner* hosts saved sandbox-driven
  block-tree scans (see [Scanner](#scanner)).
- **Chart area**:
  - **Top-left readout strip** — always-on `O H L C V %chg` snapshot.
    Tracks your cursor X position.
  - **Floating price label** — pinned to the right edge of the price
    axis, follows the crosshair. Same widget appears on the volume
    axis below.
  - **Price panel** (top, ~2/3 of vertical space) — candles + any
    indicator overlays you've added (SMA, EMA, Bollinger Bands,
    Keltner Channels, VWAP/AVWAP, MACD, Chandelier Stops, RSI, ATR,
    RVOL/RRVOL, ADX, SMI, LRSI). See [Indicators](#indicators).
  - **Volume panel** (bottom) — green/red bars matching the candle
    direction.

The *Table* tab shows the same data as a sortable OHLC grid. The
*Watchlist* tab hosts pinned watchlist sub-tabs (see [Watchlists](#watchlists)).

---

## Loading your first chart

1. **Click anywhere on the chart canvas** to focus it, then type any
   symbol (case insensitive). The toolbar *Ticker:* readout shows
   what you've typed so far; the chart canvas also shows a small
   live preview of the buffer.
   Examples: `AAPL`, `AMD`, `BTC-USD`, `^GSPC` (S&P 500 index).
2. Press **Enter** to commit and load the chart (or **Esc** to
   cancel the in-progress buffer). **Backspace** deletes the last
   letter. Digits and most punctuation are ignored deliberately —
   only `A-Z`, `.`, `_`, and `-` are accepted so a stray numeric
   keystroke can't start a phantom symbol buffer.
3. Pick an **Interval** from the dropdown:
   - `1m`, `5m`, `15m`, `30m`, `60m` — intraday (live-streaming)
   - `1d` — daily candles (longer history)
4. Pick a **Source**:
   - `Live` — fetches the latest from yfinance.
   - `Sample` — bundled offline sample data, useful with no internet.

If the ticker isn't found, you'll see a friendly error in the status
area (or a stack trace in the console — see
[Troubleshooting](#troubleshooting)).

---

## Indicators

The **Indicators** menu drives every overlay/study on the chart. The
catalog is registry-driven (`indicators.base.INDICATORS`), so the menu,
the parameter dialog, and the persistence layer all stay in sync.

### Catalog (built-in)

| Family | Members |
|---|---|
| Trend / overlay | SMA, EMA, Bollinger Bands, Keltner Channels, VWAP, AVWAP, Chandelier Stops |
| Momentum / oscillator | RSI, MACD, ADX, SMI, LRSI |
| Volatility | ATR (modes: rolling RMA, time-of-day) |
| Volume / participation | RVOL Simple Rolling, RVOL Time-of-Day, Cumulative-Day RVOL, RRVOL (relative-to-comparison-symbol) |

Each indicator declares a typed parameter schema (`ParamDef`) which
auto-generates the *Add Indicator* dialog — no per-indicator UI code.

### Adding an indicator

1. **Indicators → (pick one)**. The submenu for each indicator is a
   *scope picker* — **Primary**, **Compare**, or **Both** — so the
   same study can be applied to one or both panels of compare mode.
2. The parameter dialog opens. Tweak length, source column, line
   style, color, etc. Click **Add Indicator**.
3. The overlay appears immediately. Multiple instances of the same
   indicator (e.g. SMA-20 *and* SMA-50) coexist — each is its own row
   in the dialog.

### ATR modes (rolling vs time-of-day)

ATR has a `mode` parameter:

- **`rolling`** (default `length=14`) — classic Wilder RMA of true range.
- **`tod`** (default `length=20` *sessions*) — for each intraday bar at
  time-of-day `(h:m)`, averages the TR of the same `(h:m)` slot across
  the prior `length` regular sessions. Pairs with the **Time-of-Day
  RVOL** indicator and the **key-bar** rule on the View menu (see
  below). On non-intraday charts, ToD falls back to a fixed 20-bar
  rolling mean for reproducibility.

### Indicator presets

Save the entire indicator stack (every active study + scope + params)
as a named preset:

- **Indicators → Save Preset…** — give it a name (e.g. *RDT default*).
- **Indicators → Load Preset → (name)** — replaces the current stack.
- **Indicators → Delete Preset → (name)** — removes from the catalog.
- **Indicators → Clear All** — strips every indicator from the chart
  (preserves saved presets).

Presets persist on disk under the user-data directory; they are
**not** in your config JSON, so they travel with the install rather
than the configuration file.

---

## Display modes (View menu)

The **View** menu hosts purely visual toggles — they change *what you
see* without altering the underlying OHLCV, indicator math, or scanner
inputs.

### Heikin-Ashi Candles

`View → Heikin-Ashi Candles` substitutes the chart's wick/body draw
with a [Heikin-Ashi](https://en.wikipedia.org/wiki/Candlestick_chart#Heikin-Ashi_candlesticks)
recurrence: each candle's open is `(prev_HA_open + prev_HA_close) / 2`
and the close is the average of OHLC. The result smooths trend
continuation visually while *every other surface* (volume bars,
indicators, hover, OHLCV readout, table tab, autoscale) keeps reading
the real candles. Persisted under settings key `"heikin_ashi"`.

If you want to *scan* on Heikin-Ashi properties (e.g. "5 consecutive
flat-bottom HA bars"), the Scanner has dedicated `ha_*` builtin fields
that don't depend on this toggle.

### Highlight Key Bars

`View → Highlight Key Bars` renders bars that match the
[r/realdaytrading](https://www.reddit.com/r/RealDayTrading/) **key-bar**
rule as hollow candles (transparent face, bolder edge) so they jump
out of a busy chart. A bar qualifies when **all three** are true:

- True range > 1.0× the time-of-day ATR baseline (intraday) or 20-bar
  rolling TR mean (daily).
- Volume > 1.1× the time-of-day RVOL baseline.
- Body size > 69% of the candle's `H − L` range.

Direction is taken from `close` vs `open`. Persisted under settings
key `"highlight_key_bars"`. Scanner-side coverage is provided by 9
`key_bar*` builtin fields (see [Scanner](#scanner)) that share the
same compute path, so a bar that fires in the scanner is the same bar
rendered hollow on the chart.

---

## Mouse and keyboard cheat-sheet

The single biggest "I had no idea you could do that" set of features
in this app:

### Mouse on the chart

| Action | What it does |
|---|---|
| **Scroll wheel down** | Zoom IN, anchored on cursor (TradingView style) |
| **Scroll wheel up** | Zoom OUT, anchored on cursor |
| **Click + drag** | Pan horizontally |
| **Right-click + drag** | Rubber-band zoom — drag across a subset of bars to frame just that window. Release to commit; the chart zooms to the selected X range and auto-fits the Y axis. |
| **Double-click a 1d candle** | Drill down to 5-minute bars for that day |
| **Double-click again (in drill-down)** | Return to the 1d view |
| **Move mouse** | Crosshair + readout strip + floating price label update live |
| **Right-click on a watchlist sub-tab** | Pin/unpin/reorder context menu |

### Mouse-wheel zoom direction

By default scroll DOWN zooms IN. If you prefer the macOS
"natural-scroll" convention (scroll UP zooms IN), open
**Settings → Mouse-wheel zoom** and tick *Invert*.

### Keyboard

| Key | What it does |
|---|---|
| **Type letters** while the chart has focus | Click-to-type ticker entry. Letters / `.` / `_` / `-` accumulate into the *Ticker:* (or *Compare:*, if the last click was on the compare panel) readout. **Enter** commits and loads the chart, **Esc** cancels the buffer, **Backspace** deletes a letter. |
| **Space** | Cycle to the next ticker in the active pinned watchlist |
| **Alt+H** with cursor on a chart | Drop a horizontal price line at the cursor's price (TradingView-style). Double-click the line to edit color / width / style / label, right-click for "Edit Properties…" or "Delete This Line". Right-click empty chart for "Remove All Drawings on <TICKER>". Lines persist per-ticker across interval changes, primary↔compare swaps, and app restarts. |
| **R** *(`Ctrl+R`)* | Reset view (snaps back to 1d, right-edge default window) |
| **Ctrl+,** | Open Settings dialog |
| **Ctrl+L** | Open Watchlists dialog |

> 💡 **Space-to-cycle**: pin a watchlist (see below), focus the chart
> or watchlist tab, and tap Space repeatedly — you'll flip through every
> ticker in the list without touching the mouse.

---

## Watchlists

Watchlists are named groups of tickers. Up to **5** can be *pinned*,
which makes them appear as always-visible sub-tabs in the *Watchlist*
notebook tab with live last-price + daily-change updates.

### Creating watchlists

1. Click the **Watchlists…** button in the toolbar (or visit the
   *Watchlists* dialog via the same).
2. Click **New**, give the list a name, and **OK**.
3. With the list selected, click **Add** and type a ticker.
4. Click **Pin** to surface the list as a sub-tab.

### Loading sample watchlists

The repo ships `config/example_watchlists.json` with four
ready-to-go lists (Megacap Tech, Semiconductors, ETFs, Crypto). To
load it:

- **Watchlists → Load Watchlists…** → pick `config/example_watchlists.json`.

You should immediately see three sub-tabs (Megacap Tech, Semiconductors,
ETFs) with live prices populating in the background.

### Saving your own watchlists

Watchlists follow the explicit-save model — see
[Persistence model](#persistence-model). Use **Watchlists → Save Watchlists**
or **Watchlists → Save Watchlists As…** to write the JSON to disk. The
manager dialog (Ctrl+L) also has a **Save and Close** button that
persists and dismisses in one click. The format is documented in
[`src/tradinglab/watchlists/manager.spec.md`](../src/tradinglab/watchlists/manager.spec.md).

---

## Compare mode

Comparing two tickers on a shared X axis is a one-click operation:

1. Tick the **Compare mode** checkbox in the toolbar. The chart splits
   into two stacked panels with a shared time axis (primary on top,
   compare below).
2. To set the compare ticker, **click on the compare (lower) panel**
   and type the symbol (e.g. with `AMD` in *Ticker:*, type `NVDA`).
   Press **Enter** to commit. The *Compare:* readout in the toolbar
   updates to show the loaded ticker.

Pan / zoom / drill-down work on whichever panel your cursor is over.
Y-axes auto-fit independently to each panel's visible window.

To turn it off, untick *Compare mode*. The compare panel disappears
and the primary panel reclaims the full vertical space.

---

## Drill-down zoom (1d → 5m)

This is one of the most powerful features and is **completely
undiscoverable** without being told:

1. Load a ticker on the **1d** interval (e.g. `AMD` at `1d`).
2. **Double-click any candle.** The chart switches to **5m** bars
   covering just that day, automatically aligned to market hours.
3. Pan, zoom, hover — same as normal.
4. **Double-click again anywhere** to pop back out to the daily view,
   preserving your prior zoom window.

Tips:
- The drill-down day is "sticky" — if you change the ticker while
  drilled in, the new ticker also opens at that day's 5m view.
- Compare mode works in drill-down too.
- The Reset-view button (top-right of the toolbar) cancels drill-down
  and re-centers on the latest bars.

---

## Sandbox bar-replay

Sandbox is a **bar-by-bar replay** of historical data for discretionary
practice — not a vectorised backtester. You step the clock yourself,
journal every trade as you would live, and review at the end. It's the
single biggest feature in the app you'd never discover from the menu
bar.

### Starting a session

1. **Sandbox → Start session…** opens the start dialog.
2. Pick a **reference symbol** + **interval** — this anchors the
   master clock. (You'll add more tickers mid-session; this one just
   defines the bar cadence.)
3. **Pick a date** or click **Random eligible date** — the eligibility
   list is the set of session dates that have at least
   `daily_lookback_bars` (default 100) of prior daily bars cached, so
   every session starts with proper context.
4. **Blind mode** (checkbox) hides the chosen date and switches on
   auto-cycle. You'll see "(hidden)" in the date field; the dialog
   draws the date itself on Start, and on session-end the next session
   auto-cycles into the same window. Use this for honest practice —
   no peeking at calendars.
5. Set economics: starting cash, slippage (bps), commission per fill.
6. Click **Start**. The chart switches to the chosen session date and a
   **Sandbox panel** opens to the right: clock readout, cash + open
   positions, focus-list, Buy / Sell, Next-bar, End-session.

### During a session

- **N** advances one bar (or click "Next bar (N)" on the panel). The
  clock readout updates; engine ticks fills, MAE/MFE, mark-to-market in
  that order.
- **Type a new ticker** in the regular ticker box (or double-click a
  watchlist row) to **register it mid-session**. Bars older than the
  master clock load instantly; future bars become visible only as the
  clock advances. The master timeline is frozen — adding tickers never
  extends it.
- **Buy / Sell** opens the **pre-trade form**: setup tag, thesis
  (mandatory, non-empty), conviction, size, target, notes. The engine
  will reject submission without a thesis.
- **Every closed round-trip** pops a **post-trade review** modal —
  also mandatory, also non-empty. You cannot dismiss it with the X
  button; this is by design.
- **Set focus** on a ticker (panel list) to drive Buy / Sell against it.
- **Switch to daily interval** during a session via the standard
  interval selector — the chart shows daily bars **strictly before**
  the current session date (capped to `daily_lookback_bars`) for
  multi-timeframe context. Switch back without losing intraday state.

### Ending and reviewing

1. **End session** flattens any open positions at the last close
   (synthetic auto-flat fills, no slippage / commission), runs the
   post-trade review for any auto-flatted positions, and finalises the
   session result.
2. **Auto-cycle** (blind mode) re-draws a fresh date and re-registers
   your reference ticker. Pre-loaded extra tickers must be re-added.
3. **Sandbox → Save Session…** writes a versioned JSON envelope plus
   any captured screenshots into a sibling folder. Versioning is
   strict (engine version `sandbox-1d`) — saves don't migrate forward.
4. **Sandbox → Load Session…** opens a saved session as a read-only
   review window with the full **Performance View** (per-trade table,
   per-setup aggregates).
5. **Sandbox → View Performance…** on a live or just-ended session
   opens the same view without saving.

### Universe data prep

`Sandbox → Prepare Universe Data…` pre-fetches the candle cache for a
chosen ticker universe (e.g. an S&P 500 list) over a date window so
that random-date sessions and Scanner runs don't stall on first load.
The dialog reports per-ticker progress and is restartable. You'll see
a "Run Sandbox → Prepare Universe Data… first." message in any
universe-driven feature that lacks coverage.

Four built-in baskets are available: **S&P 500** (~503 symbols),
**Nasdaq-100 / QQQ** (~105), and the two full-exchange baskets
**NYSE — all common stocks** (~2,088) and **NASDAQ — all common
stocks** (~2,894). Full-exchange runs are bigger commitments — the
dialog shows an estimated wall-clock and disk-size before you press
Start, and a Stop button that is safe to hit at any time (the
manifest is unioned with prior runs so you can resume from where you
stopped). See [`docs/UNIVERSES.md`](UNIVERSES.md) for a full guide to
basket composition, refresh cadence, and the survivorship-bias
caveat that applies to full-exchange replays.

### Setup tags

`Sandbox → Manage Setup Tags…` edits the dropdown of *setup* labels
that appear in the pre-trade journal (e.g. *ORB*, *VWAP reclaim*,
*Key-bar entry*). Tags become a column on the Performance view's
per-setup aggregate table.

### Tips

- Random + blind is the most honest practice mode — you're forced to
  read the chart, not the calendar.
- The pre-trade thesis becomes a column in the performance table —
  write it as if a future you will read it back. (You will.)
- The post-trade review is captured per round-trip, not per fill;
  scaling out of a position counts as one closed trade once the
  position returns to flat.
- For the full controller contract, see
  [`backtest/replay.spec.md`](../src/tradinglab/backtest/replay.spec.md).

---

## Scanner

The **Scanner** notebook tab is a sandbox-driven, block-tree screener.
You compose conditions out of typed builtin fields — including indicator
fields, Heikin-Ashi fields, and the `key_bar*` family — and the runner
evaluates them across the registered universe at the current sandbox
clock.

Conceptually it's the screener wired to the same data + clock as
sandbox replay, so a scan firing at 10:35 ET means *exactly* the bars
the chart is showing at 10:35 ET. There is no live-data scanner mode —
this is by design (consistency with bar-replay practice).

### Layout

- **Library** (left) — the persisted set of `ScanDefinition`s, loaded
  from disk at launch.
- **Sub-tabs** (right) — *open* scans. Only open scans cost compute;
  closed scans live in the library and are free.
- **One sub-tab per scan**:
  - **Block-tree editor** — and/or groups of conditions, each with a
    `field op value` triple (e.g. `rvol_simple > 2.0` AND
    `key_bar = bull`).
  - **Results table** — symbol, fired-at timestamp, and any
    `rank_by` metric. Sortable; double-click a row to load that
    ticker on the chart.
- **Toolbar**: New scan, Save, Delete, Open from library, Close (sub-tab
  only — the scan stays in the library).

### Starting fresh

When the Scanner tab is opened for the first time it shows a single
empty sub-tab — by design, you load (or create) further scans on
demand rather than re-evaluating dozens of stale scans on every clock
tick.

### Builtin field families (high-signal subset)

| Family | Examples |
|---|---|
| OHLCV | `open`, `high`, `low`, `close`, `volume`, `range`, `body` |
| RVOL | `rvol_simple`, `rvol_tod`, `rvol_cum` |
| ATR / volatility | `atr`, `atr_tod` |
| Heikin-Ashi | `ha_open`, `ha_close`, `ha_flat_top`, `ha_flat_bottom`, … |
| Key bars | `key_bar`, `key_bar_bull`, `key_bar_bear`, `bars_since_bull_key_bar`, `last_bull_key_bar_high`, … |
| Trend / overlay | `sma`, `ema`, `vwap`, `bbands_*` |

The full registry is enumerated in
[`scanner/fields.spec.md`](../src/tradinglab/scanner/fields.spec.md).
Validation errors (unknown field, bad rank-by, missing param) surface
inline in the editor.

### Persistence

Saved scans live under the user-data directory alongside indicator
presets — they are not in your config JSON.

---

## Configuration files

TradingLab follows the explicit-save model for configuration too —
see [Persistence model](#persistence-model). All configuration is
opt-in, like a text editor.

### Loading configuration

- **File → Load Configuration…** opens a JSON file picker.
- The starter file lives at [`config/example_config.json`](../config/example_config.json) and is heavily annotated with `_comment` keys (which JSON ignores on import — they're just there for you to read).
- Fields you might want to tweak: `display_tz`, `scroll_zoom_invert`, `default_window_bars`, `startup_defaults` (sets the initial ticker / interval / theme).

### Editing live

The Settings dialog (gear / **Settings** button in the toolbar) edits
the in-memory config. Changes apply immediately. They do **not** save
to disk until you choose to.

The window title shows a trailing `*` when you have unsaved changes.

### Saving

- **File → Save Configuration** writes back to the loaded file.
- **File → Save Configuration As…** writes to a new path.

### Sharing

Because everything's a plain JSON file, sharing your setup is just
sending the file. Same model as `.editorconfig` or `.vscode/settings.json`.

The full key catalog with validation rules is in
[`src/tradinglab/defaults.py`](../src/tradinglab/defaults.py); the
README's [Configuration section](../README.md#configuration) has a
table.

---

## Themes and visual tweaks

- **Toggle light/dark**: click the 🌗 button in the toolbar (or use
  Settings → Theme).
- **Customize colors**: Settings dialog → *Theme overrides*. You can
  override individual palette slots (background, axis, bull-candle
  color, bear-candle color, etc.) per theme. Saved into the config
  file's `theme_overrides` key.
- **Set a default theme on launch**: Settings → *Startup parameters*
  → Theme. Saves to `startup_defaults.theme` in your config.

---

## Where your data lives

| Kind | Location | Auto-managed? |
|---|---|---|
| Disk cache (fetched candles) | Windows: `%LOCALAPPDATA%\tradinglab\`<br>macOS: `~/Library/Application Support/tradinglab/`<br>Linux: `~/.local/share/tradinglab/` | Yes — cleared with `python scripts/clear_cache.py` |
| Configuration file | Wherever **you** save it | No — explicit Load/Save |
| Watchlists file | Wherever **you** save it | No — explicit Load/Save |

---

## Troubleshooting

### "Ticker not found" / blank chart

- Check the spelling. yfinance uses Yahoo's symbology, not your
  broker's. Try `BRK-B` (not `BRK.B`), `BTC-USD` (not `BTC`),
  `^GSPC` (the S&P 500 index symbol).
- Try a different *Source* — `Sample` always works offline.
- Some tickers don't have intraday data; switch to `1d`.

### Nothing happens when I scroll-zoom

- Make sure your cursor is **inside** the chart area when you scroll
  (not over the toolbar or readout strip).
- Some trackpads emit very large scroll events; the app clamps these
  to keep zoom sane. If you still feel zoom is off, tweak
  `scroll_zoom_factor_per_step` in your config (advanced).

### My settings keep "disappearing" between sessions

This is by design — see [Persistence model](#persistence-model).
Use **File → Save Configuration As…** once to write a file, then
**File → Load Configuration…** at the start of each session (or set
up a `startup_defaults` block in your config and load it on launch).

If you want the same file loaded automatically on launch, that's a
feature on the roadmap; for now you can launch with
`python -m tradinglab` and `File → Load Configuration…`
right after.

### Worker count keeps resetting

Worker-pool size is intentionally **not** persisted — it's
hardware-dependent and the app auto-detects via `os.cpu_count()`. The
Settings slider lets you override it for the current session only.

### App starts slow / freezes briefly on first ticker load

The first fetch primes the disk cache. Subsequent loads of the same
`(ticker, interval)` are instant from the on-disk cache. Pre-warming
via watchlist preload is happening in the background.

### Where do I find logs / debug info?

The app prints to stdout. If you launched from a terminal, look there.
If you double-clicked a packaged build, you'll need to launch from a
terminal to see them.

For in-app history of fetches, retries, and warnings, use
**File → Status History…** — it opens a single-instance dialog with the
rolling status log (handy when a transient yfinance hiccup scrolls
off the toolbar status line).

---

## Where to go next

- **Read the README** — [`README.md`](../README.md) for install + a
  feature overview.
- **Browse the spec catalog** — [`docs/SPEC_INDEX.md`](SPEC_INDEX.md)
  links to one `.spec.md` per module documenting its public API and
  design decisions. Great if you're going to contribute.
- **Run the smoke tests** — `pytest tests/smoke -v`. The full smoke
  suite is ~123 end-to-end checks covering every major subsystem. If
  they're green, the build is healthy.
- **Tweak your config** — start from `config/example_config.json`,
  uncomment-equivalent (just delete `_comment_*` keys you don't need),
  and load it.

If something's confusing or you hit a wall, open an issue! Onboarding
docs are a living document and concrete pain points are the most
valuable feedback.

Happy charting. 🕯️📈
