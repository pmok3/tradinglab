"""Tests for data.base range capability + fetch_range dispatch, and the
Alpaca fetcher's explicit-range path."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from tradinglab.data import base


def _unregister(name: str) -> None:
    base.DATA_SOURCES.pop(name, None)
    base._RANGE_CAPABLE.discard(name)
    base._INTERNAL_SOURCES.discard(name)


def test_source_supports_range_and_ok_dispatch():
    calls: dict = {}

    def fake(_t, _i, *, start=None, end=None):
        calls["start"], calls["end"] = start, end
        return ["bar"]

    base.register_source("rangesrc", fake, supports_range=True)
    try:
        assert base.source_supports_range("rangesrc") is True
        bars, status = base.fetch_range("rangesrc", "AAPL", "5m", 1000, 2000)
        assert status == "ok" and bars == ["bar"]
        assert calls["start"] == dt.datetime.fromtimestamp(1000, dt.timezone.utc)
        assert calls["end"] == dt.datetime.fromtimestamp(2000, dt.timezone.utc)
    finally:
        _unregister("rangesrc")


def test_fetch_range_unsupported_source():
    base.register_source("norange", lambda _t, _i: [])
    try:
        assert base.source_supports_range("norange") is False
        bars, status = base.fetch_range("norange", "AAPL", "5m", 1, 2)
        assert bars is None and status == "unsupported"
    finally:
        _unregister("norange")


def test_fetch_range_missing_source_is_error():
    bars, status = base.fetch_range("nope_source_xyz", "AAPL", "5m", 1, 2)
    assert bars is None and status == "error"


def test_fetch_range_empty_return():
    base.register_source("emptysrc", lambda _t, _i, *, start=None, end=None: [], supports_range=True)
    try:
        bars, status = base.fetch_range("emptysrc", "AAPL", "5m", 1, 2)
        assert bars == [] and status == "empty"
    finally:
        _unregister("emptysrc")


def test_fetch_range_fetch_raises_is_error():
    def boom(_t, _i, *, start=None, end=None):
        raise RuntimeError("boom")

    base.register_source("boomsrc", boom, supports_range=True)
    try:
        bars, status = base.fetch_range("boomsrc", "AAPL", "5m", 1, 2)
        assert bars is None and status == "error"
    finally:
        _unregister("boomsrc")


def test_plain_reregister_clears_range_flag():
    fake = lambda _t, _i, *, start=None, end=None: []  # noqa: E731
    base.register_source("togglesrc", fake, supports_range=True)
    try:
        assert base.source_supports_range("togglesrc") is True
        base.register_source("togglesrc", fake)  # plain re-register
        assert base.source_supports_range("togglesrc") is False
    finally:
        _unregister("togglesrc")


def test_alpaca_uses_explicit_range(monkeypatch):
    import tradinglab.data.alpaca_source as A

    monkeypatch.setattr(
        A, "get_credentials",
        lambda: SimpleNamespace(alpaca=SimpleNamespace(
            is_configured=lambda: True, api_key_id="k", api_secret_key="s", feed="iex")),
    )
    captured: dict = {}

    def fake_page(_ticker, _tf, start, end, _creds, _token):
        captured["start"], captured["end"] = start, end
        return {"bars": [], "next_page_token": None}

    monkeypatch.setattr(A, "_http_get_page", fake_page)
    start = dt.datetime(2024, 3, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(2024, 3, 15, tzinfo=dt.timezone.utc)
    A.fetch_alpaca_data("AAPL", "5m", start=start, end=end)
    assert captured["start"] == start
    assert captured["end"] == end
