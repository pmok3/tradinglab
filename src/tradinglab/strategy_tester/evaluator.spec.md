# strategy_tester/evaluator.py — Spec

## Purpose
Headless trigger-evaluation kernel for the Strategy Tester. The live `EntryEvaluator` / `ExitEvaluator` are Tk-thread-guarded (they touch `PaperBrokerEngine`, journal, indicator-manager, audit log). The mechanical tester builds its own worker-safe context, delegates trigger decisions to the shared entry / exit dispatch registries, and emits `Order`s directly into a fresh per-symbol `SandboxEngine`.

## Public API
- `evaluate_symbol(*, symbol, candles, interval, entry_strategy, exit_strategy, starting_cash, cost_model, deck_seed=0, cancel_token=None, warmup_until_ts=None, dependency_candles=None) -> SessionResult` — primary entry point. Side-effect-free apart from creating an engine in-process. Returns a standard `SessionResult` that the existing `performance.py` builders + Sandbox post-mortem renderer consume verbatim. When `cancel_token` is supplied the per-bar loop polls `cancel_token.is_cancelled()` every `_CANCEL_POLL_INTERVAL=256` bars (power-of-2 → bitmask AND on the hot path) and exits early on trip — the returned `SessionResult` is well-formed but truncated. A token whose `is_cancelled()` raises is swallowed (duck-typed contract; never gate evaluation on a probe failure). When `warmup_until_ts` is supplied (UTC epoch seconds) the per-bar loop **still ticks the engine** for bars with `ts < warmup_until_ts` (so indicators hydrate + scanner state stays consistent) but **no entry or exit triggers are checked** for those bars; the returned `SessionResult.equity_curve` is trimmed to entries with `ts >= warmup_until_ts`. `None` (the default) keeps the legacy behaviour (no warmup gate). When `dependency_candles` is supplied, it is a same-interval `{symbol: candles}` map used to build a scanner `BarsRegistry` for cross-symbol `FieldRef.symbol` conditions.
- `EvalContext` — dataclass; mutable per-symbol state. Internal but exposed for test fixtures.
- `class UnsupportedTriggerKind(NotImplementedError)` — typed signal for trigger kinds the headless path doesn't yet handle. Runner catches and marks the symbol as `error` without aborting the rest of the Run.
- `collect_dependency_symbols(entry_strategy, exit_strategy) -> set[str]` — returns non-active ticker pins referenced by entry / exit conditions, including scanner-alert scans that can be loaded from disk. The runner uses this to companion-fetch cross-symbol dependencies once per run.
- `_ENTRY_HANDLERS` — **back-compat alias** for `entries.dispatch._ENTRY_DISPATCH` (literally the same dict object). Audit item #4: the live `EntryEvaluator` and this mechanical evaluator now share a single registry, so adding a new entry-`TriggerKind` lights up both call sites at once and drift is structurally impossible. See `entries/dispatch.spec.md`. Existing tests that pop from `_ENTRY_HANDLERS` to simulate "unsupported kind" still work because the alias is the same object.
- `_EXIT_HANDLERS` — **back-compat alias** for `exits.dispatch._EXIT_DISPATCH` (literally the same dict object). The live `ExitEvaluator` and this mechanical evaluator share one exit-trigger registry; the mechanical path passes `legacy_signed_offsets=True` in `ExitTriggerContext` so old strategy-tester manifests keep their signed offset semantics.

