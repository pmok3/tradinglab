"""Unit tests for :mod:`tradinglab.events.yfinance_events`.

The module under test is a thin shell — it owns the ``yfinance``
import, the two ``Ticker`` property reads (``earnings_dates`` and
``actions``), symbol normalization, and ``EventBundle`` assembly.
All decoding lives in :mod:`tradinglab.events.normalize` (covered by
``test_normalize.py``), so these tests assert only shell-level
behavior: import/Ticker/property error posture, the documented
no-raise fallback, partial-success bundle assembly, symbol
normalization, and the one-shot warning guard.

Tests replace ``yfinance`` in ``sys.modules`` with the ``fake_yfinance``
stub, or ``None`` to simulate an import failure. No test uses the real
SDK or network, whether or not ``yfinance`` is installed.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import sys
import types
from unittest.mock import Mock

import pandas as pd
import pytest

from tradinglab.events import yfinance_events
from tradinglab.events.base import DividendRecord, EarningsRecord, EventBundle

# ---------------------------------------------------------------------------
# stub yfinance
# ---------------------------------------------------------------------------

_EPOCH_UTC = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


class _FakeTicker:
    """Minimal stand-in for ``yfinance.Ticker``.

    Instances are created by the stub module's ``Ticker`` factory and
    registered in ``created``. Property payloads / exceptions are set
    per instance by each test via the ``fake_yfinance`` fixture.
    """

    created: list[_FakeTicker] = []

    def __init__(self, symbol: str):
        if _FakeTicker.ctor_exc is not None:
            raise _FakeTicker.ctor_exc
        self.symbol = symbol
        _FakeTicker.created.append(self)
        self.earnings_df = None
        self.actions_df = None
        self.earnings_exc = None
        self.actions_exc = None

    ctor_exc = None

    @property
    def earnings_dates(self):
        if self.earnings_exc is not None:
            raise self.earnings_exc
        return self.earnings_df

    @property
    def actions(self):
        if self.actions_exc is not None:
            raise self.actions_exc
        return self.actions_df


def _make_yfinance_stub():
    module = types.ModuleType("yfinance")
    module.Ticker = _FakeTicker
    return module


@pytest.fixture
def fake_yfinance(monkeypatch):
    """Inject a stub ``yfinance`` module into ``sys.modules``.

    Yields the stub module so tests can swap ``Ticker`` or tweak the
    fake. Resets the one-shot warning guard
    (:data:`yfinance_events._logged_earnings_dates_failure`) so each
    test starts with a clean set.
    """
    stub = _make_yfinance_stub()
    monkeypatch.setitem(sys.modules, "yfinance", stub)
    _FakeTicker.created = []
    _FakeTicker.ctor_exc = None
    monkeypatch.setattr(yfinance_events, "_logged_earnings_dates_failure",
                        set())
    return stub


# ---------------------------------------------------------------------------
# frame builders
# ---------------------------------------------------------------------------

def _ny_dt(year, month, day, hour=8, minute=0):
    return pd.Timestamp(year=year, month=month, day=day,
                        hour=hour, minute=minute, tz="America/New_York")


def _midnight_ms(d):
    return int((d - _EPOCH_UTC).total_seconds() * 1000)


def _earnings_frame(*rows):
    """Build a yfinance-style earnings frame from (ts, est, act) rows."""
    data = {"EPS Estimate": [], "Reported EPS": []}
    idx = []
    for ts, est, act in rows:
        idx.append(ts)
        data["EPS Estimate"].append(est)
        data["Reported EPS"].append(act)
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx))


def _actions_frame(*rows):
    """Build a yfinance-style actions frame from (ts, div, split) rows."""
    data = {"Dividends": [], "Stock Splits": []}
    idx = []
    for ts, div, split in rows:
        idx.append(ts)
        data["Dividends"].append(div)
        data["Stock Splits"].append(split)
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx))


# ---------------------------------------------------------------------------
# happy paths — event parsing through the shell
# ---------------------------------------------------------------------------

def test_earnings_dates_happy_path(fake_yfinance):
    tk = _FakeTicker("AAPL")
    tk.earnings_df = _earnings_frame(
        (_ny_dt(2024, 1, 25, 16, 5), 2.10, 2.18),
        (_ny_dt(2024, 4, 25, 16, 5), 1.50, 1.45),
    )
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    bundle = yfinance_events.fetch_yfinance_events("AAPL")

    assert isinstance(bundle, EventBundle)
    assert bundle.symbol == "AAPL"
    assert len(bundle.earnings) == 2
    first = bundle.earnings[0]
    assert isinstance(first, EarningsRecord)
    assert first.ts == _midnight_ms(
        dt.datetime(2024, 1, 25, tzinfo=dt.timezone.utc))
    assert first.eps_estimate == pytest.approx(2.10)
    assert first.eps_actual == pytest.approx(2.18)
    assert first.source == "yfinance"
    assert bundle.dividends == []


def test_dividends_happy_path(fake_yfinance):
    tk = _FakeTicker("MSFT")
    tk.actions_df = _actions_frame(
        (_ny_dt(2024, 2, 14, 16, 0), 0.75, 0.0),
        (_ny_dt(2024, 5, 15, 16, 0), 0.75, 0.0),
    )
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    bundle = yfinance_events.fetch_yfinance_events("MSFT")

    assert isinstance(bundle, EventBundle)
    assert len(bundle.dividends) == 2
    cash = bundle.dividends[0]
    assert isinstance(cash, DividendRecord)
    assert cash.kind == "cash"
    assert cash.amount == pytest.approx(0.75)
    assert cash.ex_ts == _midnight_ms(
        dt.datetime(2024, 2, 14, tzinfo=dt.timezone.utc))
    assert bundle.earnings == []


def test_stock_splits_happy_path(fake_yfinance):
    tk = _FakeTicker("NVDA")
    tk.actions_df = _actions_frame(
        (_ny_dt(2024, 6, 7, 16, 0), 0.0, 10.0),   # 10:1 forward
        (_ny_dt(2022, 8, 31, 16, 0), 0.0, 0.5),   # 1:2 reverse
    )
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    bundle = yfinance_events.fetch_yfinance_events("NVDA")

    assert len(bundle.dividends) == 2
    rev, fwd = bundle.dividends  # ascending: 2022 reverse split first
    assert fwd.kind == "stock_split"
    assert (fwd.ratio_num, fwd.ratio_den) == (10, 1)
    assert math.isnan(fwd.amount)
    assert (rev.ratio_num, rev.ratio_den) == (1, 2)


def test_partial_success_earnings_only_returns_bundle(fake_yfinance):
    """Earnings present, actions empty → bundle with empty dividends."""
    tk = _FakeTicker("AAPL")
    tk.earnings_df = _earnings_frame((_ny_dt(2024, 1, 25, 16, 5), 2.1, 2.18))
    tk.actions_df = pd.DataFrame(
        {"Dividends": [], "Stock Splits": []},
        index=pd.DatetimeIndex([]),
    )
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    bundle = yfinance_events.fetch_yfinance_events("AAPL")

    assert isinstance(bundle, EventBundle)
    assert len(bundle.earnings) == 1
    assert bundle.dividends == []


def test_partial_success_dividends_only_returns_bundle(fake_yfinance):
    """Actions present, earnings empty → bundle with empty earnings."""
    tk = _FakeTicker("KO")
    tk.earnings_df = pd.DataFrame(
        {"EPS Estimate": [], "Reported EPS": []},
        index=pd.DatetimeIndex([]),
    )
    tk.actions_df = _actions_frame((_ny_dt(2024, 3, 14, 16, 0), 0.485, 0.0))
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    bundle = yfinance_events.fetch_yfinance_events("KO")

    assert isinstance(bundle, EventBundle)
    assert bundle.earnings == []
    assert len(bundle.dividends) == 1


# ---------------------------------------------------------------------------
# empty / missing data
# ---------------------------------------------------------------------------

def test_both_frames_empty_returns_none(fake_yfinance):
    tk = _FakeTicker("ZZZZ")
    tk.earnings_df = pd.DataFrame(
        {"EPS Estimate": [], "Reported EPS": []},
        index=pd.DatetimeIndex([]),
    )
    tk.actions_df = pd.DataFrame(
        {"Dividends": [], "Stock Splits": []},
        index=pd.DatetimeIndex([]),
    )
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    assert yfinance_events.fetch_yfinance_events("ZZZZ") is None


def test_none_frames_return_none(fake_yfinance):
    tk = _FakeTicker("ZZZZ")
    tk.earnings_df = None
    tk.actions_df = None
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    assert yfinance_events.fetch_yfinance_events("ZZZZ") is None


def test_missing_yfinance_import_returns_none(monkeypatch):
    """``import yfinance`` failing (not installed) collapses to None."""
    monkeypatch.setitem(sys.modules, "yfinance", None)
    assert yfinance_events.fetch_yfinance_events("AAPL") is None


def test_blank_symbol_returns_none(fake_yfinance):
    assert yfinance_events.fetch_yfinance_events("") is None
    assert yfinance_events.fetch_yfinance_events("   ") is None
    assert _FakeTicker.created == []


def test_none_symbol_returns_none(fake_yfinance):
    assert yfinance_events.fetch_yfinance_events(None) is None
    assert _FakeTicker.created == []


# ---------------------------------------------------------------------------
# malformed provider data — tolerant, no raise
# ---------------------------------------------------------------------------

def test_unexpected_columns_tolerated(fake_yfinance):
    """Earnings frame with no known columns → record with NaN fields."""
    tk = _FakeTicker("AAPL")
    tk.earnings_df = pd.DataFrame(
        {"Some New Column": [1.23]},
        index=pd.DatetimeIndex([_ny_dt(2024, 1, 25, 16, 5)]),
    )
    tk.actions_df = pd.DataFrame(
        {"Dividends": [], "Stock Splits": []},
        index=pd.DatetimeIndex([]),
    )
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    bundle = yfinance_events.fetch_yfinance_events("AAPL")

    assert isinstance(bundle, EventBundle)
    assert len(bundle.earnings) == 1
    assert math.isnan(bundle.earnings[0].eps_estimate)
    assert math.isnan(bundle.earnings[0].eps_actual)


@pytest.mark.xfail(strict=True, raises=ValueError, reason=(
    "NaT index row makes fetch_yfinance_events raise "
    "ValueError('cannot convert float NaN to integer'), violating the "
    "documented no-raise posture. Chain: pandas 2.1.4 pd.NaT.date() "
    "returns NaT instead of raising, so normalize._index_to_date returns "
    "NaT rather than None, and date_to_midnight_ms does int(NaT.year). "
    "Fix: one-line NaT check in _index_to_date."))
def test_nat_index_rows_are_skipped(fake_yfinance):
    """A NaT index row is skipped; decodable rows still decode."""
    tk = _FakeTicker("AAPL")
    tk.earnings_df = _earnings_frame(
        (_ny_dt(2024, 1, 25, 16, 5), 2.10, 2.18),
        (pd.NaT, 1.50, 1.45),
    )
    tk.actions_df = None
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    bundle = yfinance_events.fetch_yfinance_events("AAPL")

    assert isinstance(bundle, EventBundle)
    assert len(bundle.earnings) == 1
    assert bundle.earnings[0].eps_estimate == pytest.approx(2.10)


@pytest.mark.xfail(strict=True, raises=ValueError, reason=(
    "Same NaT chain as test_nat_index_rows_are_skipped: a frame whose "
    "rows are ALL NaT-indexed should decode to zero records and make "
    "the fetcher return None, but currently raises ValueError."))
def test_frames_with_no_decodable_rows_return_none(fake_yfinance):
    """Every row NaT-indexed → zero records → None, not an exception."""
    tk = _FakeTicker("ZZZZ")
    tk.earnings_df = _earnings_frame((pd.NaT, 1.50, 1.45))
    tk.actions_df = _actions_frame((pd.NaT, 0.75, 0.0))
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    assert yfinance_events.fetch_yfinance_events("ZZZZ") is None


def test_tz_naive_index_tolerated(fake_yfinance):
    """A tz-naive DatetimeIndex still decodes; slot is unknown ("")."""
    tk = _FakeTicker("AAPL")
    tk.earnings_df = _earnings_frame(
        (pd.Timestamp(2024, 1, 25, 16, 5), 2.10, 2.18),
    )
    tk.actions_df = None
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    bundle = yfinance_events.fetch_yfinance_events("AAPL")

    assert isinstance(bundle, EventBundle)
    assert len(bundle.earnings) == 1
    assert bundle.earnings[0].ts == _midnight_ms(
        dt.datetime(2024, 1, 25, tzinfo=dt.timezone.utc))
    assert bundle.earnings[0].when == ""


# ---------------------------------------------------------------------------
# error paths — documented no-raise fallback
# ---------------------------------------------------------------------------

def test_ticker_constructor_raises_returns_none(fake_yfinance):
    _FakeTicker.ctor_exc = ConnectionError("provider unreachable")
    assert yfinance_events.fetch_yfinance_events("AAPL") is None


def test_earnings_dates_raises_returns_none_and_warns(fake_yfinance, caplog):
    """A raising ``earnings_dates`` (e.g. missing lxml ImportError)
    still yields None when actions are empty, and logs one warning."""
    tk = _FakeTicker("AAPL")
    tk.earnings_exc = ImportError("Missing optional dependency 'lxml'")
    tk.actions_df = None
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    with caplog.at_level(logging.WARNING, logger=yfinance_events.__name__):
        result = yfinance_events.fetch_yfinance_events("AAPL")

    assert result is None
    warnings = [r for r in caplog.records
                if "earnings_dates" in r.getMessage()]
    assert len(warnings) == 1
    assert "lxml" in warnings[0].getMessage()


def test_actions_raises_returns_none_with_earnings(fake_yfinance):
    """A raising ``actions`` collapses to empty dividends; the earnings
    half of the bundle is still returned (partial success)."""
    tk = _FakeTicker("AAPL")
    tk.earnings_df = _earnings_frame((_ny_dt(2024, 1, 25, 16, 5), 2.1, 2.18))
    tk.actions_exc = ConnectionError("provider unreachable")
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    bundle = yfinance_events.fetch_yfinance_events("AAPL")

    assert isinstance(bundle, EventBundle)
    assert len(bundle.earnings) == 1
    assert bundle.dividends == []


def test_earnings_dates_raises_returns_bundle_with_actions(fake_yfinance):
    """An earnings failure still returns cash dividends and stock splits."""
    tk = _FakeTicker("AAPL")
    tk.earnings_exc = ConnectionError("provider unreachable")
    tk.actions_df = _actions_frame(
        (_ny_dt(2024, 5, 15, 16, 0), 0.75, 0.0),
        (_ny_dt(2024, 6, 7, 16, 0), 0.0, 4.0),
    )
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    bundle = yfinance_events.fetch_yfinance_events("AAPL")

    assert isinstance(bundle, EventBundle)
    assert bundle.symbol == "AAPL"
    assert bundle.earnings == []
    assert len(bundle.dividends) == 2
    cash, split = bundle.dividends
    assert cash == DividendRecord(
        ex_ts=_midnight_ms(dt.datetime(2024, 5, 15, tzinfo=dt.timezone.utc)),
        symbol="AAPL", amount=0.75, kind="cash", source="yfinance",
    )
    assert (split.ex_ts, split.symbol, split.kind,
            split.ratio_num, split.ratio_den, split.source) == (
        _midnight_ms(dt.datetime(2024, 6, 7, tzinfo=dt.timezone.utc)),
        "AAPL", "stock_split", 4, 1, "yfinance",
    )
    assert math.isnan(split.amount)


def test_both_properties_raise_returns_none(fake_yfinance):
    tk = _FakeTicker("ZZZZ")
    tk.earnings_exc = ConnectionError("boom")
    tk.actions_exc = ConnectionError("boom")
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    assert yfinance_events.fetch_yfinance_events("ZZZZ") is None


# ---------------------------------------------------------------------------
# stateful behaviors — one-shot warning guard, no event cache
# ---------------------------------------------------------------------------

def test_one_shot_warning_guard_hit(fake_yfinance, caplog):
    """Same exception kind twice → the set is a hit → exactly one
    WARNING, and the guard set contains the kind."""
    for _ in range(2):
        tk = _FakeTicker("AAPL")
        tk.earnings_exc = ImportError("Missing optional dependency 'lxml'")
        tk.actions_df = None
        fake_yfinance.Ticker = lambda symbol, _tk=tk: _tk  # noqa: E731
        with caplog.at_level(logging.WARNING,
                             logger=yfinance_events.__name__):
            assert yfinance_events.fetch_yfinance_events("AAPL") is None

    assert yfinance_events._logged_earnings_dates_failure == {"ImportError"}
    warnings = [r for r in caplog.records
                if "earnings_dates" in r.getMessage()]
    assert len(warnings) == 1


def test_one_shot_warning_guard_new_kind_logs_again(fake_yfinance, caplog):
    """A NEW exception kind is a set miss → warned once for the new
    kind; the guard grows by one entry per distinct kind."""
    kinds = [ImportError("no lxml"), RuntimeError("weird html")]
    for exc in kinds:
        tk = _FakeTicker("AAPL")
        tk.earnings_exc = exc
        tk.actions_df = None
        fake_yfinance.Ticker = lambda symbol, _tk=tk: _tk  # noqa: E731
        with caplog.at_level(logging.WARNING,
                             logger=yfinance_events.__name__):
            assert yfinance_events.fetch_yfinance_events("AAPL") is None

    assert yfinance_events._logged_earnings_dates_failure == {
        "ImportError", "RuntimeError"}
    warnings = [r for r in caplog.records
                if "earnings_dates" in r.getMessage()]
    assert len(warnings) == 2


def test_no_memoization_between_calls(fake_yfinance):
    """The module holds no event cache (TTL lives in events/cache.py,
    tested separately): each call constructs a fresh Ticker."""

    def _mk(symbol):
        tk = _FakeTicker(symbol)
        tk.earnings_df = _earnings_frame(
            (_ny_dt(2024, 1, 25, 16, 5), 2.1, 2.18))
        return tk
    fake_yfinance.Ticker = _mk

    first = yfinance_events.fetch_yfinance_events("AAPL")
    second = yfinance_events.fetch_yfinance_events("AAPL")

    assert len(_FakeTicker.created) == 2
    assert first is not second
    assert first.earnings[0].ts == second.earnings[0].ts
    assert first.earnings[0].symbol == second.earnings[0].symbol
    assert first.fetched_at >= 0


# ---------------------------------------------------------------------------
# boundary conditions
# ---------------------------------------------------------------------------

def test_symbol_normalization_strip_and_upper(fake_yfinance):
    """Whitespace trimmed, lower-cased input; the Ticker sees the
    normalized symbol."""
    tk = _FakeTicker("AAPL")
    tk.earnings_df = _earnings_frame((_ny_dt(2024, 1, 25, 16, 5), 2.1, 2.18))
    fake_yfinance.Ticker = Mock(return_value=tk)

    bundle = yfinance_events.fetch_yfinance_events("  aapl  ")

    fake_yfinance.Ticker.assert_called_once_with("AAPL")
    assert bundle.symbol == "AAPL"


def test_fetched_at_is_recent_utc_ms(fake_yfinance):
    tk = _FakeTicker("AAPL")
    tk.earnings_df = _earnings_frame((_ny_dt(2024, 1, 25, 16, 5), 2.1, 2.18))
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    bundle = yfinance_events.fetch_yfinance_events("AAPL")

    now_ms = int((dt.datetime.now(tz=dt.timezone.utc)
                  - _EPOCH_UTC).total_seconds() * 1000)
    assert isinstance(bundle.fetched_at, int)
    assert 0 < bundle.fetched_at <= now_ms
    assert now_ms - bundle.fetched_at < 60_000


def test_bundle_records_sorted_ascending(fake_yfinance):
    """Records arrive sorted ascending even when the provider frame is
    newest-first (bundle + normalize both sort)."""
    tk = _FakeTicker("AAPL")
    tk.earnings_df = _earnings_frame(
        (_ny_dt(2024, 7, 25, 16, 5), 1.50, 1.45),
        (_ny_dt(2024, 1, 25, 16, 5), 2.10, 2.18),
    )
    tk.actions_df = _actions_frame(
        (_ny_dt(2024, 5, 15, 16, 0), 0.80, 0.0),
        (_ny_dt(2024, 2, 14, 16, 0), 0.75, 0.0),
    )
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    bundle = yfinance_events.fetch_yfinance_events("AAPL")

    assert isinstance(bundle, EventBundle)
    assert bundle.symbol == "AAPL"
    assert len(bundle.earnings) == 2
    assert len(bundle.dividends) == 2
    assert [(r.ts, r.symbol, r.when, r.eps_estimate, r.eps_actual, r.source)
            for r in bundle.earnings] == [
        (_midnight_ms(dt.datetime(2024, 1, 25, tzinfo=dt.timezone.utc)),
         "AAPL", "AMC", 2.10, 2.18, "yfinance"),
        (_midnight_ms(dt.datetime(2024, 7, 25, tzinfo=dt.timezone.utc)),
         "AAPL", "AMC", 1.50, 1.45, "yfinance"),
    ]
    assert bundle.dividends == [
        DividendRecord(
            ex_ts=_midnight_ms(dt.datetime(2024, 2, 14, tzinfo=dt.timezone.utc)),
            symbol="AAPL", amount=0.75, kind="cash", source="yfinance",
        ),
        DividendRecord(
            ex_ts=_midnight_ms(dt.datetime(2024, 5, 15, tzinfo=dt.timezone.utc)),
            symbol="AAPL", amount=0.80, kind="cash", source="yfinance",
        ),
    ]


def test_utc_date_boundary_floors_correctly(fake_yfinance):
    """A 23:59 ET print belongs to the NEXT UTC date; the ts floor
    reflects that (boundary of the date-range edge)."""
    tk = _FakeTicker("AAPL")
    tk.earnings_df = _earnings_frame(
        (_ny_dt(2024, 1, 25, 23, 59), 2.10, 2.18),
    )
    tk.actions_df = None
    fake_yfinance.Ticker = lambda symbol: tk  # noqa: E731

    bundle = yfinance_events.fetch_yfinance_events("AAPL")

    assert bundle.earnings[0].ts == _midnight_ms(
        dt.datetime(2024, 1, 26, tzinfo=dt.timezone.utc))
    assert bundle.earnings[0].when == "AMC"
