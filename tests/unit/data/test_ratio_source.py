"""Tests for synthetic ratio pseudo-symbols (``AMD/NVDA`` = AMD / NVDA)."""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta

import pytest

from tradinglab.data.ratio_source import (
    RATIO_DELIMITER,
    canonical_ratio_symbol,
    compute_ratio_candles,
    fetch_ratio,
    is_ratio_symbol,
    parse_ratio_symbol,
    ratio_display_label,
)
from tradinglab.models import Candle

_T0 = datetime(2026, 6, 15, 9, 30)


def _c(ts, o, h, lo, cl, v=1000, session="regular"):
    return Candle(date=ts, open=o, high=h, low=lo, close=cl, volume=v, session=session)


def _series(prices, start=_T0, step=timedelta(days=1)):
    out, t = [], start
    for p in prices:
        out.append(_c(t, p, p + 1.0, p - 1.0, p + 0.5))
        t += step
    return out


# --------------------------------------------------------------------------- parse
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("AMD/NVDA", ("AMD", "NVDA")),
        ("amd/nvda", ("AMD", "NVDA")),
        ("  AmD / nVdA  ", ("AMD", "NVDA")),
        ("AAPL", None),
        ("", None),
        ("RSP", None),
        # Separator-free strings are ordinary tickers, NOT ratios — the
        # legacy ``RSPSPY`` shorthand is no longer recognised.
        ("RSPSPY", None),
        ("SPYRSP", None),
    ],
)
def test_parse_ratio_symbol(raw, expected):
    assert parse_ratio_symbol(raw) == expected


def test_parse_ratio_symbol_none_safe():
    assert parse_ratio_symbol(None) is None  # type: ignore[arg-type]


def test_is_ratio_symbol():
    assert is_ratio_symbol("RSP/SPY")
    assert is_ratio_symbol("  rsp / spy ")
    assert not is_ratio_symbol("AAPL")
    assert not is_ratio_symbol("")
    # Legacy shorthand is no longer a ratio.
    assert not is_ratio_symbol("RSPSPY")


# ------------------------------------------------------------- general A/B form
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("AMD/NVDA", ("AMD", "NVDA")),
        ("amd/nvda", ("AMD", "NVDA")),
        ("  amd / nvda  ", ("AMD", "NVDA")),
        ("XLF/SPY", ("XLF", "SPY")),
        ("RSP/SPY", ("RSP", "SPY")),
        # A separator-free token is just an ordinary leg name now (no
        # alias expansion), so these are plain 2-leg ratios:
        ("RSPSPY/SPY", ("RSPSPY", "SPY")),
        ("RSP/RSPSPY", ("RSP", "RSPSPY")),
        # rejects
        ("A/B/C", None),       # nested
        ("RSPSPY", None),      # separator-free -> not a ratio
        ("AMD/", None),        # empty denominator
        ("/NVDA", None),       # empty numerator
        ("/", None),
        ("AMD//NVDA", None),   # double delimiter -> 3 parts
        # real symbols with - / . must NOT be treated as ratios
        ("BRK-B", None),
        ("BRK.B", None),
        ("BTC-USD", None),
        ("AAPL", None),
    ],
)
def test_parse_general_ratio_form(raw, expected):
    assert parse_ratio_symbol(raw) == expected


def test_is_ratio_symbol_general():
    assert is_ratio_symbol("AMD/NVDA")
    assert is_ratio_symbol("amd / nvda")
    assert not is_ratio_symbol("BRK-B")
    assert not is_ratio_symbol("A/B/C")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("amd / nvda", "AMD/NVDA"),   # normalised: upper, no spaces
        ("AMD/NVDA", "AMD/NVDA"),
        ("RSPSPY", "RSPSPY"),         # non-ratio: upper+strip (no expansion)
        ("aapl", "AAPL"),             # non-ratio: upper+strip
        ("  msft ", "MSFT"),
    ],
)
def test_canonical_ratio_symbol(raw, expected):
    assert canonical_ratio_symbol(raw) == expected


def test_canonical_ratio_symbol_empty_safe():
    assert canonical_ratio_symbol("") == ""
    assert canonical_ratio_symbol(None) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("AMD/NVDA", "AMD / NVDA"),
        ("amd/nvda", "AMD / NVDA"),
        ("RSPSPY", "RSPSPY"),      # non-ratio unchanged (no expansion)
        ("AAPL", "AAPL"),          # non-ratio unchanged
    ],
)
def test_ratio_display_label(raw, expected):
    assert ratio_display_label(raw) == expected


def test_ratio_delimiter_is_slash():
    assert RATIO_DELIMITER == "/"