## Decision contract
For each bar `i`:
1. `engine.tick()` advances clock to `i`, fills any pending orders at `i.open`, updates MAE/MFE on `i.H/L`, marks-to-market at `i.close`.
2. **ET conversion**: bar timestamps (UTC epoch seconds) are converted to America/New_York via the **vectorized helper `_compute_et_arrays(bars.ts)`** called ONCE per symbol before the per-bar loop. It returns `(et_date_ints, rth_mask, et_offsets_sec)` — numpy arrays giving each bar's days-since-1970 in ET, Mon-Fri 09:30-16:00 RTH membership, and signed UTC offset (EST=-18000s, EDT=-14400s). The hot loop reads `et_date_ints[i]` / `rth_mask[i]` instead of calling `datetime.fromtimestamp(ts, _ET)` per bar (which walks the zoneinfo transition table). On a 25k-bar 5m × 1y run this trims ~25k slow zoneinfo allocations per symbol down to one numpy pass + ~250-500 zoneinfo probes (one per unique UTC day in the input). DST safety: each unique UTC day is probed at BOTH 00:00 UTC and 23:59:59 UTC; for the ~363 non-transition days/year the two probes agree and the offset broadcasts to every bar in that UTC day, while the ~2 transition days/year (where the probes disagree because the 02:00 ET switch = 07:00 UTC falls inside the day) get per-bar offset resolution so every bar lands on the right side of the switch. A real `datetime` is still constructed (via the cheap `_bar_ts_to_et(ts)` slow path) per bar **only when the strategy has an arm_window gate configured** — that's the one gate whose HH:MM compare can't be served by the precomputed ints. `require_market_open` is served by `rth_mask[i]` (no datetime construction). All time gates (`arm_window_start/end`, `require_market_open`, TIME_OF_DAY exit cutoff) still compare in ET — output is bit-for-bit identical to the prior per-bar implementation.
3. **Per-ET-day session reset**: if `et_date_ints[i] != ctx.current_session_et_date` (both stored as days-since-epoch ints — integer compare in the hot loop), reset `fires_total = 0` and `fires_by_symbol = 0` BEFORE checking entries. This mirrors the live `EntryEvaluator._roll_session_counters_if_needed` semantics (with ET correctness; live uses UTC). Without this, `max_fires_per_session_per_symbol=1` caps the entire backtest at 1 entry per symbol — the smoking-gun "AAPL/NVDA/SPY each have 1 trade on a year of 5m" bug.
4. **Per-ET-day `eod_kill_switch` flatten**: if the ET date rolled AND `exit_strategy.eod_kill_switch=True` AND a position is still open from the prior trading day, synthesise an exit fill at the **last regular-session bar at or before `i-1`** (via `_find_last_rth_bar_at_or_before(bars, i-1, rth_mask=rth_mask)` — the precomputed numpy mask turns the walk-back into a single O(idx) `np.flatnonzero` scan instead of a Python loop of zoneinfo lookups) using the cost model's slippage + commission. The RTH-only walk-back is REQUIRED: 1-minute yfinance data routinely includes extended-hours bars (premarket 04:00 ET, postmarket up to 20:00 ET); without the RTH filter the kill would flatten at e.g. 19:55 ET postmarket close, producing incorrect P&L and screenshots dated at extended-hours prices ("market-on-close at 15:55 ET" is the documented behaviour). If no RTH bar exists in `[0, i-1]` (extremely rare: all prior bars premarket), the kill is **silently skipped** — the position stays open and the next bar's normal processing continues. Without per-day kill, a strategy with `position_already_open_policy=BLOCK` and no intraday-firing stop will hold a position across all days and the daily reset is moot. Same code path as the end-of-run kill switch.
5. `_sync_position_state_from_engine` mirrors engine state into the EvalContext.
6. If a position is open, check every enabled exit-leg trigger against `i.O/H/L/C`. First leg to fire wins; `submit_order` is queued and will fill at `i+1.open`.
7. Otherwise check the entry trigger; **enforce the time-of-day gates in this order**: arm_window → require_market_open → cooldown_secs → fires_total/fires_by_symbol caps → trigger handler. Size via `_compute_quantity(decision_price=i.close)`; submit if `qty > 0`. On fire, set `ctx.last_fire_ts = ts`.

## Time-of-day gates (`_check_entry`)
- **`arm_window_start/end`** — `"HH:MM"` strings; default `"09:35"/"15:30"` ET. Blank string disables the gate (mirrors live `_parse_hhmm("")` → None). Supports midnight wrap (start > end → "fire if t >= start OR t <= end").
- **`require_market_open`** — `True` by default. Blocks Saturday/Sunday and any time outside 09:30-16:00 ET. Does NOT enforce holidays (would require a calendar dep); acceptable for user-supplied backtest data. **Auto-skipped on non-intraday intervals** (1d / 1wk / 1mo) — same `is_intraday(interval)` gate the runner's `_filter_rth_only` uses (audit `daily-rth-bypass`). Daily bars are timestamped 00:00 ET (outside RTH) and the "regular trading hours" concept is meaningless for a bar summarising an entire session; without this skip, every daily-timeframe strategy that left the flag at the default produced zero trades.
- **`cooldown_secs`** — `0` by default. Blocks fires when `(bar_ts - ctx.last_fire_ts) < cooldown_secs`.

## TIME_OF_DAY exit ET fix
`_check_exits` passes `now = _bar_ts_to_et(int(bar_ts))` into `exits.dispatch.ExitTriggerContext` for `TIME_OF_DAY` triggers. Template cutoffs like `"15:55"` are unambiguously ET; comparing against UTC would fire 5h early.

