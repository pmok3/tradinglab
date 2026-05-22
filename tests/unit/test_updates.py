"""Tests for the background GitHub Releases poll.

Pure-logic — no real network. Every test that exercises the network
path monkeypatches ``tradinglab.updates.RELEASES_URL`` to a fake
endpoint AND ``urllib.request.urlopen`` to a fake. The default empty
``RELEASES_URL`` short-circuits :func:`check_now` to
``status="disabled"`` — that path has its own dedicated test which
also asserts urlopen is never reached.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from tradinglab import updates as updates_mod


@pytest.fixture(autouse=True)
def _reset_updates_cache():
    updates_mod.reset_cache_for_tests()
    yield
    updates_mod.reset_cache_for_tests()


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal context-manager double for ``urllib.request.urlopen``."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, _n: int = -1) -> bytes:
        return self._body


def _make_urlopen(payload: dict, counter: dict):
    body = json.dumps(payload).encode("utf-8")

    def fake_urlopen(_req, timeout=None):
        counter["n"] += 1
        return _FakeResponse(body)

    return fake_urlopen


# ---------------------------------------------------------------------------
# 1. Disabled when RELEASES_URL is blank
# ---------------------------------------------------------------------------


def test_check_now_disabled_when_releases_url_blank(monkeypatch):
    # Default value is the empty string — the "disabled" short-circuit.
    assert updates_mod.RELEASES_URL == ""

    def _boom(*a, **kw):
        raise AssertionError("urlopen must not be called when disabled")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    result = updates_mod.check_now()

    assert isinstance(result, updates_mod.UpdateResult)
    assert result.status == "disabled"
    assert result.latest == ""
    assert result.url == ""


# ---------------------------------------------------------------------------
# 2. RTH suppression
# ---------------------------------------------------------------------------


def test_check_now_rth_suppression(monkeypatch):
    monkeypatch.setattr(updates_mod, "RELEASES_URL",
                        "https://example.invalid/releases.json")
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: True)

    counter = {"n": 0}
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _make_urlopen({"tag_name": "v9.9.9",
                       "html_url": "https://example.invalid/r"},
                      counter),
    )

    result = updates_mod.check_now()
    assert result.status == "rth_suppressed"
    assert counter["n"] == 0  # network was never touched

    # Flip suppression off — the call should now go through.
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)
    result = updates_mod.check_now()
    assert counter["n"] == 1
    assert result.status in {"up_to_date", "available"}


# ---------------------------------------------------------------------------
# 3. _parse_version corner cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tag, expected", [
    ("0.1.0", (0, 1, 0)),
    ("v0.1.0", (0, 1, 0)),               # strips leading 'v'
    ("0.1.0+ab12cd3", (0, 1, 0)),        # drops +local
    ("0.1.0 (2026-05-07)", (0, 1, 0)),   # drops ' (date)' tail
    ("0.1", (0, 1)),                      # no padding to 3-tuple
    ("0.1.x", (0, 1)),                    # int-parse breaks at 'x'
    ("", (0,)),                           # degenerate -> (0,)
])
def test_parse_version_corner_cases(tag, expected):
    assert updates_mod._parse_version(tag) == expected


# ---------------------------------------------------------------------------
# 4. force=True bypasses the cache; default cache prevents re-polls
# ---------------------------------------------------------------------------


def test_check_now_force_bypasses_cache(monkeypatch):
    monkeypatch.setattr(updates_mod, "RELEASES_URL",
                        "https://example.invalid/releases.json")
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)

    counter = {"n": 0}
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _make_urlopen({"tag_name": "v0.0.1",
                       "html_url": "https://example.invalid/r"},
                      counter),
    )

    first = updates_mod.check_now()
    assert counter["n"] == 1
    assert first.status in {"up_to_date", "available"}

    # Second call within TTL — cache hit, no new network call.
    second = updates_mod.check_now()
    assert counter["n"] == 1
    assert second == first

    # force=True bypasses the cache and re-polls.
    third = updates_mod.check_now(force=True)
    assert counter["n"] == 2
    assert third.status == first.status


# ---------------------------------------------------------------------------
# 5. Network errors are cached (no request storm on a flapping connection)
# ---------------------------------------------------------------------------


def test_check_now_caches_network_errors(monkeypatch):
    monkeypatch.setattr(updates_mod, "RELEASES_URL",
                        "https://example.invalid/releases.json")
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)

    counter = {"n": 0}

    def fake_urlopen(_req, timeout=None):
        counter["n"] += 1
        raise urllib.error.URLError("dns")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    first = updates_mod.check_now()
    assert first.status == "error"
    assert "URLError" in first.error
    assert counter["n"] == 1

    # Within the cache TTL — error path also caches, urlopen NOT called again.
    second = updates_mod.check_now()
    assert second.status == "error"
    assert second == first
    assert counter["n"] == 1


# ---------------------------------------------------------------------------
# 6. schedule_check_async marshals result via after_fn, swallows worker error
# ---------------------------------------------------------------------------


def test_schedule_check_async_uses_after_fn(monkeypatch):
    monkeypatch.setattr(updates_mod, "RELEASES_URL",
                        "https://example.invalid/releases.json")
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)

    counter = {"n": 0}
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _make_urlopen({"tag_name": "v0.0.1",
                       "html_url": "https://example.invalid/r"},
                      counter),
    )

    after_calls: list[tuple] = []
    after_done = threading.Event()

    def fake_after(delay, fn):
        after_calls.append((delay, fn))
        after_done.set()

    received: list = []

    def callback(r):
        received.append(r)

    updates_mod.schedule_check_async(fake_after, callback)

    assert after_done.wait(timeout=5.0), "worker never called after_fn"
    assert len(after_calls) == 1
    delay, marshaled = after_calls[0]
    assert delay == 0

    # Invoke the marshaled callable — this is what Tk's main loop would do.
    marshaled()
    assert len(received) == 1
    assert received[0].status in {"up_to_date", "available"}

    # Now: if check_now itself raises, the worker must NOT propagate but
    # SHOULD still call after_fn with an error-shaped result.
    after_calls.clear()
    received.clear()
    after_done.clear()

    def boom(*a, **kw):
        raise RuntimeError("explode")

    monkeypatch.setattr(updates_mod, "check_now", boom)

    updates_mod.schedule_check_async(fake_after, callback)
    assert after_done.wait(timeout=5.0), "worker swallowed both result and after_fn"
    assert len(after_calls) == 1
    after_calls[0][1]()
    assert len(received) == 1
    assert received[0].status == "error"
    assert "RuntimeError" in received[0].error
