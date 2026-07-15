"""Offline tests for the Alpaca data source (mapper + pagination).

No network: the pure response-mapper and the ``next_page_token``
accumulator are exercised with injected payloads.
"""

from __future__ import annotations

from tradinglab.core.timezones import ET
from tradinglab.data.alpaca_source import (
    _accumulate_bars,
    _alpaca_bucket_for,
    _alpaca_rate_per_min,
    _observe_rate_limit_header,
    _request_with_retry,
    _reset_tier_detection,
    _resolve_adjustment,
    _retry_after_seconds,
    _to_alpaca_symbol,
    candles_from_alpaca_response,
    is_live_capable,
    pop_pending_downgrade_notice,
)
from tradinglab.data.credentials import AlpacaCredentials
from tradinglab.data.prefetch.buckets import UNLIMITED_RATE


def _bar(ts, o, h, low, c, v):
    return {"t": ts, "o": o, "h": h, "l": low, "c": c, "v": v}


# ---------------------------------------------------------------------------
# Mapper
# ---------------------------------------------------------------------------


def test_mapper_envelope():
    payload = {"bars": [_bar("2024-03-07T14:30:00Z", 175.0, 175.5, 174.8, 175.2, 1000)]}
    out = candles_from_alpaca_response(payload, interval="5m")
    assert len(out) == 1
    c = out[0]
    assert (c.open, c.high, c.low, c.close, c.volume) == (175.0, 175.5, 174.8, 175.2, 1000)


def test_mapper_accepts_bare_list():
    rows = [_bar("2024-03-07T14:30:00Z", 1.0, 2.0, 0.5, 1.5, 10)]
    assert len(candles_from_alpaca_response(rows, interval="1d")) == 1


def test_mapper_empty_inputs():
    assert candles_from_alpaca_response({"bars": []}, interval="1d") == []
    assert candles_from_alpaca_response({}, interval="1d") == []


def test_mapper_drops_non_finite_rows():
    payload = {"bars": [
        _bar("2024-03-07T14:30:00Z", 1.0, 2.0, 0.5, 1.5, 10),
        _bar("2024-03-07T14:35:00Z", float("nan"), 2.0, 0.5, 1.5, 10),
    ]}
    out = candles_from_alpaca_response(payload, interval="5m")
    assert len(out) == 1  # the NaN-open row is dropped by the shared normalizer


def test_mapper_timestamps_are_eastern():
    # Alpaca returns UTC; the mapper converts to US Eastern so the chart /
    # session logic read the correct exchange wall-clock (matching yfinance).
    # 14:30Z on 2024-03-07 (EST, before DST) == 09:30 ET (the RTH open).
    payload = {"bars": [_bar("2024-03-07T14:30:00Z", 1.0, 1.0, 1.0, 1.0, 1)]}
    out = candles_from_alpaca_response(payload, interval="5m")
    d = out[0].date
    assert d.tzinfo is not None
    if ET is None:
        # Missing tzdata → graceful fallback to UTC (documented). Skip the
        # Eastern-specific assertions the exe/dev environment exercises.
        import pytest
        pytest.skip("tzdata unavailable; ET conversion falls back to UTC")
    assert str(d.tzinfo) == "America/New_York"
    assert (d.hour, d.minute) == (9, 30)
    assert d.utcoffset().total_seconds() == -5 * 3600  # EST
    assert out[0].session == "regular"


def test_mapper_intraday_sessions_use_eastern():
    # Regression for the "5m data only shows 14:30–16:00" bug: a full UTC
    # session must map to the correct ET pre / regular / post labels, not a
    # +5h-shifted band. 2024-03-07 is EST (UTC-5).
    if ET is None:
        import pytest
        pytest.skip("tzdata unavailable; ET conversion falls back to UTC")
    payload = {"bars": [
        _bar("2024-03-07T13:00:00Z", 1, 1, 1, 1, 1),  # 08:00 ET → pre
        _bar("2024-03-07T14:30:00Z", 1, 1, 1, 1, 1),  # 09:30 ET → regular (open)
        _bar("2024-03-07T20:55:00Z", 1, 1, 1, 1, 1),  # 15:55 ET → regular (close)
        _bar("2024-03-07T21:30:00Z", 1, 1, 1, 1, 1),  # 16:30 ET → post
    ]}
    out = candles_from_alpaca_response(payload, interval="5m")
    assert [c.session for c in out] == ["pre", "regular", "regular", "post"]
    assert [(c.date.hour, c.date.minute) for c in out] == [
        (8, 0), (9, 30), (15, 55), (16, 30),
    ]


