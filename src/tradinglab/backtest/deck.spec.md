# backtest/deck.py — Spec

## Purpose
Eligible-day enumeration + seeded shuffle for sandbox sessions. Two parallel APIs:

* **DeckEntry** flavour (`build_eligible_deck` / `shuffle_deck` / `draw_one`) — `(symbol, session_date)` pairs across a multi-symbol universe. Used by Phase 1c's symbol-and-date deck.
* **Date-only** flavour (`build_eligible_dates` / `shuffle_dates` / `draw_one_date`) — sorted list of dates, used by the open-universe (Phase 1c-redux) sandbox where the master clock is anchored on a single reference ticker (typically SPY).

Plus `filter_candles_to_session` — trim a candle list to a session window.

## Public API
- `@dataclass(frozen=True) class DeckEntry(symbol, session_date)`.
- `build_eligible_deck(candles_by_symbol, *, min_bars_per_day=20) -> List[DeckEntry]` — sorted by `(symbol, session_date)`. Canonical (re-build → identical list).
- `shuffle_deck(deck, seed) -> List[DeckEntry]` — deterministic permutation; uses an isolated `random.Random(seed)` so the global RNG is untouched.
- `draw_one(deck, seed) -> DeckEntry` — first card off a freshly-shuffled deck. `IndexError` on empty.
- `filter_candles_to_session(candles, session_date, lookback_days=5, *, bounded=False, regular_only=False) -> List[Any]` — keep bars from the Nth-most-recent **trading day** prior to `session_date` through end-of-data (or through `session_date + 1d` when `bounded=True`). `lookback_days` is interpreted as **trading days with data**, not calendar days, so a Monday session with `lookback_days=1` keeps Friday's bars (rather than letting Sunday's calendar cutoff drop them entirely). `regular_only=True` drops pre/post-market candles and excludes pre/post-only days from the lookback slot count.
- `build_eligible_dates(candles, *, min_bars_per_day=20, regular_only=False, min_lookback_days=0) -> List[date]` — sorted dates with at least `min_bars_per_day` bars. `min_lookback_days` drops the first N qualifying dates so a randomised draw always has prior context.
- `shuffle_dates(dates, seed) -> List[date]` — deterministic permutation.
- `draw_one_date(dates, seed) -> date` — `IndexError` on empty.

## Dependencies
- External: stdlib only (`random`, `collections.defaultdict`, `datetime`).

## Design Decisions
- **Date eligibility uses bar count, not market-calendar lookup**: half-trading days, IPO first-listing days, and holidays-with-stub-data are all rejected by the same `min_bars_per_day` threshold (20 by default — covers a 5-min interval normal session minus a few minutes' slack).
- **Shuffles are seeded and isolated**: `random.Random(seed)` means a recorded `deck_seed` is enough to replay an entire study, and indicator backtests / other concurrent users of `random` aren't perturbed.
- **`min_lookback_days` lives here, not in the controller**: keeping it pure (no Tk imports) means the start-dialog can apply it in `_filtered_eligible_dates` to count *drawable* dates, not just *eligible* ones.
- **`regular_only` filter at deck build time**, not at engine time: a sandbox session configured for regular hours only must compute eligibility from regular bars too, otherwise an early-close holiday with extensive pre-market data sneaks in as eligible.
- **`bounded=True` for auto-cycle**: each cycle covers exactly one session day (plus lookback context). Without bounding, the auto-cycle would let cycle N's bars leak into cycle N+1's master timeline.

## Invariants
- `build_eligible_deck` / `build_eligible_dates` are deterministic on the same input (sorted output).
- `shuffle_deck(deck, seed)` and `shuffle_dates(dates, seed)` produce the same permutation across processes / machines.
- `draw_one(empty, seed)` raises `IndexError` (not `KeyError`); same for `draw_one_date`.
- `filter_candles_to_session` returns `[]` on empty input — never `None`.

## Testing
- `check_g1_sandbox_phase1c` — `build_eligible_deck` canonical ordering + size; `shuffle_deck` determinism; `draw_one` IndexError on empty.
- `check_g2_sandbox_open_universe` — `build_eligible_dates` + `min_lookback_days` trim; `filter_candles_to_session` lookback + bounded behaviour.
- `check_b6_sandbox_auto_cycle` — `bounded=True` filtering for the auto-cycle path.
- `check_b36_lookback_trading_days_not_calendar` — `filter_candles_to_session` counts trading days (with data), not calendar days, so a Monday session with `lookback_days=1` keeps Friday's bars; `regular_only=True` ignores pre/post-only days when consuming lookback slots.

