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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from ..constants import provider_lookback_days
from ..core.timezones import ET
from ..models import Candle
from ._http import MAX_RESPONSE_BYTES, credentialed_opener
from .credentials import AlpacaCredentials, get_credentials
from .normalize import candles_from_json_rows
from .prefetch.buckets import global_bucket_registry
from .rate_limiter import TokenBucket

LOG = logging.getLogger(__name__)


_ALPACA_KEYMAP = {
    "ts": "t",
    "open": "o", "high": "h", "low": "l",
    "close": "c", "volume": "v",
}

# Bar-price adjustment modes Alpaca's ``/bars`` endpoint accepts. ``split``
# is our default (see ``_resolve_adjustment``): un-split-adjusted ``raw``
# prices make a post-split chart look like a 90% crash (NVDA/AAPL etc.),
# which is jarring next to yfinance's auto-adjusted series. ``split`` fixes
# the split-cliff while leaving dividend-driven price levels intact; ``all``
# additionally back-adjusts dividends (closest to yfinance ``auto_adjust``).
_ALLOWED_ADJUSTMENTS = frozenset({"raw", "split", "dividend", "all"})

# Transient HTTP statuses worth retrying (429 rate-limit + 5xx). A 429 was
# previously swallowed by the caller's broad ``except`` → ``None`` →
# silent stale-cache fallback; now we honour ``Retry-After`` and back off.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_HTTP_RETRIES = 3
_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 8.0

# Light throttle BETWEEN paginated pages of a single fetch so a deep
# multi-page request (e.g. 1m history) doesn't burst the free "Basic"
# plan's 200-calls-per-MINUTE ceiling. Only applied between pages, never
# before the first page (single-page fetches — the common case — pay
# nothing). Injectable for tests.
_PAGE_PAUSE_S = 0.2


# --- Account-wide proactive rate limiting ---------------------------------
#
# Per-minute request budget by Alpaca plan tier. Free "Basic" = 200/min;
# paid "Algo Trader Plus" = 10,000/min. A single process-wide token bucket
# paces ALL Alpaca requests — interactive fetches AND the universe preloader
# — because every request funnels through `_http_get_page`. Proactive pacing
# keeps us under budget so the reactive `Retry-After` handling in
# `_request_with_retry` is only a rare-overshoot safety net (see the
# rate-limiting discussion in the spec).
_ALPACA_RATE_BY_TIER = {"free": 200, "paid": 10000}
_DEFAULT_TIER = "free"

# Shared bucket for the single Alpaca account. This IS the process-wide
# ``SourceBucketRegistry`` bucket for ``"alpaca"`` (Decision 1: one accounting
# gate for every fetch path — the direct fetch path here AND the background
# prefetch scheduler share it). Starts at the SAFE free rate; reconfigured live
# when the tier changes (free↔paid) so an upgrade takes effect without a restart.
_ALPACA_BUCKET = global_bucket_registry().bucket_for("alpaca")
_ALPACA_BUCKET_RATE = _ALPACA_RATE_BY_TIER[_DEFAULT_TIER]

# --- Auto-detect: downgrade to free tier from the X-RateLimit-Limit header ---
#
# Alpaca returns an ``X-RateLimit-Limit`` header on every data response (200
# on the free "Basic" plan, 10,000 on paid). If the user selected "Paid" but
# the account is actually free, the header reveals the true limit — so we
# clamp the bucket to 200 AND force the ``iex`` feed (a free key requesting
# ``feed=sip`` 403s on every call), then surface a one-shot popup so the user
# can fix the setting or upgrade. Detection is downward-only (never auto-raise
# a selected plan) and one-shot per process. Thread-safe: the observation runs
# on a fetch worker thread; the popup is shown on the Tk thread by the
# worker-inbox drain (see gui/polling.py) via ``pop_pending_downgrade_notice``.
_detect_lock = threading.Lock()
_detected_free = False
_pending_downgrade_notice: str | None = None


