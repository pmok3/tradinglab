# `indicators/_palette.py` — spec

## Purpose

Single source of truth for **indicator default colors** (and the
**`FALLBACK_GRAY`** used by GUI sites that resolve "no style set" to a
muted neutral). Eliminates the prior pattern where the same
matplotlib-`tab10` hex codes were copy-pasted across 15+ indicator
`default_style` blocks, plus `"#888888"` was sprinkled across 6+ GUI
fallback sites with no shared constant.

## Public surface

Two layers, exposed via module-level constants:

### Tab10 names (raw palette)

`TAB10_BLUE` `TAB10_ORANGE` `TAB10_GREEN` `TAB10_RED` `TAB10_PURPLE`
`TAB10_BROWN` `TAB10_PINK` `TAB10_GRAY` `TAB10_OLIVE` `TAB10_CYAN` —
matplotlib's `tab10` color cycle. Indicators whose chosen hue is
"specifically TradingView-blue for SMA, TradingView-orange for EMA"
import these directly.

### Semantic roles

| Role             | Default mapping  | Used for                                         |
|------------------|------------------|--------------------------------------------------|
| `PRIMARY_LINE`   | `TAB10_BLUE`     | Single-line indicators (SMA, BB middle, RMA-BB, MACD line) |
| `SECONDARY_LINE` | `TAB10_ORANGE`   | Second line in a 2-line indicator (EMA, MACD signal, SMI signal, EMA-KC) |
| `TERTIARY_LINE`  | `TAB10_GREEN`    | Third line (ADX `+DI`, WMA-MA, SMA-BB)           |
| `QUATERNARY`     | `TAB10_RED`      | Bands / hi-lo / "hot" series (ADX `-DI`, RSI, EMA-BB) |
| `QUINARY`        | `TAB10_PURPLE`   | Extras (VWAP, WMA-BB, WMA-KC)                    |
| `BULLISH`        | `sentiment_recolor("#1bb556")` | MFE marker dot — base green, recoloured to the live bull hue under the Okabe-Ito palette (audit `color-blind-palette-audit`) |
| `BEARISH`        | `sentiment_recolor("#d62728")` | MAE marker dot — base red, recoloured to the live bear hue under Okabe-Ito |
| `FALLBACK_GRAY`  | `"#888888"`      | Neutral fallback when no style is set            |

### Role-vs-name distinction

Roles describe **what an indicator IS** (a single primary line, a band
of bullish color, etc.); tab10 names describe the **underlying palette
mapping**. The split matters because:

* If a future dark-mode rework picks a brighter blue, change
  `PRIMARY_LINE = "#3399cc"` once and every "primary single line"
  indicator updates in lockstep — without touching 15+ files.
* If a single indicator wants the literal tab10 brown for thematic
  reasons (AVWAP defaults to brown because that's its long-standing
  hue), it imports `TAB10_BROWN` directly and is unaffected by future
  role remapping.

## Off-palette literals

Several indicators carry hues that are NOT in tab10:

* MACD histogram palette (`#26a69a` / `#b2dfdb` / `#ffcdd2` / `#ef5350`)
  — Material teal/red, the TradingView default for momentum bars.
* Chandelier stops (`#2e7d32` / `#c62828`) — darker Material green/red
  picked specifically to NOT camouflage against candle bodies.
* RVOL (`#aec7e8`) / RRVOL (`#c5b0d5`) — tab20 light variants picked
  to read as "lower-pane volume tint" rather than "primary line".
* ATR RMA-kernel default (`#ffbb78`) — tab20 light orange to pair
  visually with EMA's primary orange without colliding.
* Prior-day H/L/C (`#26a69a` / `#ef5350` / `#9e9e9e`) — Material
  teal/salmon/gray to match the indicator's "session marker" semantic.
* Overlap-score (`#ab47bc`) — Material purple, intentionally distinct
  from tab10 purple so it doesn't conflict with VWAP.
* AVWAP bands (`#4393c3`) — ColorBrewer blue chosen specifically for
  band readability against the brown centerline.

These are **intentionally left as literals** with explanatory comments
at their declaration site. Promoting them to roles would hide the
per-indicator visual decision behind a generic role name.

## Future dark-mode hook

When a global dark-mode palette ships, the intended migration is:

1. Add a `_active_palette: Literal["light", "dark"]` module variable.
2. Convert each role constant into a property/lookup
   `def PRIMARY_LINE() -> str: return _PALETTES[_active_palette].primary`.
3. The single switch in this module ripples through every consumer
   without per-site code changes.

## Tests pinning the contract

`tests/unit/indicators/test_palette.py`:

* Every constant is a valid `#xxxxxx` hex.
* Roles match the documented tab10 mappings (`PRIMARY_LINE == TAB10_BLUE` etc.).
* No `default_style` block in `src/tradinglab/indicators/*.py`
  contains a literal hex string that's also a palette constant —
  forces all such colors to come through this module.
* All registered indicators keep their `default_style` output-key
  invariant (visual regression guard).
