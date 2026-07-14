"""Tests for ``data.base.fetch_page`` — the newest-``limit``-bars-before-``end``
page primitive (Option A) the prefetch scheduler's range planner drives, plus
the native Alpaca page fetcher.

Contract (principal-SWE review): distinct from ``fetch_range`` (``[start,end)``);
``fetch_page`` returns the most recent ``limit`` bars strictly before ``end``
(``end=None`` → newest page), i.e. one HTTP request = one rate-limiter token.
Result is a rich :class:`FetchPageResult` so the scheduler owns retry/poison/AIMD
(it needs the error + ``Retry-After``). The Alpaca fetcher does ONE HTTP attempt,
does NOT re-acquire the shared bucket (the scheduler already spent the token),
and returns bars ascending despite ``sort=desc``.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import urllib.error
from types import SimpleNamespace

from tradinglab.data import base


def _unregister(name: str) -> None:
    base.DATA_SOURCES.pop(name, None)
    base._RANGE_CAPABLE.discard(name)
    base._INTERNAL_SOURCES.discard(name)
    base._PAGE_FETCHERS.pop(name, None)


# ------------------------------------------------------------ base dispatch
def test_source_supports_page_and_ok_dispatch():
    calls: dict = {}

    def fake_page(t, i, *, end=None, limit=None):
        calls.update(ticker=t, interval=i, end=end, limit=limit)
        return ["bar1", "bar2"]

    base.register_source("pagesrc", lambda _t, _i: [], page_fetcher=fake_page)
    try:
        assert base.source_supports_page("pagesrc") is True
        res = base.fetch_page("pagesrc", "AAPL", "5m", end_ts=2000, limit=500)
        assert res.status == "ok" and res.bars == ["bar1", "bar2"]
        assert res.error is None and res.retry_after_s is None
        assert calls["ticker"] == "AAPL" and calls["interval"] == "5m"
        assert calls["end"] == dt.datetime.fromtimestamp(2000, dt.timezone.utc)
        assert calls["limit"] == 500
    finally:
        _unregister("pagesrc")


def test_fetch_page_band0_end_none_means_newest():
    captured: dict = {}

    def fake_page(t, i, *, end=None, limit=None):
        captured["end"] = end
        return ["b"]

    base.register_source("p0", lambda _t, _i: [], page_fetcher=fake_page)
    try:
        res = base.fetch_page("p0", "AAPL", "1d", end_ts=None, limit=100)
        assert res.status == "ok"
        assert captured["end"] is None  # newest page — no upper bound
    finally:
        _unregister("p0")


def test_fetch_page_unsupported_source():
    base.register_source("nopage", lambda _t, _i: [])
    try:
        assert base.source_supports_page("nopage") is False
        res = base.fetch_page("nopage", "AAPL", "5m", end_ts=1, limit=10)
        assert res.bars is None and res.status == "unsupported"
    finally:
        _unregister("nopage")


def test_fetch_page_missing_source_is_unsupported():
    res = base.fetch_page("ghost_src_xyz", "AAPL", "5m")
    assert res.bars is None and res.status == "unsupported"


def test_fetch_page_empty_return():
    base.register_source(
        "emptyp", lambda _t, _i: [],
        page_fetcher=lambda t, i, *, end=None, limit=None: [],
    )
    try:
        res = base.fetch_page("emptyp", "AAPL", "5m", end_ts=1, limit=10)
        assert res.bars == [] and res.status == "empty"
    finally:
        _unregister("emptyp")


def test_fetch_page_generic_error():
    def boom(t, i, *, end=None, limit=None):
        raise RuntimeError("net down")

    base.register_source("boomp", lambda _t, _i: [], page_fetcher=boom)
    try:
        res = base.fetch_page("boomp", "AAPL", "5m", end_ts=1, limit=10)
        assert res.bars is None and res.status == "error"
        assert isinstance(res.error, RuntimeError)
        assert res.retry_after_s is None
    finally:
        _unregister("boomp")


def test_fetch_page_error_extracts_retry_after():
    def throttled(t, i, *, end=None, limit=None):
        raise urllib.error.HTTPError(
            "u", 429, "Too Many Requests",
            {"Retry-After": "7"}, io.BytesIO(b""),
        )

    base.register_source("thr", lambda _t, _i: [], page_fetcher=throttled)
    try:
        res = base.fetch_page("thr", "AAPL", "5m", end_ts=1, limit=10)
        assert res.status == "error"
        assert res.retry_after_s == 7.0
        assert isinstance(res.error, urllib.error.HTTPError)
    finally:
        _unregister("thr")


def test_plain_reregister_clears_page_fetcher():
    fake = lambda t, i, *, end=None, limit=None: []  # noqa: E731
    base.register_source("togglep", lambda _t, _i: [], page_fetcher=fake)
    try:
        assert base.source_supports_page("togglep") is True
        base.register_source("togglep", lambda _t, _i: [])  # plain re-register
        assert base.source_supports_page("togglep") is False
    finally:
        _unregister("togglep")


# ------------------------------------------------------ Alpaca page fetcher
def _fake_creds(**over):
    d = dict(is_configured=lambda: True, api_key_id="k", api_secret_key="s",
             feed="iex", adjustment="split")
    d.update(over)
    return SimpleNamespace(alpaca=SimpleNamespace(**d))


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, _n=None):
        return json.dumps(self._payload).encode("utf-8")


def test_alpaca_page_params_sort_desc_end_limit_no_start(monkeypatch):
    import tradinglab.data.alpaca_source as A

    monkeypatch.setattr(A, "get_credentials", _fake_creds)
    monkeypatch.setattr(A, "_detected_free", False, raising=False)
    # The scheduler owns the token; the page fetch must NOT re-acquire.
    acquired = {"n": 0}

    class _SpyBucket:
        def acquire(self, *a, **k):
            acquired["n"] += 1
            return True

    monkeypatch.setattr(A, "_alpaca_bucket_for", lambda creds: _SpyBucket())

    captured: dict = {}
    payload = {"bars": [
        {"t": "2024-03-15T14:35:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
        {"t": "2024-03-15T14:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.4, "v": 12},
    ]}

    class _FakeOpener:
        def open(self, req, timeout=None):
            captured["url"] = req.full_url
            return _FakeResp(payload)

    monkeypatch.setattr(A, "credentialed_opener", lambda: _FakeOpener())

    end = dt.datetime(2024, 3, 15, 15, 0, tzinfo=dt.timezone.utc)
    bars = A.fetch_alpaca_page("AAPL", "5m", end=end, limit=500)

    url = captured["url"]
    assert "sort=desc" in url
    assert "limit=500" in url
    assert "start=" not in url
    assert "page_token" not in url
    assert "end=2024-03-15T15" in url
    # One HTTP page, scheduler-owned token → the source did not acquire.
    assert acquired["n"] == 0
    # Ascending output despite sort=desc (deepening reads bars[0] as oldest).
    assert bars, "expected candles"
    assert [c.date for c in bars] == sorted(c.date for c in bars)


def test_alpaca_page_band0_omits_end(monkeypatch):
    import tradinglab.data.alpaca_source as A

    monkeypatch.setattr(A, "get_credentials", _fake_creds)
    monkeypatch.setattr(A, "_detected_free", False, raising=False)
    monkeypatch.setattr(A, "_alpaca_bucket_for",
                        lambda creds: SimpleNamespace(acquire=lambda *a, **k: True))
    captured: dict = {}

    class _FakeOpener:
        def open(self, req, timeout=None):
            captured["url"] = req.full_url
            return _FakeResp({"bars": []})

    monkeypatch.setattr(A, "credentialed_opener", lambda: _FakeOpener())
    A.fetch_alpaca_page("AAPL", "1d", end=None, limit=10000)
    assert "end=" not in captured["url"]  # band 0 = newest page


def test_alpaca_page_not_configured_returns_empty(monkeypatch):
    import tradinglab.data.alpaca_source as A
    monkeypatch.setattr(
        A, "get_credentials",
        lambda: SimpleNamespace(alpaca=SimpleNamespace(is_configured=lambda: False)),
    )
    assert A.fetch_alpaca_page("AAPL", "5m", end=None, limit=100) == []


def test_alpaca_page_unsupported_interval_returns_empty(monkeypatch):
    import tradinglab.data.alpaca_source as A
    monkeypatch.setattr(A, "get_credentials", _fake_creds)
    assert A.fetch_alpaca_page("AAPL", "7m", end=None, limit=100) == []


def test_alpaca_page_http_error_propagates(monkeypatch):
    import tradinglab.data.alpaca_source as A

    monkeypatch.setattr(A, "get_credentials", _fake_creds)
    monkeypatch.setattr(A, "_detected_free", False, raising=False)
    monkeypatch.setattr(A, "_alpaca_bucket_for",
                        lambda creds: SimpleNamespace(acquire=lambda *a, **k: True))

    class _BoomOpener:
        def open(self, req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests",
                {"Retry-After": "3"}, io.BytesIO(b""),
            )

    monkeypatch.setattr(A, "credentialed_opener", lambda: _BoomOpener())
    # fetch_page wraps the raise into a FetchPageResult with the Retry-After.
    # Use a temp source name so we never touch the real "alpaca" registration.
    base.register_source("alpaca_pg_test", A.fetch_alpaca_data,
                         page_fetcher=A.fetch_alpaca_page)
    try:
        res = base.fetch_page("alpaca_pg_test", "AAPL", "5m",
                              end_ts=1_700_000_000, limit=100)
        assert res.status == "error"
        assert res.retry_after_s == 3.0
    finally:
        _unregister("alpaca_pg_test")
