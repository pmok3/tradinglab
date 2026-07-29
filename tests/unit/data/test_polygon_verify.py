"""Unit tests for :func:`tradinglab.data.polygon_source.verify_polygon`.

Audit: ``credential-verification``. Offline — the opener is injected.

Polygon exists here mainly to prove the verification abstraction in
``data/verify.py`` is genuinely provider-agnostic rather than an
Alpaca-shaped hook, and to pin Polygon's own quirk: it signals some plan
failures with **HTTP 200 + a ``NOT_AUTHORIZED`` body** rather than a 4xx.
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from tradinglab.data import verify
from tradinglab.data.credentials import PolygonCredentials
from tradinglab.data.polygon_source import verify_polygon

_KEY = "POLYTESTAPIKEY0000001"


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers: dict[str, str] = {}

    def read(self, _n=None):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _Opener:
    def __init__(self, outcome):
        self._outcome = outcome
        self.headers: dict[str, str] = {}
        self.url: str = ""
        self.calls = 0

    def open(self, req, timeout=None):  # noqa: ARG002
        self.calls += 1
        self.headers = dict(req.headers)
        self.url = req.full_url
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return _Resp(self._outcome)


def _http_error(code: int, body: bytes = b"{}"):
    return urllib.error.HTTPError(
        "https://api.polygon.io/v2/aggs/ticker/AAPL/prev", code, "err", {},
        io.BytesIO(body),
    )


def _creds(key: str | None = _KEY) -> PolygonCredentials:
    return PolygonCredentials(api_key=key)


def _ok_payload():
    return {"status": "OK", "resultsCount": 1, "results": [{"c": 1.0}]}


class TestSuccess:
    def test_reports_ok(self):
        r = verify_polygon(_creds(), opener=_Opener(_ok_payload()))
        assert r.ok
        assert r.vendor == "polygon"
        assert r.entitlements["results"] == 1
        assert r.latency_ms is not None


class TestAuthTransport:
    def test_uses_bearer_header_never_the_query_param(self):
        # The apiKey query form lands in URLError reprs, which flow into the
        # status log and any diagnostic bundle — leaking the token.
        opener = _Opener(_ok_payload())
        verify_polygon(_creds(), opener=opener)
        auth = next(v for k, v in opener.headers.items()
                    if k.lower() == "authorization")
        assert auth == f"Bearer {_KEY}"
        assert _KEY not in opener.url
        assert "apiKey" not in opener.url


class TestFailures:
    def test_not_configured_skips_the_network(self):
        opener = _Opener(_ok_payload())
        r = verify_polygon(_creds(None), opener=opener)
        assert r.status == verify.STATUS_NOT_CONFIGURED
        assert opener.calls == 0

    def test_401_is_invalid_credentials(self):
        r = verify_polygon(_creds(), opener=_Opener(_http_error(401)))
        assert r.status == verify.STATUS_INVALID_CREDENTIALS

    def test_403_is_forbidden_not_invalid_credentials(self):
        # Polygon 403 = valid key, insufficient plan. Telling the user to
        # re-copy a correct key would be the wrong remediation.
        r = verify_polygon(_creds(), opener=_Opener(_http_error(403)))
        assert r.status == verify.STATUS_FORBIDDEN
        assert not r.ok

    def test_network_error_does_not_blame_the_key(self):
        r = verify_polygon(
            _creds(), opener=_Opener(urllib.error.URLError("dns down")))
        assert r.status == verify.STATUS_NETWORK_ERROR
        assert not r.is_credential_problem

    @pytest.mark.parametrize("api_status", ["NOT_AUTHORIZED", "ERROR"])
    def test_http_200_with_error_body_is_forbidden_not_ok(self, api_status):
        # Polygon's quirk: some plan failures come back 200. Trusting the
        # HTTP status alone would green-light an unusable key.
        r = verify_polygon(_creds(), opener=_Opener(
            {"status": api_status, "message": "upgrade your plan"}))
        assert r.status == verify.STATUS_FORBIDDEN
        assert "upgrade your plan" in r.summary

    def test_non_dict_payload_is_error(self):
        r = verify_polygon(_creds(), opener=_Opener(["unexpected"]))
        assert r.status == verify.STATUS_ERROR

    def test_secret_never_leaks_into_the_result(self):
        body = json.dumps({"message": f"bad key {_KEY}"}).encode()
        r = verify_polygon(_creds(), opener=_Opener(_http_error(401, body)))
        assert _KEY not in r.summary
        assert _KEY not in r.as_log_line()


class TestRegisteredThroughTheGenericFacade:
    def test_verify_vendor_routes_to_polygon(self):
        assert verify.has_verifier("polygon")
        r = verify.verify_vendor(
            "polygon", _creds(), opener=_Opener(_ok_payload()))
        assert r.ok
        assert r.vendor == "polygon"
