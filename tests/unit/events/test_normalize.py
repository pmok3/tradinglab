"""Unit tests for :mod:`tradinglab.events.normalize`.

Covers the yfinance schema-drift variant matrix that
:func:`normalize_earnings_df` and :func:`normalize_actions_df` claim to
tolerate. Builds tiny synthetic pandas DataFrames per column variant
and asserts the canonical records round-trip cleanly.

Variants exercised:
* EPS estimate column: ``EPS Estimate`` / ``Estimate`` / ``EPS_Estimate``
* EPS actual column: ``Reported EPS`` / ``EPS Actual`` / ``Actual``
* Revenue estimate: ``Revenue Estimate`` / ``Rev Estimate``
* Revenue actual: ``Revenue Actual`` / ``Reported Revenue`` / ``Revenue``
* Dividends column: ``Dividends`` / ``Dividend``
* Stock splits column: ``Stock Splits`` / ``Splits``

Plus:
* Missing-column tolerance (NaN fields)
* Case-insensitive column lookup
* Tz-aware index decode (slot inference and UTC midnight floor)
* Split-ratio float → (num, den) for forward / reverse / no-op
* Cash+split on a single row emits two records
* Empty / None frames return []
* Output sorted ascending by ts / ex_ts
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from tradinglab.events.base import DividendRecord, EarningsRecord
from tradinglab.events.normalize import (
    EARNINGS_ACT_VARIANTS,
    EARNINGS_EST_VARIANTS,
    REVENUE_ACT_VARIANTS,
    REVENUE_EST_VARIANTS,
    coerce_float,
    date_to_midnight_ms,
    normalize_actions_df,
    normalize_earnings_df,
    slot_from_hour,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ny_dt(year, month, day, hour=8, minute=0):
    """Build a tz-aware US/Eastern Timestamp."""
    return pd.Timestamp(year=year, month=month, day=day,
                        hour=hour, minute=minute, tz="America/New_York")


def _make_earn_df(*, est_col="EPS Estimate", act_col="Reported EPS",
                  rev_est_col=None, rev_act_col=None,
                  rows=None):
    """Build a yfinance-style earnings DataFrame with configurable columns."""
    if rows is None:
        rows = [
            (_ny_dt(2024, 1, 25, 16, 5), 2.10, 2.18, None, None),
            (_ny_dt(2024, 4, 25, 16, 5), 1.50, 1.45, None, None),
        ]
    data = {est_col: [], act_col: []}
    if rev_est_col:
        data[rev_est_col] = []
    if rev_act_col:
        data[rev_act_col] = []
    idx = []
    for (i, est, act, rev_est, rev_act) in rows:
        idx.append(i)
        data[est_col].append(est)
        data[act_col].append(act)
        if rev_est_col:
            data[rev_est_col].append(rev_est)
        if rev_act_col:
            data[rev_act_col].append(rev_act)
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx))


# ---------------------------------------------------------------------------
# coerce_float
# ---------------------------------------------------------------------------

def test_coerce_float_passthrough():
    assert coerce_float(1.5) == 1.5
    assert coerce_float(0) == 0.0
    assert coerce_float("2.5") == 2.5


def test_coerce_float_nan_inputs():
    assert math.isnan(coerce_float(None))
    assert math.isnan(coerce_float(math.nan))
    assert math.isnan(coerce_float("not-a-number"))
    assert math.isnan(coerce_float(pd.NA))
    assert math.isnan(coerce_float(pd.NaT))


def test_coerce_float_inf_collapses_to_nan():
    assert math.isnan(coerce_float(float("inf")))
    assert math.isnan(coerce_float(float("-inf")))


# ---------------------------------------------------------------------------
# date_to_midnight_ms / slot_from_hour
# ---------------------------------------------------------------------------

def test_date_to_midnight_ms_epoch():
    import datetime as dt
    assert date_to_midnight_ms(dt.date(1970, 1, 1)) == 0


def test_date_to_midnight_ms_known_value():
    import datetime as dt
    # 2024-01-01 UTC midnight = 1704067200 sec = 1704067200000 ms
    assert date_to_midnight_ms(dt.date(2024, 1, 1)) == 1704067200000


def test_slot_from_hour_classification():
    assert slot_from_hour(7) == "BMO"
    assert slot_from_hour(8) == "BMO"
    assert slot_from_hour(10) == "DMH"
    assert slot_from_hour(15) == "DMH"
    assert slot_from_hour(16) == "AMC"
    assert slot_from_hour(17) == "AMC"
    assert slot_from_hour(None) == ""


# ---------------------------------------------------------------------------
# Column-variant matrix: earnings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("est_col", list(EARNINGS_EST_VARIANTS))
def test_earnings_estimate_column_variants(est_col):
    df = _make_earn_df(est_col=est_col, act_col="Reported EPS")
    recs = normalize_earnings_df(df, symbol="AAPL", source="yfinance")
    assert len(recs) == 2
    assert all(not math.isnan(r.eps_estimate) for r in recs)


@pytest.mark.parametrize("act_col", list(EARNINGS_ACT_VARIANTS))
def test_earnings_actual_column_variants(act_col):
    df = _make_earn_df(est_col="EPS Estimate", act_col=act_col)
    recs = normalize_earnings_df(df, symbol="AAPL", source="yfinance")
    assert len(recs) == 2
    assert all(not math.isnan(r.eps_actual) for r in recs)


@pytest.mark.parametrize("rev_est_col", list(REVENUE_EST_VARIANTS))
def test_revenue_estimate_column_variants(rev_est_col):
    rows = [(_ny_dt(2024, 1, 25, 16, 5), 2.10, 2.18, 9.5e10, 9.6e10)]
    df = _make_earn_df(rev_est_col=rev_est_col,
                       rev_act_col="Revenue Actual",
                       rows=rows)
    recs = normalize_earnings_df(df, symbol="AAPL", source="yfinance")
    assert recs[0].revenue_estimate == pytest.approx(9.5e10)


@pytest.mark.parametrize("rev_act_col", list(REVENUE_ACT_VARIANTS))
def test_revenue_actual_column_variants(rev_act_col):
    rows = [(_ny_dt(2024, 1, 25, 16, 5), 2.10, 2.18, 9.5e10, 9.6e10)]
    df = _make_earn_df(rev_est_col="Revenue Estimate",
                       rev_act_col=rev_act_col,
                       rows=rows)
    recs = normalize_earnings_df(df, symbol="AAPL", source="yfinance")
    assert recs[0].revenue_actual == pytest.approx(9.6e10)


def test_earnings_case_insensitive_column_lookup():
    rows = [(_ny_dt(2024, 1, 25, 16, 5), 2.10, 2.18, None, None)]
    df = _make_earn_df(est_col="eps estimate", act_col="reported eps",
                       rows=rows)
    recs = normalize_earnings_df(df, symbol="X", source="yfinance")
    assert len(recs) == 1
    assert recs[0].eps_estimate == pytest.approx(2.10)
    assert recs[0].eps_actual == pytest.approx(2.18)


def test_earnings_missing_columns_yield_nan_fields():
    # Frame has only the est column; act + revenue cols absent.
    df = pd.DataFrame({"EPS Estimate": [1.0, 2.0]},
                      index=pd.DatetimeIndex([
                          _ny_dt(2024, 1, 25, 16, 5),
                          _ny_dt(2024, 4, 25, 16, 5),
                      ]))
    recs = normalize_earnings_df(df, symbol="X", source="yfinance")
    assert len(recs) == 2
    assert all(math.isnan(r.eps_actual) for r in recs)
    assert all(math.isnan(r.revenue_estimate) for r in recs)
    assert all(math.isnan(r.revenue_actual) for r in recs)


# ---------------------------------------------------------------------------
# Index / slot decode
# ---------------------------------------------------------------------------

def test_earnings_tz_aware_index_floors_to_utc_midnight():
    # 2024-01-25 16:05 ET floors to 2024-01-25 00:00 UTC midnight.
    import datetime as dt
    expected = date_to_midnight_ms(dt.date(2024, 1, 25))
    rows = [(_ny_dt(2024, 1, 25, 16, 5), 1.0, 1.1, None, None)]
    df = _make_earn_df(rows=rows)
    recs = normalize_earnings_df(df, symbol="X", source="yfinance")
    assert recs[0].ts == expected


def test_earnings_slot_amc_at_16_00_et():
    rows = [(_ny_dt(2024, 1, 25, 16, 5), 1.0, 1.1, None, None)]
    df = _make_earn_df(rows=rows)
    recs = normalize_earnings_df(df, symbol="X", source="yfinance")
    assert recs[0].when == "AMC"


def test_earnings_slot_bmo_pre_market():
    rows = [(_ny_dt(2024, 1, 25, 7, 0), 1.0, 1.1, None, None)]
    df = _make_earn_df(rows=rows)
    recs = normalize_earnings_df(df, symbol="X", source="yfinance")
    assert recs[0].when == "BMO"


def test_earnings_slot_dmh_intraday():
    rows = [(_ny_dt(2024, 1, 25, 12, 0), 1.0, 1.1, None, None)]
    df = _make_earn_df(rows=rows)
    recs = normalize_earnings_df(df, symbol="X", source="yfinance")
    assert recs[0].when == "DMH"


def test_earnings_output_sorted_ascending_by_ts():
    # Provide rows in descending order; expect ascending output.
    rows = [
        (_ny_dt(2024, 10, 25, 16, 5), 3.0, 3.1, None, None),
        (_ny_dt(2024, 1, 25, 16, 5), 1.0, 1.1, None, None),
        (_ny_dt(2024, 4, 25, 16, 5), 2.0, 2.1, None, None),
    ]
    df = _make_earn_df(rows=rows)
    recs = normalize_earnings_df(df, symbol="X", source="yfinance")
    ts_list = [r.ts for r in recs]
    assert ts_list == sorted(ts_list)


# ---------------------------------------------------------------------------
# Empty / None inputs
# ---------------------------------------------------------------------------

def test_earnings_empty_df_returns_empty_list():
    df = pd.DataFrame({"EPS Estimate": [], "Reported EPS": []})
    assert normalize_earnings_df(df, symbol="X") == []


def test_earnings_none_returns_empty_list():
    assert normalize_earnings_df(None, symbol="X") == []


def test_actions_empty_df_returns_empty_list():
    df = pd.DataFrame({"Dividends": [], "Stock Splits": []})
    assert normalize_actions_df(df, symbol="X") == []


def test_actions_none_returns_empty_list():
    assert normalize_actions_df(None, symbol="X") == []


# ---------------------------------------------------------------------------
# Actions: dividend column
# ---------------------------------------------------------------------------

def test_actions_cash_dividends_with_zero_rows_dropped():
    df = pd.DataFrame({
        "Dividends": [0.0, 0.25, 0.30, 0.0],
        "Stock Splits": [0.0, 0.0, 0.0, 0.0],
    }, index=pd.DatetimeIndex([
        _ny_dt(2024, 1, 1), _ny_dt(2024, 4, 1),
        _ny_dt(2024, 7, 1), _ny_dt(2024, 10, 1),
    ]))
    recs = normalize_actions_df(df, symbol="X", source="yfinance")
    assert len(recs) == 2
    assert all(r.kind == "cash" for r in recs)
    assert recs[0].amount == pytest.approx(0.25)
    assert recs[1].amount == pytest.approx(0.30)


def test_actions_dividend_column_variant_singular():
    df = pd.DataFrame({"Dividend": [0.25]},
                      index=pd.DatetimeIndex([_ny_dt(2024, 1, 1)]))
    recs = normalize_actions_df(df, symbol="X")
    assert len(recs) == 1
    assert recs[0].kind == "cash"


# ---------------------------------------------------------------------------
# Actions: split column + ratio decoding
# ---------------------------------------------------------------------------

def test_actions_forward_2_for_1_split():
    df = pd.DataFrame({"Stock Splits": [2.0]},
                      index=pd.DatetimeIndex([_ny_dt(2024, 6, 7)]))
    recs = normalize_actions_df(df, symbol="X")
    assert len(recs) == 1
    assert recs[0].kind == "stock_split"
    assert recs[0].ratio_num == 2
    assert recs[0].ratio_den == 1
    assert math.isnan(recs[0].amount)


def test_actions_reverse_1_for_10_split():
    df = pd.DataFrame({"Stock Splits": [0.1]},
                      index=pd.DatetimeIndex([_ny_dt(2024, 6, 7)]))
    recs = normalize_actions_df(df, symbol="X")
    assert len(recs) == 1
    assert recs[0].kind == "stock_split"
    assert recs[0].ratio_num == 1
    assert recs[0].ratio_den == 10


def test_actions_split_value_one_is_noop():
    df = pd.DataFrame({"Stock Splits": [1.0, 1.0]},
                      index=pd.DatetimeIndex([
                          _ny_dt(2024, 6, 7), _ny_dt(2024, 9, 7),
                      ]))
    recs = normalize_actions_df(df, symbol="X")
    assert recs == []


def test_actions_split_zero_or_negative_dropped():
    df = pd.DataFrame({"Stock Splits": [0.0, -1.0]},
                      index=pd.DatetimeIndex([
                          _ny_dt(2024, 6, 7), _ny_dt(2024, 9, 7),
                      ]))
    recs = normalize_actions_df(df, symbol="X")
    assert recs == []


def test_actions_split_column_variant():
    df = pd.DataFrame({"Splits": [3.0]},
                      index=pd.DatetimeIndex([_ny_dt(2024, 6, 7)]))
    recs = normalize_actions_df(df, symbol="X")
    assert len(recs) == 1
    assert recs[0].kind == "stock_split"
    assert recs[0].ratio_num == 3


# ---------------------------------------------------------------------------
# Actions: combined single-row cash+split
# ---------------------------------------------------------------------------

def test_actions_cash_and_split_on_same_row_emits_two_records():
    df = pd.DataFrame({
        "Dividends": [0.25],
        "Stock Splits": [2.0],
    }, index=pd.DatetimeIndex([_ny_dt(2024, 6, 7)]))
    recs = normalize_actions_df(df, symbol="X")
    assert len(recs) == 2
    kinds = {r.kind for r in recs}
    assert kinds == {"cash", "stock_split"}
    # Same ex_ts.
    assert recs[0].ex_ts == recs[1].ex_ts


# ---------------------------------------------------------------------------
# Actions: output sort
# ---------------------------------------------------------------------------

def test_actions_output_sorted_ascending_by_ex_ts():
    df = pd.DataFrame({
        "Dividends": [0.30, 0.25, 0.40],
    }, index=pd.DatetimeIndex([
        _ny_dt(2024, 10, 1), _ny_dt(2024, 1, 1), _ny_dt(2024, 7, 1),
    ]))
    recs = normalize_actions_df(df, symbol="X")
    ex_ts_list = [r.ex_ts for r in recs]
    assert ex_ts_list == sorted(ex_ts_list)


# ---------------------------------------------------------------------------
# Symbol normalisation
# ---------------------------------------------------------------------------

def test_symbol_uppercased_and_stripped():
    df = _make_earn_df()
    recs = normalize_earnings_df(df, symbol="  aapl  ", source="yfinance")
    assert all(r.symbol == "AAPL" for r in recs)