# ------------------------------------------------------------------------- compute
def test_compute_component_quotient_and_envelope():
    num = [_c(_T0, 160.0, 162.0, 158.0, 161.0)]
    den = [_c(_T0, 600.0, 604.0, 596.0, 602.0)]
    out = compute_ratio_candles(num, den)
    assert len(out) == 1
    bar = out[0]
    assert bar.open == pytest.approx(160.0 / 600.0)
    assert bar.close == pytest.approx(161.0 / 602.0)
    # envelope widened to be a valid candle
    assert bar.high >= max(bar.open, bar.close)
    assert bar.low <= min(bar.open, bar.close)
    assert bar.high == pytest.approx(max(160 / 600, 162 / 604, 158 / 596, 161 / 602))
    assert bar.low == pytest.approx(min(160 / 600, 162 / 604, 158 / 596, 161 / 602))
    assert bar.volume == 0
    assert bar.date == _T0


def test_compute_inner_join_on_timestamp():
    # numerator has 4 bars, denominator only the middle 2 → 2 ratio bars
    num = _series([100, 101, 102, 103])
    den = [num_bar for num_bar in _series([50, 51, 52, 53])][1:3]
    out = compute_ratio_candles(num, den)
    assert [b.date for b in out] == [num[1].date, num[2].date]


def test_compute_skips_nonpositive_denominator():
    num = [_c(_T0, 100.0, 101.0, 99.0, 100.5), _c(_T0 + timedelta(days=1), 100.0, 101.0, 99.0, 100.5)]
    den = [
        _c(_T0, 0.0, 0.0, 0.0, 0.0),  # non-positive → skipped
        _c(_T0 + timedelta(days=1), 50.0, 51.0, 49.0, 50.5),
    ]
    out = compute_ratio_candles(num, den)
    assert len(out) == 1
    assert out[0].date == _T0 + timedelta(days=1)


def test_compute_carries_numerator_session():
    num = [_c(_T0, 100.0, 101.0, 99.0, 100.5, session="premarket")]
    den = [_c(_T0, 50.0, 51.0, 49.0, 50.5, session="regular")]
    assert compute_ratio_candles(num, den)[0].session == "premarket"


@pytest.mark.parametrize("num,den", [([], []), (_series([1, 2]), []), ([], _series([1, 2]))])
def test_compute_empty_legs(num, den):
    assert compute_ratio_candles(num, den) == []


def test_compute_no_overlapping_dates():
    num = _series([100, 101], start=_T0)
    den = _series([50, 51], start=_T0 + timedelta(days=10))
    assert compute_ratio_candles(num, den) == []


# --------------------------------------------------------------------------- fetch
def test_fetch_ratio_happy_path():
    num, den = _series([160, 161]), _series([600, 601])

    def leg(t, _interval):
        return num if t == "RSP" else den

    out = fetch_ratio("RSP/SPY", "1d", leg_fetcher=leg)
    assert out is not None and len(out) == 2
    assert out[0].close == pytest.approx(160.5 / 600.5)


def test_fetch_ratio_non_ratio_returns_none():
    called = []

    def leg(t, _i):
        called.append(t)
        return _series([1])

    assert fetch_ratio("AAPL", "1d", leg_fetcher=leg) is None
    assert called == []  # short-circuits before any leg fetch


@pytest.mark.parametrize("bad_leg", ["RSP", "SPY"])
def test_fetch_ratio_either_leg_none(bad_leg):
    def leg(t, _i):
        return None if t == bad_leg else _series([100, 101])

    assert fetch_ratio("RSP/SPY", "1d", leg_fetcher=leg) is None


@pytest.mark.parametrize("bad_leg", ["RSP", "SPY"])
def test_fetch_ratio_either_leg_empty(bad_leg):
    def leg(t, _i):
        return [] if t == bad_leg else _series([100, 101])

    assert fetch_ratio("RSP/SPY", "1d", leg_fetcher=leg) is None


def test_fetch_ratio_passes_interval_through():
    seen = []

    def leg(t, interval):
        seen.append((t, interval))
        return _series([100, 101])

    fetch_ratio("RSP/SPY", "5m", leg_fetcher=leg)
    assert seen == [("RSP", "5m"), ("SPY", "5m")]


# ------------------------------------------------------------- fetch_live_data hook
def test_fetch_live_data_routes_ratio_to_fetch_ratio(monkeypatch):
    import tradinglab.data.yfinance_source as yfs

    captured = {}

    def fake_fetch_ratio(ticker, interval, *, leg_fetcher):
        captured.update(ticker=ticker, interval=interval, leg_fetcher=leg_fetcher)
        return ["sentinel"]

    monkeypatch.setattr(yfs, "fetch_ratio", fake_fetch_ratio)
    out = yfs.fetch_live_data("rsp/spy", "1d")
    assert out == ["sentinel"]
    assert captured["ticker"] == "rsp/spy"
    assert captured["interval"] == "1d"
    # leg fetcher is the same function (recursion) so legs use the same source
    assert captured["leg_fetcher"] is yfs.fetch_live_data


