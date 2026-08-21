# Ratio Charts

A **ratio chart** plots one symbol divided by another, bar for bar — a fast way
to read *relative* strength. Instead of asking "is AMD going up?", a ratio asks
"is AMD outperforming NVDA?" — the chart rises when the numerator is stronger
than the denominator and falls when it's weaker.

## How to chart a ratio

Just type two tickers separated by a slash into the ticker box:

```
AMD/NVDA
```

Press Enter and the chart shows AMD ÷ NVDA. That's the whole workflow — there is
no separate dialog or mode to enable. A ratio works **anywhere a normal symbol
does**: the main chart, the compare overlay, and watchlists.

Input is case- and space-insensitive, so `amd / nvda` and `AMD/NVDA` are the
same chart. The chart title and watermark show it as **`AMD / NVDA`**.

## What it looks like

- **Candlesticks**, exactly like a normal symbol. Each bar's open/high/low/close
  is the ratio of the two symbols' corresponding prices.
- **No volume pane** — a two-ticker ratio has no meaningful volume, so the
  volume panel is hidden to keep the chart clean. (Dividing by a *number*
  keeps the volume pane — see below.)
- Indicators, drawings, crosshair, and pan/zoom all work as usual.

> The candle high/low is an approximation: it's the envelope of the two
> symbols' OHLC ratios, since the true intra-bar path of a ratio can't be known
> from sealed bars. The open and close are exact.

## Options (View → Ratio charts (A/B))

- **Rebase to 100** — rescales the series so the **leftmost bar currently on
  screen** equals 100, re-anchoring live as you pan and zoom. The chart then
  reads as *relative performance* ("AMD has gained 12% on NVDA since the left
  edge") instead of an absolute quotient like `1.17`. Off by default.

You can also use the normal **log price scale** toggle on a ratio chart.

## Dividing by a number

The denominator can be a **plain positive number** instead of a ticker:

```
^VIX/15.87
```

This is a *scaled symbol* — one real instrument on a rescaled axis, not a
relationship between two. The classic use is the one above: VIX is annualised
30-day implied volatility, so dividing by √252 ≈ 15.87 converts it into the
market's approximate **1-day expected move in percent**. A VIX of 24 reads as
about 1.5% — i.e. a 1% day is unremarkable in that regime.

| Type this | Reads as |
|---|---|
| `^VIX/15.87` | Approximate daily 1-sigma implied move, in % |
| `^TNX/10` | The 10-year Treasury index quote as a yield in % |
| `SPX/10` | S&P 500 index roughly aligned to SPY's scale |
| `AAPL/100` | Any symbol rescaled to taste |

Decimals are supported (`15.87`, `0.5`). The divisor must be **positive**, and
only the **denominator** may be a number — `100/VIX` and `VIX*16` are not
supported. `16/4` and `^VIX/0` are rejected.

A scaled symbol behaves differently from a two-ticker ratio in ways that matter:

- **The volume pane stays visible**, showing the underlying's real volume.
  Dividing by a constant doesn't make volume meaningless the way a quotient
  does, and volume-weighted studies stay valid (VWAP scales by the same number;
  RVOL is unchanged). *(An index like `^VIX` has no volume of its own, of
  course.)*
- **Corporate events still show** — `AAPL/100` displays Apple's splits and
  dividends.
- **No bars are ever dropped.** A number has no trading calendar, so unlike a
  two-ticker ratio there's nothing to inner-join against.
- **The high and low are exact**, not the envelope approximation noted above.
- **"Rebase to 100" does nothing**, on purpose. Rebasing multiplies the whole
  series by a constant, which would cancel your divisor exactly and give you
  back a plain `^VIX` chart — silently throwing away the units you asked for.

> Because the divisor is always shown in the title and watermark
> (**`^VIX / 15.87`**), a rescaled chart can't be mistaken for the raw symbol.

## Index symbols (VIX, SPX, …)

Indices aren't tradeable stocks, so every data provider spells them
differently — Yahoo wants `^VIX`, Schwab `$VIX`, Polygon `I:VIX`. You can just
type the plain name and it resolves for whichever source is active:

```
VIX     →  ^VIX      (on Yahoo)
SPX     →  ^GSPC     (on Yahoo)
```

The box updates to show the real symbol, and **it re-resolves automatically if
you switch data source**, so a chart of `^VIX` becomes `$VIX` when you move to
Schwab rather than silently failing.

Recognised shorthands: `VIX`, `VVIX`, `VXN`, `SPX`, `NDX`, `DJI`, `RUT`, `TNX`,
`OEX`, `IXIC`. Anything else is passed through untouched — including `COMP` and
`MOVE`, which are **real listed stocks** and are deliberately never treated as
index shorthand. (The Nasdaq Composite is `IXIC`, not `COMP`.)

## Useful ratios

| Type this | Reads as |
|---|---|
| `RSP/SPY` | Market breadth — equal-weight vs cap-weight S&P 500 |
| `QQQ/SPY` | Risk appetite — Nasdaq-100 vs S&P 500 |
| `IWM/SPY` | Small-cap risk appetite — Russell 2000 vs S&P 500 |
| `SMH/SPY` | Semiconductors vs the market |
| `XLF/SPY` | Financials sector relative strength |
| `AMD/SMH` | A stock vs its sector |
| `AMD/NVDA` | Two peers — who's leading? |
| `HYG/IEF` | Risk-on / risk-off — high-yield vs Treasuries |

## Notes & limitations

- **Both legs come from the same data source at the same interval.** A ratio of
  two different providers or two different timeframes isn't supported.
- **Only one `/` (two legs).** `A/B/C` is rejected.
- Real symbols that contain `-` or `.` (such as `BRK-B`, `BRK.B`, `BTC-USD`)
  are never mistaken for ratios — only the `/` separator denotes a ratio.
- Ratios are computed live from their two legs and are **not** written to the
  on-disk candle cache; the underlying legs cache normally.
- Split/dividend adjustment follows the data source (yfinance auto-adjusts), so
  a ratio of two adjusted series is internally consistent. A divisor chosen to
  align an index with an ETF (`SPX/10 ≈ SPY`) drifts over long histories — it's
  a visual alignment, never an executable price.
- A two-ticker ratio silently drops bars where the two symbols' calendars don't
  overlap (halts, differing histories, index-vs-ETF sessions).
