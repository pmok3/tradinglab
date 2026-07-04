# Entries and Exits Guide

This guide walks you through creating, managing, and using entry and exit
strategies in TradingLab. Together, they form the core trade-management
pipeline: **entries** define when and how to get into a position; **exits**
define when and how to get out.

---

## Quick start

1. Open the **Entries** tab at the bottom of the main window.
2. Click **New** to create an entry strategy.
3. Choose a trigger (Market, Limit, Stop, etc.), set sizing, and save.
4. Click **Arm** to activate it.
5. Open the **Exits** tab and create an exit strategy (stop-loss, trailing stop, etc.).
6. Link the exit to your entry via the **On-fill exits** section in the entry editor.
7. When the entry triggers and fills, exits auto-attach to the new position.

---

## Entry strategies

An entry strategy defines:
- **What direction** — long or short
- **What universe** — which symbols to watch
- **What trigger** — the condition that fires the entry
- **What size** — how many shares or how much capital
- **What happens after fill** — which exit strategies auto-attach

### Creating an entry

In the **Entries** tab, click **New** (or **Load Template** to start from a preset).

### Universe — where to look

Choose exactly one:

| Universe type | Description |
|---|---|
| **Symbols** | A fixed list of tickers (e.g., AAPL, NVDA, AMD) |
| **Scanner** | Link to a saved scanner — entries fire on scanner matches |
| **Attached chart** | Use whatever ticker is currently on the chart |

### Trigger types — when to enter