def test_fetch_live_data_non_ratio_does_not_call_fetch_ratio(monkeypatch):
    import tradinglab.data.yfinance_source as yfs

    def boom(*a, **k):  # pragma: no cover - must not be hit
        raise AssertionError("fetch_ratio called for a non-ratio symbol")

    monkeypatch.setattr(yfs, "fetch_ratio", boom)

    # Fake yfinance so the normal path returns quickly without network.
    fake_yf = types.ModuleType("yfinance")

    class _Ticker:
        def __init__(self, _t):
            pass

        def history(self, **_k):
            import pandas as pd

            return pd.DataFrame()

    fake_yf.Ticker = _Ticker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)

    assert yfs.fetch_live_data("AAPL", "1d") is None


# ------------------------------------------------- source-agnostic registration
# Regression: a ratio typed while a NON-yfinance source (Alpaca / Polygon) was
# active used to be passed VERBATIM to that source's fetcher, which has no
# symbol literally named "IGV/SMH" -> empty -> "Ratio '...' could not be loaded.
# Check that both legs are valid tickers" — even though each leg fetched fine.
# register_source now wraps EVERY fetcher so ratios resolve leg-by-leg through
# the SAME source. These tests pin that fix.


def _restore_source(name, fn):
    from tradinglab.data.base import DATA_SOURCES, register_source
    if fn is None:
        DATA_SOURCES.pop(name, None)
    else:
        # ``fn`` is the wrapped fetcher already in the registry; restore it
        # directly so we don't double-wrap the real source.
        DATA_SOURCES[name] = fn


def test_register_source_makes_any_source_ratio_aware():
    """A source that only knows single symbols (returns None for a "/"
    symbol, like a real vendor API) still serves ratios once registered —
    the wrapper decomposes NUM/DEN and the source never sees the "/" form."""
    from tradinglab.data.base import DATA_SOURCES, register_source

    seen: list[str] = []

    def vendor(ticker, interval):
        seen.append(ticker)
        if "/" in ticker:  # a real vendor has no symbol named "IGV/SMH"
            return None
        base = 10.0 if ticker == "IGV" else 20.0
        return _series([base, base + 1.0, base + 2.0])

    prev = DATA_SOURCES.get("vendor_probe")
    try:
        register_source("vendor_probe", vendor)
        wrapped = DATA_SOURCES["vendor_probe"]
        # The stored fetcher is the ratio-aware wrapper; raw is __wrapped__.
        assert getattr(wrapped, "_tl_ratio_aware", False) is True
        assert wrapped.__wrapped__ is vendor

        # Single leg still works.
        assert len(wrapped("IGV", "1d") or []) == 3
        # The ratio now resolves — the vendor never sees "IGV/SMH".
        out = wrapped("IGV/SMH", "1d")
        assert out is not None and len(out) == 3
        assert "IGV/SMH" not in seen  # decomposed BEFORE hitting the source
        assert "IGV" in seen and "SMH" in seen
        # close = price + 0.5 (see _series): 10.5/20.5, 11.5/21.5, 12.5/22.5.
        assert out[0].close == pytest.approx(10.5 / 20.5)
        assert out[2].close == pytest.approx(12.5 / 22.5)
    finally:
        _restore_source("vendor_probe", prev)


def test_register_source_is_idempotent_no_double_wrap():
    """Re-registering an already-wrapped fetcher must not double-wrap it."""
    from tradinglab.data.base import DATA_SOURCES, register_source

    def vendor(ticker, interval):
        return _series([1.0, 2.0]) if "/" not in ticker else None

    prev = DATA_SOURCES.get("vendor_probe2")
    try:
        register_source("vendor_probe2", vendor)
        once = DATA_SOURCES["vendor_probe2"]
        # Re-register the wrapper itself (mirrors the internal-flag test's
        # ``register_source(name, DATA_SOURCES.get(name))`` restore pattern).
        register_source("vendor_probe2", once)
        twice = DATA_SOURCES["vendor_probe2"]
        assert twice is once  # same wrapper object, not wrapped again
        assert twice.__wrapped__ is vendor  # still one layer deep
    finally:
        _restore_source("vendor_probe2", prev)


def test_register_source_ratio_forwards_range_kwargs():
    """Range-fetch kwargs (start/end) reach BOTH legs of a ratio so the
    targeted intraday fetch path works for ratios too."""
    from tradinglab.data.base import DATA_SOURCES, register_source

    seen: list[tuple] = []

    def vendor(ticker, interval, *, start=None, end=None):
        seen.append((ticker, start, end))
        return _series([5.0, 6.0]) if "/" not in ticker else None

    prev = DATA_SOURCES.get("vendor_probe3")
    try:
        register_source("vendor_probe3", vendor, supports_range=True)
        wrapped = DATA_SOURCES["vendor_probe3"]
        out = wrapped("IGV/SMH", "5m", start="S", end="E")
        assert out is not None and len(out) == 2
        # Both legs fetched with the same range window.
        assert ("IGV", "S", "E") in seen and ("SMH", "S", "E") in seen
    finally:
        _restore_source("vendor_probe3", prev)
