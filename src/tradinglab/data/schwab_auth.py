"""Schwab OAuth token cache + auto-refresh.

Schwab Market Data + Trader APIs use OAuth 2.0 with a short-lived
access token (~30 min) and a refresh token that must itself be
re-issued every ~7 days via the browser-redirect flow.

This module owns:

* The on-disk token cache JSON file.
* The "is the cached access token still fresh?" check.
* The HTTPS POST to Schwab's ``/oauth/token`` endpoint that swaps a
  refresh token for a new access token (+ rotated refresh token).

It deliberately does NOT own the one-time browser dance that creates
the *first* refresh token — that's
:mod:`tradinglab.data.schwab_login`. Splitting them keeps this
module pure-stdlib and importable from anywhere without any UI risk.

Cache file format
-----------------

Path: ``~/.tradinglab/tokens/schwab.json`` (override with the
``TRADINGLAB_TOKEN_DIR`` env var). Mode 0600 on POSIX. Schema::

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

Token refresh is guarded by an in-process lock so two threads can't
both race a refresh and invalidate each other. We don't take a
file-system lock — multiple processes sharing the same cache is
already a rare ops scenario, and Schwab tolerates a token being used
from two places (the older one is invalidated automatically).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ..core.io_helpers import atomic_write_json
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


_lock = threading.Lock()


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


def load_token_cache(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Read the cache file. Returns ``None`` if missing or unparseable."""
    p = path or token_cache_path()
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        LOG.warning("schwab: cannot read token cache %s: %s", p, e)
        return None


def save_token_cache(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Persist ``data`` atomically. Sets mode 0600 on POSIX."""
    p = path or token_cache_path()
    payload = dict(data)
    payload.setdefault(
        "saved_at", datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    atomic_write_json(p, payload, indent=2, sort_keys=False)
    try:
        os.chmod(p, 0o600)
    except OSError:
        # Windows ignores chmod modes other than read-only; not worth
        # surfacing.
        pass


# ---------------------------------------------------------------------------
# Token shape helpers (pure)
# ---------------------------------------------------------------------------


def is_access_token_fresh(cache: Dict[str, Any], *, now: Optional[float] = None) -> bool:
    """True iff ``cache.access_token`` is set and not within the skew of expiry."""
    if not cache or not cache.get("access_token"):
        return False
    exp = cache.get("access_token_expires_at")
    if not isinstance(exp, (int, float)):
        return False
    if now is None:
        now = time.time()
    return now + ACCESS_TOKEN_REFRESH_SKEW_SEC < exp


def is_refresh_token_alive(cache: Dict[str, Any], *, now: Optional[float] = None) -> bool:
    """True iff there's any usable refresh token in the cache.

    We treat a missing ``refresh_token_expires_at`` as alive, because
    older cache files written before that field was introduced should
    still get one chance to refresh (the server is the real authority
    on expiry — it'll 400 if the token is stale).
    """
    if not cache or not cache.get("refresh_token"):
        return False
    exp = cache.get("refresh_token_expires_at")
    if exp is None:
        return True
    if not isinstance(exp, (int, float)):
        return False
    if now is None:
        now = time.time()
    return now < exp


def build_token_cache(
    response: Dict[str, Any], *, now: Optional[float] = None,
) -> Dict[str, Any]:
    """Translate Schwab's /oauth/token response into our cache schema.

    Schwab returns ``{"access_token", "refresh_token", "expires_in",
    "token_type", "scope", ...}``. ``expires_in`` is seconds until the
    access token expires (1800 for Schwab). The refresh token's own
    expiry is not returned — we use the documented 7-day lifetime.
    """
    if now is None:
        now = time.time()
    expires_in = int(response.get("expires_in") or 1800)
    refresh_lifetime = int(response.get("refresh_expires_in") or 7 * 24 * 3600)
    return {
        "access_token": response["access_token"],
        "refresh_token": response["refresh_token"],
        "token_type": response.get("token_type", "Bearer"),
        "scope": response.get("scope"),
        "access_token_expires_at": int(now) + expires_in,
        "refresh_token_expires_at": int(now) + refresh_lifetime,
    }


# ---------------------------------------------------------------------------
# HTTP: refresh access token
# ---------------------------------------------------------------------------


def _basic_auth_header(creds: SchwabCredentials) -> str:
    """Schwab requires app_key:app_secret in HTTP Basic auth on token POSTs."""
    raw = f"{creds.app_key}:{creds.app_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _post_token(  # pragma: no cover - network path; exercised via integration
    creds: SchwabCredentials, body: Dict[str, str],
) -> Dict[str, Any]:
    data = urllib.parse.urlencode(body).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL, data=data, method="POST",
        headers={
            "Authorization": _basic_auth_header(creds),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    with credentialed_opener().open(req, timeout=15) as resp:
        return json.loads(resp.read(MAX_RESPONSE_BYTES).decode("utf-8"))


def refresh_access_token(
    creds: SchwabCredentials, refresh_token: str,
    *, _post=None,
) -> Dict[str, Any]:
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
    creds: SchwabCredentials, *, path: Optional[Path] = None,
    _now: Optional[float] = None, _post=None,
) -> Optional[str]:
    """Return a valid access token, refreshing the cache if needed.

    Behavior:

    * No cache file or no refresh token → return ``None`` (caller
      should advise the user to run ``schwab_login``).
    * Access token still fresh → return it directly.
    * Refresh token alive → POST to /oauth/token, persist the new
      tokens, return the new access token.
    * Refresh token expired → return ``None``; caller should advise
      the user to run ``schwab_login`` again.

    Never raises on the *happy* paths. Network errors during refresh
    are logged and produce ``None``.
    """
    if not creds.is_configured():
        return None
    with _lock:
        cache = load_token_cache(path) or {}
        if is_access_token_fresh(cache, now=_now):
            return cache["access_token"]
        if not is_refresh_token_alive(cache, now=_now):
            LOG.warning(
                "schwab: refresh token missing or expired. "
                "Run `python -m tradinglab.data.schwab_login` to re-authorize.")
            return None
        try:
            response = refresh_access_token(
                creds, cache["refresh_token"], _post=_post,
            )
        except Exception as exc:  # broad: any urllib/json error
            LOG.warning("schwab: token refresh failed: %s", exc)
            return None
        new_cache = build_token_cache(response, now=_now)
        save_token_cache(new_cache, path)
        return new_cache["access_token"]