## Trigger scope (all wired)
Wired:
- **Entry**: MARKET, LIMIT, STOP, STOP_LIMIT, INDICATOR, **SCANNER_ALERT** — dispatched via the shared `entries.dispatch._ENTRY_DISPATCH` registry (aliased as `_ENTRY_HANDLERS`). The mechanical `_check_entry` builds an `entries.dispatch.TriggerContext` (filling in `scanner_eval_ctx` once per symbol, `normalized_conditions` once per evaluator, `scanner_alert_prev_match` per-bar) and calls `entries.dispatch.check_trigger_fires`. Same code path as the live `EntryEvaluator`. See `entries/dispatch.spec.md`.
- **Exit**: MARKET, LIMIT, STOP, STOP_LIMIT, INDICATOR, **TRAILING_STOP**, **TIME_OF_DAY**, **CHANDELIER** — dispatched via the shared `exits.dispatch._EXIT_DISPATCH` registry (aliased as `_EXIT_HANDLERS`). The mechanical `_check_exits` builds an `exits.dispatch.ExitTriggerContext` and calls `exits.dispatch.check_trigger_decision`. See `exits/dispatch.spec.md`.
- `eod_kill_switch` (synthetic flatten on last bar)

Every `TriggerKind` enum value has a handler. `UnsupportedTriggerKind` is now
the defensive "missing-handler" fallback only — it should never fire in
practice unless a new kind is added to the schema before its handler. The
mechanical `_check_entry` / `_check_exits` raise `UnsupportedTriggerKind`
BEFORE invoking dispatch when a trigger kind is missing from the shared
registry, preserving the typed runner contract even though shared dispatch
silently no-fires on unknown kinds.

### TRAILING_STOP / TIME_OF_DAY / CHANDELIER exits
All three delegate through `exits.dispatch` to the **pure-function
evaluators in `exits/spec.py`** — the same source-of-truth the live
`ExitEvaluator` uses. The strategy tester's `_check_exits`:
1. Builds a `positions.model.Position` from `EvalContext.position_*` via
   `_ctx_to_position(ctx)`.
2. Builds an `exits.spec.Bar` from the current `_BarTuple` via
   `_bar_to_specbar(bar, ts)` (tz-aware UTC datetime from epoch seconds).
3. Looks up / creates a `TriggerState` keyed by `trigger.id` in
   `EvalContext.trigger_states`.
4. For TRAILING_STOP dispatch calls `update_trail_state(...)`
   then `evaluate_trailing_stop(...)`. ATR variant short-circuits when
   `atr_value` is unavailable (no `BarsRegistry`).
5. For TIME_OF_DAY dispatch calls `evaluate_time_of_day(..., now=ET bar time)`.
6. For CHANDELIER dispatch calls `update_chandelier_state(...)` then
   `evaluate_chandelier_stop(...)`. Activation-bar (entry bar) is seeded with
   `is_activation=True` so the rolling-high/low window starts from the entry
   bar's H/L; subsequent bars advance with `is_activation=False`. ATR
   warm-up requires `atr_period` non-activation bars.

### Per-trigger state reset on position activation
`EvalContext` carries:
- `trigger_states: dict[str, TriggerState]` — per-trigger HWM / chandelier
  window / ATR state.
- `prev_position_open: bool` — previous bar's open/closed state.
- `scanner_alert_prev_match: dict[str, bool]` — per-SCANNER_ALERT-trigger
  previous-bar match state.

`evaluate_symbol` detects position-open transitions (False → True between
ticks) and calls `_reset_trigger_states_on_activation(ctx, exit_strategy,
bar, ts)`:
- Clears `trigger_states` entirely (fresh state for the new position).
- Seeds CHANDELIER state by calling `update_chandelier_state(state, trigger,
  position, bar, is_activation=True)` for every enabled CHANDELIER leg.

### SCANNER_ALERT entries
`_build_normalized_conditions(...)` loads the saved `ScanDefinition` once via
`scanner.storage.load(scanner_id)` and normalises its `.root` Group
intervals to the test's outer interval (same `_normalize_intervals` path as
INDICATOR triggers; same multi-interval limitation). The shared
`entries.dispatch` SCANNER_ALERT handler then evaluates the root group per
bar and fires on **edge transitions** (False/None → True):
- **Bar 0**: observes the current match state into
  `scanner_alert_prev_match[trigger.id]` and returns no-fire. This avoids
  the backtest trap where every already-matching symbol fires on day 1.
