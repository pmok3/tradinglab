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

The browser flow is available in the Connect to Schwab dialog and the
``schwab_login`` CLI. ``schwab_auth`` owns protected persistence + refresh.

REST price-history endpoint
---------------------------

The stdlib HTTP adapter is offline-tested. Registration stays disabled until
live commissioning; implementing transport does not establish entitlements.

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

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import partial
from http.client import HTTPException
from typing import Any

from ..core.timezones import ET
from ..models import Candle
from . import verify as _verify
from ._http import MAX_RESPONSE_BYTES, credentialed_opener
from .credentials import SchwabCredentials, get_credentials
from .normalize import CandleArrays, candles_from_json_rows, pop_prebuilt_arrays, stash_arrays
from .schwab_auth import TokenCacheError, _post_token, get_access_token, schwab_failure_result

LOG = logging.getLogger(__name__)


# Logical → Schwab JSON keys. Schwab spells out OHLCV fully.
_SCHWAB_KEYMAP = {
    "ts": "datetime",
    "open": "open", "high": "high", "low": "low",
    "close": "close", "volume": "volume",
}


def candles_from_schwab_response(
    payload: dict[str, Any], *, interval: str,
    start: datetime | None = None, end: datetime | None = None,
) -> list[Candle]:
    """Map a parsed Schwab ``/pricehistory`` response to candles.

    Tolerates both the standard envelope (``{"candles": [...]}``) and a
    bare list of bars (some streaming-flavored endpoints). Returns an
    empty list when ``empty: true`` or no candles present — never None.

    Schwab's ``datetime`` field is epoch **milliseconds (UTC)**; we convert
    to **US Eastern** (``core.timezones.ET``) so ``classify_session`` and
    the chart read the correct exchange wall-clock, matching yfinance /
    Alpaca. Omitting the conversion would shift the intraday session +5h.

    For ``interval="1h"`` the payload must contain genuine one-minute bars.
    Aggregation never crosses an ET date/session boundary; trailing partial
    buckets are kept, but buckets starting before ``start`` are omitted.
    """
    if isinstance(payload, list):
        rows = payload
    else:
        if payload.get("empty"):
            return []
        rows = payload.get("candles") or []
    candles = candles_from_json_rows(
        rows, interval="1m" if interval == "1h" else interval,
        keymap=_SCHWAB_KEYMAP, ts_unit="ms", tz=ET,
    )
    # The stash owns the original list by identity; consume it before any
    # replacement or aggregation so discarded minute bars cannot be retained.
    arrays = pop_prebuilt_arrays(candles)
    by_date = {c.date: i for i, c in enumerate(candles)}
    indices = [
        by_date[stamp] for stamp in sorted(by_date)
        if (start is None or stamp >= start) and (end is None or stamp < end)
    ]
    candles = [candles[i] for i in indices]
    if interval != "1h":
        if arrays is not None and candles:
            stash_arrays(candles, CandleArrays(
                opens=arrays.opens[indices], highs=arrays.highs[indices],
                lows=arrays.lows[indices], closes=arrays.closes[indices],
                volumes=arrays.volumes[indices],
            ))
        return candles
    del arrays
    # Anchor regular hours at 09:30 ET, independent of the response's first
    # timestamp. Separate extended sessions so a 16:00 tick cannot alter RTH.
    from ..streaming.resampler import BarResampler
    resampler = BarResampler("1h", session_segments=True)
    hourly: list[Candle] = []
    for candle in candles:
        hourly.extend(event.candle for event in resampler.on_1m_tick(candle, forming=False) if event.closed)
    trailing = resampler.current_forming()
    if trailing is not None:
        hourly.append(trailing)
    return [c for c in hourly if (start is None or c.date >= start) and (end is None or c.date < end)]


# ---------------------------------------------------------------------------
# Fetcher (HTTP) — registration remains separately commissioning-gated.
# ---------------------------------------------------------------------------


# Map our short interval strings to Schwab's (periodType, frequencyType,
# frequency) triples. Daily intervals use periodType=year. Intraday
# uses periodType=day.
_INTERVAL_TO_SCHWAB = {
    "1m":  ("day",   "minute", 1),
    "5m":  ("day",   "minute", 5),
    "10m": ("day",   "minute", 10),
    "15m": ("day",   "minute", 15),
    "30m": ("day",   "minute", 30),
    "1h":  ("day",   "minute", 1),  # aggregate genuine 1m with the shared resampler
    "1d":  ("year",  "daily",  1),
    "1wk": ("year",  "weekly", 1),
    "1mo": ("year",  "monthly", 1),
}
PRICE_HISTORY_URL = "https://api.schwabapi.com/marketdata/v1/pricehistory"


def price_history_params(
    ticker: str, interval: str, *,
    start: datetime | None = None, end: datetime | None = None,
) -> dict[str, str | int]:
    """Pure request mapping; date ranges are aware, paired, and half-open."""
    if interval not in _INTERVAL_TO_SCHWAB:
        raise ValueError("Unsupported Schwab interval.")
    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError("A symbol is required.")
    period_type, frequency_type, frequency = _INTERVAL_TO_SCHWAB[interval]
    params: dict[str, str | int] = {
        "symbol": ticker.strip(),
        "periodType": period_type,
        "frequencyType": frequency_type,
        "frequency": frequency,
        "needExtendedHoursData": "true",
        "needPreviousClose": "false",
    }
    if start is None and end is None:
        params["period"] = 10 if period_type == "day" else 1
    else:
        if start is None or end is None:
            raise ValueError("Schwab ranges require both start and end.")
        if start.utcoffset() is None or end.utcoffset() is None:
            raise ValueError("Schwab range bounds must be timezone-aware.")
        if start >= end:
            raise ValueError("Schwab range start must precede end.")
        params["startDate"] = int(start.timestamp() * 1000)
        params["endDate"] = int(end.timestamp() * 1000) - 1
    return params


