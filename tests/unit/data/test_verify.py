"""Unit tests for the vendor-agnostic credential-verification core.

Audit: ``credential-verification``. Pins the status taxonomy that the
whole feature rests on — the difference between "your key is wrong",
"your key is right but your plan is wrong", and "your network is down"
is the entire point of :mod:`tradinglab.data.verify` (a bool would
collapse all three into one useless "failed").

No network: every HTTP outcome is synthesised from a fake
``urllib.error.HTTPError``.
"""
from __future__ import annotations

import io
import urllib.error

import pytest

from tradinglab.data import verify


def _http_error(code: int, body: bytes = b"", headers=None):
    return urllib.error.HTTPError(
        "https://example.test/probe", code, "err", headers or {},
        io.BytesIO(body),
    )


class TestStatusForHttpCode:
    @pytest.mark.parametrize("code,expected", [
        (200, verify.STATUS_OK),
        (204, verify.STATUS_OK),
        (401, verify.STATUS_INVALID_CREDENTIALS),
        (403, verify.STATUS_FORBIDDEN),
        (429, verify.STATUS_RATE_LIMITED),
        (500, verify.STATUS_NETWORK_ERROR),
        (503, verify.STATUS_NETWORK_ERROR),
        (404, verify.STATUS_ERROR),
        (418, verify.STATUS_ERROR),
        (None, verify.STATUS_ERROR),
    ])
    def test_mapping(self, code, expected):
        assert verify.status_for_http_code(code) == expected

    def test_403_is_not_invalid_credentials(self):
        # The load-bearing distinction: a 403 usually means the key is
        # VALID and the plan is insufficient. Telling the user to re-copy
        # a correct key sends them down the wrong path entirely.
        assert (verify.status_for_http_code(403)
                != verify.status_for_http_code(401))
        assert verify.status_for_http_code(403) == verify.STATUS_FORBIDDEN

    def test_every_mapped_status_is_in_the_declared_taxonomy(self):
        for code in (200, 401, 403, 429, 500, 404, None):
            assert verify.status_for_http_code(code) in verify.ALL_STATUSES


class TestRedact:
    def test_replaces_long_secret(self):
        out = verify.redact("key SUPERSECRETVALUE rejected",
                            ["SUPERSECRETVALUE"])
        assert "SUPERSECRETVALUE" not in out
        assert "***" in out

    def test_ignores_short_values(self):
        # A 3-char "secret" would mangle unrelated prose and can't be a
        # real API key.
        out = verify.redact("the cat sat", ["cat"])
        assert out == "the cat sat"

    def test_accepts_a_bare_string(self):
        assert "***" in verify.redact("x LONGSECRET1 y", "LONGSECRET1")

    def test_skips_none_entries(self):
        assert verify.redact("abc", [None, "LONGSECRET1"]) == "abc"


