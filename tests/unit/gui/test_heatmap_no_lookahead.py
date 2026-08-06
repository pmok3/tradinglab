"""No-future-leakage tests for the sandbox heatmap price path.

The bug these pin: ``1d`` bars are timestamped at the session's *open*
but carry the session's settled *close*, so a naive "last bar whose
timestamp <= clock" lookup handed a mid-session replay clock the
finished day's close. The map then showed the answer from the opening
print onward — and, because both legs were constant for the whole day,
never changed intraday either.

The invariant now lives in two places:

* ``backtest.heatmap.completed_session_closes`` — daily bars are only
  read when their session date is **strictly before** the clock's
  session date (the same rule ``SandboxController.daily_visible_for``
  applies to the daily chart).
* ``gui.sandbox_heatmap.SessionPriceSource`` — the price leg comes from
  intraday bars at/before the clock, the base leg from the last
  completed daily session.

The metamorphic case (``test_..._identical_with_and_without_future``) is
the one that would have caught the original bug: deleting every bar
after the clock must not change a single value.
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

import pytest

from tradinglab.backtest.heatmap import (
    completed_session_closes,
    session_date_of,
)
from tradinglab.gui.sandbox_heatmap import SessionPriceSource, _candle_epoch
from tradinglab.models import Candle

_UTC = _dt.timezone.utc

# Replay clock: 2024-06-05, mid-session.
_DAY = _dt.date(2024, 6, 5)
_CLOCK = int(_dt.datetime(2024, 6, 5, 14, 30, tzinfo=_UTC).timestamp())


def _bar(when: _dt.datetime, close: float) -> Candle:
    return Candle(
        date=when, open=close, high=close, low=close, close=close, volume=1_000
    )


def _daily(closes: dict[_dt.date, float]) -> list[Candle]:
    return [
        _bar(_dt.datetime(d.year, d.month, d.day, tzinfo=_UTC), c)
        for d, c in sorted(closes.items())
    ]


def _intraday(day: _dt.date, closes: list[tuple[int, int, float]]) -> list[Candle]:
    return [
        _bar(_dt.datetime(day.year, day.month, day.day, h, m, tzinfo=_UTC), c)
        for h, m, c in closes
    ]


# The in-progress day's daily bar carries a close far from every
# intraday print, so any leak is unmistakable in an assertion.
_DAILY = _daily(
    {
        _dt.date(2024, 6, 3): 100.0,
        _dt.date(2024, 6, 4): 101.0,
        _DAY: 999.0,  # settled close of the day being replayed — the future
        _dt.date(2024, 6, 6): 1234.0,  # tomorrow — doubly the future
    }
)

_INTRADAY = _intraday(
    _DAY,
    [
        (13, 30, 102.0),
        (14, 0, 103.0),
        (14, 30, 104.0),  # the bar at the clock
        (15, 0, 500.0),   # after the clock
        (16, 0, 999.0),   # after the clock
    ],
)


def _loader(daily: list[Candle], intraday: list[Candle]):
    def _load(_source: str, _symbol: str, interval: str) -> list[Candle]:
        return list(daily) if interval == "1d" else list(intraday)

    return _load


def _source(daily=_DAILY, intraday=_INTRADAY) -> SessionPriceSource:
    src = SessionPriceSource(
        source="test", interval="5m", loader=_loader(daily, intraday)
    )
    src.build(["AAA"], _CLOCK)
    return src


# ---------------------------------------------------------------------------
# pure layer
# ---------------------------------------------------------------------------


def test_session_date_of_matches_utc_date() -> None:
    assert session_date_of(_CLOCK) == _DAY
    # ms input is normalized like every other epoch in the codebase
    assert session_date_of(_CLOCK * 1000) == _DAY


def test_completed_session_closes_excludes_in_progress_and_future() -> None:
    got = completed_session_closes(_DAILY, _CLOCK, count=2)
    assert got == (100.0, 101.0), got
    # never the replay day's settled close, never tomorrow's
    assert 999.0 not in got
    assert 1234.0 not in got


def test_completed_session_closes_is_oldest_first_and_short_when_shallow() -> None:
    shallow = _daily({_dt.date(2024, 6, 4): 101.0, _DAY: 999.0})
    assert completed_session_closes(shallow, _CLOCK, count=2) == (101.0,)
    assert completed_session_closes(shallow, _CLOCK, count=1) == (101.0,)
    assert completed_session_closes([], _CLOCK, count=2) == ()
    assert completed_session_closes(_DAILY, _CLOCK, count=0) == ()


def test_completed_session_closes_skips_nan_close() -> None:
    bars = _daily({_dt.date(2024, 6, 3): 100.0, _dt.date(2024, 6, 4): 101.0})
    bars.insert(2, _bar(_dt.datetime(2024, 6, 4, tzinfo=_UTC), float("nan")))
    assert completed_session_closes(bars, _CLOCK, count=2) == (100.0, 101.0)


# ---------------------------------------------------------------------------
# SessionPriceSource
# ---------------------------------------------------------------------------


def test_price_is_intraday_at_clock_and_base_is_prior_session() -> None:
    price, prior = _source()("AAA", _CLOCK)
    assert price == 104.0, "price must be the intraday bar AT the clock"
    assert prior == 101.0, "base must be the prior COMPLETED session close"


def test_price_advances_with_the_clock_within_the_session() -> None:
    src = _source()
    seen = [
        src("AAA", int(_dt.datetime(2024, 6, 5, h, m, tzinfo=_UTC).timestamp()))[0]
        for h, m in ((13, 30), (14, 0), (14, 30))
    ]
    assert seen == [102.0, 103.0, 104.0], (
        "the map must actually change intraday; a frozen series was the "
        "second symptom of the daily-bar leak"
    )


def test_no_price_before_the_first_bar_of_the_session() -> None:
    src = _source()
    early = int(_dt.datetime(2024, 6, 5, 9, 0, tzinfo=_UTC).timestamp())
    price, prior = src("AAA", early)
    assert price is None
    assert prior == 101.0


def test_model_identical_with_and_without_future_bars() -> None:
    """Metamorphic: truncating everything after the clock changes nothing.

    This is the property the original implementation violated, and it
    holds for every clock inside the session — not just the one.
    """
    truncated_daily = [c for c in _DAILY if c.date.timestamp() <= _CLOCK]
    truncated_intraday = [c for c in _INTRADAY if c.date.timestamp() <= _CLOCK]
    full = _source()
    trimmed = _source(daily=truncated_daily, intraday=truncated_intraday)
    for hour, minute in ((13, 30), (13, 45), (14, 0), (14, 30)):
        clock = int(_dt.datetime(2024, 6, 5, hour, minute, tzinfo=_UTC).timestamp())
        assert full("AAA", clock) == trimmed("AAA", clock), (
            f"future bars leaked into the {hour:02d}:{minute:02d} reading"
        )


def test_daily_fallback_uses_two_completed_sessions() -> None:
    src = _source(intraday=[])
    price, prior = src("AAA", _CLOCK)
    assert (price, prior) == (101.0, 100.0), (
        "with no intraday coverage the honest read is the last completed "
        "session's move — never the in-progress day's settled close"
    )
    assert src.stale_symbols() == {"AAA"}
    assert src.covered_symbols() == {"AAA"}


def test_daily_fallback_needs_two_sessions_or_reports_nothing() -> None:
    only_one = _daily({_dt.date(2024, 6, 4): 101.0, _DAY: 999.0})
    src = _source(daily=only_one, intraday=[])
    assert src("AAA", _CLOCK) == (None, None)
    assert src.covered_symbols() == set()


def test_snapshot_is_not_served_across_sessions() -> None:
    src = _source()
    next_day = int(_dt.datetime(2024, 6, 6, 14, 30, tzinfo=_UTC).timestamp())
    assert src("AAA", next_day) == (None, None), (
        "a stale snapshot must not be painted as the new session"
    )


def test_unknown_symbol_is_neutral_not_an_error() -> None:
    assert _source()("ZZZ", _CLOCK) == (None, None)


def test_build_is_idempotent_and_incremental() -> None:
    calls: list[tuple[str, str]] = []

    def _counting(_source_name: str, symbol: str, interval: str) -> list[Candle]:
        calls.append((symbol, interval))
        return list(_DAILY) if interval == "1d" else list(_INTRADAY)

    src = SessionPriceSource(source="test", interval="5m", loader=_counting)
    src.build(["AAA"], _CLOCK)
    first = len(calls)
    src.build(["AAA"], _CLOCK)
    assert len(calls) == first, "re-building the same session must not re-parse"
    src.build(["AAA", "BBB"], _CLOCK)
    assert len(calls) > first, "a newly added symbol must be parsed"


def test_loader_failure_degrades_to_neutral() -> None:
    def _boom(*_a: object) -> list[Candle]:
        raise OSError("cache unreadable")

    src = SessionPriceSource(source="test", interval="5m", loader=_boom)
    src.build(["AAA"], _CLOCK)
    assert src("AAA", _CLOCK) == (None, None)


@pytest.mark.parametrize("clock", [_CLOCK, _CLOCK * 1000])
def test_epoch_units_are_normalized(clock: int) -> None:
    src = SessionPriceSource(
        source="test", interval="5m", loader=_loader(_DAILY, _INTRADAY)
    )
    src.build(["AAA"], clock)
    assert src("AAA", clock) == (104.0, 101.0)


def test_naive_bars_are_read_as_utc_not_machine_local() -> None:
    """A tz-naive bar must resolve as UTC, matching the replay clock.

    ``datetime.timestamp()`` on a naive datetime goes through the
    *machine's* zone, while ``backtest.bars._candle_ts_epoch`` — which
    builds the clock these are compared against — treats naive as UTC.
    On a box east of UTC the mismatch shifts the series earlier and the
    bisect returns a bar from AFTER the clock: the look-ahead leak, back
    again via the user's locale. Synthetic sources emit naive dates.
    """
    def _strip(bars: list[Candle]) -> list[Candle]:
        return [
            Candle(
                date=c.date.replace(tzinfo=None),
                open=c.open, high=c.high, low=c.low,
                close=c.close, volume=c.volume,
            )
            for c in bars
        ]

    # The helper itself is the contract: naive in, UTC epoch out.
    naive = _strip(_INTRADAY)
    for aware_bar, naive_bar in zip(_INTRADAY, naive, strict=True):
        assert _candle_epoch(naive_bar) == _candle_epoch(aware_bar)

    src = SessionPriceSource(
        source="test", interval="5m", loader=_loader(_strip(_DAILY), naive)
    )
    src.build(["AAA"], _CLOCK)
    assert src("AAA", _CLOCK) == (104.0, 101.0), (
        "naive bars must read as UTC regardless of machine locale"
    )


def test_candle_epoch_degrades_instead_of_raising() -> None:
    assert _candle_epoch(None) is None
    assert _candle_epoch(SimpleNamespace(date=None)) is None
    assert _candle_epoch(SimpleNamespace(date="not-a-datetime")) is None


def test_a_new_session_invalidates_the_previous_snapshot() -> None:
    """Rolling to a new day must not leave the old day's entries readable.

    Auto-cycle draws a *random* eligible date, so the previous session
    can be in the new one's future — serving its closes would be a leak,
    not merely a staleness bug.
    """
    src = _source()
    assert src("AAA", _CLOCK) == (104.0, 101.0)
    next_day = int(_dt.datetime(2024, 6, 6, 14, 30, tzinfo=_UTC).timestamp())
    src.build([], next_day)  # roll with nothing parsed yet
    assert src("AAA", next_day) == (None, None)
    assert src("AAA", _CLOCK) == (None, None), (
        "the previous session's clock must not resolve either"
    )
    assert src.stale_symbols() == set()
    assert src.covered_symbols() == set()
