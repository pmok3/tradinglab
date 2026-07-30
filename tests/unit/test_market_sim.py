"""Contract tests for the synthetic market generator.

The generator underpins the oracle / property / soak suites, so if it drifts
those suites silently start validating the wrong thing. These tests pin the
properties those suites actually depend on:

* **Structure** — real RTH sessions, weekday-only, correct bar counts.
* **Session semantics delegated to the app** — the generator must agree with
  :mod:`tradinglab.core.session_calendar`, never with a private copy of the
  boundary numbers.
* **Cross-interval self-consistency** — required by the existing
  ``check_b29_aggregation_matches_recompute`` contract.
* **Determinism** — required by the byte-identical-journal and
  replay-determinism oracles.
* **Numeric realism** — the specific distributional properties whose absence
  made the previous fixture unable to exercise ATR / RSI / RVOL / Chandelier.
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pytest

from tests._fixtures import market_sim as ms
from tradinglab.core.session_calendar import (
    RTH_CLOSE_MIN,
    RTH_OPEN_MIN,
    classify_session,
    is_regular_session,
)

_RTH_BARS_5M = (RTH_CLOSE_MIN - RTH_OPEN_MIN) // 5  # 78


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------


def test_generates_real_multi_session_structure():
    cs = ms.candles("AAA", "5m", days=8)
    days = sorted({c.date.date() for c in cs})
    assert len(days) == 8, "one session per requested day"
    assert len(cs) == 8 * _RTH_BARS_5M, "78 RTH 5m bars per session"


def test_never_emits_weekend_sessions():
    cs = ms.candles("AAA", "5m", days=20)
    assert not [d for d in {c.date.date() for c in cs} if d.weekday() >= 5]


def test_sessions_are_separated_by_overnight_gaps():
    """Consecutive sessions must be discontinuous.

    The previous fixture ran 12.4 continuous hours across one day, so every
    session-boundary code path (VWAP daily reset, PriorDayHLC, gap_pct, the
    >=6-session RVOL warmup gate) was unreachable.
    """
    cs = ms.candles("AAA", "5m", days=6)
    by_day: dict[date, list] = {}
    for c in cs:
        by_day.setdefault(c.date.date(), []).append(c)
    days = sorted(by_day)
    assert len(days) >= 2
    for prev, cur in zip(days, days[1:], strict=False):
        assert by_day[cur][0].date > by_day[prev][-1].date
        # A gap the intraday process does not predict.
        assert by_day[cur][0].open != by_day[prev][-1].close


def test_bar_count_exceeds_rvol_warmup_gate():
    """``indicators.rvol`` needs >= 6 regular sessions or it returns all-NaN."""
    from tradinglab.indicators.rvol import _MIN_WARMUP_SESSIONS

    cs = ms.candles("AAA", "5m", days=_MIN_WARMUP_SESSIONS + 3)
    assert len({c.date.date() for c in cs}) > _MIN_WARMUP_SESSIONS


# --------------------------------------------------------------------------
# Session semantics are the APP's, not the generator's
# --------------------------------------------------------------------------


def test_session_labels_agree_with_app_classifier():
    """Every bar's label must equal what the app's own classifier returns.

    This is the load-bearing constraint: a generator carrying its own copy of
    the 09:30/16:00 boundaries would make the RTH tests validate the generator
    instead of the application.
    """
    cs = ms.candles("AAA", "1m", scenario="extended", days=3)
    assert cs
    for c in cs:
        assert c.session == classify_session(c.date.hour, c.date.minute)


def test_regular_bars_are_regular_per_app_predicate():
    cs = ms.candles("AAA", "5m", days=4)
    assert all(is_regular_session(c.date) for c in cs)


def test_extended_scenario_produces_all_three_session_classes():
    cs = ms.candles("AAA", "5m", scenario="extended", days=3)
    assert {c.session for c in cs} == {"pre", "regular", "post"}


def test_default_scenario_is_regular_only():
    cs = ms.candles("AAA", "5m", days=3)
    assert {c.session for c in cs} == {"regular"}


# --------------------------------------------------------------------------
# Cross-interval self-consistency (check_b29 contract)
# --------------------------------------------------------------------------


def _fold_by(base, step):
    groups, cur, key = [], [], None
    for c in base:
        k = (c.date.date(), (c.date.hour * 60 + c.date.minute) // step)
        if key is not None and k != key:
            groups.append(cur)
            cur = []
        key = k
        cur.append(c)
    if cur:
        groups.append(cur)
    return groups


@pytest.mark.parametrize("interval", ["2m", "5m", "15m", "30m"])
def test_aggregation_of_1m_equals_direct_fetch(interval):
    base = ms.candles("AGG", "1m", days=5)
    direct = ms.candles("AGG", interval, days=5)
    groups = _fold_by(base, ms.interval_minutes(interval))
    assert len(groups) == len(direct)
    for g, d in zip(groups, direct, strict=False):
        assert g[0].open == pytest.approx(d.open)
        assert max(x.high for x in g) == pytest.approx(d.high)
        assert min(x.low for x in g) == pytest.approx(d.low)
        assert g[-1].close == pytest.approx(d.close)
        assert sum(x.volume for x in g) == d.volume


def test_daily_bars_aggregate_the_regular_session():
    base = ms.candles("AGG", "1m", days=5)
    daily = ms.candles("AGG", "1d", days=5)
    by_day: dict[date, list] = {}
    for c in base:
        if c.session == "regular":
            by_day.setdefault(c.date.date(), []).append(c)
    assert len(daily) == len(by_day)
    for d in daily:
        grp = by_day[d.date.date()]
        assert d.open == pytest.approx(grp[0].open)
        assert d.close == pytest.approx(grp[-1].close)
        assert d.high == pytest.approx(max(x.high for x in grp))
        assert d.low == pytest.approx(min(x.low for x in grp))
        assert d.volume == sum(x.volume for x in grp)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_same_arguments_produce_identical_series():
    assert ms.candles("DET", "5m", days=6) == ms.candles("DET", "5m", days=6)


def test_different_tickers_produce_different_series():
    assert ms.candles("DTA", "5m", days=6) != ms.candles("DTB", "5m", days=6)


def test_seed_changes_the_series():
    assert ms.candles("SEED", "5m", days=6, seed=0) != ms.candles(
        "SEED", "5m", days=6, seed=1)


def test_tickers_span_two_price_magnitudes():
    """Dollar-denominated indicators behave differently at $30 vs $400."""
    prices = [ms.candles(f"T{i:02d}", "5m", days=2)[0].open for i in range(40)]
    assert min(prices) < 100.0 < max(prices)


# --------------------------------------------------------------------------
# OHLC / tick-size invariants
# --------------------------------------------------------------------------


def test_ohlc_ordering_holds():
    for c in ms.candles("OHL", "5m", scenario="earnings_gap", days=6):
        assert c.low <= min(c.open, c.close)
        assert max(c.open, c.close) <= c.high
        assert c.low <= c.high


def test_prices_are_cent_rounded():
    for c in ms.candles("CNT", "5m", days=4):
        for v in (c.open, c.high, c.low, c.close):
            assert round(v, 2) == pytest.approx(v)


def test_volume_is_non_negative_int():
    for c in ms.candles("VOL", "5m", days=4):
        assert isinstance(c.volume, int) and c.volume >= 0


# --------------------------------------------------------------------------
# Numeric realism — the properties whose absence made the old fixture inert
# --------------------------------------------------------------------------


def _closes(cs):
    return np.array([c.close for c in cs], dtype=float)


def test_price_series_is_not_degenerate():
    """The old fixture had exactly TWO distinct closes and ONE True Range."""
    cs = ms.candles("REAL", "5m", days=8)
    closes = _closes(cs)
    highs = np.array([c.high for c in cs])
    lows = np.array([c.low for c in cs])
    tr = np.maximum(highs[1:] - lows[1:],
                    np.maximum(abs(highs[1:] - closes[:-1]),
                               abs(lows[1:] - closes[:-1])))
    assert len(set(closes.round(2))) > 100, "needs a real price distribution"
    assert len(set(tr.round(4))) > 50, "constant True Range makes ATR untestable"


def test_returns_are_fat_tailed():
    """Gaussian returns have zero excess kurtosis; real 1m equity is 5-30."""
    r = np.diff(np.log(_closes(ms.candles("KRT", "1m", days=10))))
    excess = float(((r - r.mean()) ** 4).mean() / r.var() ** 2 - 3.0)
    assert excess > 1.5, f"returns too thin-tailed (excess kurtosis {excess:.2f})"


def test_volatility_clusters():
    """Big bars follow big bars: |return| must be autocorrelated."""
    r = np.abs(np.diff(np.log(_closes(ms.candles("CLU", "1m", days=10)))))
    acf = float(np.corrcoef(r[:-1], r[1:])[0, 1])
    assert acf > 0.05, f"no volatility clustering (|ret| autocorr {acf:.3f})"


def test_volume_is_u_shaped_not_monotone():
    """``rvol(mode='time_of_day')`` is meaningless without an intraday shape."""
    cs = [c for c in ms.candles("UVO", "5m", days=6) if c.session == "regular"]
    mins = np.array([c.date.hour * 60 + c.date.minute for c in cs])
    vols = np.array([c.volume for c in cs], dtype=float)
    open_v = vols[mins < RTH_OPEN_MIN + 30].mean()
    lunch_v = vols[(mins > 12 * 60) & (mins < 13 * 60 + 30)].mean()
    assert open_v > 2.0 * lunch_v, "open should print far heavier than lunch"
    assert not np.all(np.diff(vols) > 0), "monotone volume is the old tell"


def test_indicators_are_live_on_this_data():
    """ATR must vary and RSI must leave 50 — both were pinned on the old data."""
    cs = ms.candles("IND", "5m", days=10)
    closes = np.array([c.close for c in cs], dtype=float)
    delta = np.diff(closes)
    up = np.where(delta > 0, delta, 0.0)
    dn = np.where(delta < 0, -delta, 0.0)
    n = 14
    rs = up[-n:].mean() / max(dn[-n:].mean(), 1e-12)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    assert abs(rsi - 50.0) > 1.0, f"RSI pinned near 50 ({rsi:.2f}) as before"


# --------------------------------------------------------------------------
# Scenarios each make a specific branch reachable
# --------------------------------------------------------------------------


def test_earnings_gap_scenario_produces_a_large_overnight_gap():
    cs = ms.candles("ERN", "5m", scenario="earnings_gap", days=8)
    by_day: dict[date, list] = {}
    for c in cs:
        by_day.setdefault(c.date.date(), []).append(c)
    days = sorted(by_day)
    gaps = [abs(by_day[b][0].open / by_day[a][-1].close - 1.0) * 100.0
            for a, b in zip(days, days[1:], strict=False)]
    assert max(gaps) > 3.0, "earnings scenario must gap hard"


def test_half_day_scenario_shortens_one_session():
    cs = ms.candles("HAF", "5m", scenario="half_day", days=8)
    per_day: dict[date, int] = {}
    for c in cs:
        per_day[c.date.date()] = per_day.get(c.date.date(), 0) + 1
    counts = sorted(per_day.values())
    assert counts[0] < counts[-1], "one session must close early"


def test_halt_scenario_leaves_a_hole_in_the_timeline():
    cs = ms.candles("HLT", "1m", scenario="halt", days=6)
    by_day: dict[date, list] = {}
    for c in cs:
        by_day.setdefault(c.date.date(), []).append(c)
    holes = 0
    for day in by_day.values():
        mins = sorted(c.date.hour * 60 + c.date.minute for c in day)
        holes += sum(1 for a, b in zip(mins, mins[1:], strict=False) if b - a > 1)
    assert holes >= 1, "halt must break bar contiguity"


def test_illiquid_scenario_yields_zero_volume_bars_after_aggregation():
    """Zero-volume must survive aggregation or the rvol/vwap zero-denominator
    branches are never reached at the intervals the app actually charts."""
    cs = ms.candles("ILQ", "5m", scenario="illiquid", days=8)
    assert sum(1 for c in cs if c.volume == 0) > 0


@pytest.mark.parametrize("scenario", ["dst_spring", "dst_fall"])
def test_dst_scenarios_actually_flip_the_utc_offset(scenario):
    """A DST fixture with naive timestamps is inert — there is no offset."""
    cs = ms.candles("DST", "5m", scenario=scenario, days=8)
    offsets = {c.date.utcoffset() for c in cs}
    assert None not in offsets, "DST scenarios must be timezone-aware"
    assert len(offsets) == 2, f"expected an EST/EDT flip, got {offsets}"


def test_unknown_scenario_raises_helpfully():
    with pytest.raises(KeyError, match="unknown market scenario"):
        ms.candles("AAA", "5m", scenario="nope")


# --------------------------------------------------------------------------
# Fetcher adapter
# --------------------------------------------------------------------------


def test_fetcher_matches_candles_and_is_data_sources_shaped():
    f = ms.fetcher(scenario="trend", days=5)
    assert f("XYZ", "5m") == ms.candles("XYZ", "5m", scenario="trend", days=5)
    assert f("XYZ", "1wk") == ms.candles("XYZ", "1wk", scenario="trend", days=5)


def test_unsupported_interval_returns_empty():
    assert ms.candles("AAA", "3s") == []


def test_generated_timestamps_are_monotonic():
    for iv in ("1m", "5m", "1d"):
        cs = ms.candles("MON", iv, days=6)
        ts = [c.date for c in cs]
        assert ts == sorted(ts), f"{iv} timestamps must be ascending"
        assert len(set(ts)) == len(ts), f"{iv} timestamps must be unique"


def test_returns_a_fresh_mutable_list_each_call():
    """The internal simulation is cached; callers must not share state."""
    a = ms.candles("MUT", "5m", days=3)
    b = ms.candles("MUT", "5m", days=3)
    assert a is not b
    a.pop()
    assert len(ms.candles("MUT", "5m", days=3)) == len(b)


def test_generator_start_date_is_a_monday_anchor():
    cs = ms.candles("ANC", "5m", days=1)
    assert cs[0].date.date() == date(2026, 3, 2)
    assert isinstance(cs[0].date, datetime)
