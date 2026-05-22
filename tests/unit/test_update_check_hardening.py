"""Tests for the H3/L2 hardening of :func:`_update_check._fetch_release_info`.

Two security fixes verified here:

* **H3 — Read cap.** The release-manifest fetch now caps the response
  body at 64 KB. Without this, a hostile or misconfigured upstream
  could stream gigabytes into the daemon thread.

* **L2 — URL scheme allow-list.** Only ``http://`` and ``https://``
  URLs are accepted. ``file://`` / ``ftp://`` / other schemes
  return ``None`` immediately — defense against a misconfigured
  override env var pointing at the local filesystem.
"""
from __future__ import annotations

from typing import Any, List
from unittest import mock

import pytest

from tradinglab import _update_check


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
    with mock.patch.object(_update_check, "_fetch_release_info", wraps=_update_check._fetch_release_info):
        with mock.patch("urllib.request.urlopen", return_value=spy):
            _update_check._fetch_release_info("https://api.example.com/release.json", timeout=1.0)
    assert spy.read_calls, "the fetcher must have called resp.read(...)"
    assert spy.read_calls[0] == _update_check._MAX_RESPONSE_BYTES


@pytest.mark.parametrize("scheme_url", [
    "file:///etc/passwd",
    "ftp://example.com/file",
    "ldap://example.com/x",
    "data:text/plain,hello",
    "javascript:alert(1)",
])
def test_fetch_release_info_rejects_non_http_schemes(scheme_url: str) -> None:
    """Non-HTTP schemes must short-circuit to None without any network IO."""
    with mock.patch("urllib.request.urlopen") as m_open:
        result = _update_check._fetch_release_info(scheme_url, timeout=1.0)
    assert result is None
    assert not m_open.called, (
        f"urlopen must not be reached for scheme in {scheme_url!r}"
    )


@pytest.mark.parametrize("scheme_url", [
    "http://api.example.com/release.json",
    "https://api.example.com/release.json",
])
def test_fetch_release_info_accepts_http_and_https(scheme_url: str) -> None:
    spy = _SpyResp()
    with mock.patch("urllib.request.urlopen", return_value=spy):
        result = _update_check._fetch_release_info(scheme_url, timeout=1.0)
    assert result == {"version": "0.1.0"}


def test_max_response_bytes_constant_is_small() -> None:
    """64 KB is the audit-recommended cap; a release-manifest JSON is
    a few hundred bytes, so anything larger is hostile."""
    assert _update_check._MAX_RESPONSE_BYTES == 64 * 1024


def test_fetch_release_info_handles_bad_json_gracefully() -> None:
    spy = _SpyResp(body=b"not valid json \xff")
    with mock.patch("urllib.request.urlopen", return_value=spy):
        result = _update_check._fetch_release_info(
            "https://api.example.com/release.json", timeout=1.0,
        )
    assert result is None
