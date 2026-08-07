"""Live vs replay heatmap context.

The interesting cases are the ones where "live" is not simply "replay
with a different clock": market-state classification (including the
overnight-vs-pre-market split that ``classify_session`` deliberately
does not make), the refusal to clamp the clock outside market hours,
and the daily→intraday interval fallback that keeps the live price leg
off the look-ahead-prone daily bar.
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

import pytest

from tradinglab.core.timezones import ET, to_et
from tradinglab.gui.heatmap_context import (
    MARKET_STATES,
    LiveHeatmapContext,
    SandboxHeatmapContext,
    market_state_at,
)

pytestmark = pytest.mark.skipif(ET is None, reason="tzdata unavailable")


def _et(y, m, d, hh, mm) -> float:
    return _dt.datetime(y, m, d, hh, mm, tzinfo=ET).timestamp()


# -- market_state_at ---------------------------------------------------------


@pytest.mark.parametrize(
    "hh, mm, expected",
    [
        (2, 0, "closed"),    # overnight — nothing trades
        (4, 0, "pre"),       # extended session opens
        (8, 15, "pre"),
        (9, 29, "pre"),
        (9, 30, "regular"),
        (12, 0, "regular"),
        (15, 59, "regular"),
        (16, 0, "post"),
        (19, 59, "post"),
        (20, 0, "closed"),   # extended session closes
        (23, 0, "closed"),
    ],
)
def test_market_state_across_a_weekday(hh, mm, expected):
    # 2024-06-03 is a Monday.
    assert market_state_at(_et(2024, 6, 3, hh, mm)) == expected


@pytest.mark.parametrize("day", [1, 2])  # 2024-06-01 Sat, 06-02 Sun
def test_weekend_is_closed_at_every_hour(day):
    for hh in (4, 10, 12, 17, 21):
        assert market_state_at(_et(2024, 6, day, hh, 0)) == "closed"


def test_overnight_is_not_reported_as_pre_market():
    """``classify_session`` folds overnight into "pre" because that is
    the right *bar* tag. A live map must not: 08:00 has tradeable
    prints and 02:00 does not."""
    assert market_state_at(_et(2024, 6, 3, 2, 0)) == "closed"
    assert market_state_at(_et(2024, 6, 3, 8, 0)) == "pre"


def test_a_bad_timestamp_reads_as_closed_rather_than_raising():
    assert market_state_at(float("nan")) in MARKET_STATES
    assert market_state_at(1e30) in MARKET_STATES


def test_state_is_always_a_known_member():
    assert market_state_at() in MARKET_STATES


# -- LiveHeatmapContext ------------------------------------------------------


def _live(app=None, *, now: float = 0.0) -> LiveHeatmapContext:
    return LiveHeatmapContext(app or SimpleNamespace(), clock=lambda: now)


def test_live_clock_is_not_clamped_outside_market_hours():
    """The clock is not the uncertain thing — the data is.

    Clamping to the last completed session would make a Saturday map
    claim to be Friday-at-the-close, hiding that nothing has updated in
    two days.
    """
    saturday = _et(2024, 6, 1, 11, 0)
    ctx = _live(now=saturday)
    assert ctx.clock_ts() == int(saturday)
    assert ctx.market_state() == "closed"


def test_live_context_is_always_active_and_never_blind():
    ctx = _live()
    assert ctx.is_active() is True
    assert ctx.blind is False
    assert ctx.is_live is True


def test_current_session_date_is_utc_not_et():
    """The roll key must match ``SessionPriceSource``'s snapshot key.

    ``SessionPriceSource`` keys its validity window on
    ``backtest.heatmap.session_date_of`` (UTC). If the roll detector used
    the ET date instead, then between 00:00 UTC and 00:00 ET the price
    snapshot would lapse — every symbol returning ``(None, None)`` —
    while the detector still saw "same day" and never re-primed, leaving
    the map fully unpriced for ~5 hours every evening.
    """
    from tradinglab.backtest.heatmap import session_date_of

    # 21:30 ET on 2024-01-15 is already 2024-01-16 in UTC.
    evening = _et(2024, 1, 15, 21, 30)
    ctx = _live(now=evening)
    assert ctx.current_session_date() == session_date_of(evening)
    assert ctx.current_session_date() != to_et(int(evening)).date()


@pytest.mark.parametrize(
    "y, m, d, hh, mm",
    [
        (2024, 1, 15, 19, 30),   # EST, past 00:00 UTC
        (2024, 1, 15, 23, 59),
        (2024, 6, 3, 20, 30),    # EDT, past 00:00 UTC
        (2024, 6, 3, 10, 0),     # same day in both zones
    ],
)
def test_session_date_matches_the_price_source_key_all_day(y, m, d, hh, mm):
    from tradinglab.backtest.heatmap import session_date_of

    ts = _et(y, m, d, hh, mm)
    assert _live(now=ts).current_session_date() == session_date_of(ts)


def test_data_source_follows_the_chart():
    app = SimpleNamespace(source_var=SimpleNamespace(get=lambda: "alpaca"))
    assert _live(app).data_source == "alpaca"


def test_data_source_is_blank_when_the_app_has_none():
    assert _live().data_source == ""


def test_interval_follows_the_chart_when_intraday():
    app = SimpleNamespace(interval_var=SimpleNamespace(get=lambda: "1m"))
    assert _live(app).interval == "1m"


def test_a_daily_chart_falls_back_to_an_intraday_interval():
    """A daily bar carries the settled close but is stamped at the open,
    so using it as the live *price* leg would reintroduce the exact
    look-ahead the sandbox path was hardened against."""
    app = SimpleNamespace(interval_var=SimpleNamespace(get=lambda: "1d"))
    assert _live(app).interval == "5m"


def test_interval_defaults_when_the_app_has_none():
    assert _live().interval == "5m"


def test_focus_symbol_is_normalized():
    app = SimpleNamespace(ticker_var=SimpleNamespace(get=lambda: "  aapl "))
    assert _live(app).focus_symbol == "AAPL"


def test_focus_symbol_is_blank_without_an_app_var():
    assert _live().focus_symbol == ""


def test_positions_snapshot_is_empty_without_a_tracker():
    assert _live().positions_snapshot() == []


def test_positions_snapshot_reads_the_apps_paper_tracker():
    rows = [{"symbol": "AAPL", "quantity": 10}]
    app = SimpleNamespace(paper_positions_snapshot=lambda: rows)
    assert _live(app).positions_snapshot() == rows


def test_positions_snapshot_swallows_a_raising_tracker():
    def boom():
        raise RuntimeError("no")

    app = SimpleNamespace(paper_positions_snapshot=boom)
    assert _live(app).positions_snapshot() == []


def test_set_focus_is_inert():
    assert _live().set_focus("AAPL") is None


# -- SandboxHeatmapContext ---------------------------------------------------


def test_sandbox_context_delegates_unknown_attributes():
    ctl = SimpleNamespace(clock_ts=lambda: 123, engine="ENGINE", blind=True)
    ctx = SandboxHeatmapContext(ctl)
    assert ctx.clock_ts() == 123
    assert ctx.engine == "ENGINE"
    assert ctx.blind is True


def test_sandbox_context_is_not_live_and_reads_as_mid_session():
    ctx = SandboxHeatmapContext(SimpleNamespace())
    assert ctx.is_live is False
    assert ctx.market_state() == "regular"


def test_sandbox_is_active_guards_a_missing_or_raising_hook():
    assert SandboxHeatmapContext(SimpleNamespace()).is_active() is False

    def boom():
        raise RuntimeError("no")

    assert SandboxHeatmapContext(SimpleNamespace(is_active=boom)).is_active() is False
    assert SandboxHeatmapContext(SimpleNamespace(is_active=lambda: True)).is_active()


def test_sandbox_context_raises_for_a_genuinely_missing_attribute():
    ctx = SandboxHeatmapContext(SimpleNamespace())
    with pytest.raises(AttributeError):
        _ = ctx.definitely_not_there
