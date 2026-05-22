"""Unit tests for the credential-stripping redirect handler.

Default urllib follows 30x redirects with every request header
replayed to the redirect target — including ``Authorization`` and
``APCA-*``. A misbehaving or compromised vendor edge could redirect
to attacker-controlled DNS and harvest the bearer token. The
:class:`_StripCredentialsOnRedirect` handler removes credential
headers on cross-host redirects (same-host redirects keep all
headers, since a vendor moving ``/v1/foo`` → ``/v2/foo`` on the
same host shouldn't break auth).

We test the handler directly with hand-built ``urllib.request.Request``
objects rather than spinning up an HTTP server — same coverage,
zero network.
"""
from __future__ import annotations

import urllib.request
from email.message import Message

import pytest

from tradinglab.data import _http


def _make_request(url: str, headers: dict) -> urllib.request.Request:
    req = urllib.request.Request(url)
    for k, v in headers.items():
        req.add_header(k, v)
    return req


def _fake_response_headers() -> Message:
    msg = Message()
    msg["Location"] = "ignored"  # the redirect handler reads newurl directly
    return msg


def _redirect(req: urllib.request.Request, newurl: str) -> urllib.request.Request:
    """Drive the handler's redirect_request method end-to-end."""
    handler = _http._StripCredentialsOnRedirect()
    return handler.redirect_request(
        req=req,
        fp=None,
        code=302,
        msg="Found",
        headers=_fake_response_headers(),
        newurl=newurl,
    )


# ---------------------------------------------------------------------------
# Cross-host redirect — credentials MUST be stripped
# ---------------------------------------------------------------------------


def test_cross_host_redirect_strips_authorization_header() -> None:
    req = _make_request(
        "https://api.polygon.io/v2/aggs/foo",
        {"Authorization": "Bearer SECRET", "Accept": "application/json"},
    )
    new = _redirect(req, "https://attacker.example.com/harvest")
    assert new is not None
    # Authorization header must NOT be replayed to the new host.
    assert "Authorization" not in new.headers
    assert "authorization" not in new.unredirected_hdrs
    # Non-credential headers are preserved.
    assert new.headers.get("Accept") == "application/json"


def test_cross_host_redirect_strips_apca_headers() -> None:
    req = _make_request(
        "https://data.alpaca.markets/v2/bars",
        {
            "APCA-API-KEY-ID": "AK_id",
            "APCA-API-SECRET-KEY": "secret",
            "Accept": "application/json",
        },
    )
    new = _redirect(req, "https://attacker.example.com/")
    assert new is not None
    # All APCA-* headers must be stripped (substring match).
    for h in list(new.headers):
        assert "apca" not in h.lower(), f"{h!r} should have been stripped"
    for h in list(new.unredirected_hdrs):
        assert "apca" not in h.lower(), f"{h!r} should have been stripped"
    assert new.headers.get("Accept") == "application/json"


def test_cross_host_redirect_strips_token_substring_headers() -> None:
    req = _make_request(
        "https://api.example.com/foo",
        {
            "X-Access-Token": "abc",
            "X-Some-Secret": "def",
            "Accept": "application/json",
        },
    )
    new = _redirect(req, "https://attacker.example.com/")
    assert new is not None
    assert "X-Access-Token" not in new.headers
    assert "X-Some-Secret" not in new.headers
    assert new.headers.get("Accept") == "application/json"


# ---------------------------------------------------------------------------
# Same-host redirect — credentials are PRESERVED
# ---------------------------------------------------------------------------


def test_same_host_redirect_keeps_authorization_header() -> None:
    req = _make_request(
        "https://api.polygon.io/v2/aggs/foo",
        {"Authorization": "Bearer SECRET", "Accept": "application/json"},
    )
    new = _redirect(req, "https://api.polygon.io/v3/aggs/foo")
    assert new is not None
    # Authorization replayed on a vendor's own intra-host move
    # (e.g. /v1 → /v2 path bump) — that's intended.
    assert new.headers.get("Authorization") == "Bearer SECRET"


def test_same_host_redirect_case_insensitive_hostname() -> None:
    req = _make_request(
        "https://api.polygon.io/foo",
        {"Authorization": "Bearer SECRET"},
    )
    new = _redirect(req, "https://API.POLYGON.IO/bar")
    assert new is not None
    assert new.headers.get("Authorization") == "Bearer SECRET", (
        "case-only hostname differences must NOT count as cross-host"
    )


def test_cross_host_is_recognised_when_hostname_differs_only_in_subdomain() -> None:
    req = _make_request(
        "https://api.polygon.io/foo",
        {"Authorization": "Bearer SECRET"},
    )
    new = _redirect(req, "https://otherhost.polygon.io/bar")
    assert new is not None
    # Different subdomain ⇒ different host ⇒ strip.
    assert "Authorization" not in new.headers


# ---------------------------------------------------------------------------
# Opener singleton behaviour
# ---------------------------------------------------------------------------


def test_credentialed_opener_is_singleton() -> None:
    a = _http.credentialed_opener()
    b = _http.credentialed_opener()
    assert a is b, "the opener should be built once and reused"


def test_credentialed_opener_installs_strip_handler() -> None:
    opener = _http.credentialed_opener()
    found = [h for h in opener.handlers if isinstance(h, _http._StripCredentialsOnRedirect)]
    assert found, "the opener must install _StripCredentialsOnRedirect"


def test_max_response_bytes_constant_is_positive() -> None:
    assert isinstance(_http.MAX_RESPONSE_BYTES, int)
    assert _http.MAX_RESPONSE_BYTES > 0
    assert _http.MAX_RESPONSE_BYTES <= 64 * 1024 * 1024, (
        "8 MB is the audit-recommended cap; raising it requires a "
        "matching review of the security audit's I4 finding."
    )
