"""Unit tests for :func:`tradinglab.data.alpaca_source.verify_alpaca`.

Audit: ``credential-verification``. Offline — every HTTP outcome comes
from a fake opener injected via the ``opener=`` seam.

The load-bearing case is :class:`TestSipDowngradeDisambiguation`: a 403
while requesting ``feed=sip`` is ambiguous (bad key vs Free account), and
the probe resolves it with ONE extra ``feed=iex`` request. That converts
this repo's most common misconfiguration — documented in
``alpaca_source._observe_rate_limit_header`` — from a reactive mid-session
popup into a save-time fix.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest

from tradinglab.data import verify
from tradinglab.data.alpaca_source import (
    _reset_tier_detection,
    verify_alpaca,
)
from tradinglab.data.credentials import AlpacaCredentials

_KEY = "AKTESTKEYID000000001"
_SECRET = "TESTSECRETVALUE00000000002"


def _creds(**kw) -> AlpacaCredentials:
    base = dict(api_key_id=_KEY, api_secret_key=_SECRET,
                feed="iex", adjustment="split", tier="free")
    base.update(kw)
    return AlpacaCredentials(**base)


def _bars_payload(n: int = 1) -> dict:
    return {
        "bars": [{"t": "2024-03-07T14:30:00Z", "o": 1.0, "h": 2.0,
                  "l": 0.5, "c": 1.5, "v": 100}] * n,
        "symbol": "AAPL",
        "next_page_token": None,
    }


class _FakeResponse:
    def __init__(self, payload, headers=None):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = dict(headers or {})

    def read(self, _n=None):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _http_error(code: int, headers=None, body: bytes = b"{}"):
    return urllib.error.HTTPError(
        "https://data.alpaca.markets/v2/stocks/AAPL/bars", code, "err",
        dict(headers or {}), io.BytesIO(body),
    )


class _FakeOpener:
    """Yields queued outcomes in order; records the feed of each request."""

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.feeds: list[str] = []
        self.calls: int = 0

    def open(self, req, timeout=None):  # noqa: ARG002
        self.calls += 1
        qs = urllib.parse.urlparse(req.full_url).query
        self.feeds.append(urllib.parse.parse_qs(qs).get("feed", [""])[0])
        outcome = (self._outcomes.pop(0) if self._outcomes
                   else _FakeResponse(_bars_payload()))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def _reset():
    _reset_tier_detection()
    yield
    _reset_tier_detection()


class TestNotConfigured:
    def test_empty_fields_short_circuit_without_network(self):
        opener = _FakeOpener()
        r = verify_alpaca(_creds(api_key_id=None, api_secret_key=None),
                          opener=opener)
        assert r.status == verify.STATUS_NOT_CONFIGURED
        assert opener.calls == 0

    def test_key_without_secret_is_not_configured(self):
        opener = _FakeOpener()
        r = verify_alpaca(_creds(api_secret_key=None), opener=opener)
        assert r.status == verify.STATUS_NOT_CONFIGURED
        assert opener.calls == 0


class TestSuccess:
    def test_free_plan_reports_feed_and_budget(self):
        opener = _FakeOpener(_FakeResponse(
            _bars_payload(), {"X-RateLimit-Limit": "200"}))
        r = verify_alpaca(_creds(), opener=opener)
        assert r.ok
        assert r.http_status == 200
        assert r.entitlements["feed"] == "iex"
        assert r.entitlements["plan"] == "free"
        assert r.entitlements["requests_per_min"] == 200
        assert "IEX" in r.summary
        assert r.latency_ms is not None
        assert opener.feeds == ["iex"]

    def test_paid_plan_uses_sip(self):
        opener = _FakeOpener(_FakeResponse(
            _bars_payload(), {"X-RateLimit-Limit": "10000"}))
        r = verify_alpaca(_creds(feed="sip", tier="paid"), opener=opener)
        assert r.ok
        assert r.entitlements["plan"] == "paid"
        assert opener.feeds == ["sip"]
        assert "SIP" in r.summary

    def test_paid_selected_but_account_is_free_warns_in_detail(self):
        # 200 OK, but the header proves a Free account while the user
        # selected Paid. Registration would succeed and every later SIP
        # request would 403 — say so now, at save time.
        opener = _FakeOpener(_FakeResponse(
            _bars_payload(), {"X-RateLimit-Limit": "200"}))
        r = verify_alpaca(_creds(tier="paid"), opener=opener)
        assert r.ok
        assert "Paid" in r.detail and "Free" in r.detail

    def test_missing_rate_header_still_succeeds(self):
        opener = _FakeOpener(_FakeResponse(_bars_payload(), {}))
        r = verify_alpaca(_creds(), opener=opener)
        assert r.ok
        assert "requests_per_min" not in r.entitlements

    def test_non_bars_payload_is_error_not_ok(self):
        opener = _FakeOpener(_FakeResponse({"unexpected": True}, {}))
        r = verify_alpaca(_creds(), opener=opener)
        assert r.status == verify.STATUS_ERROR
        assert not r.ok


class TestFailures:
    def test_401_is_invalid_credentials(self):
        opener = _FakeOpener(_http_error(401))
        r = verify_alpaca(_creds(), opener=opener)
        assert r.status == verify.STATUS_INVALID_CREDENTIALS
        assert r.is_credential_problem

    def test_network_error_does_not_blame_the_key(self):
        opener = _FakeOpener(urllib.error.URLError("dns down"))
        r = verify_alpaca(_creds(), opener=opener)
        assert r.status == verify.STATUS_NETWORK_ERROR
        assert not r.is_credential_problem

    def test_403_on_iex_stays_forbidden_without_a_second_probe(self):
        # Already on the lowest feed — there is nothing to downgrade to.
        opener = _FakeOpener(_http_error(403))
        r = verify_alpaca(_creds(feed="iex"), opener=opener)
        assert r.status == verify.STATUS_FORBIDDEN
        assert opener.calls == 1

    def test_probe_does_not_burn_the_retry_ladder(self):
        # A "Test connection" click must answer immediately; the fetch
        # path's 3x exponential backoff would leave the button stuck on
        # "Testing…" for seconds. Exactly one request for a 429.
        opener = _FakeOpener(_http_error(429), _http_error(429),
                             _http_error(429), _http_error(429))
        r = verify_alpaca(_creds(), opener=opener)
        assert r.status == verify.STATUS_RATE_LIMITED
        assert opener.calls == 1

    def test_secret_is_not_echoed_into_the_result(self):
        body = json.dumps({"message": f"bad key {_SECRET}"}).encode()
        opener = _FakeOpener(_http_error(401, body=body))
        r = verify_alpaca(_creds(), opener=opener)
        assert _SECRET not in r.summary
        assert _KEY not in r.summary
        assert _SECRET not in r.as_log_line()


class TestSipDowngradeDisambiguation:
    def test_sip_403_then_iex_200_proves_the_key_is_valid(self):
        opener = _FakeOpener(
            _http_error(403, {"X-RateLimit-Limit": "200"}),
            _FakeResponse(_bars_payload(), {"X-RateLimit-Limit": "200"}),
        )
        r = verify_alpaca(_creds(feed="sip", tier="paid"), opener=opener)

        assert r.status == verify.STATUS_FORBIDDEN
        assert opener.feeds == ["sip", "iex"]
        # The message must exonerate the key and point at the plan setting.
        assert "valid" in r.summary.lower()
        assert "sip" in r.summary.lower()
        assert "Free" in r.detail
        assert r.entitlements["plan"] == "free"
        assert r.entitlements["requested_feed"] == "sip"
        assert r.entitlements["feed"] == "iex"

    def test_sip_403_then_iex_403_reports_the_original(self):
        opener = _FakeOpener(_http_error(403), _http_error(403))
        r = verify_alpaca(_creds(feed="sip", tier="paid"), opener=opener)
        assert r.status == verify.STATUS_FORBIDDEN
        assert opener.calls == 2
        # We could not prove the key works, so we must NOT claim it does.
        assert "valid" not in r.summary.lower()

    def test_sip_403_then_iex_401_reports_the_original_403(self):
        opener = _FakeOpener(_http_error(403), _http_error(401))
        r = verify_alpaca(_creds(feed="sip", tier="paid"), opener=opener)
        assert r.http_status == 403

    def test_downgrade_latch_forces_iex_on_a_later_probe(self):
        # The 403's X-RateLimit-Limit header latches free-tier detection
        # process-wide; the next probe must request IEX even though the
        # credential object still says sip/paid.
        opener = _FakeOpener(
            _http_error(403, {"X-RateLimit-Limit": "200"}),
            _FakeResponse(_bars_payload(), {"X-RateLimit-Limit": "200"}),
        )
        verify_alpaca(_creds(feed="sip", tier="paid"), opener=opener)

        second = _FakeOpener(_FakeResponse(
            _bars_payload(), {"X-RateLimit-Limit": "200"}))
        r2 = verify_alpaca(_creds(feed="sip", tier="paid"), opener=second)
        assert second.feeds == ["iex"]
        assert r2.ok


class TestRegisteredThroughTheGenericFacade:
    def test_verify_vendor_routes_to_alpaca(self):
        assert verify.has_verifier("alpaca")
        opener = _FakeOpener(_FakeResponse(
            _bars_payload(), {"X-RateLimit-Limit": "200"}))
        r = verify.verify_vendor("alpaca", _creds(), opener=opener)
        assert r.ok
        assert r.vendor == "alpaca"
