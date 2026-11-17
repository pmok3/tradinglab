"""Tests for the background GitHub Releases update poll.

Pure logic — no real network. Tests that exercise the network path monkeypatch
the private HTTPS opener to a fake and isolate the six-hour cache to a pytest
``tmp_path``.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from tradinglab import updates as updates_mod


@pytest.fixture(autouse=True)
def _reset_updates_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(
        updates_mod,
        "_cache_path",
        lambda: tmp_path / "update_check_cache.json",
    )
    monkeypatch.setattr(updates_mod, "_configured_tunable_url", lambda: "")
    monkeypatch.delenv(updates_mod.ENV_URL, raising=False)
    updates_mod.reset_cache_for_tests(clear_disk=True)
    yield
    updates_mod.reset_cache_for_tests(clear_disk=True)


class _FakeResponse:
    """Minimal context-manager double for ``urllib.request.urlopen``."""

    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self.status = status
        self.read_calls: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n: int = -1) -> bytes:
        self.read_calls.append(n)
        if n is None or n < 0:
            return self._body
        return self._body[:n]


def _make_urlopen(payload: dict, counter: dict, *, status: int = 200):
    body = json.dumps(payload).encode("utf-8")

    def fake_urlopen(_req, timeout=None):
        counter["n"] += 1
        return _FakeResponse(body, status=status)

    return fake_urlopen


def test_releases_url_points_to_latest_github_endpoint() -> None:
    assert updates_mod.DEFAULT_RELEASES_URL == (
        "https://api.github.com/repos/pmok3/tradinglab/releases/latest"
    )
    assert updates_mod.RELEASES_URL == updates_mod.DEFAULT_RELEASES_URL


def test_resolve_url_precedence(monkeypatch) -> None:
    monkeypatch.setattr(updates_mod, "RELEASES_URL", "https://default.example/latest")
    monkeypatch.setenv(updates_mod.ENV_URL, "https://env.example/latest")
    monkeypatch.setattr(
        updates_mod,
        "_configured_tunable_url",
        lambda: "https://tunable.example/latest",
    )
    assert updates_mod._resolve_url() == "https://tunable.example/latest"

    monkeypatch.setattr(updates_mod, "_configured_tunable_url", lambda: "")
    assert updates_mod._resolve_url() == "https://env.example/latest"

    monkeypatch.delenv(updates_mod.ENV_URL, raising=False)
    assert updates_mod._resolve_url() == "https://default.example/latest"


def test_check_now_disabled_when_all_urls_blank(monkeypatch) -> None:
    monkeypatch.setattr(updates_mod, "RELEASES_URL", "")

    def _boom(*_a, **_kw):
        raise AssertionError("urlopen must not be called when disabled")

    monkeypatch.setattr(updates_mod._HTTPS_OPENER, "open", _boom)

    result = updates_mod.check_now()

    assert isinstance(result, updates_mod.UpdateResult)
    assert result.status == "disabled"
    assert result.latest == ""
    assert result.url == ""


def test_check_now_rth_suppression(monkeypatch) -> None:
    monkeypatch.setattr(
        updates_mod,
        "RELEASES_URL",
        "https://example.invalid/releases.json",
    )
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: True)

    counter = {"n": 0}
    monkeypatch.setattr(
        updates_mod._HTTPS_OPENER,
        "open",
        _make_urlopen(
            {"tag_name": "v9.9.9", "html_url": "https://example.invalid/r"},
            counter,
        ),
    )

    result = updates_mod.check_now()
    assert result.status == "rth_suppressed"
    assert counter["n"] == 0

    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)
    result = updates_mod.check_now()
    assert counter["n"] == 1
    assert result.status in {"up_to_date", "available"}


@pytest.mark.parametrize(
    "tag, expected",
    [
        ("0.1.0", (0, 1, 0)),
        ("v0.1.0", (0, 1, 0)),
        ("0.1.0+ab12cd3", (0, 1, 0)),
        ("0.1.0-rc1", (0, 1, 0)),
        ("0.1.0 (2026-05-07)", (0, 1, 0)),
        ("0.1", (0, 1, 0)),
        ("0.1.x", (0,)),
        ("", (0,)),
    ],
)
def test_parse_version_corner_cases(tag, expected) -> None:
    assert updates_mod._parse_version(tag) == expected


@pytest.mark.parametrize(
    "current, advertised, expected",
    [
        ("0.1.0", "0.2.0", "0.2.0"),
        ("0.1.0", "v0.2.0", "0.2.0"),
        ("0.1.0", "0.2.0+dev", "0.2.0"),
        ("0.2.0", "0.2.0", None),
        ("0.2.0", "0.1.5", None),
        ("garbage", "0.2.0", None),
        ("0.1.0", "not-a-version", None),
    ],
)
def test_compare_versions(current, advertised, expected) -> None:
    assert updates_mod.compare_versions(current, advertised) == expected


def test_extract_version_accepts_plain_and_github_payloads() -> None:
    assert updates_mod._extract_version_from_payload({"version": "0.2.3"}) == "0.2.3"
    assert updates_mod._extract_version_from_payload({"tag_name": "v0.2.3"}) == "v0.2.3"
    assert updates_mod._extract_version_from_payload(
        {"version": "0.2.3", "tag_name": "v9.9.9"},
    ) == "0.2.3"
    assert updates_mod._extract_version_from_payload({}) is None
    assert updates_mod._extract_version_from_payload({"version": "   "}) is None
    assert updates_mod._extract_version_from_payload(None) is None


def test_check_now_available_from_github_payload(monkeypatch) -> None:
    monkeypatch.setattr(updates_mod, "RELEASES_URL", "https://example.invalid/releases.json")
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)
    monkeypatch.setattr(updates_mod, "_current_version", lambda: "0.1.1")

    counter = {"n": 0}
    monkeypatch.setattr(
        updates_mod._HTTPS_OPENER,
        "open",
        _make_urlopen(
            {"tag_name": "v0.2.0", "html_url": "https://example.invalid/r"},
            counter,
        ),
    )

    result = updates_mod.check_now(force=True)
    assert result.status == "available"
    assert result.latest == "v0.2.0"
    assert result.url == "https://example.invalid/r"
    assert counter["n"] == 1


def test_check_now_available_from_plain_version_payload(monkeypatch) -> None:
    monkeypatch.setattr(updates_mod, "RELEASES_URL", "https://example.invalid/releases.json")
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)
    monkeypatch.setattr(updates_mod, "_current_version", lambda: "0.1.1")

    counter = {"n": 0}
    monkeypatch.setattr(
        updates_mod._HTTPS_OPENER,
        "open",
        _make_urlopen({"version": "0.2.0"}, counter),
    )

    result = updates_mod.check_now(force=True)
    assert result.status == "available"
    assert result.latest == "0.2.0"
    assert result.url == ""


def test_check_now_force_bypasses_cache(monkeypatch) -> None:
    monkeypatch.setattr(updates_mod, "RELEASES_URL", "https://example.invalid/releases.json")
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)
    monkeypatch.setattr(updates_mod, "_current_version", lambda: "0.1.1")

    counter = {"n": 0}
    monkeypatch.setattr(
        updates_mod._HTTPS_OPENER,
        "open",
        _make_urlopen(
            {"tag_name": "v0.1.1", "html_url": "https://example.invalid/r"},
            counter,
        ),
    )

    first = updates_mod.check_now()
    assert counter["n"] == 1
    assert first.status == "up_to_date"

    second = updates_mod.check_now()
    assert counter["n"] == 1
    assert second == first

    third = updates_mod.check_now(force=True)
    assert counter["n"] == 2
    assert third.status == first.status


def test_check_now_reuses_disk_cache_after_memory_reset(monkeypatch) -> None:
    monkeypatch.setattr(updates_mod, "RELEASES_URL", "https://example.invalid/releases.json")
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)
    monkeypatch.setattr(updates_mod, "_current_version", lambda: "0.1.1")

    counter = {"n": 0}
    monkeypatch.setattr(
        updates_mod._HTTPS_OPENER,
        "open",
        _make_urlopen(
            {"tag_name": "v0.1.1", "html_url": "https://example.invalid/r"},
            counter,
        ),
    )

    first = updates_mod.check_now()
    assert counter["n"] == 1

    updates_mod.reset_cache_for_tests()
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: True)
    second = updates_mod.check_now()
    assert counter["n"] == 1
    assert second == first


def test_check_now_caches_network_errors(monkeypatch) -> None:
    monkeypatch.setattr(updates_mod, "RELEASES_URL", "https://example.invalid/releases.json")
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)

    counter = {"n": 0}

    def fake_urlopen(_req, timeout=None):
        counter["n"] += 1
        raise urllib.error.URLError("dns")

    monkeypatch.setattr(updates_mod._HTTPS_OPENER, "open", fake_urlopen)

    first = updates_mod.check_now()
    assert first.status == "error"
    assert "URLError" in first.error
    assert counter["n"] == 1

    second = updates_mod.check_now()
    assert second.status == "error"
    assert second == first
    assert counter["n"] == 1


def test_check_now_rejects_non_http_url_without_network(monkeypatch) -> None:
    monkeypatch.setattr(updates_mod, "RELEASES_URL", "file:///not/a/release.json")
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)

    def _boom(*_a, **_kw):
        raise AssertionError("urlopen must not be called for non-http schemes")

    monkeypatch.setattr(updates_mod._HTTPS_OPENER, "open", _boom)

    result = updates_mod.check_now(force=True)
    assert result.status == "error"
    assert "https://" in result.error


def test_schedule_check_async_uses_after_fn(monkeypatch) -> None:
    monkeypatch.setattr(updates_mod, "RELEASES_URL", "https://example.invalid/releases.json")
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)

    counter = {"n": 0}
    monkeypatch.setattr(
        updates_mod._HTTPS_OPENER,
        "open",
        _make_urlopen(
            {"tag_name": "v0.0.1", "html_url": "https://example.invalid/r"},
            counter,
        ),
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

    marshaled()
    assert len(received) == 1
    assert received[0].status in {"up_to_date", "available"}

    after_calls.clear()
    received.clear()
    after_done.clear()

    def boom(*_a, **_kw):
        raise RuntimeError("explode")

    monkeypatch.setattr(updates_mod, "check_now", boom)

    updates_mod.schedule_check_async(fake_after, callback)
    assert after_done.wait(timeout=5.0), "worker swallowed both result and after_fn"
    assert len(after_calls) == 1
    after_calls[0][1]()
    assert len(received) == 1
    assert received[0].status == "error"
    assert "RuntimeError" in received[0].error


# ---------------------------------------------------------------------------
# HTTPS-only overrides
# ---------------------------------------------------------------------------


def test_http_tunable_override_rejected(monkeypatch) -> None:
    """An http:// ``update_check_url`` tunable must be refused, never fetched."""
    monkeypatch.setattr(
        updates_mod, "_configured_tunable_url", lambda: "http://mirror.example/latest"
    )
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)

    counter = {"n": 0}

    def _boom(*_a, **_kw):
        counter["n"] += 1
        raise AssertionError("urlopen must not be called for an http:// override")

    monkeypatch.setattr(updates_mod._HTTPS_OPENER, "open", _boom)

    result = updates_mod.check_now(force=True)

    assert result.status == "error"
    assert "https" in result.error
    assert counter["n"] == 0