def test_mapper_daily_timestamp_keeps_session_date():
    # Alpaca daily bars are stamped at 05:00Z (midnight ET). Converting to ET
    # must NOT roll the calendar date back a day.
    if ET is None:
        import pytest
        pytest.skip("tzdata unavailable; ET conversion falls back to UTC")
    payload = {"bars": [_bar("2025-02-25T05:00:00Z", 1, 1, 1, 1, 1)]}
    out = candles_from_alpaca_response(payload, interval="1d")
    assert out[0].date.date().isoformat() == "2025-02-25"


# ---------------------------------------------------------------------------
# Pagination accumulator
# ---------------------------------------------------------------------------


def test_accumulate_single_page():
    page = {"bars": [_bar("2024-01-01T00:00:00Z", 1, 1, 1, 1, 1)], "next_page_token": None}
    seen = []

    def fetch(token):
        seen.append(token)
        return page

    out = _accumulate_bars(fetch)
    assert seen == [None]
    assert len(out["bars"]) == 1


def test_accumulate_walks_multiple_pages():
    p1 = {"bars": [_bar("2024-01-01T00:00:00Z", 1, 1, 1, 1, 1)], "next_page_token": "t2"}
    p2 = {"bars": [_bar("2024-01-01T00:05:00Z", 2, 2, 2, 2, 2)], "next_page_token": "t3"}
    p3 = {"bars": [_bar("2024-01-01T00:10:00Z", 3, 3, 3, 3, 3)], "next_page_token": None}
    seq = {None: p1, "t2": p2, "t3": p3}
    seen = []

    def fetch(token):
        seen.append(token)
        return seq[token]

    out = _accumulate_bars(fetch, page_pause_s=0.0)
    assert seen == [None, "t2", "t3"]
    assert len(out["bars"]) == 3


def test_accumulate_stops_on_non_dict():
    out = _accumulate_bars(lambda _tok: None)
    assert out == {"bars": []}


def test_accumulate_treats_empty_token_as_end():
    page = {"bars": [_bar("2024-01-01T00:00:00Z", 1, 1, 1, 1, 1)], "next_page_token": ""}
    out = _accumulate_bars(lambda _tok: page)
    assert len(out["bars"]) == 1  # empty-string token → stop after page 1


def test_accumulate_respects_max_pages_cap():
    # A never-null token must not loop forever.
    def fetch(_token):
        return {"bars": [_bar("2024-01-01T00:00:00Z", 1, 1, 1, 1, 1)],
                "next_page_token": "always"}

    out = _accumulate_bars(fetch, max_pages=5, page_pause_s=0.0)
    assert len(out["bars"]) == 5


def test_accumulate_then_map_round_trip():
    p1 = {"bars": [_bar("2024-01-01T00:00:00Z", 1, 1, 1, 1, 1)], "next_page_token": "t2"}
    p2 = {"bars": [_bar("2024-01-01T00:05:00Z", 2, 2, 2, 2, 2)], "next_page_token": None}
    seq = {None: p1, "t2": p2}
    payload = _accumulate_bars(lambda tok: seq[tok], page_pause_s=0.0)
    candles = candles_from_alpaca_response(payload, interval="5m")
    assert len(candles) == 2


# ---------------------------------------------------------------------------
# Inter-page throttle (perf item #4)
# ---------------------------------------------------------------------------


def test_accumulate_throttles_between_pages_not_before_first():
    """A small pause is applied BETWEEN pages (never before page 1) so a
    deep multi-page fetch doesn't burst the 200/min ceiling."""
    p1 = {"bars": [_bar("2024-01-01T00:00:00Z", 1, 1, 1, 1, 1)], "next_page_token": "t2"}
    p2 = {"bars": [_bar("2024-01-01T00:05:00Z", 2, 2, 2, 2, 2)], "next_page_token": "t3"}
    p3 = {"bars": [_bar("2024-01-01T00:10:00Z", 3, 3, 3, 3, 3)], "next_page_token": None}
    seq = {None: p1, "t2": p2, "t3": p3}
    sleeps: list[float] = []
    _accumulate_bars(lambda tok: seq[tok], sleep_fn=sleeps.append, page_pause_s=0.3)
    # 3 pages → 2 inter-page pauses (before page 2 and page 3), none before page 1.
    assert sleeps == [0.3, 0.3]


