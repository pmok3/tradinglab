# models.py — Spec

## Purpose
Defines the `Candle` dataclass — the one data type that flows end-to-end through fetchers, caches, normalizers, renderers, streaming, and indicators. Intentionally trivial: mutating a `Candle` in place (for streaming ticks) must be cheap and must preserve object identity.

## Public API
- `@dataclass class Candle`: fields `date: datetime`, `open: float`, `high: float`, `low: float`, `close: float`, `volume: int`, `session: str = "regular"`. Session ∈ `{"pre","regular","post","gap"}`.
- `Candle.is_bull: bool` (property) — `close >= open`.
- `Candle.is_extended: bool` — `session in ("pre","post")`. **Explicitly excludes `gap`** so `core.pairing.apply_pair_filter` doesn't treat placeholders as real extended-hours bars.
- `Candle.is_gap: bool` — `session == "gap"`.
- `Candle.gap(date) -> Candle` — classmethod; returns a NaN-priced, zero-volume placeholder for compare-mode timestamp alignment. NaNs are chosen so `np.nanmin`/`np.nanmax` in `core.viewport.y_limits_for_slice` automatically skip them.

## Dependencies
- Internal: none.
- External: `math` (for `math.nan`), `dataclasses`, `datetime`.

## Design Decisions
- **OHLCV semantics** — `open` = first trade in the bar's interval; `close` = last trade; `high`/`low` = extremes within the interval; `volume` = aggregate share count. The `date` field is the bar's **start** timestamp (matches yfinance and TradingView). A `5m` bar dated `09:30:00 ET` covers the half-open interval `[09:30, 09:35)`.
- **Timezone convention for `date`** — `date` may be tz-naive (then treated as the asset's local exchange tz, US/Eastern for US equities) or tz-aware (US/Eastern after normalisation). The original timezone is NOT preserved on the engine's int64 epoch timeline; reconstruct ET via the display-tz at render time.
- **`session='gap'` is fetcher-forbidden** — Only `Candle.gap()` (compare-mode alignment placeholder) emits it. Fetchers and stream sources never produce `session='gap'` directly.
- Regular `@dataclass` (not frozen, not `slots=True`): streaming updates mutate in place to preserve object identity, which matters because pair-aligned compare views and `_SeriesArrays._candles` hold the same list reference. Freezing or slotting would force/limit the cheap mutable object shape that streaming and tests rely on.
- `session` is a `str` (not `Enum`) — keeps pickling and JSON serialization free, and the literal comparisons (`== "regular"`, `in ("pre","post")`) read naturally. Performance-critical code paths already use integer-valued discriminators via `is_extended`/`is_gap` properties.
- NaN-priced gap placeholders rather than `None` so numpy operations (`nanmin`, `nanmax`, slice indexing) work without branching. `volume=0` so volume autoscale ignores them naturally.
- `is_extended` deliberately excludes `"gap"` — regression: compare-mode Pre/Post toggling used to flicker because gap placeholders inherited extended-hours filtering.

## Invariants
- `Candle.gap(d).is_gap is True`, `is_extended is False`, `is_bull is False` (since `nan >= nan` is False in Python; callers must short-circuit on `is_gap` before relying on `is_bull`).
- Mutating a `Candle`'s OHLCV fields (streaming tick) does **not** create a new object; all views pointing at it see the update.

## Data Flow / Algorithm
Trivial — pure data.

## Testing
- Implicitly exercised by almost every smoke check; `check_60_pair_filter_align` specifically exercises gap creation and `is_gap`/`is_extended` semantics.

## Known limitations
- No adjusted-close field; if dividend/split-adjusted charts become a thing, add `close_adj` alongside `close` (not replacing it — raw prices are still useful).
