"""Security hardening tests for :mod:`tradinglab.updates` HTTP fetches."""
from __future__ import annotations

import io
import urllib.request
from email.message import Message
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
    with mock.patch.object(updates._HTTPS_OPENER, "open", return_value=spy):
        updates._fetch_release_info("https://api.example.com/release.json", timeout=1.0)
    assert spy.read_calls, "the fetcher must have called resp.read(...)"
    assert spy.read_calls[0] == updates._MAX_RESPONSE_BYTES


@pytest.mark.parametrize(
    "scheme_url",
    [
        "http://example.com/latest",
        "file:///etc/passwd",
        "ftp://example.com/file",
        "ldap://example.com/x",
        "data:text/plain,hello",
        "javascript:alert(1)",
    ],
)
def test_fetch_release_info_rejects_non_http_schemes(scheme_url: str) -> None:
    """Non-HTTP schemes must fail before any network IO."""
    with mock.patch.object(updates._HTTPS_OPENER, "open") as m_open:
        with pytest.raises(ValueError):
            updates._fetch_release_info(scheme_url, timeout=1.0)
    assert not m_open.called


@pytest.mark.parametrize(
    "scheme_url",
    [
        "https://api.example.com/release.json",
    ],
)
def test_fetch_release_info_accepts_https(scheme_url: str) -> None:
    spy = _SpyResp()
    with mock.patch.object(updates._HTTPS_OPENER, "open", return_value=spy):
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
    with mock.patch.object(updates._HTTPS_OPENER, "open", return_value=spy):
        result = updates.check_now(force=True)
    assert result.status == "error"
    assert "UnicodeDecodeError" in result.error or "JSONDecodeError" in result.error


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("destination", ["http://example.invalid/plain", "https://example.invalid/safe"])
def test_redirect_policy_before_following(monkeypatch, code, destination):
    """Exercise urllib's real redirect chain with transport replaced, not redirect handling."""
    seen = []

    def transport(_handler, req):
        seen.append(req.full_url)
        headers = Message()
        status = 200
        if len(seen) == 1:
            headers["Location"] = destination
            status = code
        response = urllib.response.addinfourl(
            io.BytesIO(b'{"version":"99.0.0"}'), headers, req.full_url, status,
        )
        response.msg = "test response"
        return response

    monkeypatch.setattr(urllib.request.HTTPSHandler, "https_open", transport)
    monkeypatch.setattr(
        urllib.request.HTTPHandler, "http_open",
        lambda *_a: pytest.fail("plaintext request reached the transport"),
    )
    monkeypatch.setattr(updates, "_HTTPS_OPENER", urllib.request.build_opener(
        urllib.request.ProxyHandler({}), updates._HTTPSOnlyRedirectHandler(),
    ))
    if destination.startswith("http:"):
        with pytest.raises(ValueError, match="redirect URL must use https"):
            updates._fetch_release_info("https://example.invalid/latest", 1.0)
        assert seen == ["https://example.invalid/latest"]
    else:
        assert updates._fetch_release_info("https://example.invalid/latest", 1.0) == {"version": "99.0.0"}
        assert seen == ["https://example.invalid/latest", destination]


@pytest.mark.parametrize("url,allowed", [
    ("https://example.invalid/release", True),
    ("http://example.invalid/release", False),
    ("file:///tmp/release", False),
])
def test_banner_never_offers_unsafe_browser_target(_tk_root, monkeypatch, caplog, url, allowed):
    import tkinter as tk
    from tkinter import ttk

    from tradinglab.gui import update_check

    class Host(update_check.UpdateCheckMixin, tk.Toplevel):
        pass

    host = Host(_tk_root)
    host.withdraw()
    opened = []
    monkeypatch.setattr(update_check.webbrowser, "open", lambda target: opened.append(target))
    try:
        host._show_update_banner("99.0.0", url=url)
        buttons = [
            w for w in host._update_banner_frame.winfo_children()
            if isinstance(w, ttk.Button) and w.cget("text") == "View release"
        ]
        assert bool(buttons) is allowed
        for button in buttons:
            button.invoke()
        assert opened == ([url] if allowed else [])
        if not allowed:
            assert "Refusing non-HTTPS" in caplog.text
    finally:
        host.destroy()
