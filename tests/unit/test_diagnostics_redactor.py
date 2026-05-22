"""Unit tests for :func:`tradinglab.diagnostics.redact_log_line`.

The redactor catches three secret shapes that show up in log messages
emitted by the vendor fetchers:

* ``Authorization: Bearer …`` (or bare ``Bearer xxxxx``)
* ``Authorization: Basic …`` (or bare ``Basic xxxxx``)
* Query-string secrets like ``?apiKey=…``, ``&token=…``,
  ``&access_token=…``

It's called at two sites:

* :class:`tradinglab.status.StatusLog._emit` — before the message hits
  disk / stdout / the status-bar StringVar.
* :func:`tradinglab.diagnostics.build_diagnostic_bundle` — when
  bundling log files and crash dumps written by older builds.

These tests verify the redactor itself is correct and idempotent.
"""
from __future__ import annotations

import pytest

from tradinglab.diagnostics import redact_log_line


# ---------------------------------------------------------------------------
# Bearer
# ---------------------------------------------------------------------------


def test_redacts_authorization_bearer_header_in_log_line() -> None:
    line = "GET /v2/aggs Authorization: Bearer abcDEF1234567890_-"
    out = redact_log_line(line)
    assert "abcDEF1234567890_-" not in out
    assert "Bearer <redacted>" in out


def test_redacts_bare_bearer_with_arbitrary_token() -> None:
    line = "raised: HTTPError Bearer XYZ-abc.def_ghi=="
    out = redact_log_line(line)
    assert "XYZ-abc.def_ghi==" not in out
    assert "Bearer <redacted>" in out


def test_bearer_redaction_is_case_insensitive() -> None:
    line = "auth header: BEARER SECRETXYZ"
    out = redact_log_line(line)
    assert "SECRETXYZ" not in out


# ---------------------------------------------------------------------------
# Basic
# ---------------------------------------------------------------------------


def test_redacts_authorization_basic_header() -> None:
    line = "Authorization: Basic dXNlcjpwYXNzd29yZA=="
    out = redact_log_line(line)
    assert "dXNlcjpwYXNzd29yZA==" not in out
    assert "Basic <redacted>" in out


# ---------------------------------------------------------------------------
# Query-string secrets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [
    "apiKey", "api_key", "access_token", "refresh_token",
    "token", "client_secret", "password", "secret",
])
def test_redacts_query_string_secret(name: str) -> None:
    line = f"URLError on https://api.example.com/v1/foo?{name}=SECRET_VAL_123"
    out = redact_log_line(line)
    assert "SECRET_VAL_123" not in out
    assert "<redacted>" in out


def test_redacts_query_string_secret_in_middle_of_querystring() -> None:
    line = "https://api.example.com/v1/foo?bar=baz&apiKey=SECRET&qux=42"
    out = redact_log_line(line)
    assert "SECRET" not in out
    # Surrounding params are preserved.
    assert "bar=baz" in out
    assert "qux=42" in out


def test_preserves_innocent_query_params() -> None:
    line = "https://api.example.com/v1/bars?ticker=AAPL&interval=5m"
    out = redact_log_line(line)
    # No secrets, so the line should round-trip unchanged.
    assert out == line


# ---------------------------------------------------------------------------
# Edge cases and invariants
# ---------------------------------------------------------------------------


def test_empty_string_returns_empty_string() -> None:
    assert redact_log_line("") == ""


def test_none_input_returns_input_unchanged() -> None:
    # The redactor degrades gracefully for non-string inputs; the
    # caller (StatusLog._emit) coerces non-strings to str() first,
    # but defensive code still helps.
    assert redact_log_line(None) is None  # type: ignore[arg-type]


def test_redactor_is_idempotent() -> None:
    """Running twice produces the same output as running once."""
    line = "Bearer SECRET and ?apiKey=ALSO_SECRET"
    once = redact_log_line(line)
    twice = redact_log_line(once)
    assert once == twice


def test_no_secret_input_is_returned_unchanged() -> None:
    line = "AMD/5m: 503 bars cached"
    assert redact_log_line(line) == line
