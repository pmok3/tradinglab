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
    resolve_startup_defaults,
)

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
    """The default ratio should be 0.80.

    Pinned so a future `CHART_PANE_STARTUP_RATIO = 0.50` tweak that
    silently shrinks the chart trips a CI failure rather than only
    surfacing on launch. Change the constant *and* this assertion in
    the same commit when retuning.
    """
    from tradinglab.constants import CHART_PANE_STARTUP_RATIO
    assert CHART_PANE_STARTUP_RATIO == 0.80


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