def test_http_env_override_rejected(monkeypatch) -> None:
    """An http:// TRADINGLAB_UPDATE_URL must be refused, never fetched."""
    monkeypatch.setenv(updates_mod.ENV_URL, "http://mirror.example/latest")
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)

    counter = {"n": 0}

    def _boom(*_a, **_kw):
        counter["n"] += 1
        raise AssertionError("urlopen must not be called for an http:// override")

    monkeypatch.setattr(updates_mod._HTTPS_OPENER, "open", _boom)

    result = updates_mod.check_now(force=True)

    assert result.status == "error"
    assert "https" in result.error
    assert counter["n"] == 0


@pytest.mark.parametrize(
    "bad", ["file:///etc/passwd", "api.github.com/repos/x/latest", "ftp://x/y"]
)
def test_non_http_scheme_override_rejected(monkeypatch, bad) -> None:
    """Non-http(s) override schemes (file://, bare hosts) are refused too."""
    monkeypatch.setattr(updates_mod, "_configured_tunable_url", lambda: bad)
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)

    counter = {"n": 0}

    def _boom(*_a, **_kw):
        counter["n"] += 1
        raise AssertionError("urlopen must not be called for a bad override")

    monkeypatch.setattr(updates_mod._HTTPS_OPENER, "open", _boom)

    result = updates_mod.check_now(force=True)

    assert result.status == "error"
    assert "https" in result.error
    assert counter["n"] == 0


