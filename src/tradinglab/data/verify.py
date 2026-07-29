"""Credential *verification* — "are these keys actually usable?"

:meth:`~tradinglab.data.credentials.AlpacaCredentials.is_configured` only
answers "are the fields non-empty?". That presence check is the right gate
for **registration** (startup must never depend on the network), but it is
not an answer to the question a new user actually asks after pasting an API
key: *is this thing going to work?*

A typo'd secret today registers the source, then silently returns ``None``
from every fetch — the user sees an empty chart with no explanation. This
module adds the missing signal.

Design
------

Verification is a **per-vendor capability registered into a side table**,
exactly mirroring the ``_PAGE_FETCHERS`` / ``_RANGE_CAPABLE`` idiom in
:mod:`tradinglab.data.base`. A vendor opts in by calling
:func:`register_verifier`; everything else (the credentials dialog, the
status bar, future CLI diagnostics) goes through the vendor-agnostic
:func:`verify_vendor`. Adding Polygon / Schwab / a future provider is one
registration line, not a new UI branch.

Why a status taxonomy instead of a bool
---------------------------------------

"Your key is wrong", "your key is right but your plan doesn't include this
feed", and "your network is down" demand three completely different user
actions. A boolean collapses them into one useless "failed". The
:data:`STATUS_*` values exist so the UI can render the *remediation*, not
just the verdict:

============================ ===============================================
``ok``                       Credentials work for the capability we need.
``not_configured``           Required fields are empty — nothing to test.
``invalid_credentials``      Vendor rejected the key/secret (HTTP 401).
``forbidden``                Authenticated, but not entitled (HTTP 403) —
                             typically a plan/feed mismatch. The key is FINE.
``rate_limited``             HTTP 429. Credentials are fine; try again.
``network_error``            No usable response (DNS/TLS/timeout/5xx). Says
                             nothing about the credentials.
``unsupported``              No verifier registered for this vendor.
``error``                    Reached the vendor but couldn't interpret it.
============================ ===============================================

Security
--------

Verifiers MUST NOT put secrets into :attr:`VerifyResult.summary` /
:attr:`~VerifyResult.detail` — those strings are rendered in the GUI and
may be copied into a diagnostic bundle. :func:`redact` is provided for
scrubbing a vendor error body before it is surfaced.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

LOG = logging.getLogger(__name__)


# --- Status taxonomy -------------------------------------------------------

STATUS_OK = "ok"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_INVALID_CREDENTIALS = "invalid_credentials"
STATUS_FORBIDDEN = "forbidden"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_NETWORK_ERROR = "network_error"
STATUS_UNSUPPORTED = "unsupported"
STATUS_ERROR = "error"

#: Every status this module can produce. Pinned by tests so a new status
#: can't be introduced without a deliberate taxonomy review.
ALL_STATUSES: frozenset[str] = frozenset({
    STATUS_OK, STATUS_NOT_CONFIGURED, STATUS_INVALID_CREDENTIALS,
    STATUS_FORBIDDEN, STATUS_RATE_LIMITED, STATUS_NETWORK_ERROR,
    STATUS_UNSUPPORTED, STATUS_ERROR,
})

#: Statuses that mean "the credentials themselves are the problem" — the
#: user must go edit a field. Everything else is environmental or transient.
CREDENTIAL_PROBLEM_STATUSES: frozenset[str] = frozenset({
    STATUS_INVALID_CREDENTIALS, STATUS_FORBIDDEN, STATUS_NOT_CONFIGURED,
})

#: Maximum bytes of a vendor error body we will read before giving up.
#: Bounded because the body lands in a GUI label and a log line.
MAX_ERROR_BODY_BYTES: int = 2048


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of a credential-verification probe. Never raised — returned.

    ``summary`` is a single user-facing line ("Alpaca ready — IEX feed").
    ``detail`` is optional remediation ("Switch the plan dropdown to Free").
    ``entitlements`` carries vendor-specific facts worth showing or caching
    (feed, plan tier, per-minute request budget).
    """

    status: str
    vendor: str
    summary: str
    detail: str = ""
    entitlements: dict[str, Any] = field(default_factory=dict)
    latency_ms: float | None = None
    http_status: int | None = None

    @property
    def ok(self) -> bool:
        """True only for :data:`STATUS_OK`."""
        return self.status == STATUS_OK

    @property
    def is_credential_problem(self) -> bool:
        """True when the user must edit a credential field to fix this."""
        return self.status in CREDENTIAL_PROBLEM_STATUSES

    def as_log_line(self) -> str:
        """Compact, secret-free line for the app log."""
        base = f"{self.vendor}: {self.status} — {self.summary}"
        if self.latency_ms is not None:
            base += f" ({self.latency_ms:.0f} ms)"
        return base


