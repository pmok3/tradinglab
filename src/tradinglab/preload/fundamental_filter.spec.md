# `preload/fundamental_filter.py` — Spec

## Public API

* `FundamentalFilter` — frozen dataclass:
  - `min_avg_volume_millions: Optional[float] = None`
  - `min_close: Optional[float] = None`
  - `max_close: Optional[float] = None`
  - `lookback_days: int = 20`

* `is_filter_active(spec) -> bool` — True iff at least one criterion
  is set. Caller uses this to short-circuit the pre-pass fetch (no
  point hitting yfinance for 500 daily-bar series when the user left
  every filter field blank).

* `passes_fundamental_filter(daily_bars, spec) -> bool` — pure;
  given a sorted-ascending `Sequence[Candle]` and the filter spec,
  return whether the symbol qualifies.

* `filter_symbols(symbols, bars_lookup, spec) -> List[str]` —
  convenience wrapper for tests / scripts; the GUI dialog interleaves
  per-symbol lookup with progress reporting and so calls
  `passes_fundamental_filter` directly.

## Semantics

* A `None` criterion means "no constraint on this dimension". A
  filter with every criterion `None` accepts every symbol
  (`is_filter_active` returns False so the dialog skips the pre-pass).
* Min/max close are evaluated against the **last bar's close**, not
  the mean — the trader wants "right now" pricing, not a smoothed
  average.
* Average volume is computed over the last `lookback_days` bars
  inclusive. Default 20 (one trading month, the standard convention).
* A symbol with fewer than `lookback_days` bars **fails** the
  volume gate when the volume filter is active. Better to under-
  include than to over-include a thin-data ticker.
* An empty / None bars list **fails** every active criterion.

## Consumers

* `tradinglab.gui.universe_prepare_dialog` runs the filter as
  a pre-pass in its worker thread before delegating to
  `tradinglab.preload.service`.