def _alpaca_rate_per_min(creds: AlpacaCredentials) -> int:
    """Per-minute request budget for ``creds``'s tier (default free/200).

    Capped at the free budget once a header-driven downgrade has been
    detected, so a persisted ``tier="paid"`` can't re-raise the bucket.
    """
    tier = (getattr(creds, "tier", None) or _DEFAULT_TIER).lower()
    rate = _ALPACA_RATE_BY_TIER.get(tier, _ALPACA_RATE_BY_TIER[_DEFAULT_TIER])
    if _detected_free:
        rate = min(rate, _ALPACA_RATE_BY_TIER["free"])
    return rate


def _alpaca_bucket_for(creds: AlpacaCredentials) -> TokenBucket:
    """Return the shared bucket, reconfiguring it if the tier's rate changed."""
    global _ALPACA_BUCKET_RATE
    rate = _alpaca_rate_per_min(creds)
    if rate != _ALPACA_BUCKET_RATE:
        _ALPACA_BUCKET.configure(rate)
        _ALPACA_BUCKET_RATE = rate
    return _ALPACA_BUCKET


def _observe_rate_limit_header(headers: Any) -> None:
    """Auto-detect a free-tier key from the ``X-RateLimit-Limit`` header.

    Called with the response headers of every Alpaca data request (success
    OR error — a free key sending ``feed=sip`` 403s but the 403 still carries
    the header). If the header reveals the free budget (≤200) while we were
    pacing for a paid plan, downgrade: clamp the shared bucket to 200, latch
    ``_detected_free`` (so ``_alpaca_rate_per_min`` + the feed override stick
    even with a persisted ``tier="paid"``), and record a one-shot popup
    notice. Downward-only + one-shot; never auto-raises. Never raises.
    """
    global _ALPACA_BUCKET_RATE, _detected_free, _pending_downgrade_notice
    if headers is None:
        return
    try:
        raw = headers.get("X-RateLimit-Limit")
    except Exception:  # noqa: BLE001 - be robust to odd header objects
        raw = None
    if raw is None:
        return
    try:
        observed = int(str(raw).strip())
    except (TypeError, ValueError):
        return
    if observed <= 0:
        return
    free_rate = _ALPACA_RATE_BY_TIER["free"]
    with _detect_lock:
        if _detected_free:
            return  # one-shot per process
        if observed <= free_rate and _ALPACA_BUCKET_RATE > free_rate:
            _detected_free = True
            _ALPACA_BUCKET.configure(free_rate)
            _ALPACA_BUCKET_RATE = free_rate
            _pending_downgrade_notice = (
                "Alpaca plan mismatch\n\n"
                "Your Alpaca API key is on the FREE tier "
                f"({free_rate} requests/min, IEX feed), but the data plan is "
                "set to Paid. TradingLab has switched to free-tier limits and "
                "the IEX feed for this session.\n\n"
                "Note: IEX volume is only a fraction of consolidated volume, "
                "so RVOL/RRVOL and the volume pane will be understated. Set "
                "the Alpaca data plan to Free in Settings → Credentials, or "
                "upgrade your Alpaca subscription for the SIP feed."
            )
            LOG.warning(
                "alpaca: X-RateLimit-Limit=%s reveals a FREE-tier key while "
                "'paid' was selected — downgraded to %d req/min + IEX feed.",
                observed, free_rate,
            )


def pop_pending_downgrade_notice() -> str | None:
    """Return + clear the one-shot free-tier-downgrade popup message (or None).

    Polled on the Tk thread by ``gui/polling._drain_worker_inbox`` so the
    popup is shown on the main thread (cross-thread Tk from the fetch worker
    is unsafe on this build).
    """
    global _pending_downgrade_notice
    with _detect_lock:
        msg = _pending_downgrade_notice
        _pending_downgrade_notice = None
        return msg


def _reset_tier_detection() -> None:
    """Test helper: clear latched detection state + restore the free bucket."""
    global _ALPACA_BUCKET_RATE, _detected_free, _pending_downgrade_notice
    with _detect_lock:
        _detected_free = False
        _pending_downgrade_notice = None
        _ALPACA_BUCKET.configure(_ALPACA_RATE_BY_TIER[_DEFAULT_TIER])
        _ALPACA_BUCKET_RATE = _ALPACA_RATE_BY_TIER[_DEFAULT_TIER]


