"""Alpaca Market Data v2 → ``List[Candle]``.

Two-layer module mirroring :mod:`schwab_source`:

1. :func:`candles_from_alpaca_response` — pure mapper, offline-testable.
2. :func:`fetch_alpaca_data` — HTTP fetcher gated on credentials.

Alpaca uses static API key + secret (no OAuth dance), so the fetcher
is a straight ``urllib`` request with two headers. Pagination is
handled via the ``next_page_token`` field returned by the API.

Reference response shape (``/v2/stocks/{symbol}/bars``)::

    {
      "bars": [
        {"t": "2024-03-07T14:30:00Z", "o": 175.0, "h": 175.5,
         "l": 174.8, "c": 175.2, "v": 1234567, "n": 1500, "vw": 175.1}
      ],
      "symbol": "AAPL",
      "next_page_token": null
    }
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from ..constants import provider_lookback_days
from ..models import Candle
from ._http import MAX_RESPONSE_BYTES, credentialed_opener
from .credentials import AlpacaCredentials, get_credentials
from .normalize import candles_from_json_rows

LOG = logging.getLogger(__name__)


_ALPACA_KEYMAP = {
    "ts": "t",
    "open": "o", "high": "h", "low": "l",
    "close": "c", "volume": "v",
}


def candles_from_alpaca_response(
    payload: dict[str, Any], *, interval: str,
) -> list[Candle]:
    """Map a parsed Alpaca ``/bars`` response to candles."""
    if isinstance(payload, list):
        rows = payload
    else:
        rows = payload.get("bars") or []
    return candles_from_json_rows(
        rows, interval=interval, keymap=_ALPACA_KEYMAP, ts_unit="iso",
    )


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


# Our interval string → Alpaca's "timeframe" param.
_INTERVAL_TO_ALPACA = {
    "1m": "1Min", "5m": "5Min", "15m": "15Min", "30m": "30Min",
    "1h": "1Hour", "1d": "1Day", "1wk": "1Week", "1mo": "1Month",
}


_ALPACA_BASE = "https://data.alpaca.markets"


def fetch_alpaca_data(
    ticker: str = "AAPL", interval: str = "1d",
    *, lookback_days: int | None = None,
) -> list[Candle] | None:
    """``DataFetcher``-compatible Alpaca fetcher.

    Returns ``None`` on any error. Default lookback windows come from
    :func:`constants.provider_lookback_days` — Alpaca has no yfinance-style
    60-day intraday cap, so intraday reaches years back and daily reaches
    Alpaca's full IEX history (~2016; the server caps to plan availability).
    """
    creds = get_credentials().alpaca
    if not creds.is_configured():
        LOG.debug("alpaca: not configured, skipping fetch")
        return None
    timeframe = _INTERVAL_TO_ALPACA.get(interval)
    if timeframe is None:
        LOG.warning("alpaca: unsupported interval %r", interval)
        return None
    if lookback_days is None:
        lookback_days = provider_lookback_days("alpaca", interval)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    try:
        payload = _accumulate_bars(
            lambda token: _http_get_page(ticker, timeframe, start, end, creds, token)
        )
    except Exception as exc:  # pragma: no cover - network path
        LOG.warning("alpaca: fetch failed for %s %s: %s", ticker, interval, exc)
        return None
    return candles_from_alpaca_response(payload, interval=interval)


#: Hard cap on pages walked per fetch. 200 pages x 10 000 bars = 2 000 000
#: bars — orders of magnitude beyond any interval/lookback we request, so
#: hitting it means the vendor is mis-paginating (never-null token) and we
#: bail rather than loop forever.
_MAX_PAGES = 200


def _accumulate_bars(
    fetch_page: Callable[[str | None], dict[str, Any]],
    *,
    max_pages: int = _MAX_PAGES,
) -> dict[str, Any]:
    """Walk Alpaca ``next_page_token`` pagination into one ``{"bars": [...]}``.

    ``fetch_page(page_token) -> payload`` is injected so the pagination
    loop is unit-testable offline. Stops when the payload has no
    ``next_page_token`` (last page), a non-dict payload comes back, or
    ``max_pages`` is exceeded.
    """
    all_bars: list[Any] = []
    token: str | None = None
    for _ in range(max_pages):
        payload = fetch_page(token)
        if not isinstance(payload, dict):
            break
        all_bars.extend(payload.get("bars") or [])
        token = payload.get("next_page_token") or None
        if not token:
            break
    else:  # pragma: no cover - safety cap; real fetches terminate on token
        LOG.warning("alpaca: pagination hit %d-page cap; result truncated",
                    max_pages)
    return {"bars": all_bars}


def _http_get_page(
    ticker: str, timeframe: str, start: datetime, end: datetime,
    creds: AlpacaCredentials, page_token: str | None,
) -> dict[str, Any]:  # pragma: no cover - network path
    params = {
        "timeframe": timeframe,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "limit": "10000",
        "adjustment": "raw",
        "feed": creds.feed,
    }
    if page_token:
        params["page_token"] = page_token
    url = f"{_ALPACA_BASE}/v2/stocks/{urllib.parse.quote(ticker)}/bars?" + \
        urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": creds.api_key_id or "",
        "APCA-API-SECRET-KEY": creds.api_secret_key or "",
        "Accept": "application/json",
    })
    with credentialed_opener().open(req, timeout=15) as resp:
        return json.loads(resp.read(MAX_RESPONSE_BYTES).decode("utf-8"))
