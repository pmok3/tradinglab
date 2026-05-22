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
| **Indicator** | Fires when a scanner-style indicator condition is met |
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

---

## Exit strategies

An exit strategy defines one or more **legs**, each containing **triggers**
that manage an open position. Exits are attached to positions, not to entries
directly — the entry's **on-fill exits** list is what creates the link.

### Creating an exit

In the **Exits** tab, click **Edit Strategies** to open the exit library.
Click **New** (or **Bracket Template** for a pre-built stop + target, or
**Load Template** for other presets).

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
| **Indicator** | Close when a scanner-style indicator condition fires |

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

TradingLab ships with built-in templates to get you started:

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

Load a template from the entry/exit editor, then customize it for your setup.

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
