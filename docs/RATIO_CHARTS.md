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
- **No volume pane** — a ratio has no meaningful volume, so the volume panel is
  hidden to keep the chart clean.
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
  a ratio of two adjusted series is internally consistent.