# --- Redaction -------------------------------------------------------------


def redact(text: str, secrets: object, *, placeholder: str = "***") -> str:
    """Replace every non-empty secret in ``secrets`` with ``placeholder``.

    ``secrets`` may be a single string or any iterable of optional strings
    (``None`` entries are skipped). Used before putting a vendor error body
    into a :class:`VerifyResult` — some providers echo the submitted key
    back in their error message.

    Short values (< 8 chars) are ignored: redacting a 2-character "secret"
    would mangle unrelated text and such a value can't be a real API key.
    """
    if isinstance(secrets, str):
        candidates: list[Any] = [secrets]
    else:
        try:
            candidates = list(secrets)  # type: ignore[arg-type]
        except TypeError:
            candidates = [secrets]
    out = text
    for raw in candidates:
        if not isinstance(raw, str):
            continue
        val = raw.strip()
        if len(val) < 8:
            continue
        out = out.replace(val, placeholder)
    return out


def _vendor_message(body: str) -> str:
    """Best-effort one-line vendor message from a JSON or plaintext body.

    Providers use ``{"message": ...}`` (Alpaca), ``{"error": ...}``
    (Polygon) or plain text. Returns ``""`` when nothing useful is found.
    Never raises.
    """
    text = (body or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return " ".join(text.split())[:200]
    if isinstance(parsed, dict):
        for key in ("message", "error", "detail", "error_message"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                return " ".join(val.split())[:200]
    return ""


def read_error_body(exc: BaseException, secrets: object = ()) -> str:
    """Read + redact a bounded slice of an ``HTTPError`` response body.

    Returns ``""`` when the body is unreadable (already-consumed stream,
    non-HTTP error, etc.). Never raises — a diagnostic nicety must never
    become the reason verification fails.
    """
    reader = getattr(exc, "read", None)
    if reader is None:
        return ""
    try:
        raw = reader(MAX_ERROR_BODY_BYTES)
    except Exception:  # noqa: BLE001 — diagnostics only
        return ""
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return ""
    else:
        text = str(raw)
    return redact(_vendor_message(text), secrets)


# --- HTTP → status mapping (pure, offline-testable) ------------------------


def status_for_http_code(code: int | None) -> str:
    """Map an HTTP status code to a :data:`STATUS_*` value.

    * 2xx → ``ok``
    * 401 → ``invalid_credentials`` (vendor rejected the key)
    * 403 → ``forbidden`` — authenticated but not entitled. Deliberately
      NOT ``invalid_credentials``: on most vendors a 403 means the key is
      valid and the *plan* is wrong, which is a completely different fix.
      Vendor verifiers may refine this (see ``alpaca_source.verify_alpaca``,
      which re-probes with a downgraded feed to prove the key works).
    * 429 → ``rate_limited`` (credentials are fine)
    * 5xx → ``network_error`` (vendor-side; says nothing about the key)
    * anything else → ``error``
    """
    if code is None:
        return STATUS_ERROR
    if 200 <= code < 300:
        return STATUS_OK
    if code == 401:
        return STATUS_INVALID_CREDENTIALS
    if code == 403:
        return STATUS_FORBIDDEN
    if code == 429:
        return STATUS_RATE_LIMITED
    if 500 <= code < 600:
        return STATUS_NETWORK_ERROR
    return STATUS_ERROR


_DEFAULT_SUMMARY: dict[str, str] = {
    STATUS_INVALID_CREDENTIALS: "Rejected — check the key and secret.",
    STATUS_FORBIDDEN: "Authenticated, but this account is not entitled.",
    STATUS_RATE_LIMITED: "Rate limited — credentials look fine, try again.",
    STATUS_NETWORK_ERROR: "Could not reach the provider.",
    STATUS_ERROR: "Unexpected response from the provider.",
}

_DEFAULT_DETAIL: dict[str, str] = {
    STATUS_INVALID_CREDENTIALS: (
        "Re-copy both values from the provider dashboard — a trailing "
        "space or a swapped key/secret is the usual cause."
    ),
    STATUS_FORBIDDEN: (
        "The credentials are valid but your plan does not include the "
        "requested data. Check the plan setting."
    ),
    STATUS_RATE_LIMITED: "Wait a few seconds and test again.",
    STATUS_NETWORK_ERROR: (
        "Check your internet connection, VPN or firewall, then retry."
    ),
}


def result_from_exception(
    exc: BaseException, *, vendor: str, secrets: object = (),
    latency_ms: float | None = None,
) -> VerifyResult:
    """Translate a probe exception into a :class:`VerifyResult`.

    Handles ``HTTPError`` (status-code mapping + redacted vendor message),
    ``URLError`` / ``OSError`` / timeouts (``network_error`` — never blame
    the credentials for a dead network), and anything else (``error``).
    Pure apart from reading the error body; unit-tested with synthetic
    exceptions and no network.
    """
    if isinstance(exc, urllib.error.HTTPError):
        code = getattr(exc, "code", None)
        status = status_for_http_code(code)
        vendor_msg = read_error_body(exc, secrets)
        summary = _DEFAULT_SUMMARY.get(status, _DEFAULT_SUMMARY[STATUS_ERROR])
        if code is not None:
            summary = f"{summary} (HTTP {code})"
        if vendor_msg:
            summary = f"{summary} {vendor_msg}"
        return VerifyResult(
            status=status, vendor=vendor, summary=summary,
            detail=_DEFAULT_DETAIL.get(status, ""),
            latency_ms=latency_ms, http_status=code,
        )
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
        reason = getattr(exc, "reason", None) or exc
        return VerifyResult(
            status=STATUS_NETWORK_ERROR, vendor=vendor,
            summary=f"Could not reach the provider: {reason}",
            detail=_DEFAULT_DETAIL[STATUS_NETWORK_ERROR],
            latency_ms=latency_ms,
        )
    return VerifyResult(
        status=STATUS_ERROR, vendor=vendor,
        summary=f"Unexpected error while testing: {exc}",
        latency_ms=latency_ms,
    )


def not_configured(vendor: str, *, detail: str = "") -> VerifyResult:
    """Canonical "nothing to test yet" result."""
    return VerifyResult(
        status=STATUS_NOT_CONFIGURED, vendor=vendor,
        summary="Not configured — fill in the fields above, then test.",
        detail=detail,
    )


class _Stopwatch:
    """Context manager capturing elapsed milliseconds (monotonic)."""

    __slots__ = ("_start", "elapsed_ms")

    def __init__(self) -> None:
        self._start = 0.0
        self.elapsed_ms: float = 0.0

    def __enter__(self) -> _Stopwatch:
        self._start = time.monotonic()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.elapsed_ms = (time.monotonic() - self._start) * 1000.0


def stopwatch() -> _Stopwatch:
    """Return a fresh elapsed-time context manager (see :class:`_Stopwatch`)."""
    return _Stopwatch()


# --- Registry --------------------------------------------------------------

#: ``(creds=None, *, timeout=..., opener=None) -> VerifyResult``. ``creds``
#: is the vendor credential object; ``None`` means "read the process-wide
#: credentials". ``opener`` is an injection seam so tests never hit network.
Verifier = Callable[..., VerifyResult]

_VERIFIERS: dict[str, Verifier] = {}

#: Default probe timeout. Shorter than the 15 s fetch timeout: a user
#: sitting in front of a "Test connection" button wants a fast answer, and
#: a slow provider is itself useful information.
DEFAULT_TIMEOUT_S: float = 10.0


def register_verifier(vendor: str, fn: Verifier) -> None:
    """Register ``fn`` as the credential verifier for ``vendor``.

    Idempotent — repeat registration overwrites, so tests can stub a
    vendor probe the same way they stub a fetcher via
    :func:`~tradinglab.data.base.register_source`.
    """
    _VERIFIERS[vendor] = fn


def unregister_verifier(vendor: str) -> None:
    """Remove ``vendor``'s verifier if present (no-op otherwise)."""
    _VERIFIERS.pop(vendor, None)


def has_verifier(vendor: str) -> bool:
    """True when ``vendor`` has a registered verifier."""
    return vendor in _VERIFIERS


def verifiable_vendors() -> list[str]:
    """Vendors with a registered verifier, in registration order."""
    return list(_VERIFIERS)


def verify_vendor(
    vendor: str, creds: Any | None = None, *,
    timeout: float = DEFAULT_TIMEOUT_S, opener: Any | None = None,
) -> VerifyResult:
    """Run ``vendor``'s credential probe. **Never raises.**

    ``creds`` lets a caller verify values that are not yet persisted — the
    credentials dialog passes what the user has typed so they can iterate
    without committing a bad blob to the DPAPI store. ``None`` falls back
    to the process-wide credentials.

    A verifier that raises is caught here and reported as ``error`` rather
    than propagating into a Tk callback or a worker thread.
    """
    fn = _VERIFIERS.get(vendor)
    if fn is None:
        return VerifyResult(
            status=STATUS_UNSUPPORTED, vendor=vendor,
            summary=f"No connection test is available for {vendor}.",
        )
    try:
        return fn(creds, timeout=timeout, opener=opener)
    except Exception as exc:  # noqa: BLE001 — probe must never escape
        LOG.warning("verify: %s probe raised: %s", vendor, exc)
        return result_from_exception(exc, vendor=vendor)


# --- Last-known-result cache ----------------------------------------------
#
# In-process only, and secret-free by construction (a VerifyResult never
# holds credential values). Lets the Help menu / status bar answer "is
# Alpaca ready?" without re-probing on every repaint.

_LAST: dict[str, VerifyResult] = {}


def record_result(result: VerifyResult) -> VerifyResult:
    """Cache ``result`` as the latest verdict for its vendor. Returns it."""
    _LAST[result.vendor] = result
    LOG.info("verify: %s", result.as_log_line())
    return result


def last_result(vendor: str) -> VerifyResult | None:
    """Most recent :class:`VerifyResult` for ``vendor``, if any."""
    return _LAST.get(vendor)


def clear_results() -> None:
    """Drop every cached verdict (called when credentials change)."""
    _LAST.clear()


__all__ = [
    "ALL_STATUSES",
    "CREDENTIAL_PROBLEM_STATUSES",
    "DEFAULT_TIMEOUT_S",
    "MAX_ERROR_BODY_BYTES",
    "STATUS_ERROR",
    "STATUS_FORBIDDEN",
    "STATUS_INVALID_CREDENTIALS",
    "STATUS_NETWORK_ERROR",
    "STATUS_NOT_CONFIGURED",
    "STATUS_OK",
    "STATUS_RATE_LIMITED",
    "STATUS_UNSUPPORTED",
    "VerifyResult",
    "Verifier",
    "clear_results",
    "has_verifier",
    "last_result",
    "not_configured",
    "read_error_body",
    "record_result",
    "redact",
    "register_verifier",
    "result_from_exception",
    "status_for_http_code",
    "stopwatch",
    "unregister_verifier",
    "verifiable_vendors",
    "verify_vendor",
]