def test_https_override_still_works(monkeypatch) -> None:
    """An https:// override keeps flowing through to a normal check."""
    monkeypatch.setattr(
        updates_mod,
        "_configured_tunable_url",
        lambda: "https://mirror.example/latest",
    )
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)
    monkeypatch.setattr(updates_mod, "_current_version", lambda: "0.1.1")

    counter = {"n": 0}
    monkeypatch.setattr(
        updates_mod._HTTPS_OPENER,
        "open",
        _make_urlopen(
            {"tag_name": "v0.2.0", "html_url": "https://mirror.example/r"}, counter
        ),
    )

    result = updates_mod.check_now(force=True)

    assert result.status == "available"
    assert result.latest == "v0.2.0"
    assert counter["n"] == 1


def test_https_scheme_helper() -> None:
    assert updates_mod._is_https_url("https://example.invalid/x")
    assert not updates_mod._is_https_url("http://example.invalid/x")
    assert not updates_mod._is_https_url("https://")
    assert not updates_mod._is_https_url("not a url")


@pytest.mark.parametrize("key", ["html_url", "url"])
@pytest.mark.parametrize("url", ["http://example.invalid/r", "file:///tmp/update", "https://"])
def test_returned_release_link_requires_https(monkeypatch, key, url):
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)
    monkeypatch.setattr(
        updates_mod._HTTPS_OPENER, "open",
        _make_urlopen({"tag_name": "v99.0.0", key: url}, {"n": 0}),
    )
    result = updates_mod.check_now(force=True)
    assert result.status == "error"
    assert result.url == ""
    assert "release URL" in result.error


