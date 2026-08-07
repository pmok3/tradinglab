# streaming/synthetic_quotes.py — Spec

## Purpose
Deterministic offline [`QuoteSource`](quotes.spec.md) — the counterpart
to [`streaming/synthetic`](synthetic.spec.md) for the quote axis, and
the reason the live heatmap path is exercisable on a machine with no
brokerage credentials.

## Public API
- `base_price(symbol) -> float` — deterministic starting price, matching
  `streaming.synthetic`'s convention so a symbol looks consistent across
  both axes.
- `synthetic_quote(symbol, step, *, now=None) -> Quote` — pure. `step ==
  0` is the subscribe-time snapshot (every field populated); later steps
  report only `last` / `day_volume` / `ts`.
- `class SyntheticQuoteSource(tick_period: float = 0.5)` —
  `subscribe_quotes(symbols, on_quote)` returns a subscription with
  `set_symbols` / `close`.

## Dependencies
- Internal: [`streaming/quotes`](quotes.spec.md).
- External: stdlib only (`random`, `threading`, `time`).

## Design Decisions
- **One thread for the whole subscription, not one per symbol.** That is
  the defining shape of a quote source — real adapters multiplex
  hundreds of symbols over a single connection — so exercising consumers
  against a per-symbol fan-out would validate the wrong concurrency
  model and hide contention bugs that only appear when one thread walks
  the full set.
- **It emits partial updates on purpose.** The first message per symbol
  is a full picture; every message after it carries only what moved.
  Real vendors behave this way, and a consumer that quietly assumed full
  records would pass a suite built on full records and then blank out in
  production. This is the primary reason the synthetic source exists at
  all rather than tests hand-rolling `Quote` objects.
- **A symbol that leaves and returns gets a fresh snapshot.**
  `set_symbols` intersects the seen-set, so a returning symbol replays
  `step == 0`. Without it a consumer would merge price-only updates onto
  nothing and never recover `prev_close` — the same failure the real
  adapters must avoid on resubscribe after a reconnect.
- **Subscriber exceptions are swallowed**, matching
  `streaming.base`'s contract that one bad subscriber cannot kill a
  source.
- **Deterministic per `(symbol, step)`**, so a test can assert an exact
  value without freezing time.

## Invariants
- The driver thread is a daemon (process exit terminates it).
- `close()` is idempotent and never raises.
- `synthetic_quote` is pure: same inputs (including `now`) → equal
  `Quote`.
- Prices are strictly positive.

## Testing
`tests/streaming/test_quotes.py` — determinism per symbol and per step,
step 0 full vs later partial, a live subscription populates a
`QuoteBook` and yields a computable percent, a returning symbol is
re-snapshotted, and a raising subscriber does not stop the source.

## Known limitations / Future work
- The walk is memoryless (each step re-derives from the base price
  rather than from the previous step), so the series is not a realistic
  path. Adequate for wiring tests; anything asserting on *price
  behaviour* should use `tests/_fixtures/market_sim.py` instead (§7.35).

## Recent history
- Introduced with the quote axis so the live heatmap could be built and
  tested before Schwab OAuth was available.
