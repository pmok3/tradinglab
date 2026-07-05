# side.py — Spec

## Purpose
Provide one canonical value type — `Side` — for the position-direction
concept ("is this a long-side or short-side position") so the next
sign-flip / favorable-price branch a contributor writes doesn't drift
the way the 30+ existing call sites have.

Audit reference: `files/generalization-audit.md` item #10.

## The 3 prior vocabularies (why they exist)

| Vocabulary | Where | Why it was chosen |
|---|---|---|
| `"long"` / `"short"` strings | `exits/spec.py` (30+ sites), `positions/model.py`, persisted JSON | Reads naturally in spec docs ("LONG closes when price ≥ stop"). Persisted shape — can't be silently changed. |
| `"buy"` / `"sell"` strings | `strategy_tester/evaluator.py` (~10 sites), some `positions/tracker.py` sites | Matches `Order.side` / `Fill.side` string values directly — the evaluator builds `Order` records and the easiest way to plumb the direction was to match. |
| `backtest.orders.Side` enum (`BUY` / `SELL`) | `backtest/engine.py`, `Order.side`, `Fill.side` | Typed enum that JSON-round-trips as a string (`Side(str, Enum)`). The proper "action" vocabulary for an order book. |

These all describe the **same** concept from different angles. The
"favorable=high for long, low for short" / "unfavorable=low for long,
high for short" branches were getting recomputed at every site as
inline `if side == "buy" / "long"` ladders — exactly the bug pattern
in CLAUDE.md §7.7–§7.9.

## Public API
- `class Side(Enum)` — `LONG = 1`, `SHORT = -1`. The numeric value
  IS the sign; `side.sign` is exposed for self-documenting math.

### Factories
- `Side.from_str(value)` — parses `"long"|"short"|"buy"|"sell"|"l"|
  "s"|"+1"|"1"|"-1"`, case-insensitive, whitespace-tolerant. Raises
  `ValueError` with a helpful message on anything else (silent
  coercion would defeat the value type).
- `Side.from_order_side(order_side)` — maps `backtest.orders.Side`
  (BUY/SELL) → `Side.LONG/SHORT`. **Assumes opening fill semantics**
  (BUY opens LONG, SELL opens SHORT). Callers in close-fill context
  must adapt at the boundary themselves.
- `Side.from_sign(scalar)` — `>0 → LONG`, `<0 → SHORT`, `0 → ValueError`.

### Adapters back to legacy vocabularies
- `side.as_long_short() -> "long" | "short"` — for `exits/spec.py`
  callers and persisted `PostTradeReview.side`.
- `side.as_buy_sell() -> "buy" | "sell"` — for `Order.side` /
  `Fill.side` string compares (or use `as_order_side` instead).
- `side.as_order_side() -> backtest.orders.Side` — the enum form for
  fresh `Order` / `Fill` construction.

### Numeric / branch-eliminating helpers
- `side.sign -> +1 | -1` — replaces `1 if side == "buy" else -1`.
- `side.is_long -> bool`, `side.is_short -> bool`.
- `side.opposite() -> Side` — for `exit_side = side.opposite()`.
- `side.favorable_price(bar) -> float` — `bar.high` (long) /
  `bar.low` (short). `bar` is any object with `.high` / `.low`.
- `side.unfavorable_price(bar) -> float` — opposite extreme.
- `side.adverse_excursion_price(bar)` — alias for `unfavorable_price`.
- `side.favorable_excursion_price(bar)` — alias for `favorable_price`.

The MAE/MFE aliases are pure self-documentation: at a MAE call site,
`side.adverse_excursion_price(bar)` reads more obviously than
`side.unfavorable_price(bar)`. Both forms compute identically.

## Migration policy ("new code adopts, old sites opportunistic")
Per audit #10 the value type is a **pilot**, NOT a sweep.

- **New code MUST adopt `Side`** for any new direction-dependent
  branch. Don't write a new `if side == "buy"` ladder.
- **`strategy_tester/evaluator.py` is the pilot** (~10 sites
  migrated in the same commit that introduces this module).
- **Old sites (`exits/spec.py` 30+ sites, `positions/tracker.py`,
  `backtest/engine.py`) stay on their existing vocabulary.** They
  migrate opportunistically — when a contributor edits the
  surrounding code for an unrelated reason, switch the inline branch
  to a `Side` call at the same time. Don't open a "migrate all of
  exits/spec.py to Side" PR — that's a separate sprint.
- **Persisted JSON shapes (`PostTradeReview.side`, `Fill.side`)
  stay strings.** Convert at the boundary via
  `side = Side.from_str(post.side)` inside the consumer.

## Dependencies
- Internal: `..backtest.orders` (lazy, inside `as_order_side`, so the
  core layer's "no app-wide imports at module load" rule holds).
- External: `enum` (stdlib).

## Design Decisions
- **`Enum` not `StrEnum` / `IntEnum`.** A plain `Enum` whose values
  happen to be `±1` is the cleanest way to express both "this is a
  closed set of two values" and "the sign is meaningful." Exposing
  `side.sign` keeps the numeric use explicit at every call site.
- **Lazy import of `backtest.orders.Side` inside `as_order_side`.**
  `core/` is the GUI/backtest-free layer (`core/__init__.py` docstring).
  A top-level import would invert the dependency edge.
- **`from_str` is strict.** It raises on unknown input rather than
  defaulting to LONG / SHORT. The value type exists to stop drift —
  silent fallback would defeat that.
- **`from_order_side` assumes opening fill.** Open/close
  disambiguation lives at the caller (the order book knows whether a
  fill is opening a new position or closing an existing one); the
  factory just translates the BUY/SELL token. Documented in the
  docstring.
- **`bar` typed as `Any` in price helpers.** Multiple bar shapes flow
  through — `models.Candle`, `exits.spec.Bar`, the strategy_tester
  `_BarTuple` (a namedtuple-like with `.high` / `.low`). Duck-typing
  on `.high` / `.low` is intentional. `float(...)` cast on return
  normalises numpy scalars.

## Migration scoreboard
| Module | Status |
|---|---|
| `strategy_tester/evaluator.py` | ✅ Pilot — ~10 sites |
| `exits/spec.py` | ⏸ Deferred — 30+ sites, separate sprint |
| `positions/tracker.py` | ⏸ Deferred |
| `backtest/engine.py` | ⏸ Deferred (uses OrderSide enum; adapter via `Side.from_order_side` already available) |
| `gui/strategy_tab.py` builders / journal forms | ⏸ Deferred |

## Tests
- `tests/core/test_side.py` — round-trips every `from_str` / `as_X`
  form, `from_order_side` mapping, `sign` / `is_long` / `is_short`
  invariants, `favorable_price` / `unfavorable_price` on a synthetic
  Candle, MAE/MFE alias correctness, and bad-input `ValueError`.
- `tests/unit/strategy_tester/test_side_pilot.py` — pins the
  evaluator's migrated paths still produce the same fills and the
  same `PostTradeReview.side` strings.
