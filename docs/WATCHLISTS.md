# Watchlists Guide

Watchlists let you organize tickers into named groups, pin your
favorites as always-visible tabs, and cycle through them with a single
keypress. They're the fastest way to move between symbols during a
trading session.

---

## Quick start

1. Click the **Watchlists** button in the toolbar (or press **Ctrl+L**).
2. Click **New** to create a watchlist and add tickers.
3. Check **Pinned** to make it appear as a sub-tab at the bottom of the window.
4. Close the dialog. Your pinned watchlist now shows as a tab with live prices.
5. **Double-click** any ticker in the tab to load it on the chart.
6. Press **Space** to cycle to the next ticker in the list.

---

## Creating and managing watchlists

Open the watchlist manager via the **Watchlists** toolbar button or
**Ctrl+L**. From here you can:

| Action | How |
|---|---|
| **Create** | Click New, enter a name, add tickers |
| **Rename** | Select a watchlist, click Rename |
| **Delete** | Select a watchlist, click Delete |
| **Add tickers** | Select a watchlist, type a ticker in the entry box, click Add |
| **Remove tickers** | Select a ticker in the list, click Remove |
| **Pin / Unpin** | Check or uncheck the Pinned box next to the watchlist name |
| **Reorder pins** | Right-click a pinned tab → Move Left / Move Right |
| **Import** | Click Import to merge a watchlist file into the current set |
| **Export** | Click Export to save the current watchlists to a JSON file |

---

## Pinned tabs

Pinned watchlists appear as **sub-tabs** at the bottom of the main
window. Each tab shows a live table with:

| Column | Description |
|---|---|
| **Ticker** | Symbol name |
| **Last** | Most recent price |
| **Change** | Dollar change from prior close |
| **Change Pct** | Percentage change |
| **Next Earn** | Next earnings date (if available) |

### Limits

- Up to **5 pinned watchlists** by default (configurable in Settings
  via the `watchlist_max_pinned` tunable).
- No limit on tickers per watchlist.

### The + tab

If you have fewer than 5 pins, a **+** tab appears at the end. Click
it to quickly pin an existing unpinned watchlist without opening the
full manager dialog.

---

## Navigating tickers

### Double-click

**Double-click** any ticker in a pinned watchlist tab to load it on
the chart. It replaces the primary ticker (or the compare ticker if
you double-click while hovering the compare panel).

### Space key cycling

Press **Space** while the chart has focus to cycle to the **next ticker**
in the active pinned watchlist. This is the fastest way to scan through
your list:

- Cycles through all tickers in the currently selected pinned tab
- Wraps around to the first ticker after reaching the end
- Preserves your current interval and zoom level
- Works in drilldown mode — it switches the ticker without leaving the
  5m view

If no watchlist is pinned, the default watchlist is auto-created and
pinned so Space always has something to cycle through.

---

## Preloading

When you pin a watchlist, TradingLab **preloads** chart data for every
ticker in the background. This means:

- Switching to a pinned ticker is **instant** — the candles are already
  cached.
- The "Last" / "Change" / "Change Pct" columns update from the preloaded
  data.
- The "Next Earn" column fetches earnings dates in the background.

Preloading runs on a background thread pool and doesn't block the UI.
It respects the cache staleness rules — if data is already fresh, it
skips the fetch.

---

## Loading and saving

Watchlists follow the same explicit-save model as configuration files:

| Menu item | What it does |
|---|---|
| **Watchlists → Load Watchlists** | Replace the current watchlist set from a JSON file |
| **Watchlists → Save Watchlists** | Write the current set to the loaded file |
| **Watchlists → Save Watchlists As** | Write to a new file |
| **Watchlists → Open Watchlists Manager…** (Ctrl+L) | Open the full manager dialog — the bottom row's **Save and Close** button persists the set and dismisses the dialog in one click |

### File format

Watchlist files are JSON with this structure:

```json
{
  "version": 2,
  "watchlists": [
    { "name": "Megacap Tech", "tickers": ["AAPL", "MSFT", "NVDA"] },
    { "name": "Crypto", "tickers": ["BTC-USD", "ETH-USD"] }
  ],
  "pinned": ["Megacap Tech"]
}
```

Files can be shared between machines — just load them on the other
computer. Version 1 files (without the `pinned` field) are accepted
and upgraded automatically.

### Starter file

A starter file is included at `config/example_watchlists.json`. Copy
it, edit the tickers, and load it via Watchlists → Load Watchlists.

---

## Tips

- **Organize by setup type**, not by sector. "Gap Ups Today" and
  "Earnings This Week" are more useful during a session than "Tech"
  and "Healthcare".
- **Keep pinned lists short** — 10-15 tickers max. You can scan through
  15 tickers with Space in under a minute. A 50-ticker list becomes a
  scroll-fest.
- **Use the export** before making big changes. If you accidentally
  delete a watchlist, you can re-import from the backup.
- **Tickers are auto-uppercased** and deduplicated. Adding "aapl" when
  "AAPL" already exists is silently ignored.
- **Unsaved changes prompt on quit.** If you've edited watchlists
  without saving, the app asks before closing. Saving writes to the
  last-loaded file; Save As creates a new one.
