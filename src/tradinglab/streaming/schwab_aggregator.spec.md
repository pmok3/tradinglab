# streaming/schwab_aggregator.py - Spec

Last updated: 2026-09-07

## Purpose
Pure Schwab field decoding and provisional 1-minute aggregation. LEVELONE
is a change-only snapshot, **not** time-and-sales; observed prints do not
prove true minute OHLC. CHART_EQUITY is authoritative by timestamp.

## Public API
- `LEVELONE_FIELDS`: numeric IDs to symbol, bid/ask prices/sizes, last price,
  cumulative day volume, high/low, previous close, trade timestamp.
- `CHART_EQUITY_FIELDS`: `0` symbol, `1` sequence, `2` open, `3` high,
  `4` low, `5` close, `6` volume, `7` chart time (epoch ms), `8` chart day.
- `decode_levelone_content(content)` and `decode_chart_equity_content(content)`
  translate numeric keys. Actual Schwab `key` takes precedence over numeric
  `0`; symbol is stripped/uppercased. Unknown fields (including envelope
  `seq`) are ignored. Missing values stay absent; no invented zero values.
- `chart_equity_to_candle(decoded) -> Candle | None`: validates complete,
  finite-positive OHLC, nonnegative volume, coherent OHLC envelope and
  timestamp; logs a sanitized warning for missing/invalid data and returns
  `None`. Timestamp is floored to a minute in **aware ET**, never host-local
  time. Session classification is ET in both DST and standard time.
- `MinuteBarBuilder()` owns one provisional bar/snapshot.
- `apply_levelone(decoded, *, now=None) -> list[tuple[str, Candle]]`.
  `now` is compatibility-only and never used as an event timestamp.
- `maybe_rollover(now)` always returns `[]`. A wall clock supplies no
  evidence of trading. No `open_initial_bar`/prior-close seeding remains.

## Dependencies
- `constants.classify_session`, `floor_to_interval`, `core.timezones.ET`,
  `core.session_calendar` boundaries, `models.Candle`; stdlib only.

## Design Decisions
- First finite-positive last trade plus vendor field 35 initializes all
  OHLC at the **observed** trade price and emits a single `rollover`.
  Same-minute snapshot changes emit `tick`. A newer minute emits only its
  observed bar; missing minutes are never manufactured.
- Trade timestamp, price and cumulative volume can arrive as separate
  deltas. Timestamp-only changes reuse last trade price, not midpoint;
  price/volume-only deltas remain at the last vendor trade timestamp.
  Missing initial timestamps never fall back to receive time. Bid/ask or
  unrelated day-high/previous-close changes emit nothing.
- Invalid numeric values do not mutate state. Valid wire price/timestamp
  deltas are retained separately from bar-emission eligibility: a backwards
  timestamp changes the cached snapshot, but not the eligible-trade watermark,
  OHLC or cumulative volume baseline. A later forward-time delta with price
  omitted therefore uses the retained price, matching the merged quote.
  Price/volume-only deltas after a backwards timestamp remain ineligible
  until a forward timestamp arrives; they cannot reuse the newer emission
  watermark as if it were the wire timestamp.
- Trade time must be weekday 04:00-20:00 ET for US equity
  provisional bars. Holidays need no speculative clock calendar: no feed
  event means no bar on a holiday, weekend, disconnect or quiet minute.
- Cumulative volume uses a persistent baseline across adjacent observed
  minutes. First cumulative is baseline (zero known delta). Later nonnegative
  deltas accumulate; same-day backwards totals do not lower the baseline
  or double-count recovered volume. Day changes and multi-minute gaps
  rebaseline rather than attribute unseen trading to the latest minute.
- Missing cumulative volume at a reset stays unknown until a new report;
  it is never treated as zero cumulative or inherited across sessions.
- Missing ET timezone support rejects bars instead of misclassifying UTC
  hours as ET.

## Invariants
- At most one event per LEVELONE content update, bounded regardless of gap.
- No zero/NaN/inf OHLC, midpoint trades, previous-close fake opens, receive
  time buckets or fabricated intervening candles.
- Approximation is explicit: first bar starts mid-minute when subscribing,
  snapshot cadence can miss extrema, and volume deltas are not exact tape
  allocation across boundaries. Consumers finalize using CHART `closed`.

## Field-map evidence
Current `schwab-py` documentation, checked 2026-09-07:
https://schwab-py.readthedocs.io/en/latest/streaming.html
Its Data Field Relabeling example uses `CHART_EQUITY` `key`/`seq` with the
numeric OHLCV/time table above. This corroborates the original numeric map;
the missing real-world `key` identifier was the decode defect.
LEVELONE differs from legacy TDA from field 10 onward: Schwab previous
close is **12**, not TDA's 15; trade time is epoch milliseconds at **35**.

## Testing
`tests/unit/test_schwab_streaming.py`: realistic field map, finite OHLC,
ET winter/summer classification, trade-time versus receive-time, partial
images/deltas, adjacent-minute cumulative volume, day reset/regressions,
long gaps, no midpoint or quiet/session/holiday clock bars.
`tests/streaming/test_schwab_connection.py` additionally drives actual
LEVELONE envelopes with suppressed backwards price changes followed by
timestamp-only/volume deltas. Bars and merged quotes converge without
backwards emissions or volume-baseline corruption, including after CHART
finalization suppresses provisional events. Source dispatch must apply
snapshot updates before filtering CHART-superseded provisional candles.