class TestResultFromException:
    def test_http_401_reports_invalid_credentials(self):
        r = verify.result_from_exception(_http_error(401), vendor="alpaca")
        assert r.status == verify.STATUS_INVALID_CREDENTIALS
        assert r.http_status == 401
        assert r.is_credential_problem
        assert not r.ok
        assert "401" in r.summary
        assert r.detail  # remediation is mandatory for a user-fixable state

    def test_http_429_is_not_a_credential_problem(self):
        r = verify.result_from_exception(_http_error(429), vendor="alpaca")
        assert r.status == verify.STATUS_RATE_LIMITED
        assert not r.is_credential_problem

    def test_url_error_is_network_not_credentials(self):
        r = verify.result_from_exception(
            urllib.error.URLError("dns boom"), vendor="polygon")
        assert r.status == verify.STATUS_NETWORK_ERROR
        assert not r.is_credential_problem

    def test_timeout_is_network_error(self):
        r = verify.result_from_exception(TimeoutError("slow"), vendor="x")
        assert r.status == verify.STATUS_NETWORK_ERROR

    def test_unknown_exception_is_error(self):
        r = verify.result_from_exception(ValueError("odd"), vendor="x")
        assert r.status == verify.STATUS_ERROR

    def test_vendor_message_is_surfaced(self):
        r = verify.result_from_exception(
            _http_error(403, b'{"message": "subscription does not permit"}'),
            vendor="alpaca")
        assert "subscription does not permit" in r.summary

    def test_secret_never_leaks_into_summary(self):
        secret = "AKSUPERSECRETKEYVALUE"
        r = verify.result_from_exception(
            _http_error(401, b'{"message": "bad key AKSUPERSECRETKEYVALUE"}'),
            vendor="alpaca", secrets=(secret,))
        assert secret not in r.summary
        assert secret not in r.detail
        assert secret not in r.as_log_line()

    def test_unreadable_body_does_not_raise(self):
        class Boom(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("u", 401, "e", {}, None)

            def read(self, *_a):
                raise OSError("stream gone")

        r = verify.result_from_exception(Boom(), vendor="alpaca")
        assert r.status == verify.STATUS_INVALID_CREDENTIALS


class TestNotConfigured:
    def test_shape(self):
        r = verify.not_configured("alpaca", detail="need both fields")
        assert r.status == verify.STATUS_NOT_CONFIGURED
        assert r.is_credential_problem
        assert not r.ok
        assert r.detail == "need both fields"


class TestRegistry:
    @pytest.fixture(autouse=True)
    def _restore(self):
        before = dict(verify._VERIFIERS)
        yield
        verify._VERIFIERS.clear()
        verify._VERIFIERS.update(before)

    def test_unknown_vendor_is_unsupported_not_an_exception(self):
        r = verify.verify_vendor("nope")
        assert r.status == verify.STATUS_UNSUPPORTED
        assert r.vendor == "nope"

    def test_register_and_dispatch(self):
        verify.register_verifier(
            "fake", lambda creds=None, **kw: verify.VerifyResult(
                status=verify.STATUS_OK, vendor="fake", summary="yes"))
        assert verify.has_verifier("fake")
        assert "fake" in verify.verifiable_vendors()
        assert verify.verify_vendor("fake").ok

    def test_creds_are_passed_through(self):
        seen = {}

        def _v(creds=None, **kw):
            seen["creds"] = creds
            seen["timeout"] = kw.get("timeout")
            return verify.VerifyResult(
                status=verify.STATUS_OK, vendor="fake", summary="ok")

        verify.register_verifier("fake", _v)
        verify.verify_vendor("fake", {"k": 1}, timeout=3.5)
        assert seen["creds"] == {"k": 1}
        assert seen["timeout"] == 3.5

    def test_raising_verifier_is_caught(self):
        def _boom(creds=None, **kw):
            raise RuntimeError("probe exploded")

        verify.register_verifier("fake", _boom)
        r = verify.verify_vendor("fake")
        # Must never escape into a Tk callback / worker thread.
        assert r.status == verify.STATUS_ERROR
        assert "probe exploded" in r.summary

    def test_unregister(self):
        verify.register_verifier(
            "fake", lambda creds=None, **kw: verify.not_configured("fake"))
        verify.unregister_verifier("fake")
        assert not verify.has_verifier("fake")

    def test_real_vendors_are_registered(self):
        # alpaca_source / polygon_source register at import time.
        import tradinglab.data  # noqa: F401
        assert verify.has_verifier("alpaca")
        assert verify.has_verifier("polygon")


class TestResultCache:
    @pytest.fixture(autouse=True)
    def _clean(self):
        verify.clear_results()
        yield
        verify.clear_results()

    def test_record_and_read_back(self):
        r = verify.VerifyResult(
            status=verify.STATUS_OK, vendor="alpaca", summary="ready")
        assert verify.record_result(r) is r
        assert verify.last_result("alpaca") is r

    def test_missing_vendor_returns_none(self):
        assert verify.last_result("alpaca") is None

    def test_clear(self):
        verify.record_result(verify.VerifyResult(
            status=verify.STATUS_OK, vendor="alpaca", summary="ready"))
        verify.clear_results()
        assert verify.last_result("alpaca") is None

    def test_cached_result_holds_no_secret_fields(self):
        # VerifyResult is secret-free by construction — it must never gain
        # a field that could carry a key into an in-memory cache or log.
        fields = set(verify.VerifyResult.__dataclass_fields__)
        assert fields == {
            "status", "vendor", "summary", "detail", "entitlements",
            "latency_ms", "http_status",
        }
