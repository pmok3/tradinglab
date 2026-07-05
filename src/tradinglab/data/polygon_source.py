"""Polygon.io Aggregates v2 → ``List[Candle]``.

Two-layer module mirroring :mod:`schwab_source` and :mod:`alpaca_source`:

1. :func:`candles_from_polygon_response` — pure mapper, offline-testable.
2. :func:`fetch_polygon_data` — HTTP fetcher gated on credentials.

Polygon accepts authentication via either an ``apiKey`` query parameter
or an ``Authorization: Bearer <key>`` header. We use the header form so
the API key never appears in URL strings — query params show up in
``URLError`` repr, which then flows into our status logs and the
diagnostic-bundle exporter. Header-based auth keeps the secret out of
that leak chain.

Reference response shape (``/v2/aggs/ticker/{ticker}/range/...``)::

    {
      "ticker": "AAPL",
      "results": [
        {"t": 1709824200000, "o": 175.0, "h": 175.5, "l": 174.8,
         "c": 175.2, "v": 1234567, "n": 1500, "vw": 175.1}
      ],
      "resultsCount": 1,
      "next_url": null
    }
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from ..constants import provider_lookback_days
from ..core.timezones import ET
from ..models import Candle
from ._http import MAX_RESPONSE_BYTES, credentialed_opener
from .credentials import PolygonCredentials, get_credentials
from .normalize import candles_from_json_rows

LOG = logging.getLogger(__name__)


_POLYGON_KEYMAP = {
    "ts": "t",
    "open": "o", "high": "h", "low": "l",
    "close": "c", "volume": "v",
}


def candles_from_polygon_response(
    payload: dict[str, Any], *, interval: str,
) -> list[Candle]:
    """Map a parsed Polygon ``/aggs`` response to candles.

    Polygon's ``t`` is epoch **milliseconds (UTC)**; we convert to
    **US Eastern** (``core.timezones.ET``) so ``classify_session`` and the
    chart read the correct exchange wall-clock, matching yfinance / Alpaca /
    Schwab (otherwise the intraday session is shifted +5h).
    """
    if isinstance(payload, list):
        rows = payload
    else:
        rows = payload.get("results") or []
    return candles_from_json_rows(
        rows, interval=interval, keymap=_POLYGON_KEYMAP, ts_unit="ms", tz=ET,
    )


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


# Our interval → Polygon's (multiplier, timespan).
_INTERVAL_TO_POLYGON: dict[str, tuple[int, str]] = {
    "1m": (1, "minute"), "5m": (5, "minute"),
    "15m": (15, "minute"), "30m": (30, "minute"),
    "1h": (1, "hour"),
    "1d": (1, "day"), "1wk": (1, "week"), "1mo": (1, "month"),
}


_POLYGON_BASE = "https://api.polygon.io"


def fetch_polygon_data(
    ticker: str = "AAPL", interval: str = "1d",
    *, lookback_days: int | None = None,
) -> list[Candle] | None:
    """``DataFetcher``-compatible Polygon fetcher.

    Returns ``None`` on any error.
    """
    creds = get_credentials().polygon
    if not creds.is_configured():
        LOG.debug("polygon: not configured, skipping fetch")
        return None
    tf = _INTERVAL_TO_POLYGON.get(interval)
    if tf is None:
        LOG.warning("polygon: unsupported interval %r", interval)
        return None
    if lookback_days is None:
        lookback_days = provider_lookback_days("polygon", interval)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback_days)
    try:
        payload = _http_get_aggs(ticker, tf, start.isoformat(), end.isoformat(), creds)
    except Exception as exc:  # pragma: no cover - network path
        LOG.warning("polygon: fetch failed for %s %s: %s", ticker, interval, exc)
        return None
    return candles_from_polygon_response(payload, interval=interval)


def _http_get_aggs(
    ticker: str, timeframe: tuple[int, str], start: str, end: str,
    creds: PolygonCredentials,
) -> dict[str, Any]:  # pragma: no cover - network path
    multiplier, timespan = timeframe
    url = (
        f"{_POLYGON_BASE}/v2/aggs/ticker/{urllib.parse.quote(ticker)}"
        f"/range/{multiplier}/{timespan}/{start}/{end}?"
        + urllib.parse.urlencode({
            "adjusted": "true", "sort": "asc", "limit": "50000",
        })
    )
    # Authenticate via the bearer header (NOT the apiKey query param).
    # The query-string form lands in URLError.__str__() on transient
    # network errors, which then lands in our daily status log, which
    # then lands in any diagnostic bundle the user ships to support —
    # leaking the bearer token in cleartext. Header-based auth is
    # never echoed by urllib's exception reprs.
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {creds.api_key or ''}",
        "Accept": "application/json",
    })
    with credentialed_opener().open(req, timeout=15) as resp:
        return json.loads(resp.read(MAX_RESPONSE_BYTES).decode("utf-8"))
