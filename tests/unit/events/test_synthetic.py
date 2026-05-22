"""Unit tests for :mod:`tradinglab.events.synthetic_events`.

The synthetic generator is the bedrock of the offline test suite — it
must be:

* **Deterministic**: same ticker, same Python interpreter → byte-identical
  output. Two calls with the same ticker must yield equal bundles.
* **Past-vs-future discipline**: rows whose ts is in the past must have
  finite EPS actuals; rows whose ts is in the future must have NaN
  actuals.
* **Bundle invariants**: earnings sorted by ts, dividends sorted by
  ex_ts (enforced by :class:`EventBundle.__post_init__`).
* **Quarterly cadence**: ~91-day spacing between earnings, divs offset
  ~45 days from earnings.
* **Empty input → None**: blank ticker → None.
* **Always non-None for non-empty input**: the synthetic generator
  doesn't fail. Real providers may return None for delisted symbols;
  the synthetic source mirrors a "well-traded ticker".

The seed-determinism guarantee is the most important property — smoke
tests rely on it to assert deterministic-replay invariants across runs.
"""
from __future__ import annotations

import datetime as _dt
import math

from tradinglab.events.base import EventBundle
from tradinglab.events.synthetic_events import fetch_synthetic_events


_EPOCH = _dt.datetime(1970, 1, 1)


def _today_ms() -> int:
    return int((_dt.datetime.utcnow() - _EPOCH).total_seconds() * 1000)


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------

def test_synthetic_is_deterministic_per_ticker():
    a = fetch_synthetic_events("AAPL")
    b = fetch_synthetic_events("AAPL")
    assert a is not None and b is not None
    # Bundle equality, axis by axis.
    assert a.symbol == b.symbol
    assert len(a.earnings) == len(b.earnings)
    assert len(a.dividends) == len(b.dividends)
    for x, y in zip(a.earnings, b.earnings):
        assert x.ts == y.ts
        assert x.when == y.when
        assert x.eps_estimate == y.eps_estimate
        # NaN==NaN is False, so compare NaN-ness separately.
        if math.isnan(x.eps_actual):
            assert math.isnan(y.eps_actual)
        else:
            assert x.eps_actual == y.eps_actual
    for x, y in zip(a.dividends, b.dividends):
        assert x.ex_ts == y.ex_ts
        assert x.kind == y.kind
        if math.isnan(x.amount):
            assert math.isnan(y.amount)
        else:
            assert x.amount == y.amount
        assert x.ratio_num == y.ratio_num
        assert x.ratio_den == y.ratio_den


def test_synthetic_differs_per_ticker():
    a = fetch_synthetic_events("AAPL")
    b = fetch_synthetic_events("MSFT")
    # Seed differs by ticker — at minimum first earnings ts should
    # differ. (Vanishingly unlikely to collide.)
    assert a.earnings[0].ts != b.earnings[0].ts \
        or a.dividends[0].amount != b.dividends[0].amount


# ---------------------------------------------------------------------------
# past-vs-future discipline
# ---------------------------------------------------------------------------

def test_synthetic_past_earnings_have_finite_actuals():
    bundle = fetch_synthetic_events("AAPL")
    today_ms = _today_ms()
    past = [r for r in bundle.earnings if r.ts <= today_ms]
    assert past, "synthetic should always produce some past prints"
    assert all(not math.isnan(r.eps_actual) for r in past), \
        "past earnings must have finite actuals"
    assert all(not math.isnan(r.eps_estimate) for r in past), \
        "past earnings must have finite estimates"


def test_synthetic_future_earnings_have_nan_actuals():
    bundle = fetch_synthetic_events("AAPL")
    today_ms = _today_ms()
    future = [r for r in bundle.earnings if r.ts > today_ms]
    # Future rows may or may not exist depending on when the base date
    # is shifted from today; guard the assertion.
    if future:
        assert all(math.isnan(r.eps_actual) for r in future), \
            "future earnings must have NaN actuals"
        assert all(math.isnan(r.revenue_actual) for r in future), \
            "future earnings must have NaN revenue actuals"
        # Estimates remain finite.
        assert all(not math.isnan(r.eps_estimate) for r in future)


