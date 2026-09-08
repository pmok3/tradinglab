"""Schwab OAuth token cache + auto-refresh.

Schwab Market Data + Trader APIs use OAuth 2.0 with a short-lived
access token (~30 min) and a refresh token that must itself be
re-issued every ~7 days via the browser-redirect flow.

This module owns:

* The on-disk token cache (DPAPI on Windows, private JSON elsewhere).
* The "is the cached access token still fresh?" check.
* The HTTPS POST to Schwab's ``/oauth/token`` endpoint that swaps a
  refresh token for a new access token (+ rotated refresh token).

It deliberately does NOT own the one-time browser dance that creates
the *first* refresh token — that's
:mod:`tradinglab.data.schwab_login`. Splitting them keeps this
module pure-stdlib and importable from anywhere without any UI risk.

Cache file format
-----------------

Path: ``paths.tokens_dir()/schwab.dat`` on Windows, ``schwab.json``
elsewhere (override with ``TRADINGLAB_TOKEN_DIR``). Schema::

    {
      "access_token": "<jwt>",
      "refresh_token": "<long opaque string>",
      "access_token_expires_at": 1709824200,   # epoch seconds
      "refresh_token_expires_at": 1710429000,  # epoch seconds (~7d out)
      "token_type": "Bearer",
      "saved_at": "2024-03-07T14:30:00+00:00"
    }

Concurrency
-----------

Refreshes are serialized, but disk locking never spans network I/O.
Clearing/replacing the cache invalidates in-flight refreshes. Multiple
processes sharing a cache are not supported (no filesystem lock).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from typing import Any

from .. import _dpapi
from ..core.io_helpers import atomic_write_json
from . import verify as _verify
from ._http import MAX_RESPONSE_BYTES, credentialed_opener
from .credentials import SchwabCredentials

LOG = logging.getLogger(__name__)


# Schwab OAuth endpoint. The price-history etc. endpoints live under
# api.schwabapi.com; the token endpoint is on the same host.
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"

# Refresh a few minutes before nominal expiry so a long-running
# request doesn't span the boundary and 401.
ACCESS_TOKEN_REFRESH_SKEW_SEC = 5 * 60


_lock = threading.RLock()
_refresh_lock = threading.Lock()
_generation = 0
_WINDOWS = sys.platform == "win32"


class TokenCacheError(RuntimeError):
    """A cache could not be read, protected, written, or removed."""


class TokenCacheChangedError(TokenCacheError):
    """A pending authorization was invalidated by another cache change."""


def token_cache_generation() -> int:
    """Snapshot the process-local revision before beginning authorization."""
    with _lock:
        return _generation


def schwab_failure_result(exc: Exception) -> _verify.VerifyResult:
    """Classify failures without ever surfacing vendor bodies or exception text."""
    if isinstance(exc, TokenCacheChangedError):
        return _verify.VerifyResult(
            status=_verify.STATUS_ERROR, vendor="schwab",
            summary="Schwab authorization was cancelled because the token cache changed.",
            detail="Start a fresh sign-in if you want to reconnect.",
        )
    if isinstance(exc, TokenCacheError):
        return _verify.VerifyResult(
            status=_verify.STATUS_ERROR, vendor="schwab",
            summary="Schwab token cache could not be used.",
            detail="Check file access and Windows user protection, or reconnect to Schwab.",
        )
    if isinstance(exc, urllib.error.HTTPError):
        # Vendor bodies can echo tokens we have not seen (including rotated ones).
        safe = urllib.error.HTTPError("", exc.code, "", exc.headers, None)
        exc.close()
    elif isinstance(exc, (OSError, urllib.error.URLError, HTTPException)):
        safe = OSError("Schwab request failed; check connectivity and retry.")
    else:
        safe = ValueError("Schwab returned an invalid response.")
    result = _verify.result_from_exception(safe, vendor="schwab")
    if result.status == _verify.STATUS_INVALID_CREDENTIALS:
        return _verify.VerifyResult(
            status=result.status, vendor="schwab", http_status=result.http_status,
            summary="Schwab rejected authorization (HTTP 401).",
            detail="Reconnect via Connect to Schwab; check the app credentials if sign-in fails.",
        )
    return result


# ---------------------------------------------------------------------------
# Token cache file
# ---------------------------------------------------------------------------


def token_cache_path() -> Path:
    """Resolve the on-disk path for the Schwab token cache.

    Routes through :func:`tradinglab.paths.tokens_dir` so the
    user-data layout (and the migration from the legacy
    ``~/.tradinglab/tokens/`` location) is defined in exactly one
    place. ``TRADINGLAB_TOKEN_DIR`` is still honored as a narrow-
    scope override for dev / smoke harnesses; ``TRADINGLAB_DATA_DIR``
    redirects everything including this.
    """
    from ..paths import tokens_dir as _td
    return _td() / "schwab.json"


def _cache_paths(path: Path | None) -> tuple[Path, Path]:
    p = path or token_cache_path()
    if p.suffix == ".dat":
        return p.with_suffix(".json"), p
    return p, p.with_suffix(".dat")


def load_token_cache(path: Path | None = None) -> dict[str, Any] | None:
    """Read protected/legacy tokens; migrate only after a successful protected write.

    ``path`` remains the JSON base path for compatibility. The protected sibling
    uses ``.dat``. A present protected cache is authoritative, even if unreadable.
    Missing caches return ``None``; invalid or inaccessible caches raise a
    secret-free ``TokenCacheError``.
    """
    p, protected = _cache_paths(path)
    with _lock:
        try:
            if protected.exists():
                data = _dpapi.load_json_object(protected)
                _validate_cache(data)
                if _WINDOWS:
                    p.unlink(missing_ok=True)
                return data
            if not p.exists():
                return None
            data = json.loads(p.read_text(encoding="utf-8"))
            _validate_cache(data)
            if _WINDOWS:
                _write_cache(data, p)
            return data
        except (OSError, ValueError, _dpapi.DpapiError) as exc:
            raise TokenCacheError("Cannot read or migrate the Schwab token cache.") from exc


def _validate_cache(data: Any) -> None:
    if not isinstance(data, dict):
        raise ValueError("Token cache must be a JSON object.")
    if not any(isinstance(data.get(k), str) and data[k] for k in ("access_token", "refresh_token")):
        raise ValueError("Token cache contains no tokens.")
    for key in ("access_token", "refresh_token"):
        if key in data and (not isinstance(data[key], str) or not data[key]):
            raise ValueError("Invalid token field.")
    for key in ("access_token_expires_at", "refresh_token_expires_at"):
        if data.get(key) is not None and not _finite_number(data[key]):
            raise ValueError("Invalid token expiry.")


def _write_cache(data: dict[str, Any], path: Path) -> None:
    protected = path.with_suffix(".dat")
    if _WINDOWS:
        _dpapi.save_json_object(protected, data)
        path.unlink(missing_ok=True)
    else:
        if protected.exists():
            raise TokenCacheError("Protected tokens require the original Windows account.")
        atomic_write_json(path, data, indent=2, sort_keys=False)
        os.chmod(path, 0o600)


def save_token_cache(
    data: dict[str, Any], path: Path | None = None, *,
    expected_generation: int | None = None,
) -> None:
    """Atomically persist tokens; Windows protection failures never fall back to JSON."""
    global _generation
    p, _ = _cache_paths(path)
    payload = dict(data)
    payload.setdefault(
        "saved_at", datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    with _lock:
        if expected_generation is not None and expected_generation != _generation:
            raise TokenCacheChangedError("Authorization cancelled because the token cache changed.")
        try:
            _validate_cache(payload)
            _write_cache(payload, p)
        except (OSError, ValueError, _dpapi.DpapiError) as exc:
            raise TokenCacheError("Cannot save the Schwab token cache securely.") from exc
        finally:
            # A partial migration (encrypted write succeeded, legacy unlink failed)
            # must also invalidate older network operations.
            _generation += 1


def clear_token_cache(path: Path | None = None) -> None:
    """Invalidate pending auth and remove both protected and legacy cache versions.

    Attempts both deletions even if one fails. Raises rather than reporting a
    successful disconnect when tokens remain on disk.
    """
    global _generation
    p, protected = _cache_paths(path)
    with _lock:
        _generation += 1
        failure = None
        for candidate in (p, protected):
            try:
                candidate.unlink(missing_ok=True)
            except OSError as exc:
                failure = exc
        if failure is not None:
            raise TokenCacheError("Cannot remove all Schwab token cache files.") from failure


# ---------------------------------------------------------------------------
# Token shape helpers (pure)
# ---------------------------------------------------------------------------


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def is_access_token_fresh(cache: dict[str, Any], *, now: float | None = None) -> bool:
    """True iff ``cache.access_token`` is set and not within the skew of expiry."""
    if not cache or not isinstance(cache.get("access_token"), str) or not cache["access_token"]:
        return False
    exp = cache.get("access_token_expires_at")
    if not _finite_number(exp):
        return False
    if now is None:
        now = time.time()
    return now + ACCESS_TOKEN_REFRESH_SKEW_SEC < exp


def is_refresh_token_alive(cache: dict[str, Any], *, now: float | None = None) -> bool:
    """True iff there's any usable refresh token in the cache.

    We treat a missing ``refresh_token_expires_at`` as alive, because
    older cache files written before that field was introduced should
    still get one chance to refresh (the server is the real authority
    on expiry — it'll 400 if the token is stale).
    """
    if not cache or not isinstance(cache.get("refresh_token"), str) or not cache["refresh_token"]:
        return False
    exp = cache.get("refresh_token_expires_at")
    if exp is None:
        return True
    if not _finite_number(exp):
        return False
    if now is None:
        now = time.time()
    return now < exp


def build_token_cache(
    response: dict[str, Any], *, now: float | None = None,
    previous: dict[str, Any] | None = None,
    creds: SchwabCredentials | None = None,
) -> dict[str, Any]:
    """Translate Schwab's /oauth/token response into our cache schema.

    Schwab returns ``{"access_token", "refresh_token", "expires_in",
    "token_type", "scope", ...}``. ``expires_in`` is seconds until the
    access token expires (1800 for Schwab). The refresh token's own
    expiry is not returned — only a new browser authorization starts a
    seven-day lifetime. Refreshes preserve (or shorten) the original deadline.
    """
    if now is None:
        now = time.time()
    if not isinstance(response, dict):
        raise ValueError("Token response must be an object.")
    expires_in = int(response.get("expires_in", 1800))
    refresh_lifetime = int(response.get("refresh_expires_in", 7 * 24 * 3600))
    if expires_in <= 0 or refresh_lifetime <= 0:
        raise ValueError("Token lifetime must be positive.")
    deadline = int(now) + refresh_lifetime
    if previous is not None:
        old_deadline = previous.get("refresh_token_expires_at")
        if old_deadline is None:
            # Legacy caches with no deadline retain their original saved age.
            # With no trustworthy age, grant only this one access-token refresh.
            old_deadline = now
            if isinstance(previous.get("saved_at"), str):
                try:
                    saved = datetime.fromisoformat(previous["saved_at"])
                    if saved.tzinfo is not None:
                        old_deadline = saved.timestamp() + 7 * 24 * 3600
                except ValueError:
                    pass
        if not _finite_number(old_deadline):
            raise ValueError("Invalid prior refresh expiry.")
        deadline = min(deadline, old_deadline)
    cache = {
        "access_token": response.get("access_token"),
        "refresh_token": response.get("refresh_token", (previous or {}).get("refresh_token")),
        "token_type": response.get("token_type", (previous or {}).get("token_type", "Bearer")),
        "scope": response.get("scope", (previous or {}).get("scope")),
        "access_token_expires_at": int(now) + expires_in,
        "refresh_token_expires_at": deadline,
    }
    _validate_cache(cache)
    if creds is not None:
        cache["credential_fingerprint"] = _credential_fingerprint(creds)
    elif previous is not None and "credential_fingerprint" in previous:
        cache["credential_fingerprint"] = previous["credential_fingerprint"]
    return cache


def _credential_fingerprint(creds: SchwabCredentials) -> str:
    payload = json.dumps([creds.app_key, creds.app_secret]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def cache_matches_credentials(cache: dict[str, Any], creds: SchwabCredentials) -> bool:
    """Legacy unbound caches remain usable; bound caches require the same app credentials."""
    fingerprint = cache.get("credential_fingerprint")
    return fingerprint is None or fingerprint == _credential_fingerprint(creds)


# ---------------------------------------------------------------------------
# HTTP: refresh access token
# ---------------------------------------------------------------------------


def _basic_auth_header(creds: SchwabCredentials) -> str:
    """Schwab requires app_key:app_secret in HTTP Basic auth on token POSTs."""
    raw = f"{creds.app_key}:{creds.app_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _post_token(
    creds: SchwabCredentials, body: dict[str, str],
    *, timeout: float = 15, opener=None,
) -> dict[str, Any]:
    data = urllib.parse.urlencode(body).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL, data=data, method="POST",
        headers={
            "Authorization": _basic_auth_header(creds),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    with (opener or credentialed_opener()).open(req, timeout=timeout) as resp:
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Schwab token response exceeds the size limit.")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Schwab token response must be an object.")
    return payload


def refresh_access_token(
    creds: SchwabCredentials, refresh_token: str,
    *, _post=None,
) -> dict[str, Any]:
    """Exchange a refresh token for a new access token.

    Returns the raw Schwab response dict. ``_post`` is an injection
    hook for tests; production calls hit the real endpoint.
    """
    if not creds.is_configured():
        raise RuntimeError("schwab credentials not configured")
    poster = _post or _post_token
    return poster(creds, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })


# ---------------------------------------------------------------------------
# Public: get_access_token
# ---------------------------------------------------------------------------


def get_access_token(
    creds: SchwabCredentials, *, path: Path | None = None,
    _now: float | None = None, _post=None,
    raise_errors: bool = False,
) -> str | None:
    """Return a valid access token, refreshing the cache if needed.

    Behavior:

    * No cache file or no refresh token → return ``None`` (caller
      should advise the user to run ``schwab_login``).
    * Access token still fresh → return it directly.
    * Refresh token alive → POST to /oauth/token, persist the new
      tokens, return the new access token.
    * Refresh token expired → return ``None``; caller should advise
      the user to run ``schwab_login`` again.

    Expected cache/network/response failures are logged without secrets and
    return ``None``. Explicit verifiers can request propagation with
    ``raise_errors=True`` to preserve the HTTP error taxonomy.
    """
    if not creds.is_configured():
        return None
    try:
        with _refresh_lock:
            with _lock:
                cache = load_token_cache(path) or {}
                generation = _generation
                if not cache_matches_credentials(cache, creds):
                    LOG.warning("schwab: app credentials changed; reconnect to Schwab.")
                    return None
                if is_access_token_fresh(cache, now=_now):
                    return cache["access_token"]
            if not is_refresh_token_alive(cache, now=_now):
                LOG.info("schwab: OAuth sign-in required via Connect to Schwab.")
                return None
            response = refresh_access_token(
                creds, cache["refresh_token"], _post=_post,
            )
            new_cache = build_token_cache(response, now=_now, previous=cache, creds=creds)
            save_token_cache(new_cache, path, expected_generation=generation)
            return new_cache["access_token"]
    except (OSError, HTTPException, ValueError, TypeError, OverflowError, TokenCacheError) as exc:
        if raise_errors:
            raise
        LOG.warning("%s", schwab_failure_result(exc).as_log_line())
        return None