- **Bars 1+**: fires when `prev == False AND current == True`; updates the
  cache regardless. Missing scanner ID (FileNotFoundError) is logged once
  and treated as silent no-fire for the rest of the run.

This diverges from the live `ScanRunner` semantic (which treats first match
after empty history as new) — deliberately, to keep backtest results sane.
Documented in the handler docstring.

INDICATOR triggers delegate to `scanner.engine.evaluate_group` against a
per-symbol `EvaluationContext` built once outside the bar loop. The
context's `current_index` is mutated each bar so the `IndicatorMemo`
cache stays warm (O(n), not O(n²)) across the entire symbol scan.
After `_build_normalized_conditions` creates the per-trigger condition
cache, `evaluate_symbol` calls `_prewarm_indicator_memos(...)` once to
walk every referenced indicator `FieldRef`, dedupe by
`(symbol, interval, kind_id, params)`, and populate the appropriate
active-symbol or `BarsRegistry` dependency `IndicatorMemo` before the
bar loop. This moves the first full-array indicator compute out of
trigger dispatch and ensures duplicate refs across entry/exit
conditions share one computed output dict.
When `dependency_candles` is non-empty, `evaluate_symbol` builds a
same-interval `BarsRegistry` containing the active symbol plus every
dependency symbol; cross-symbol `FieldRef.symbol` references then
resolve through the scanner engine's timestamp-snap rule. The
strategy's per-trigger `interval` falls back to the outer `interval`
passed to `evaluate_symbol`; true cross-interval evaluation is still
normalized to the test interval by `_build_normalized_conditions`.
Indicator-side exceptions are logged via `logging` and treated as "no
fire" so a broken indicator never aborts an entire Run.

**Single-interval normalization.** Saved scanner conditions carry
per-`Condition`/`FieldRef` ``interval`` slots that default to
``"5m"`` (per `scanner.model`). When the strategy tester runs at a
different interval (e.g. ``"1d"``) with no `BarsRegistry`, the
scanner's cross-interval gate would silently return ``None`` for
every leaf — producing zero fires across the entire universe. To
prevent that, `evaluate_symbol` calls `_build_normalized_conditions`
once per symbol to deep-clone every INDICATOR trigger's condition
tree with all internal intervals forced to match the test's outer
interval. The normalized cache is keyed by ``trigger.id`` and
threaded to the indicator handlers, which look up the rewritten tree
instead of the original. The input strategy objects are never
mutated.

Multi-leg OCO is reduced to first-leg-to-fire. Proper OCO semantics are still deferred.

## Dependencies
- `backtest.engine.SandboxEngine`, `backtest.session.SessionResult / SessionSpec / ENGINE_VERSION`
- `backtest.bars.from_candles`
- `backtest.orders.Order / Side` (imported as `OrderSide` to disambiguate from `core.side.Side`)
- `core.side.Side` — position-direction value type. Pilot adopter per audit #10 / `core/side.spec.md`. All `position_side` string compares (`"buy"` / `"sell"`) inside the evaluator route through `Side.from_str(...)` at the function entry; the persisted `_BarTuple` / `ctx.position_side` / `PostTradeReview.side` strings stay unchanged.
- `core.bars_registry.BarsRegistry` + `data.multi_interval_cache.MultiIntervalCache` — same-interval registry for cross-symbol scanner FieldRefs.
- `backtest.fills.apply_fills` (only for the EOD kill-switch flatten path)
- `entries.model` / `exits.model` enums + dataclasses
- `models.Candle`
- `.model.CostModel`
- `scanner.engine.{make_context, evaluate_group, EvaluationContext}` (INDICATOR triggers)
- `scanner.storage.load` + `scanner.model.ScanDefinition` (SCANNER_ALERT entries)
- `exits.dispatch.{ExitTriggerContext, check_trigger_decision, _EXIT_DISPATCH}` — shared exit-trigger registry. The mechanical context uses `legacy_signed_offsets=True`.
- `exits.spec.{Bar, TriggerState, update_chandelier_state}` — adapter dataclasses + activation seeding for CHANDELIER state
- `positions.model.Position` — adapter dataclass produced by `_ctx_to_position` for spec.py evaluators