# ---------------------------------------------------------------------------
# bundle sort + structure invariants
# ---------------------------------------------------------------------------

def test_synthetic_earnings_sorted_ascending():
    bundle = fetch_synthetic_events("AAPL")
    ts_list = [r.ts for r in bundle.earnings]
    assert ts_list == sorted(ts_list)


def test_synthetic_dividends_sorted_ascending():
    bundle = fetch_synthetic_events("AAPL")
    ex_ts_list = [d.ex_ts for d in bundle.dividends]
    assert ex_ts_list == sorted(ex_ts_list)


def test_synthetic_quarterly_cadence_approx_91_days():
    bundle = fetch_synthetic_events("AAPL")
    # Spot-check the first few intervals; should all be ~91 days
    # (the generator uses `int(91 * i)`).
    MS_PER_DAY = 86_400_000
    diffs_days = [
        (bundle.earnings[i + 1].ts - bundle.earnings[i].ts) // MS_PER_DAY
        for i in range(min(5, len(bundle.earnings) - 1))
    ]
    assert all(abs(d - 91) <= 1 for d in diffs_days), diffs_days


def test_synthetic_bmo_amc_alternate():
    bundle = fetch_synthetic_events("AAPL")
    whens = [r.when for r in bundle.earnings[:6]]
    # Either BMO,AMC,BMO,AMC,... or AMC,BMO,AMC,BMO,... — accept either
    # as long as adjacent rows differ.
    for prev, cur in zip(whens, whens[1:]):
        assert prev != cur, f"adjacent earnings slots collide: {whens}"


# ---------------------------------------------------------------------------
# dividends: cash + special + maybe-split
# ---------------------------------------------------------------------------

def test_synthetic_emits_at_least_one_special_dividend():
    bundle = fetch_synthetic_events("AAPL")
    specials = [d for d in bundle.dividends if d.kind == "special"]
    assert len(specials) >= 1


def test_synthetic_cash_dividends_have_positive_amount():
    bundle = fetch_synthetic_events("AAPL")
    cash = [d for d in bundle.dividends if d.kind == "cash"]
    assert cash, "synthetic should always produce cash dividends"
    assert all(d.amount > 0 for d in cash)


def test_synthetic_split_when_present_is_2_for_1():
    # Iterate several tickers; at least one will hit the 30%-split branch.
    for tk in ("AAPL", "MSFT", "GOOG", "META", "AMZN", "NVDA", "TSLA",
               "AMD", "ORCL", "INTC"):
        bundle = fetch_synthetic_events(tk)
        splits = [d for d in bundle.dividends if d.kind == "stock_split"]
        for s in splits:
            assert s.ratio_num == 2
            assert s.ratio_den == 1
            assert math.isnan(s.amount)


# ---------------------------------------------------------------------------
# Empty/invalid input
# ---------------------------------------------------------------------------

def test_synthetic_empty_ticker_returns_none():
    assert fetch_synthetic_events("") is None
    assert fetch_synthetic_events("   ") is None


def test_synthetic_uppercases_input():
    a = fetch_synthetic_events("aapl")
    b = fetch_synthetic_events("AAPL")
    assert a is not None and b is not None
    assert a.symbol == "AAPL"
    assert b.symbol == "AAPL"
    # Same seed → same output.
    assert len(a.earnings) == len(b.earnings)
    assert a.earnings[0].ts == b.earnings[0].ts


# ---------------------------------------------------------------------------
# Type
# ---------------------------------------------------------------------------

def test_synthetic_returns_event_bundle_type():
    bundle = fetch_synthetic_events("AAPL")
    assert isinstance(bundle, EventBundle)
    assert bundle.fetched_at > 0
