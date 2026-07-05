"""Pure-helper tests for :mod:`tradinglab.constants`.

Covers the four pure functions explicitly enumerated in the
``constants.spec.md`` Invariants section: :func:`classify_session`,
:func:`interval_minutes`, :func:`floor_to_interval`, and
:func:`resolve_startup_defaults`. No I/O, no Tk, no fixtures —
just parametrized arithmetic and dict-validation checks.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tradinglab.constants import (
    BUILTIN_STARTUP_DEFAULTS,
    classify_session,
    floor_to_interval,
    interval_minutes,
    page_span_days,
    provider_lookback_days,
    resolve_startup_defaults,
    targeted_window,
)

# ---------------------------------------------------------------------------
# provider_lookback_days
# ---------------------------------------------------------------------------


def test_provider_lookback_deep_history_intraday():
    # Alpaca / Polygon have no 60-day intraday cap, but each window is
    # bounded so a single up-front fetch stays ~1 API page / ≲3s.
    assert provider_lookback_days("alpaca", "5m") == 120
    assert provider_lookback_days("alpaca", "1m") == 20
    assert provider_lookback_days("polygon", "1h") == 1460


def test_provider_lookback_deep_history_daily():
    assert provider_lookback_days("alpaca", "1d") == 5490
    assert provider_lookback_days("polygon", "1wk") == 5490


def test_provider_lookback_yfinance_matches_interval_periods():
    # Non-deep sources keep the yfinance windows.
    assert provider_lookback_days("yfinance", "5m") == 60
    assert provider_lookback_days("yfinance", "1m") == 7
    assert provider_lookback_days("yfinance", "1h") == 730
    assert provider_lookback_days("yfinance", "1d") == 732  # "2y" → 2*366


def test_provider_lookback_unknown_interval_defaults_sixty():
    assert provider_lookback_days("yfinance", "bogus") == 60
    assert provider_lookback_days("", "bogus") == 60


# ---------------------------------------------------------------------------
# page_span_days / targeted_window (targeted intraday fetch)
# ---------------------------------------------------------------------------


def test_page_span_days_intraday_progression():
    d1 = page_span_days("1m")
    d5 = page_span_days("5m")
    d15 = page_span_days("15m")
    d1h = page_span_days("1h")
    # A coarser interval packs fewer bars/day → one page spans deeper history.
    assert d1 < d5 < d15 < d1h
    assert 150 <= d5 <= 210  # ~6 months for 5m


def test_page_span_days_daily_raises():
    with pytest.raises(ValueError):
        page_span_days("1d")


def test_targeted_window_centered_when_unconstrained():
    span = page_span_days("5m") * 86_400
    day = 2_000_000_000
    now = day + span  # room on both sides
    start, end = targeted_window("5m", day, now_ts=now)
    assert start < day < end
    assert end - start == span
    assert abs((day - start) - (end - day)) <= 86_400  # centered


def test_targeted_window_clamps_end_to_now():
    span = page_span_days("5m") * 86_400
    now = 2_000_000_000
    day = now - 100  # "today"
    start, end = targeted_window("5m", day, now_ts=now)
    assert end == now
    assert start == now - span  # refilled backward


def test_targeted_window_clamps_start_to_data_start():
    span = page_span_days("5m") * 86_400
    data_start = 1_000_000_000
    day = data_start + 100  # near the start of available data
    now = data_start + 10 * span
    start, end = targeted_window("5m", day, now_ts=now, data_start_ts=data_start)
    assert start == data_start
    assert end == data_start + span  # refilled forward

# ---------------------------------------------------------------------------
# classify_session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        # Regular session is [09:30, 16:00).
        (9, 30, "regular"),
        (9, 29, "pre"),
        (15, 59, "regular"),
        # Post session is [16:00, 20:00).
        (16, 0, "post"),
        (19, 59, "post"),
        # 20:00 onward and overnight (< 04:00) collapse to "pre"
        # per the spec.md design decision.
        (20, 0, "pre"),
        (3, 30, "pre"),
    ],
)
def test_classify_session_boundaries(hour: int, minute: int, expected: str) -> None:
    assert classify_session(hour, minute) == expected


# ---------------------------------------------------------------------------
# interval_minutes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "interval,expected",
    [
        ("1m", 1),
        ("5m", 5),
        ("15m", 15),
        ("30m", 30),
        ("60m", 60),
        ("1h", 60),
        ("2h", 120),
    ],
)
def test_interval_minutes_intraday(interval: str, expected: int) -> None:
    assert interval_minutes(interval) == expected


@pytest.mark.parametrize(
    "interval",
    ["1d", "1wk", "1mo", "", "garbage", "5", "d"],
)
def test_interval_minutes_rejects_non_intraday(interval: str) -> None:
    with pytest.raises(ValueError):
        interval_minutes(interval)


# ---------------------------------------------------------------------------
# floor_to_interval
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "when,step_min,expected",
    [
        # Mid-bucket: minutes floored, seconds & microseconds zeroed.
        (
            datetime(2026, 1, 2, 9, 33, 15, 123456),
            5,
            datetime(2026, 1, 2, 9, 30, 0, 0),
        ),
        # On the boundary: idempotent (seconds/microseconds still zeroed).
        (
            datetime(2026, 1, 2, 9, 30, 0),
            5,
            datetime(2026, 1, 2, 9, 30, 0),
        ),
        # 15-minute grid.
        (
            datetime(2026, 1, 2, 10, 47, 59),
            15,
            datetime(2026, 1, 2, 10, 45, 0),
        ),
        # 60-minute (hourly) grid floors back to the top of the hour.
        (
            datetime(2026, 1, 2, 14, 59, 59),
            60,
            datetime(2026, 1, 2, 14, 0, 0),
        ),
        # 1-minute step: just zeros seconds & microseconds.
        (
            datetime(2026, 1, 2, 9, 33, 45, 500),
            1,
            datetime(2026, 1, 2, 9, 33, 0),
        ),
    ],
)
def test_floor_to_interval(when: datetime, step_min: int, expected: datetime) -> None:
    result = floor_to_interval(when, step_min)
    assert result == expected
    # Spec invariant: seconds & microseconds are always zeroed.
    assert result.second == 0
    assert result.microsecond == 0


def test_floor_to_interval_idempotent_on_boundary() -> None:
    boundary = datetime(2026, 1, 2, 9, 30, 0)
    once = floor_to_interval(boundary, 5)
    twice = floor_to_interval(once, 5)
    assert once == boundary
    assert twice == once


# ---------------------------------------------------------------------------
# resolve_startup_defaults
# ---------------------------------------------------------------------------


def test_resolve_startup_defaults_no_overrides_returns_builtin_copy() -> None:
    result = resolve_startup_defaults(None)
    assert result == BUILTIN_STARTUP_DEFAULTS
    # Must be a *copy* — mutating the returned dict must not leak
    # back into BUILTIN_STARTUP_DEFAULTS.
    assert result is not BUILTIN_STARTUP_DEFAULTS
    snapshot = dict(BUILTIN_STARTUP_DEFAULTS)
    result["ticker"] = "ZZZZ"
    result["theme"] = "dark"
    assert BUILTIN_STARTUP_DEFAULTS == snapshot


@pytest.mark.parametrize("bad", [None, "", [], 0, 42, object()])
def test_resolve_startup_defaults_non_dict_overrides_returns_builtin(bad: object) -> None:
    result = resolve_startup_defaults(bad)  # type: ignore[arg-type]
    assert result == BUILTIN_STARTUP_DEFAULTS
    assert result is not BUILTIN_STARTUP_DEFAULTS


def test_resolve_startup_defaults_invalid_theme_falls_back() -> None:
    result = resolve_startup_defaults({"theme": "neon"})
    assert result["theme"] == BUILTIN_STARTUP_DEFAULTS["theme"]


@pytest.mark.parametrize("good_theme", ["light", "dark"])
def test_resolve_startup_defaults_valid_theme_accepted(good_theme: str) -> None:
    assert resolve_startup_defaults({"theme": good_theme})["theme"] == good_theme


@pytest.mark.parametrize(
    "raw,expected",
    [
        (" aapl ", "AAPL"),
        ("msft", "MSFT"),
        ("  Tsla", "TSLA"),
        ("BRK.B", "BRK.B"),
    ],
)
def test_resolve_startup_defaults_ticker_strip_upper(raw: str, expected: str) -> None:
    result = resolve_startup_defaults({"ticker": raw, "compare": raw})
    assert result["ticker"] == expected
    assert result["compare"] == expected


@pytest.mark.parametrize("blank", ["", 0, None, []])
def test_resolve_startup_defaults_ticker_blank_falls_back(blank: object) -> None:
    result = resolve_startup_defaults({"ticker": blank})  # type: ignore[dict-item]
    assert result["ticker"] == BUILTIN_STARTUP_DEFAULTS["ticker"]


def test_resolve_startup_defaults_unknown_source_falls_back() -> None:
    result = resolve_startup_defaults(
        {"source": "alphavantage"}, sources=("yfinance",)
    )
    assert result["source"] == BUILTIN_STARTUP_DEFAULTS["source"]


def test_resolve_startup_defaults_known_source_accepted() -> None:
    result = resolve_startup_defaults(
        {"source": "yfinance"}, sources=("yfinance", "synthetic")
    )
    assert result["source"] == "yfinance"


def test_resolve_startup_defaults_source_no_allowlist_accepts_any_string() -> None:
    # When sources=None, any non-empty string is accepted.
    result = resolve_startup_defaults({"source": "alphavantage"})
    assert result["source"] == "alphavantage"


def test_resolve_startup_defaults_unknown_interval_falls_back() -> None:
    result = resolve_startup_defaults(
        {"interval": "99x"}, intervals=("1m", "5m", "1d")
    )
    assert result["interval"] == BUILTIN_STARTUP_DEFAULTS["interval"]


def test_resolve_startup_defaults_known_interval_accepted() -> None:
    result = resolve_startup_defaults(
        {"interval": "5m"}, intervals=("1m", "5m", "1d")
    )
    assert result["interval"] == "5m"


def test_resolve_startup_defaults_interval_no_allowlist_accepts_any_string() -> None:
    result = resolve_startup_defaults({"interval": "99x"})
    assert result["interval"] == "99x"


def test_resolve_startup_defaults_returned_dict_isolated_from_builtin() -> None:
    # Mutating a result built with sparse overrides must not leak back
    # into the module-level BUILTIN_STARTUP_DEFAULTS dict.
    snapshot = dict(BUILTIN_STARTUP_DEFAULTS)
    result = resolve_startup_defaults({"ticker": "nvda"})
    assert result["ticker"] == "NVDA"
    result["interval"] = "tampered"
    result["theme"] = "tampered"
    assert BUILTIN_STARTUP_DEFAULTS == snapshot


def test_resolve_startup_defaults_full_override_round_trip() -> None:
    overrides = {
        "ticker": " nvda ",
        "compare": "qqq",
        "interval": "5m",
        "source": "yfinance",
        "theme": "dark",
    }
    result = resolve_startup_defaults(
        overrides,
        intervals=("1m", "5m", "1d"),
        sources=("yfinance", "synthetic"),
    )
    assert result == {
        "ticker": "NVDA",
        "compare": "QQQ",
        "interval": "5m",
        "source": "yfinance",
        "theme": "dark",
    }


# ---------------------------------------------------------------------------
# CHART_PANE_STARTUP_RATIO (2026-05-21 sprint)
# ---------------------------------------------------------------------------


def test_chart_pane_startup_ratio_is_a_finite_fraction_under_one() -> None:
    from tradinglab.constants import CHART_PANE_STARTUP_RATIO
    assert isinstance(CHART_PANE_STARTUP_RATIO, float)
    # Hardcoded constant — must be in (0, 1) so we actually leave room
    # for the notebook. A value of 1.0 would collapse the watchlist
    # column to zero width.
    assert 0.0 < CHART_PANE_STARTUP_RATIO < 1.0


def test_chart_pane_startup_ratio_default_value() -> None:
    """The default ratio should be the golden-ratio inverse (~0.618).

    The chart pane occupies the golden *major* section of the window
    and the notebook the golden *minor* — an intentionally balanced,
    aesthetically pleasing 'unboxing' split (was a flat 0.80).

    Pinned so a future `CHART_PANE_STARTUP_RATIO = 0.50` tweak that
    silently shrinks the chart trips a CI failure rather than only
    surfacing on launch. Change the constant *and* this assertion in
    the same commit when retuning.
    """
    from tradinglab.constants import (
        CHART_PANE_STARTUP_RATIO,
        GOLDEN_RATIO_INVERSE,
    )
    assert CHART_PANE_STARTUP_RATIO == GOLDEN_RATIO_INVERSE


def test_golden_ratio_constants() -> None:
    """``GOLDEN_RATIO`` / ``GOLDEN_RATIO_INVERSE`` define the canonical
    φ ≈ 1.618 and 1/φ ≈ 0.618 used for the startup pane split."""
    from tradinglab.constants import GOLDEN_RATIO, GOLDEN_RATIO_INVERSE
    assert GOLDEN_RATIO == pytest.approx(1.6180339887, abs=1e-9)
    assert GOLDEN_RATIO_INVERSE == pytest.approx(0.6180339887, abs=1e-9)
    # 1/φ == φ - 1 is the defining identity of the golden ratio.
    assert GOLDEN_RATIO_INVERSE == pytest.approx(GOLDEN_RATIO - 1.0, abs=1e-9)
    # The major + minor sections partition the whole.
    assert GOLDEN_RATIO_INVERSE + (1.0 - GOLDEN_RATIO_INVERSE) == pytest.approx(1.0)


def test_chartstack_pane_startup_width_is_positive_int() -> None:
    from tradinglab.constants import CHARTSTACK_PANE_STARTUP_WIDTH_PX
    assert isinstance(CHARTSTACK_PANE_STARTUP_WIDTH_PX, int)
    assert CHARTSTACK_PANE_STARTUP_WIDTH_PX > 0


def test_chartstack_pane_startup_width_matches_chartstack_card_default() -> None:
    """The chartstack column should match its card width default.

    Mismatches manifest as awkward gutters around the chartstack card
    on launch. Pinned so a refactor that renames the card-width const
    can't silently desync the two.
    """
    from tradinglab.constants import CHARTSTACK_PANE_STARTUP_WIDTH_PX
    assert CHARTSTACK_PANE_STARTUP_WIDTH_PX == 220