@pytest.mark.parametrize("disk", [False, True])
def test_unsafe_cached_release_link_is_not_returned(monkeypatch, disk):
    bad = updates_mod.UpdateResult(
        status="available", current=updates_mod._current_version(),
        latest="v99.0.0", url="http://example.invalid/release",
    )
    updates_mod._store_cache(bad, source_url=updates_mod.RELEASES_URL)
    if disk:
        updates_mod.reset_cache_for_tests()
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: True)
    monkeypatch.setattr(
        updates_mod._HTTPS_OPENER, "open",
        lambda *_a, **_kw: pytest.fail("cache validation must not fetch"),
    )
    result = updates_mod.check_now()
    assert result.status == "error"
    assert result.url == ""


def test_resolves_endpoint_only_once(monkeypatch):
    calls = []

    def tunable():
        calls.append(True)
        return "https://example.invalid/latest" if len(calls) == 1 else "http://example.invalid/latest"

    monkeypatch.setattr(updates_mod, "_configured_tunable_url", tunable)
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)
    monkeypatch.setattr(
        updates_mod._HTTPS_OPENER, "open",
        _make_urlopen({"version": "99.0.0"}, {"n": 0}),
    )
    assert updates_mod.check_now(force=True).status == "available"
    assert len(calls) == 1