def test_accumulate_single_page_never_sleeps():
    page = {"bars": [_bar("2024-01-01T00:00:00Z", 1, 1, 1, 1, 1)], "next_page_token": None}
    sleeps: list[float] = []
    _accumulate_bars(lambda _tok: page, sleep_fn=sleeps.append, page_pause_s=0.3)
    assert sleeps == []


# ---------------------------------------------------------------------------
# Symbol translation (perf item #3): yfinance dash → Alpaca dot
# ---------------------------------------------------------------------------


def test_to_alpaca_symbol_share_class_dash_to_dot():
    assert _to_alpaca_symbol("BRK-B") == "BRK.B"
    assert _to_alpaca_symbol("BF-B") == "BF.B"


def test_to_alpaca_symbol_plain_unchanged_and_uppercased():
    assert _to_alpaca_symbol("AMD") == "AMD"
    assert _to_alpaca_symbol("amd") == "AMD"
    assert _to_alpaca_symbol("  spy ") == "SPY"


def test_to_alpaca_symbol_handles_empty():
    assert _to_alpaca_symbol("") == ""
    assert _to_alpaca_symbol(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Adjustment resolution (perf item #2)
# ---------------------------------------------------------------------------


def test_resolve_adjustment_defaults_to_split():
    assert _resolve_adjustment(AlpacaCredentials()) == "split"


def test_resolve_adjustment_passes_valid_values():
    for v in ("raw", "split", "dividend", "all"):
        assert _resolve_adjustment(AlpacaCredentials(adjustment=v)) == v


def test_resolve_adjustment_falls_back_on_invalid():
    assert _resolve_adjustment(AlpacaCredentials(adjustment="bogus")) == "split"
    assert _resolve_adjustment(AlpacaCredentials(adjustment="")) == "split"


# ---------------------------------------------------------------------------
# HTTP retry / backoff (perf item #4)
# ---------------------------------------------------------------------------


def test_retry_after_honours_header_seconds():
    assert _retry_after_seconds({"Retry-After": "2"}, 0) == 2.0
    assert _retry_after_seconds({"Retry-After": "0"}, 5) == 0.0


def test_retry_after_falls_back_to_exponential_backoff():
    # No header → base * 2**attempt, capped.
    assert _retry_after_seconds(None, 0) == 0.5
    assert _retry_after_seconds(None, 1) == 1.0
    assert _retry_after_seconds(None, 2) == 2.0
    assert _retry_after_seconds({}, 99) == 8.0  # capped at _BACKOFF_CAP_S


def test_retry_after_ignores_garbage_header():
    assert _retry_after_seconds({"Retry-After": "soon"}, 0) == 0.5  # unparseable → backoff


def test_request_with_retry_succeeds_after_transient_429():
    import urllib.error

    calls = {"n": 0}

    def do():
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError("u", 429, "rate", {"Retry-After": "0"}, None)
        return {"ok": True}

    out = _request_with_retry(do, sleep_fn=lambda _s: None)
    assert out == {"ok": True}
    assert calls["n"] == 3


def test_request_with_retry_reraises_non_retryable():
    import urllib.error

    def do():
        raise urllib.error.HTTPError("u", 404, "nope", {}, None)

    import pytest
    with pytest.raises(urllib.error.HTTPError):
        _request_with_retry(do, sleep_fn=lambda _s: None)


def test_request_with_retry_gives_up_after_max_retries():
    import urllib.error

    calls = {"n": 0}

    def do():
        calls["n"] += 1
        raise urllib.error.HTTPError("u", 503, "down", {}, None)

    import pytest
    with pytest.raises(urllib.error.HTTPError):
        _request_with_retry(do, sleep_fn=lambda _s: None, max_retries=2)
    assert calls["n"] == 3  # initial + 2 retries


# ---------------------------------------------------------------------------
# Tier → per-minute rate budget + shared token bucket (perf item (b))
# ---------------------------------------------------------------------------


def test_alpaca_rate_per_min_by_tier():
    assert _alpaca_rate_per_min(AlpacaCredentials(tier="free")) == 200
    assert _alpaca_rate_per_min(AlpacaCredentials(tier="paid")) == UNLIMITED_RATE
    # Default + unknown tier → safe free budget.
    assert _alpaca_rate_per_min(AlpacaCredentials()) == 200
    assert _alpaca_rate_per_min(AlpacaCredentials(tier="bogus")) == 200


def test_alpaca_bucket_reconfigures_on_tier_change():
    # One shared bucket for the account; a tier change updates its rate live.
    try:
        b_free = _alpaca_bucket_for(AlpacaCredentials(tier="free"))
        assert b_free.rate_per_min == 200
        b_paid = _alpaca_bucket_for(AlpacaCredentials(tier="paid"))
        assert b_paid is b_free  # same shared instance
        assert b_paid.rate_per_min == UNLIMITED_RATE  # paid → unlimited
    finally:
        # Restore the module-global bucket to the safe free rate so this
        # process-wide state doesn't leak into other tests.
        _alpaca_bucket_for(AlpacaCredentials(tier="free"))


# ---------------------------------------------------------------------------
# Header-driven free-tier auto-detect + downgrade (perf item (b) auto-detect)
# ---------------------------------------------------------------------------


def test_observe_free_header_downgrades_a_paid_config():
    try:
        _alpaca_bucket_for(AlpacaCredentials(tier="paid"))  # bucket → unlimited
        paid = AlpacaCredentials(tier="paid")
        assert _alpaca_rate_per_min(paid) == UNLIMITED_RATE
        _observe_rate_limit_header({"X-RateLimit-Limit": "200"})
        # Downgraded: even a persisted paid tier is now capped at the free
        # budget, and a one-shot notice is queued for the popup.
        assert _alpaca_rate_per_min(paid) == 200
        notice = pop_pending_downgrade_notice()
        assert notice is not None and "IEX" in notice and "Free" in notice
        assert pop_pending_downgrade_notice() is None  # one-shot
    finally:
        _reset_tier_detection()


def test_observe_is_one_shot_no_duplicate_notices():
    try:
        _alpaca_bucket_for(AlpacaCredentials(tier="paid"))
        _observe_rate_limit_header({"X-RateLimit-Limit": "200"})
        assert pop_pending_downgrade_notice() is not None
        # A second free header must NOT re-record a notice.
        _observe_rate_limit_header({"X-RateLimit-Limit": "200"})
        assert pop_pending_downgrade_notice() is None
    finally:
        _reset_tier_detection()


def test_observe_paid_header_does_not_downgrade():
    try:
        _alpaca_bucket_for(AlpacaCredentials(tier="paid"))
        _observe_rate_limit_header({"X-RateLimit-Limit": "10000"})
        assert _alpaca_rate_per_min(AlpacaCredentials(tier="paid")) == UNLIMITED_RATE
        assert pop_pending_downgrade_notice() is None
    finally:
        _reset_tier_detection()


def test_observe_free_header_on_already_free_is_noop():
    # Free key + free config: header matches, nothing to downgrade → no popup.
    try:
        _alpaca_bucket_for(AlpacaCredentials(tier="free"))  # bucket → 200
        _observe_rate_limit_header({"X-RateLimit-Limit": "200"})
        assert pop_pending_downgrade_notice() is None
    finally:
        _reset_tier_detection()


def test_observe_ignores_missing_or_garbage_header():
    try:
        _alpaca_bucket_for(AlpacaCredentials(tier="paid"))
        for headers in (None, {}, {"X-RateLimit-Limit": "soon"},
                        {"X-RateLimit-Limit": "0"}):
            _observe_rate_limit_header(headers)
        assert _alpaca_rate_per_min(AlpacaCredentials(tier="paid")) == UNLIMITED_RATE
        assert pop_pending_downgrade_notice() is None
    finally:
        _reset_tier_detection()


# ---------------------------------------------------------------------------
# is_live_capable — free tier is 15-min delayed, must not drive live updates
# ---------------------------------------------------------------------------


def test_is_live_capable_paid_is_real_time():
    # Paid (SIP) is real-time → live-capable.
    assert is_live_capable(AlpacaCredentials(tier="paid")) is True


def test_is_live_capable_free_is_delayed():
    # Free (IEX) real-time data is delayed 15 min → NOT live-capable.
    assert is_live_capable(AlpacaCredentials(tier="free")) is False
    # Default + unknown tier fall back to the delayed (safe) answer.
    assert is_live_capable(AlpacaCredentials()) is False
    assert is_live_capable(AlpacaCredentials(tier="bogus")) is False


def test_is_live_capable_false_after_free_autodetect_downgrade():
    # A header-auto-detected free key is delayed even if 'paid' was persisted,
    # mirroring the rate-budget clamp — so live polling stays suppressed.
    try:
        _alpaca_bucket_for(AlpacaCredentials(tier="paid"))
        paid = AlpacaCredentials(tier="paid")
        assert is_live_capable(paid) is True
        _observe_rate_limit_header({"X-RateLimit-Limit": "200"})
        assert is_live_capable(paid) is False
    finally:
        _reset_tier_detection()
