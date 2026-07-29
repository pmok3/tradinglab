"""Tests for persisted verdicts, runtime-failure routing, and the Schwab verifier.

These cover the Stage 2/3 half of the credential redesign: the app should know
its own credential health at launch (without a probe), should notice when a
live fetch fails *because of* the credentials, and should give every vendor an
honest answer to "test this".
"""
from __future__ import annotations

import urllib.error

import pytest

from tradinglab.data import credential_store as cs
from tradinglab.data import verify


@pytest.fixture(autouse=True)
def _clean_verdicts():
    verify.clear_results()
    yield
    verify.clear_results()


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.test/x", code, "boom", {}, None)


# ---------------------------------------------------------------------------
# Persisted verdicts
# ---------------------------------------------------------------------------


def test_record_result_persists_status_and_summary(tmp_path, monkeypatch):
    written: list[tuple] = []
    monkeypatch.setattr(
        cs, "record_verification",
        lambda vendor, status, **kw: written.append((vendor, status, kw)))

    verify.record_result(verify.VerifyResult(
        status=verify.STATUS_OK, vendor="alpaca", summary="fine"))

    assert written and written[0][0] == "alpaca"
    assert written[0][1] == verify.STATUS_OK
    assert written[0][2]["summary"] == "fine"


def test_record_result_can_skip_persistence(monkeypatch):
    written: list = []
    monkeypatch.setattr(cs, "record_verification",
                        lambda *a, **k: written.append(a))
    verify.record_result(
        verify.VerifyResult(status=verify.STATUS_OK, vendor="alpaca",
                            summary=""),
        persist=False)
    assert written == []


def test_record_result_survives_a_store_failure(monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("read-only")

    monkeypatch.setattr(cs, "record_verification", _boom)
    result = verify.record_result(verify.VerifyResult(
        status=verify.STATUS_OK, vendor="alpaca", summary="fine"))
    # The live verdict still reaches the caller and the in-process cache.
    assert result.status == verify.STATUS_OK
    assert verify.last_result("alpaca") is not None


def test_persisted_result_reads_the_store(monkeypatch):
    monkeypatch.setattr(cs, "get_vendor", lambda v, **k: cs.VendorRecord(
        vendor=v, last_verified=cs.VerificationRecord(
            status="forbidden", checked_at=42.0, summary="plan")))

    assert verify.persisted_result("alpaca") == ("forbidden", 42.0, "plan")


def test_persisted_result_is_none_when_never_recorded(monkeypatch):
    monkeypatch.setattr(cs, "get_vendor",
                        lambda v, **k: cs.VendorRecord(vendor=v))
    assert verify.persisted_result("alpaca") is None


def test_persisted_result_tolerates_a_broken_store(monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr(cs, "get_vendor", _boom)
    assert verify.persisted_result("alpaca") is None


def test_known_status_prefers_this_session(monkeypatch):
    """The live verdict was measured against the credentials actually loaded."""
    monkeypatch.setattr(cs, "record_verification", lambda *a, **k: None)
    monkeypatch.setattr(cs, "get_vendor", lambda v, **k: cs.VendorRecord(
        vendor=v, last_verified=cs.VerificationRecord(
            status="invalid_credentials", checked_at=1.0)))
    verify.record_result(verify.VerifyResult(
        status=verify.STATUS_OK, vendor="alpaca", summary="fresh"))

    status, _at, summary = verify.known_status("alpaca")
    assert status == verify.STATUS_OK and summary == "fresh"


def test_known_status_falls_back_to_the_persisted_verdict(monkeypatch):
    monkeypatch.setattr(cs, "get_vendor", lambda v, **k: cs.VendorRecord(
        vendor=v, last_verified=cs.VerificationRecord(
            status="ok", checked_at=7.0, summary="from disk")))
    assert verify.known_status("alpaca") == ("ok", 7.0, "from disk")


def test_known_status_none_when_nothing_is_known(monkeypatch):
    monkeypatch.setattr(cs, "get_vendor",
                        lambda v, **k: cs.VendorRecord(vendor=v))
    assert verify.known_status("polygon") is None


# ---------------------------------------------------------------------------
# Runtime failure routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code,expected", [
    (401, verify.STATUS_INVALID_CREDENTIALS),
    (403, verify.STATUS_FORBIDDEN),
])
def test_runtime_credential_failures_are_recorded(code, expected, monkeypatch):
    monkeypatch.setattr(cs, "record_verification", lambda *a, **k: None)

    result = verify.note_runtime_failure("alpaca", _http_error(code))
    assert result is not None and result.status == expected
    assert verify.last_result("alpaca").status == expected


@pytest.mark.parametrize("code", [429, 500, 503])
def test_transient_failures_do_not_poison_vendor_status(code, monkeypatch):
    """A rate limit or an outage says nothing about the key."""
    monkeypatch.setattr(cs, "record_verification", lambda *a, **k: None)

    assert verify.note_runtime_failure("alpaca", _http_error(code)) is None
    assert verify.last_result("alpaca") is None


def test_network_errors_do_not_poison_vendor_status(monkeypatch):
    monkeypatch.setattr(cs, "record_verification", lambda *a, **k: None)
    assert verify.note_runtime_failure(
        "alpaca", urllib.error.URLError("unreachable")) is None
    assert verify.last_result("alpaca") is None


def test_runtime_failure_redacts_secrets(monkeypatch):
    monkeypatch.setattr(cs, "record_verification", lambda *a, **k: None)
    result = verify.note_runtime_failure(
        "alpaca", _http_error(401), secrets=("SUPER_SECRET_KEY",))
    assert result is not None
    assert "SUPER_SECRET_KEY" not in result.summary
    assert "SUPER_SECRET_KEY" not in result.detail


def test_runtime_failure_never_raises(monkeypatch):
    class Weird(Exception):
        pass

    monkeypatch.setattr(cs, "record_verification", lambda *a, **k: None)
    assert verify.note_runtime_failure("alpaca", Weird("odd")) is None


# ---------------------------------------------------------------------------
# Schwab verifier
# ---------------------------------------------------------------------------


def test_every_vendor_now_has_a_verifier():
    assert set(verify.verifiable_vendors()) == {"alpaca", "polygon", "schwab"}


def test_schwab_unconfigured_reports_not_configured():
    from tradinglab.data.credentials import SchwabCredentials
    from tradinglab.data.schwab_source import verify_schwab

    result = verify_schwab(SchwabCredentials())
    assert result.status == verify.STATUS_NOT_CONFIGURED


def test_schwab_configured_reports_unsupported_without_a_probe(monkeypatch):
    """OAuth has not shipped; a fabricated 'ok' would be a lie."""
    from tradinglab.data import schwab_source
    from tradinglab.data.credentials import SchwabCredentials

    def _boom(*_a, **_k):
        raise AssertionError("verify_schwab must not make a network call")

    monkeypatch.setattr(schwab_source, "_http_get_pricehistory", _boom)
    result = schwab_source.verify_schwab(
        SchwabCredentials(app_key="k", app_secret="s"))

    assert result.status == verify.STATUS_UNSUPPORTED
    assert not result.is_credential_problem