## Design Decisions
- **Position-state mirror, not duplicate state** — `EvalContext` carries strategy-level flags (fires_total, fires_by_symbol, initial_stop_price) but the actual open-position quantity / avg_cost comes from `engine.portfolio.positions[sym]`. Single source of truth, prevents drift.
- **Decision price = bar `i` close** (resolved before any new orders are submitted). Fill price = bar `i+1` open ± slippage. This matches the live evaluator's "decide at close, fill next open" canonical contract.
- **Sizing capped at starting_cash for FIXED_NOTIONAL** — opinionated; prevents accidental 10x leverage from a misconfigured strategy. `FIXED_QTY` is honored verbatim.
- **Exit checks run before entry checks on the same bar** — an open position must clear before re-entry on the same bar. Matches live evaluator.
- **EOD kill-switch is a final-bar synthetic fill via direct `_apply_fill_with_tracking`** — bypasses the tick loop (the loop is exhausted). Uses the **last RTH bar's open** as the fill price (walk-back via `_find_last_rth_bar_at_or_before(bars, n-1)`), slippage included. If no RTH bar exists in the window the end-of-run kill is silently skipped (position stays "open at end" in `SessionResult`).
- **Registry-based dispatch** — new trigger kinds register a handler in `entries.dispatch` or `exits.dispatch` and immediately work in both live and mechanical evaluators. Avoids scattered `if kind == X` blocks.
- **Worker isolation via `UnsupportedTriggerKind`** — distinct from `ValueError` / `RuntimeError` so the runner can map it specifically to a per-symbol error message without abort.
- **Shared exit dispatch** — PRICE, INDICATOR, TRAILING_STOP, TIME_OF_DAY, and CHANDELIER exit fire decisions use `exits.dispatch`, the same registry the live evaluator calls. Strategy tester still translates `EvalContext` ↔ `Position` / `Bar` dataclasses and threads `TriggerState` keyed by `trigger.id`.
- **Position-open transition triggers state reset** — Each new position gets fresh per-trigger state (HWM, chandelier window, ATR). Detection via `prev_position_open` flag. CHANDELIER triggers additionally get an activation-bar seed call so their rolling-extremum window starts from the entry bar.
- **SCANNER_ALERT bar-0 observes only** — deliberate divergence from live `ScanRunner` semantics. A live scanner fires on the *first* match it ever sees (after empty history); in a backtest that would fire every already-matching symbol on bar 0, which is meaningless. Bar 0 just snapshots match state; first real fire candidate is bar 1.

## Invariants
- Returns a valid `SessionResult` for every call (even with zero candles — empty fills + zero equity_curve).
- Never mutates the input `EntryStrategy` / `ExitStrategy` objects.
- Engine `bars_by_symbol` always contains exactly the one symbol under evaluation (per-symbol independent capital).
- `SessionResult.spec.tickers == (symbol,)`.

## Testing
- `tests/unit/strategy_tester/test_evaluator.py` — entry MARKET fires on first bar; LIMIT entry fires when bar.low touches; STOP entry fires when bar.high touches; FIXED_NOTIONAL sizing rounds DOWN; position open blocks re-entry; exit STOP closes on touch; EOD kill-switch flattens at end; defensive `UnsupportedTriggerKind` fallback (via registry-pop test); INDICATOR entry fires when `close > threshold` becomes true; INDICATOR entry never fires for an unreachable threshold; INDICATOR with `condition=None` silently doesn't fire; INDICATOR exit closes the position when its condition triggers; active and dependency indicator prewarm deduplicates repeated refs; **TRAILING_STOP percent fires on retrace, no fire on uninterrupted uptrend, dollar unit honoured**; **TIME_OF_DAY fires at/after cutoff (ET), no fire before, malformed string = no fire**; **CHANDELIER fires after ATR warm-up, no fire during warm-up window**; **SCANNER_ALERT fires on edge False → True, no fire when already matching, missing scanner ID = silent no-fire**; **`max_fires_per_session_per_symbol` resets on ET-date roll** (STACK + max=1 + 5-day timeline → 5 BUYs); **`arm_window` blocks bars outside 09:35-15:30 ET**; **blank arm_window strings disable the gate**; **`require_market_open=True` blocks Saturday bars**; **`require_market_open=False` allows weekends**; **`cooldown_secs=600` throttles fires to every other 5m bar**; **`cooldown_secs=0` allows every bar**; **per-day `eod_kill_switch=True` flattens overnight positions** (BLOCK + 3-day trending timeline + no intraday stop → 3 BUYs + 3 SELLs).
- `tests/smoke/test_smoke_strategy.py::check_st0_kernel_only` — 3 synthetic tickers + MARKET entry + STOP exit, validates `SessionResult` has ≥1 fill and per-symbol JSON parses.

## See also
- [model](model.spec.md), [runner](runner.spec.md)
- `entries/evaluator.spec.md` — live counterpart (Tk-bound; cannot be reused).
- `backtest/engine.spec.md` — kernel.