def _to_alpaca_symbol(ticker: str) -> str:
    """Translate an in-app (yfinance-style) symbol to Alpaca's convention.

    The app normalizes every symbol to yfinance's **dash** form for share
    classes (``BRK-B``, ``BF-B``) — see ``preload.universe.normalize_symbols``
    and ``heatmap_provider`` — but Alpaca's stocks API expects a **dot**
    (``BRK.B``). Passing the dash form returns an empty result (the symbol
    simply doesn't exist on Alpaca), which previously made every share-class
    ticker silently un-fetchable on Alpaca. Plain symbols (``AMD``) are
    unchanged; ratio pseudo-symbols (containing ``/``) never reach here —
    they are decomposed leg-by-leg by the ratio-aware wrapper installed on
    every source at ``data.base.register_source`` (see
    ``ratio_source.fetch_ratio``), so this fetcher only ever sees single
    real symbols. A US-equity Alpaca symbol never contains a dash, so a
    straight dash→dot is safe.
    """
    return (ticker or "").strip().upper().replace("-", ".")


def _resolve_adjustment(creds: AlpacaCredentials) -> str:
    """Return a valid Alpaca ``adjustment`` value for ``creds`` (default ``split``).

    Reads ``creds.adjustment`` (configurable via the ``ALPACA_ADJUSTMENT``
    credential / env var); an unknown value falls back to ``split`` with a
    warning rather than sending a bad param that would 422 the request.
    """
    adj = (getattr(creds, "adjustment", None) or "split").lower()
    if adj not in _ALLOWED_ADJUSTMENTS:
        LOG.warning(
            "alpaca: invalid adjustment %r (allowed: %s); using 'split'",
            adj, sorted(_ALLOWED_ADJUSTMENTS),
        )
        return "split"
    return adj


def _retry_after_seconds(
    headers: Any, attempt: int,
    *, base: float = _BACKOFF_BASE_S, cap: float = _BACKOFF_CAP_S,
) -> float:
    """Seconds to wait before the next retry of a transient HTTP error.

    Honours an integer ``Retry-After`` response header (Alpaca returns one
    on 429) when present + parseable; otherwise exponential backoff
    ``base * 2**attempt`` capped at ``cap``. ``attempt`` is 0-based. Pure
    function — unit-tested offline.
    """
    ra: float | None = None
    try:
        if headers is not None:
            raw = headers.get("Retry-After")
            if raw is not None:
                ra = float(str(raw).strip())
    except (TypeError, ValueError):
        ra = None
    if ra is not None and ra >= 0:
        return min(ra, cap)
    return min(base * (2 ** attempt), cap)


def _request_with_retry(
    do_request: Callable[[], Any],
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_retries: int = _MAX_HTTP_RETRIES,
) -> Any:
    """Call ``do_request()`` with bounded retries on transient HTTP errors.

    ``do_request() -> dict`` performs ONE attempt (HTTP read + JSON parse)
    and raises ``urllib.error.HTTPError`` on an error status. Retries on
    429 / 5xx (:data:`_RETRYABLE_STATUS`), sleeping per
    :func:`_retry_after_seconds`; re-raises immediately on a non-retryable
    status or once ``max_retries`` is exhausted. ``sleep_fn`` is injected so
    the retry/backoff policy is unit-testable offline with no real sleeps.
    """
    attempt = 0
    while True:
        try:
            return do_request()
        except urllib.error.HTTPError as exc:
            # Observe the rate-limit header even on an error response — a
            # free key sending feed=sip 403s, but the 403 still reveals the
            # true tier (auto-detect / downgrade).
            _observe_rate_limit_header(getattr(exc, "headers", None))
            status = getattr(exc, "code", None)
            if status not in _RETRYABLE_STATUS or attempt >= max_retries:
                raise
            delay = _retry_after_seconds(getattr(exc, "headers", None), attempt)
            LOG.warning(
                "alpaca: HTTP %s (attempt %d/%d) — retrying in %.1fs",
                status, attempt + 1, max_retries, delay,
            )
            sleep_fn(delay)
            attempt += 1


