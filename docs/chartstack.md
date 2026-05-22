# ChartStack — Quick Guide

ChartStack is a strip of small, always-on watchlist charts that
lives on the left side of the main chart window. Each card is a
miniature **daily** OHLC candlestick chart for one symbol — at a
glance.

This guide walks through enabling ChartStack, what each card
shows, the four-tier alert system, and the keyboard shortcuts
that make it useful on a busy trading desk.


## Enabling ChartStack

ChartStack ships **off** by default. Turn it on from
**Settings → ChartStack → Enabled**, then toggle the panel with
**Ctrl+\`**. When enabled, three cards appear on the left of the
chart, defaulting to your active watchlist.


## Card anatomy

Each card is a self-contained daily-candle chart for one symbol:

* **OHLC candles** — proper open / high / low / close candles
  drawn in the same bull / bear / flat colors as the main chart.
  Wick + body, body height capped so a doji is still visible.
* **Header row** — symbol on the left, last close + %change vs
  prior close on the right (color-coded against the prior close).
* **Border tint** — when an alert fires, the card's axes spines
  light up in the tier color (amber / blue / red / yellow). When
  no alert is active the spines are hidden and the card is just
  candles + header.

That's the entire visual surface. Earlier prototypes carried a
session-anchored VWAP line, pre-market high / low horizontals, a
volume-weighted sparkline, a last-3-bars overlay, and a halted-
symbol grey treatment — these were retired in the 2026-05-16
simplification because the cards got too busy to read at a
glance. The settings keys still exist for backward compatibility
but the renderer ignores them.


## Binding modes

ChartStack picks which symbols to show based on the configured
binding mode. Change it in **Settings → ChartStack → Binding mode**.

* **`PINNED_WATCHLIST`** — your active watchlist (in order).
* **`SCANNER_TOP_N`** — top scanner matches, in rank order.
* **`OPEN_POSITIONS`** — symbols you currently have positions on,
  ordered by descending |unrealized P&L|.
* **`HYBRID`** *(default)* — positions first, then your manual
  pins, then your watchlist, then scanner edges. Deduped.

To add a manual pin, right-click a card and choose **Pin to
ChartStack**. Pins persist until you unpin them (right-click →
**Unpin**) or close the session.


## Click-to-promote

Click any card and its symbol replaces what's currently on the
main chart. The previously-active symbol "demotes" into the same
slot the promoted card just vacated — so you can swap freely
without losing your context.


## Sandbox lockstep

When you start a bar-replay sandbox session, ChartStack
automatically attaches to the sandbox controller and steps in
lockstep: every `next_bar` advances every card with the same
session date + interval. Live broker streams are silenced while
the sandbox is active so the cards can't peek ahead.

When you end the sandbox session the cards smoothly switch back
to live streams.


## The four-tier alert system

Even though the cards themselves only draw candles, the alert
engine evaluates a rich set of triggers per card every cycle and
paints the card's border in the highest-tier color that fired.
You only ever see one tint per card at a time (highest wins).

| Tier | Color | Triggers | Audio |
|---|---|---|---|
| 1 | Amber | RVOL spike, ATR expansion | None |
| 2 | Blue  | PMH/PML break, new scanner edge | One chime |
| 3 | Red   | Stop within 0.3× ATR, P&L zero-cross, MAE ≥ 1R | Double-ping every 5 s |
| 4 | Yellow badge | Earnings T-1, ex-div today | None |

Triggers like "PMH/PML break" and "stop within 0.3× ATR" are
**conditions the engine evaluates** against the bars and your
position state — they are not horizontals drawn on the card.

### Audio rate-limit

Tier-2 and Tier-3 chimes share a global 2-chime / 10-second
window. **Tier-3 bypasses the cap** — a stop alert always pings.
Mute everything from **Settings → ChartStack → Alerts → Mute
audio**.

### Time-of-day gate

* **09:30–09:35 ET** — Tier-1 detectors are silenced; the
  opening five minutes are too noisy to be useful.
* **09:35–10:00 ET** — Tier-1 thresholds are doubled
  (RVOL must be 2× the configured value, ATR must be 2× the
  configured multiple) to filter out the opening melt-up.
* **10:00–close** — defaults apply.

Tiers 2, 3, and 4 fire on their own primitives at all times.


## Settings reference

All ChartStack settings live under **Settings → ChartStack**:

| Setting | Default | Notes |
|---|---|---|
| Enabled | `False` | Master on/off |
| Cards | 3 | Visible card count (3–6) |
| Binding mode | `HYBRID` | See above |
| RVOL 1m threshold | `2.5` | Tier-1 |
| RVOL 5m threshold | `1.8` | Tier-1 |
| ATR expansion | `1.8` | Tier-1 |
| Mute audio | `False` | Silences all chimes |


## 4K-display behavior

On a 4K-class display (≥ 144 PPI) ChartStack auto-allows up to **6
cards**. On a standard 1080p / 1440p display it caps at **5 cards**
to preserve the main chart's minimum usable width (~70 %). Configure
the count in **Settings → ChartStack → Cards**.


## Keyboard shortcuts

ChartStack-specific shortcuts:

* **Ctrl+\`** — Show / hide the ChartStack panel.

Standard shortcuts also apply: **Ctrl+,** opens Settings,
**Ctrl+L** opens the Watchlist dialog, **Ctrl+R** resets the view.


## Troubleshooting

**Cards show "(empty)" centered text.** Your watchlist binding
hasn't filled that slot yet. Open the Watchlist dialog
(**Ctrl+L**), add a few symbols, and the cards will repopulate
on the next refresh cycle.

**Cards show only the symbol with no candles.** The card has a
binding but no bars have arrived from the data source yet — give
it a few seconds (yfinance fetches in the background). If the
ticker really has no historical data on the daily interval, the
placeholder remains.

**Tier-3 chimes won't stop.** Either you have a position parked
right at its stop (check the **Exits** panel) or you've turned
audio mute off and have a real stop-proximity event. Mute
everything via **Settings → ChartStack → Alerts → Mute audio**.

**Configured card count is being clamped.** ChartStack auto-caps
the card count based on the display DPI (5 on 1080p / 1440p; 6 on
4K). Lower the configured count or move to a higher-DPI display.
