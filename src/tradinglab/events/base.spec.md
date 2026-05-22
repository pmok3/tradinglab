# events/base.py — Spec

## Purpose
Canonical record types + fetcher protocol + registry for the earnings/dividends feature. Sparse point-in-time records (NaN for unknown), not dense series — events aren't a candle stream.

## Public API
- `@dataclass class EarningsRecord(ts, symbol, when, eps_estimate=NaN, eps_actual=NaN, revenue_estimate=NaN, revenue_actual=NaN, source="")`.
  - `is_future` property: `isnan(eps_actual)`.
  - `surprise_pct` property: signed `(act - est) / abs(est) * 100`, NaN when either side missing or estimate == 0.
- `@dataclass class DividendRecord(ex_ts, symbol, amount=0.0, kind="cash", pay_ts=0, declared_ts=0, ratio_num=1, ratio_den=1, source="")`.
  - `is_cash_event` property: `kind in {"cash","special","spinoff"}`.
  - `is_split` property: `kind == "stock_split"`.
- `@dataclass class EventBundle(symbol, earnings=[], dividends=[], fetched_at=0)`. `__post_init__` sorts both lists ascending by their primary ts axis.
- `EventFetcher = Callable[[str], Optional[EventBundle]]`.
- `EVENT_SOURCES: Dict[str, EventFetcher]`.
- `register_event_source(name, fetcher)`.

## Dependencies
Stdlib only: `math`, `dataclasses`, `typing`.

## Design Decisions
- **`ts` is UTC ms-since-epoch.** Matches tradinglab convention. Engine clock uses seconds — consumers convert at boundary (`* 1000`).
- **`when` is a closed enum** (`"BMO" | "AMC" | "DMH" | ""`). yfinance ships tz-aware datetimes; normalizer collapses to slot so display stays stable across schema drift.
- **NaN, not Optional, for unknowns.** Same posture as candle layer; numerical aggregates work without `None` guards.
- **DividendRecord covers four `kind`s.** `"cash" | "special" | "spinoff" | "stock_split"`. Keeping splits here is pragmatic — yfinance returns them from the same `actions` API call.

## Invariants
- `EventBundle.earnings` sorted ascending by `ts`.
- `EventBundle.dividends` sorted ascending by `ex_ts`.
- `ratio_num >= 1` and `ratio_den >= 1` (callers must enforce; dataclass does not validate).