def candles_from_alpaca_response(
    payload: dict[str, Any], *, interval: str,
) -> list[Candle]:
    """Map a parsed Alpaca ``/bars`` response to candles.

    Alpaca returns UTC ISO-8601 timestamps (``"…T14:30:00Z"``). We convert
    them to **US Eastern** (``core.timezones.ET``) so ``classify_session``
    and the chart x-axis read the correct exchange wall-clock — matching
    yfinance's exchange-localized index. Without this a 09:30 ET open bar
    (14:30Z) is mis-classified and the intraday session is shifted +5h (the
    "5m data only shows 14:30–16:00" bug). If ``ET`` is unavailable
    (missing ``tzdata``) we fall back to UTC rather than crash.
    """
    if isinstance(payload, list):
        rows = payload
    else:
        rows = payload.get("bars") or []
    return candles_from_json_rows(
        rows, interval=interval, keymap=_ALPACA_KEYMAP, ts_unit="iso", tz=ET,
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
    start: datetime | None = None, end: datetime | None = None,
) -> list[Candle] | None:
    """``DataFetcher``-compatible Alpaca fetcher.

    Returns ``None`` on any error. Without ``start``/``end`` it fetches a
    trailing window whose depth comes from
    :func:`constants.provider_lookback_days` — Alpaca has no yfinance-style
    60-day intraday cap, so intraday reaches years back and daily reaches
    Alpaca's full IEX history (~2016; the server caps to plan availability).

    Passing kw-only ``start`` / ``end`` (aware datetimes) fetches that
    **explicit range** instead — the targeted intraday fetch path (see
    ``docs/TARGETED_FETCH.md``). This is what marks Alpaca ``supports_range``.
    """
    creds = get_credentials().alpaca
    if not creds.is_configured():
        LOG.debug("alpaca: not configured, skipping fetch")
        return None
    timeframe = _INTERVAL_TO_ALPACA.get(interval)
    if timeframe is None:
        LOG.warning("alpaca: unsupported interval %r", interval)
        return None
    if start is not None or end is not None:
        end_dt = end or datetime.now(timezone.utc)
        start_dt = start or (
            end_dt - timedelta(days=provider_lookback_days("alpaca", interval))
        )
    else:
        if lookback_days is None:
            lookback_days = provider_lookback_days("alpaca", interval)
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=lookback_days)
    try:
        api_symbol = _to_alpaca_symbol(ticker)
        payload = _accumulate_bars(
            lambda token: _http_get_page(api_symbol, timeframe, start_dt, end_dt, creds, token)
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
    sleep_fn: Callable[[float], None] = time.sleep,
    page_pause_s: float = _PAGE_PAUSE_S,
) -> dict[str, Any]:
    """Walk Alpaca ``next_page_token`` pagination into one ``{"bars": [...]}``.

    ``fetch_page(page_token) -> payload`` is injected so the pagination
    loop is unit-testable offline. Stops when the payload has no
    ``next_page_token`` (last page), a non-dict payload comes back, or
    ``max_pages`` is exceeded. A small ``page_pause_s`` throttle is applied
    BETWEEN successive pages (never before the first) so a deep multi-page
    fetch doesn't burst the 200-calls/min ceiling; ``sleep_fn`` is injected
    so tests run instantly.
    """
    all_bars: list[Any] = []
    token: str | None = None
    for i in range(max_pages):
        if i > 0 and page_pause_s > 0:
            sleep_fn(page_pause_s)
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
        "adjustment": _resolve_adjustment(creds),
        # Force IEX once a free-tier key has been auto-detected — a free key
        # requesting SIP 403s on every call. Otherwise honour the configured
        # (tier-derived or explicit) feed.
        "feed": "iex" if _detected_free else creds.feed,
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

    def _do_request() -> dict[str, Any]:
        # Proactive pacing: acquire ONE token per HTTP attempt (Decision 1 —
        # "token = 1 HTTP request"). Placed inside the attempt (not once before
        # the retry loop) so each paginated page AND each 429/5xx retry spends a
        # token, keeping the shared budget honest under retries.
        _alpaca_bucket_for(creds).acquire()
        with credentialed_opener().open(req, timeout=15) as resp:
            # Observe the true tier from the response header (auto-detect).
            _observe_rate_limit_header(resp.headers)
            return json.loads(resp.read(MAX_RESPONSE_BYTES).decode("utf-8"))

    # Bounded retry on 429 / 5xx with Retry-After / exponential backoff; the
    # per-attempt token acquire above makes ``_request_with_retry`` purely
    # reactive backoff.
    return _request_with_retry(_do_request)


