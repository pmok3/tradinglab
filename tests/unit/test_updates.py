"""Tests for the background GitHub Releases update poll.

Pure logic — no real network. Tests that exercise the network path monkeypatch
``urllib.request.urlopen`` to a fake and isolate the six-hour cache to a pytest
``tmp_path``.
"""

from __future__ import annotations

import json
import threading
import time
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

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

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
        urllib.request,
        "urlopen",
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
        urllib.request,
        "urlopen",
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
        urllib.request,
        "urlopen",
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
        urllib.request,
        "urlopen",
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
        urllib.request,
        "urlopen",
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

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

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

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    result = updates_mod.check_now(force=True)
    assert result.status == "error"
    assert "http or https" in result.error


class _FakeTkWidget:
    """Minimal Tk-widget double: records after() calls and their threads.

    Callbacks are NOT auto-fired — the test pumps them via ``drain()``,
    which mirrors the Tk event loop without needing a display.
    """

    def __init__(self) -> None:
        self.after_calls: list[tuple[int, object]] = []
        self.after_threads: list[int] = []

    def after(self, ms: int, fn) -> int:  # noqa: ANN001, ANN202
        self.after_calls.append((ms, fn))
        self.after_threads.append(threading.get_ident())
        return len(self.after_calls)

    def drain(self) -> bool:
        """Run all pending after() callbacks once. True if any ran."""
        pending = [fn for _, fn in self.after_calls]
        self.after_calls.clear()
        for fn in pending:
            fn()
        return bool(pending)


def _pump_until(widget: _FakeTkWidget, received: list, *, timeout: float = 5.0) -> None:
    """Drive the fake event loop until the worker result is delivered."""
    deadline = time.monotonic() + timeout
    while not received and time.monotonic() < deadline:
        if not widget.drain():
            time.sleep(0.005)
    assert received, "worker result was never delivered to the callback"


def test_schedule_check_async_delivers_result_on_tk_thread(monkeypatch) -> None:
    """Result is delivered via the widget's after() poll — and after() is
    only ever called from the scheduling (Tk) thread, never the worker.

    Regression: the worker used to call the raw ``after_fn`` itself, which
    can fail or block without a servicing owner mainloop or during teardown.
    Threaded Tcl can marshal calls while mainloop runs, but worker delivery
    must not depend on that (AGENTS.md §7.15).
    """
    monkeypatch.setattr(updates_mod, "RELEASES_URL", "https://example.invalid/releases.json")
    monkeypatch.setattr(updates_mod, "_is_rth_now", lambda: False)

    counter = {"n": 0}
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _make_urlopen(
            {"tag_name": "v0.0.1", "html_url": "https://example.invalid/r"},
            counter,
        ),
    )

    widget = _FakeTkWidget()
    received: list = []
    main_thread = threading.get_ident()

    updates_mod.schedule_check_async(widget, received.append)

    assert widget.after_calls, "initial poll was never scheduled"
    _pump_until(widget, received)

    assert len(received) == 1
    assert received[0].status in {"up_to_date", "available"}
    # The worker must never touch the widget: every after() call came
    # from the Tk (test) thread.
    assert widget.after_threads, "no after() calls recorded"
    assert all(t == main_thread for t in widget.after_threads), (
        "after() called from a worker thread — the §7.15 violation"
    )


def test_schedule_check_async_error_path(monkeypatch) -> None:
    """check_now raising still delivers an error result via the poll."""

    def boom(*_a, **_kw):
        raise RuntimeError("explode")

    monkeypatch.setattr(updates_mod, "check_now", boom)

    widget = _FakeTkWidget()
    received: list = []
    updates_mod.schedule_check_async(widget, received.append)
    _pump_until(widget, received)

    assert len(received) == 1
    assert received[0].status == "error"
    assert "RuntimeError" in received[0].error