| Trigger | How it works |
|---|---|
| **Market** | Enter at the next bar's close after arming |
| **Limit** | Long: buy on a dip to your price. Short: sell on a rally to your price |
| **Stop** | Long: breakout above your price. Short: breakdown below your price |
| **Stop-Limit** | Stop arms first, then a limit price must also be reached |
| **Indicator** | Fires when a custom AND/OR criteria tree evaluates to True — see [Custom criteria](#custom-criteria-indicator-triggers) |
| **Scanner Alert** | Fires when a linked scanner produces a new match |

### Sizing — how much

| Mode | Description |
|---|---|
| **Fixed Qty** | Always buy/sell a set number of shares |
| **Fixed Notional** | Specify a dollar amount (e.g., $5,000) — shares are calculated automatically |

### On-fill exits

Check one or more exit strategies in the **On-fill exits** section. When the entry
fills, those exits automatically attach to the new position. This is how you create
a "bracket order" workflow — the entry fires, and your stop-loss + profit target
are instantly active.

### Lifecycle guards

These prevent the strategy from firing too aggressively:

| Guard | What it does |
|---|---|
| **Cooldown** | Minimum time between fires on the same symbol |
| **Max fires per symbol** | Cap total fires per ticker |
| **Max fires per session** | Cap fires across all symbols for the trading day |
| **Max fires total** | Lifetime cap |
| **Open position policy** | Block (skip if already in a position) or Stack (allow multiple entries) |
| **Arm window** | Only fire during specific hours |
| **Require market open** | Don't fire in pre/post market |

### Arming

Saved strategies sit in the library. They only become active when you click
**Arm** in the Entries tab. Arm state is per-session — it resets when you
close the app.

The Entries tab has a **Show: Mine | Active | Templates | All** filter above
the strategy table. It opens on **All** each session — your own strategies
and the bundled starter templates together. Switch to **Mine** to hide the
templates, **Active** to see only enabled **and** armed alerts (the ones
actually watching the market right now), or **Templates** to browse just the
starters. A template you **Load** or **Duplicate** becomes your own strategy
and shows under **Mine**.

---

## Exit strategies

An exit strategy defines one or more **legs**, each containing **triggers**
that manage an open position. Exits are attached to positions, not to entries
directly — the entry's **on-fill exits** list is what creates the link.

### Creating an exit

In the **Exits** tab, click **Edit Strategies** to open the exit library.
Click **New** (or **Bracket Template** for a pre-built stop + target, or
**Load Template** for other presets). The exit library has a
**Show: Mine | Templates | All** filter, defaulting to **All**.

### Trigger types — when to exit

| Trigger | How it works |
|---|---|
| **Market** | Close immediately at next bar |
| **Limit** | Profit target — close when price reaches your level |
| **Stop** | Stop-loss — close when price drops to your level |
| **Stop-Limit** | Stop arms, then limit must be reachable |
| **Trailing Stop** | Follows price by a fixed distance (%, $, or ATR); never moves against you |
| **Chandelier Stop** | ATR-based trailing stop anchored to the highest high since entry |
| **Time of Day** | Close at a specific clock time (e.g., 15:55 to flatten before close) |
| **Indicator** | Close when a custom AND/OR criteria tree evaluates to True — see [Custom criteria](#custom-criteria-indicator-triggers) |

### Legs and OCO groups

An exit strategy can have multiple **legs** — for example:

- Leg 1: Take 50% profit at +2%
- Leg 2: Stop-loss at -1%
- Leg 3: Time-of-day flatten at 15:55

**OCO (One Cancels Other)** groups let you link legs so that when one fires,
the others in the group are cancelled. For example, a profit target and
stop-loss in the same OCO group means whichever hits first cancels the other.

OCO cancel modes:
- **Full closeout** — cancel other legs only after the ENTIRE position is closed
- **Any fire** — cancel other legs as soon as ANY trigger in the group fires

### Trailing stop options

| Option | Description |
|---|---|
| **Distance unit** | Percent (%), Dollar ($), or ATR multiples |
| **Basis** | Trail from the high-water mark of the position |
| **Activation** | Optionally, only start trailing after the position is up by X% / $X / X R-multiples |
| **Fill mode** | Intrabar (fill at stop price) or Close-only (fill at bar close) |

### Chandelier stop options

| Option | Description |
|---|---|
| **Lookback** | Rolling highest-high window (default 22) |
| **ATR period** | Smoothing period for ATR (default 22) |
| **Multiplier** | How many ATR below the high (default 3.0) |
| **MA type** | RMA (Wilder, default), SMA, EMA, or WMA |

### EOD kill switch

Every exit strategy has an optional **end-of-day kill switch**. When enabled,
any remaining open position is flattened at a specified time (default: 15:55 ET).
This is independent of the legs — it's a safety net.

### PANIC: Flatten All

The **Exits** tab has a red **PANIC: Flatten All** button that immediately
closes every open position with market orders. Use it when things go wrong.

---

## Custom criteria (Indicator triggers)

Both the **Indicator entry trigger** and the **Indicator exit trigger** use
the same tree-based criteria builder shared with the scanner. You compose
any combination of conditions and groups, combined with AND or OR, to
express the exact setup you want to fire on.

### Anatomy

A criteria tree always has one **root group**. A group contains an ordered
list of **children**, where each child is either:

- A **condition** — a single leaf comparison (`Left <operator> Right`).
- A nested **group** — for mixed logic like `A AND (B OR C)`.

Each group is combined with either:

- **AND** — fires only when *every* enabled child is True.
- **OR** — fires when *any* enabled child is True.

The AND/OR combobox appears in the group header **once the group has 2 or
more children** — with 0 or 1 children there's nothing to combine, so the
control stays hidden to reduce noise.

Use **+ Condition** to add a leaf comparison. Use **+ Group** to add a
nested sub-group (for any-of-N or mixed AND/OR logic).

### Condition row anatomy

Each condition row has these controls, left to right:

| Control | What it does |
|---|---|
| **Enabled** ☑ | Tick to include this condition; untick to temporarily mute it without deleting |
| **Left** | The left-hand side of the comparison. Pick **Number**, **Builtin**, or **Indicator** (see below) |
| **Operator** | The comparison operator (see "Operators" below) |
| **Right / op-specific params** | The right-hand side and any extra parameters (lookback, tolerance, etc.) |
| **Interval** | The bar interval this condition evaluates on (e.g., `5m`, `1d`) — per-row, so a single trigger can mix timeframes |
| **Look-back** | Optional "within last N bars" quantifier (see "Look-back" below) |
| **✕** | Delete this condition |

### Field kinds (Left / Right)

The **Left** side and any field-typed slot on the right can be one of:

| Kind | What it is | Example |
|---|---|---|
| **Number** | A fixed numeric constant | `RSI < 30` (right side is `30`) |
| **Builtin** | A bar field — `Open`, `High`, `Low`, `Close`, `Volume`, `% Change`, `Gap %`, `High of Day`, `Low of Day`, `Time of Day`, `Bars Since Open`, plus the Heikin-Ashi family (`HA Open`, `HA Close`, `HA Color`, `HA Flat-Top`, `HA Streak`, …) | `Close > Open` |
| **Indicator** | Any registered indicator (SMA, EMA, VWAP, RSI, MACD, Bollinger, RVOL, ATR, Chandelier, Keltner, Prior Day H/L/C, …) with full parameter control and — for multi-output indicators like Bollinger or MACD — an **output-key picker** so you target the specific line you want | `Close > SMA(20)` |

Switching between Number and Builtin preserves your typed numeric value, so
accidentally flipping the kind doesn't lose work.

### Operators

Every operator returns one of three values: **True**, **False**, or **None**.
`None` means *insufficient data* (operand is NaN, indicator hasn't warmed up
yet, look-back walks before the first bar) and is treated as "did not fire".

| Operator | What it does | Extra params |
|---|---|---|
| `>` `<` `>=` `<=` `==` `!=` | Standard comparisons | `right` (field) |
| `between` | `Left` is between two values, inclusive | `low`, `high` (fields) |
| `crosses_above` | `Left` crossed above `Right` somewhere in the last N bars (transition) | `right` (field), `lookback` (int) |
| `crosses_below` | `Left` crossed below `Right` somewhere in the last N bars | `right` (field), `lookback` (int) |
| `is_rising` | `Left` strictly increased over the last N bars | `lookback` (int) |
| `is_falling` | `Left` strictly decreased over the last N bars | `lookback` (int) |
| `within_pct` | `Left` is within ±tolerance% of `Target` | `target` (field), `tolerance_pct` (float) |
| `new_high_n_bars` | `Left` is the highest value in the last N bars (inclusive) | `n` (int) |
| `new_low_n_bars` | `Left` is the lowest value in the last N bars (inclusive) | `n` (int) |
| `holding_above` | `Left` has stayed above `Reference` for the last N bars | `reference` (field), `bars` (int) |
| `holding_below` | `Left` has stayed below `Reference` for the last N bars | `reference` (field), `bars` (int) |
| `inside_bar` | Current bar's high < prior high AND low > prior low (no Left needed) | — |
| `outside_bar` | Current bar's high > prior high AND low < prior low | — |
| `nr7` | Current bar's range is the narrowest of the last 7 bars | — |

> **`None` propagation (SQL-NULL semantics).** In an AND group: any child
> False → False; all children True → True; any mix involving None → None.
> In an OR group: any child True → True; all children False → False;
> otherwise None. A `None` final result is treated as "did not fire" — the
> trigger does **not** activate.

### Look-back ("within last N bars")

Every condition AND every group has an optional **look-back** cluster in
the right side of its header. It lets you say *"this thing must be true
somewhere in the last N bars"* instead of only on the current bar.

| Control | Meaning |
|---|---|
| **Bars** | How many bars back to walk. `0` = current bar only (default). `N` = walks the closed range `[i-N, i]` — that's N+1 bars total including the current one. |
| **Mode** | `any` = True if the inner predicate is True on ANY bar in the window (default — bread-and-butter mode). `all` = True only if True on EVERY bar. `exactly` = True only at exactly bar `i-N` (the oldest bar in the window). |

The `all` and `exactly` modes are hidden for transition operators
(`crosses_above`, `crosses_below`) because they're meaningless — a cross
is by definition a one-bar event.

Applying look-back to a **group** rather than to individual conditions lets
you express *"both of these things happened on the **same** bar, anywhere
in the last N bars"* — strictly more powerful than per-condition look-back.

When a look-back leaf fires, the audit log records a `MatchEvidence` payload
noting "fired N bars ago at HH:MM", visible in scanner row tooltips and the
sandbox replay overlay.

### Per-condition interval

Every condition picks its own **interval** (`1m`, `2m`, `5m`, `15m`, `30m`,
`1h`, `1d`, `1wk`, `1mo`). This means you can mix timeframes inside a
single trigger — for example, *"5m `Close > VWAP` AND 1d `Close > SMA(50)`"*
layers a daily-trend filter on top of an intraday entry signal.

### Enabled toggle

Each condition and group has its own **enabled** checkbox in the header.
Unticking it removes that node from evaluation **without** deleting it —
handy for A/B-comparing two variants of a strategy or muting one clause
during a sandbox replay without losing the work.

### Nested groups for mixed logic

Combine AND-groups and OR-groups to express any logic. The builder is fully
recursive: a group can contain groups can contain groups.

> *"Long when (5m EMA3 crosses above EMA8 OR 5m RSI(14) crosses above 50) AND
> 1d Close > SMA(50) AND today's RVOL > 1.5."*

Built as:

```
Group [AND]
├── Group [OR]
│   ├── Condition (5m)   EMA(3)     crosses_above   EMA(8)     lookback=1
│   └── Condition (5m)   RSI(14)    crosses_above   50         lookback=1
├── Condition (1d)   Close   >   SMA(50)
└── Condition (5m)   RVOL(mode=cumulative)   >   1.5
```

### Worked example: VWAP reclaim with daily-trend filter

> *"Go long when 5m `Close` crosses above `VWAP`, but only if the daily
> close is above the 50-day SMA, and only if today's cumulative RVOL > 1.5."*

```
Group [AND]
├── Condition (5m)   Close   crosses_above   VWAP        lookback=1
├── Condition (1d)   Close   >               SMA(50)
└── Condition (5m)   RVOL(mode=cumulative)   >   1.5
```

### Worked example: any-of-N exit signal

> *"Close the position if EITHER the 5m `Close` crosses below the 9 EMA,
> OR RSI(14) drops below 40."*

```
Group [OR]
├── Condition (5m)   Close     crosses_below   EMA(9)      lookback=1
└── Condition (5m)   RSI(14)   <               40
```

### Tips

- **Start with one condition, then build up.** A new condition row defaults
  to `Close > 0` — a placeholder you'll always rewrite. Pick the Left
  field first, then the operator, then the Right side.
- **Mixed timeframes are intentional.** A 5m entry can require a 1d trend
  filter on the same row; the engine pulls each operand from its own
  interval's bar history.
- **Look-back is per-row.** There's no global look-back — instead, mark
  the specific condition (or wrap several in a group and mark the group)
  that should be allowed to fire "any time in the last N bars".
- **Disable, don't delete, when experimenting.** The enabled checkbox
  lets you A/B a clause without losing it.
- **Crosses are one-bar events.** Use `lookback=1` for "just crossed"
  semantics. Larger lookbacks will keep matching for N bars after the
  cross, which can cause back-to-back fires unless you also set a cooldown.
- **Sandbox-test before going live.** Bar-replay shows the exact bar each
  trigger evaluates to True, with `MatchEvidence` tooltips noting how many
  bars ago each look-back leaf fired.

### Custom-criteria troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| Trigger never fires | A condition returns `None` (insufficient data) | Check the indicator's warm-up period — e.g., for SMA(50) on 5m you need at least 50 closed 5m bars before any output exists |
| AND group with "all True"-looking conditions still doesn't fire | One operand is silently `None`, which makes AND → None (treated as "did not fire") | Run the trigger in the sandbox with **Show insufficient-data rows** enabled to see which clause is missing data |
| Cross fires every bar instead of once | `lookback` is too high — the cross stays "true" for `lookback` bars after the transition | Set `lookback=1` for strict "just crossed" semantics, or add a cooldown lifecycle guard |
| Indicator picker doesn't show your indicator | The indicator isn't registered, or it's registered but disabled on the chosen interval | Open **Manage Indicators** and enable it for the interval you're targeting |
| AND/OR combobox is missing from a group | The group has 0 or 1 children, so the combinator is meaningless and hidden | Add a second child via **+ Condition** or **+ Group** — the combobox appears automatically |
| `all` / `exactly` modes missing from look-back | The condition uses a transition operator (`crosses_above`, `crosses_below`) | Use `any` mode (it's the only mode that's meaningful for transitions), or switch to a level operator like `>` |
| Look-back leaf fires N times in a row when set to "any" | That's expected — `any` mode is True for every bar within the window | Reduce the window, switch to `exactly` mode to fire only at bar `i-N`, or add a cooldown lifecycle guard |

---

## End-to-end workflow

```
1. Create entry strategy          → "Buy NVDA on RSI < 30"
2. Create exit strategy           → "Stop at -1%, target at +2%, EOD flatten"
3. Link exit to entry (on-fill)   → Entry editor → On-fill exits → check the exit
4. Arm the entry                  → Entries tab → click Arm
5. Wait for trigger               → RSI drops below 30 → entry fires
6. Position opens                 → Exit auto-attaches
7. Exit manages the position      → Stop/target/EOD — whichever hits first
8. Position closes                → Trade logged, stats updated
```

---

## Templates

TradingLab ships with built-in templates to get you started. On first run
they're seeded into your entry/exit libraries and are shown by default: the
**Show** filter defaults to **All**, so your own strategies and the bundled
templates appear together. Switch it to **Mine** to hide the templates, or
**Templates** to see only them:

### Entry templates
- **EMA 3/8 Cross Long** — enter long on fast/slow EMA crossover
- **EMA 9 Pullback Long** — enter long on pullback to 9 EMA
- **RSI Oversold Long** — enter long when RSI drops below 30
- **VWAP Reclaim Long** — enter long when price reclaims VWAP from below
- **VWAP Reject Short** — enter short on rejection at VWAP

### Exit templates
- **Bracket 2%** — stop at -2%, target at +2%
- **Chandelier 22/3** — chandelier trailing stop
- **Scale Out 1/3** — take ⅓ at +1%, ⅓ at +2%, trail the rest
- **Time Stop 15:55** — flatten at 15:55 ET
- **Trailing 2%** — 2% trailing stop

Load a template from the entry/exit editor (or switch the **Show** filter to
**Templates**), then customize it for your setup. A loaded or duplicated copy
is saved as your own strategy and appears under **Mine**.

---

## Tips

- **Start with a bracket template.** A simple stop + target is the safest
  first exit strategy. Get comfortable before adding trailing stops.
- **Test in the sandbox first.** The bar-replay sandbox lets you see entries
  and exits fire on historical data before risking real capital.
- **Lifecycle guards prevent overtrading.** Set a max-fires-per-session limit
  to avoid revenge trading after a losing streak.
- **Arm state resets on restart.** Strategies must be re-armed each session.
  This is intentional — it forces you to consciously decide what's active today.
- **Exits are position-bound.** When a position closes, its exit strategy
  detaches. A new entry creates a new position with fresh exit state.
- **PANIC button exists for a reason.** If you're confused about what's
  happening, flatten everything and reassess. The button is always visible.

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| Entry doesn't fire | Not armed | Click Arm in the Entries tab |
| Entry doesn't fire | Lifecycle guard blocking | Check cooldown, max fires, open position policy |
| Entry doesn't fire | Universe mismatch | Verify the symbol is in the strategy's universe |
| Exit doesn't attach | Not linked | Check On-fill exits in the entry editor |
| Exit trigger doesn't fire | Strategy disabled | Enable the strategy in the exit library |
| Trailing stop not moving | Activation threshold not met | Position hasn't reached the activation level yet |
| OCO not cancelling | Wrong cancel mode | Check Full closeout vs Any fire setting |
| Position stays open at EOD | No EOD kill switch | Enable it in the exit strategy editor |
