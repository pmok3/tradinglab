"""Security hardening tests for :mod:`tradinglab.updates` HTTP fetches."""
from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from tradinglab import updates


class _SpyResp:
    def __init__(self, body: bytes = b'{"version": "0.1.0"}', status: int = 200) -> None:
        self._body = body
        self.status = status
        self.read_calls: list[int] = []

    def read(self, n: int = -1) -> bytes:
        self.read_calls.append(n)
        if n is None or n < 0:
            return self._body
        return self._body[:n]

    def __enter__(self) -> _SpyResp:
        return self

    def __exit__(self, *_a: Any) -> None:
        return None


def test_fetch_release_info_caps_response_read() -> None:
    spy = _SpyResp()
    with mock.patch("urllib.request.urlopen", return_value=spy):
        updates._fetch_release_info("https://api.example.com/release.json", timeout=1.0)
    assert spy.read_calls, "the fetcher must have called resp.read(...)"
    assert spy.read_calls[0] == updates._MAX_RESPONSE_BYTES


@pytest.mark.parametrize(
    "scheme_url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "ldap://example.com/x",
        "data:text/plain,hello",
        "javascript:alert(1)",
    ],
)
def test_fetch_release_info_rejects_non_http_schemes(scheme_url: str) -> None:
    """Non-HTTP schemes must fail before any network IO."""
    with mock.patch("urllib.request.urlopen") as m_open:
        with pytest.raises(ValueError):
            updates._fetch_release_info(scheme_url, timeout=1.0)
    assert not m_open.called


@pytest.mark.parametrize(
    "scheme_url",
    [
        "http://api.example.com/release.json",
        "https://api.example.com/release.json",
    ],
)
def test_fetch_release_info_accepts_http_and_https(scheme_url: str) -> None:
    spy = _SpyResp()
    with mock.patch("urllib.request.urlopen", return_value=spy):
        result = updates._fetch_release_info(scheme_url, timeout=1.0)
    assert result == {"version": "0.1.0"}


def test_max_response_bytes_constant_is_small() -> None:
    assert updates._MAX_RESPONSE_BYTES == 64 * 1024


def test_fetch_release_info_handles_bad_json_gracefully_via_check_now(monkeypatch, tmp_path) -> None:
    spy = _SpyResp(body=b"not valid json \xff")
    monkeypatch.setattr(updates, "_cache_path", lambda: tmp_path / "update_check_cache.json")
    monkeypatch.setattr(updates, "RELEASES_URL", "https://api.example.com/release.json")
    monkeypatch.setattr(updates, "_configured_tunable_url", lambda: "")
    monkeypatch.setattr(updates, "_is_rth_now", lambda: False)
    updates.reset_cache_for_tests()
    with mock.patch("urllib.request.urlopen", return_value=spy):
        result = updates.check_now(force=True)
    assert result.status == "error"
    assert "UnicodeDecodeError" in result.error or "JSONDecodeError" in result.error
