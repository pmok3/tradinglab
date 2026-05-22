"""Charles Schwab Market Data API → ``List[Candle]``.

This module has two layers:

1. :func:`candles_from_schwab_response` — pure mapper over a parsed
   JSON dict. **Fully testable offline** with a sample payload.

2. :func:`fetch_schwab_data` — the actual HTTP fetcher that conforms
   to the :data:`tradinglab.data.base.DataFetcher` signature
   ``(ticker, interval) -> Optional[List[Candle]]``. This piece needs
   credentials AND a valid OAuth refresh token; if either is missing
   we return ``None`` rather than raising, so the caller falls back
   gracefully.

OAuth scope
-----------

Schwab's API uses OAuth 2.0:

* Browser redirect → authorization code
* Code → access token (~30 min) + refresh token (~7 days)
* Refresh token must be re-issued by walking the user through the
  browser flow once a week.

The interactive flow is owned by :mod:`tradinglab.data.schwab_login`
(one-time CLI) and :mod:`tradinglab.data.schwab_auth` (refresh
+ persistence). The fetcher below reads cached tokens from
``~/.tradinglab/tokens/schwab.json``; until that file exists,
:func:`fetch_schwab_data` returns ``None``.

REST price-history endpoint
---------------------------

:func:`_http_get_pricehistory` is the remaining gap — it currently
raises ``NotImplementedError``. The dispatcher in ``data/__init__.py``
keeps the "schwab" source de-registered until that GET is wired up,
so users never see a broken option in the source dropdown.

Reference response shape (Market Data v1, ``/pricehistory``)::

    {
      "candles": [
        {"open": 175.0, "high": 175.5, "low": 174.8, "close": 175.2,
         "volume": 1234567, "datetime": 1709824200000}
      ],
      "symbol": "AAPL", "empty": false
    }
"""

from __future__ import annotations

import logging
from typing import Any

from ..models import Candle
from .credentials import SchwabCredentials, get_credentials
from .normalize import candles_from_json_rows

LOG = logging.getLogger(__name__)


# Logical → Schwab JSON keys. Schwab spells out OHLCV fully.
_SCHWAB_KEYMAP = {
    "ts": "datetime",
    "open": "open", "high": "high", "low": "low",
    "close": "close", "volume": "volume",
}


def candles_from_schwab_response(
    payload: dict[str, Any], *, interval: str,
) -> list[Candle]:
    """Map a parsed Schwab ``/pricehistory`` response to candles.

    Tolerates both the standard envelope (``{"candles": [...]}``) and a
    bare list of bars (some streaming-flavored endpoints). Returns an
    empty list when ``empty: true`` or no candles present — never None.
    """
    if isinstance(payload, list):
        rows = payload
    else:
        if payload.get("empty"):
            return []
        rows = payload.get("candles") or []
    return candles_from_json_rows(
        rows, interval=interval, keymap=_SCHWAB_KEYMAP, ts_unit="ms",
    )


# ---------------------------------------------------------------------------
# Fetcher (HTTP) — OAuth lifecycle is complete (see schwab_auth + schwab_login);
# the remaining gap is _http_get_pricehistory below.
# ---------------------------------------------------------------------------


# Map our short interval strings to Schwab's (periodType, frequencyType,
# frequency) triples. Daily intervals use periodType=year. Intraday
# uses periodType=day.
_INTERVAL_TO_SCHWAB = {
    "1m":  ("day",   "minute", 1),
    "5m":  ("day",   "minute", 5),
    "15m": ("day",   "minute", 15),
    "30m": ("day",   "minute", 30),
    "1h":  ("day",   "minute", 30),  # Schwab has no 60m; downsample later if needed
    "1d":  ("year",  "daily",  1),
    "1wk": ("year",  "weekly", 1),
    "1mo": ("year",  "monthly", 1),
}


def fetch_schwab_data(
    ticker: str = "AAPL", interval: str = "1d",
) -> list[Candle] | None:
    """``DataFetcher``-compatible Schwab fetcher.

    Returns ``None`` whenever the request can't be made — missing
    credentials, missing refresh token, network error, or bad
    response. **Never raises** so app startup and the data-source
    dropdown stay robust.
    """
    creds = get_credentials().schwab
    if not creds.is_configured():
        LOG.debug("schwab: not configured, skipping fetch")
        return None
    if interval not in _INTERVAL_TO_SCHWAB:
        LOG.warning("schwab: unsupported interval %r", interval)
        return None
    access_token = _maybe_get_access_token(creds)
    if access_token is None:
        # The user hasn't completed the one-time OAuth dance yet.
        LOG.info(
            "schwab: no cached refresh token. Run the one-time auth "
            "script to populate ~/.tradinglab/tokens/schwab.json")
        return None
    try:
        payload = _http_get_pricehistory(ticker, interval, access_token)
    except Exception as exc:  # pragma: no cover - network path
        LOG.warning("schwab: fetch failed for %s %s: %s", ticker, interval, exc)
        return None
    return candles_from_schwab_response(payload, interval=interval)


def _maybe_get_access_token(creds: SchwabCredentials) -> str | None:
    """Return a valid access token by reading + refreshing the token cache.

    Delegates to :mod:`tradinglab.data.schwab_auth`, which owns
    persistence + refresh. Returns ``None`` if the user hasn't run
    ``python -m tradinglab.data.schwab_login`` yet, or if the
    refresh token has expired (7+ days since last login).
    """
    from .schwab_auth import get_access_token
    return get_access_token(creds)


# Registry-eligibility flag. Audit ``schwab-credentials-gated``: the
# real ``register_source("schwab", ...)`` call in ``data/__init__.py``
# is commented out because :func:`_http_get_pricehistory` still
# raises ``NotImplementedError``. Until that's wired up,
# ``SCHWAB_REGISTRATION_ENABLED`` stays ``False`` and downstream
# UI surfaces (credentials dialog, source-selector dropdown) gate
# themselves off this constant. When the OAuth/REST plumbing
# lands, flip this to ``True`` AND uncomment the registration
# line in :mod:`tradinglab.data.__init__` in the same change.
SCHWAB_REGISTRATION_ENABLED: bool = False


def _http_get_pricehistory(
    ticker: str, interval: str, access_token: str,
) -> dict[str, Any]:  # pragma: no cover - network path
    """Issue the GET against Schwab's price-history endpoint.

    Implementation deferred until OAuth lands; see module docstring.
    """
    raise NotImplementedError(
        "Schwab HTTP fetch requires OAuth tokens; finish "
        "_maybe_get_access_token first.")