def fetch_schwab_data(
    ticker: str = "AAPL", interval: str = "1d",
    *, start: datetime | None = None, end: datetime | None = None,
) -> list[Candle] | None:
    """``DataFetcher``-compatible Schwab fetcher.

    Returns ``None`` whenever the request can't be made — missing
    credentials, missing refresh token, network error, or bad
    response. Expected operational failures are surfaced in logs/credential
    health, not raised. No retries or REST polling are scheduled here.
    """
    creds = get_credentials().schwab
    if not creds.is_configured():
        LOG.debug("schwab: not configured, skipping fetch")
        return None
    try:
        price_history_params(ticker, interval, start=start, end=end)
    except ValueError as exc:
        LOG.warning("schwab: invalid price-history request: %s", exc)
        return None
    try:
        access_token = _maybe_get_access_token(creds)
        if access_token is None:
            LOG.info("schwab: OAuth sign-in required via Connect to Schwab.")
            return None
        payload = _http_get_pricehistory(ticker, interval, access_token, start=start, end=end)
        return candles_from_schwab_response(payload, interval=interval, start=start, end=end)
    except (OSError, HTTPException, ValueError, TypeError, KeyError, OverflowError, TokenCacheError) as exc:
        result = schwab_failure_result(exc)
        if result.is_credential_problem:
            _verify.record_result(result)
        LOG.warning("%s", result.as_log_line())
        return None


def _maybe_get_access_token(creds: SchwabCredentials) -> str | None:
    """Return a valid access token by reading + refreshing the token cache.

    Delegates to :mod:`tradinglab.data.schwab_auth`, which owns
    persistence + refresh. Returns ``None`` if the user hasn't run
    ``python -m tradinglab.data.schwab_login`` yet, or if the
    refresh token has expired (7+ days since last login).
    """
    return get_access_token(creds, raise_errors=True)


# Offline implementation is not live commissioning. Keep this gate closed.
SCHWAB_REGISTRATION_ENABLED: bool = False


def verify_schwab(
    creds: SchwabCredentials | None = None, *,
    timeout: float = _verify.DEFAULT_TIMEOUT_S,
    opener: Any | None = None,
) -> _verify.VerifyResult:
    """Explicit one-symbol OAuth probe; never called by source registration."""
    creds = creds if creds is not None else get_credentials().schwab
    if not creds.is_configured():
        return _verify.not_configured(
            "schwab", detail="An app key and app secret are required.")

    oauth_required = _verify.VerifyResult(
        status=_verify.STATUS_UNSUPPORTED,
        vendor="schwab",
        summary="Schwab OAuth sign-in is required before testing.",
        detail=(
            "Save the app credentials, then use Tools > Connect to Schwab. "
            "This check does not register or enable the uncommissioned data source."
        ),
    )
    current = get_credentials().schwab
    if (creds.app_key, creds.app_secret) != (current.app_key, current.app_secret):
        return oauth_required
    try:
        token = get_access_token(
            creds, raise_errors=True,
            _post=partial(_post_token, timeout=timeout, opener=opener),
        )
        if token is None:
            return oauth_required
        now = datetime.now(timezone.utc)
        payload = _http_get_pricehistory(
            "AAPL", "1d", token, start=now - timedelta(days=7), end=now,
            timeout=timeout, opener=opener,
        )
        candles = candles_from_schwab_response(payload, interval="1d")
        pop_prebuilt_arrays(candles)
        if not candles:
            return _verify.VerifyResult(
                status=_verify.STATUS_ERROR, vendor="schwab",
                summary="Schwab responded but returned no usable probe candles.",
            )
    except (OSError, HTTPException, ValueError, TypeError, KeyError, OverflowError, TokenCacheError) as exc:
        return schwab_failure_result(exc)
    return _verify.VerifyResult(
        status=_verify.STATUS_OK, vendor="schwab",
        summary="Schwab OAuth price-history access verified.",
        detail="AAPL daily bars are accessible. This does not commission streaming or validate other entitlements.",
    )


_verify.register_verifier("schwab", verify_schwab)


def _http_get_pricehistory(
    ticker: str, interval: str, access_token: str,
    *, start: datetime | None = None, end: datetime | None = None,
    timeout: float = 15, opener: Any | None = None,
) -> dict[str, Any]:
    """One credential-safe, bounded GET; HTTP errors retain their status codes."""
    params = price_history_params(ticker, interval, start=start, end=end)
    if not access_token:
        raise ValueError("A Schwab access token is required.")
    request = urllib.request.Request(
        PRICE_HISTORY_URL + "?" + urllib.parse.urlencode(params),
        headers={"Authorization": "Bearer " + access_token, "Accept": "application/json"},
    )
    with (opener or credentialed_opener()).open(request, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Schwab price-history response exceeds the size limit.")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Schwab price-history response must be an object.")
    if payload.get("empty") is not True and not isinstance(payload.get("candles"), list):
        raise ValueError("Schwab price-history response has no candle array.")
    return payload