def _http_get_single_page(
    ticker: str, timeframe: str, creds: AlpacaCredentials, *,
    end: datetime | None, limit: int,
) -> dict[str, Any]:
    """ONE Alpaca bars page, newest-first (``sort=desc``), no pagination.

    The prefetch scheduler's page primitive (Option A). Deliberately does NOT
    acquire the shared rate bucket (the scheduler already spent the token in
    ``next_dispatch`` — single-owner rate/retry) and does NOT retry: a single
    GET that raises ``urllib.error.HTTPError`` on an error status so
    :func:`base.fetch_page` can surface the ``Retry-After`` to the scheduler,
    which owns the backoff. The rate-limit response header is still observed so
    free-tier auto-detect keeps working. ``end`` is exclusive; ``end=None`` →
    newest page.
    """
    params = {
        "timeframe": timeframe,
        "limit": str(int(limit)),
        "sort": "desc",
        "adjustment": _resolve_adjustment(creds),
        "feed": "iex" if _detected_free else creds.feed,
    }
    if end is not None:
        params["end"] = end.isoformat().replace("+00:00", "Z")
    url = f"{_ALPACA_BASE}/v2/stocks/{urllib.parse.quote(ticker)}/bars?" + \
        urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": creds.api_key_id or "",
        "APCA-API-SECRET-KEY": creds.api_secret_key or "",
        "Accept": "application/json",
    })
    try:
        with credentialed_opener().open(req, timeout=15) as resp:
            _observe_rate_limit_header(resp.headers)
            return json.loads(resp.read(MAX_RESPONSE_BYTES).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Observe the tier header even on error (auto-detect), then re-raise so
        # the scheduler owns retry/backoff via base.fetch_page.
        _observe_rate_limit_header(getattr(exc, "headers", None))
        raise


def fetch_alpaca_page(
    ticker: str, interval: str, *,
    end: datetime | None = None, limit: int = 10_000,
) -> list[Candle]:
    """One-HTTP-page Alpaca fetch: newest ``limit`` bars strictly before ``end``.

    The concrete primitive behind :func:`base.fetch_page` for Alpaca (registered
    via ``page_fetcher=``). Unlike :func:`fetch_alpaca_data` it does NOT
    paginate, retry, or acquire the rate bucket (see
    :func:`_http_get_single_page`). Returns candles **ascending** (``sort=desc``
    yields newest-first) so scheduler deepening reads ``bars[0]`` as the oldest
    bar. ``end=None`` → newest page. Returns ``[]`` on missing creds /
    unsupported interval; raises on HTTP error (the scheduler owns backoff).
    """
    creds = get_credentials().alpaca
    if not creds.is_configured():
        return []
    timeframe = _INTERVAL_TO_ALPACA.get(interval)
    if timeframe is None:
        LOG.warning("alpaca: unsupported interval %r for page fetch", interval)
        return []
    api_symbol = _to_alpaca_symbol(ticker)
    payload = _http_get_single_page(
        api_symbol, timeframe, creds, end=end, limit=limit,
    )
    candles = candles_from_alpaca_response(payload, interval=interval)
    candles.sort(key=lambda c: c.date)  # sort=desc → return ascending
    return candles
