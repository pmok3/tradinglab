"""Hybrid data source: yfinance (recent + live) stitched over Alpaca (deep).

A single continuous OHLCV series that gives a completely-free user the best of
both providers:

* **yfinance** — real-time and carries *full consolidated* volume, but caps
  intraday history at ~60 days.
* **Alpaca (free/IEX)** — reaches back to ~2016 intraday, but its real-time is
  15-min delayed and it carries only *partial* (IEX) volume.

The two legs are merged with **yfinance winning every overlapping bar** (higher
volume quality — the user's rule). Consequences of that single rule:

* the recent / visible window is **pure yfinance** — full volume AND
  live-pollable (the live poll refetches only the yfinance leg), and
* Alpaca only contributes the deep tail **older than yfinance's oldest bar**,
  which yfinance can't reach.

The deep cache is reused only while fresh overlap can validate its price
basis. Missing overlap or inconsistent prices trigger a checked replacement.
Until it succeeds, only verified recent bars are published, with a visible
notice and bounded retries; derived caches cannot restore the rejected tail.

Registered as :data:`HYBRID_SOURCE_NAME` (``"yfinance+alpaca"``) in
:data:`DATA_SOURCES` only when Alpaca credentials are configured (yfinance is
always available). Registered period-style (no ``supports_range``): the
trailing fetch returns the full merged series.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..core.lru_dict import LRUDict
from ..models import Candle
from .alpaca_source import fetch_alpaca_data
from .ratio_source import parse_ratio_symbol
from .yfinance_source import fetch_live_data

LOG = logging.getLogger(__name__)

#: Source key this composite registers under (shown verbatim in the toolbar
#: source dropdown — there is no display-name layer).
HYBRID_SOURCE_NAME = "yfinance+alpaca"

#: Underlying source backing the DEEP (historical tail) leg. Used as the
#: disk-cache key so Alpaca's immutable history isn't re-paginated per poll.
_DEEP_SOURCE = "alpaca"

CandleFetcher = Callable[..., "list[Candle] | None"]
DeepLoader = Callable[[str, str], "list[Candle] | None"]
DeepSaver = Callable[[str, str, "list[Candle]"], bool | None]

#: Median fresh/cached close ratio outside this band means the cached deep
#: leg was restated wholesale (e.g. a stock split) and must be refetched.
#: Splits are large discrete rescalings — the smallest common forward split
#: is 3:2 (a 0.667 ratio) — while dividend drift and cross-vendor noise stay
#: well inside this band, so a breach is a price-scale change, not noise.
_RESTATED_RATIO_LO = 0.8
_RESTATED_RATIO_HI = 1.25

#: Minimum overlapping bars before the restatement check trusts its median
#: (too few bars and one noisy print could force a needless refetch).
_MIN_RESTATED_OVERLAP = 5
_RETRY_INITIAL_SECONDS = 60
_RETRY_MAX_SECONDS = 1800
_FETCH_LOCKS = tuple(threading.RLock() for _ in range(32))
_RECOVERY_LOCK = threading.RLock()


@dataclass
class _Recovery:
    quarantined: bool = False
    failures: int = 0
    retry_at: float = 0.0
    verified: list[Candle] | None = None


_RECOVERY: LRUDict[tuple[str, str, str], _Recovery] = LRUDict(maxsize=128)


def _overlap_ratios(cached: list[Candle], recent: list[Candle]) -> list[float]:
    recent_close = {c.date: c.close for c in recent}
    ratios = []
    for date, close in {c.date: c.close for c in cached}.items():
        fresh = recent_close.get(date)
        if fresh is not None and math.isfinite(fresh) and math.isfinite(close) and fresh > 0 and close > 0:
            ratio = fresh / close
            if math.isfinite(ratio) and ratio > 0:
                ratios.append(ratio)
    return ratios


def _deep_leg_compatible(cached: list[Candle], recent: list[Candle]) -> bool:
    ratios = _overlap_ratios(cached, recent)
    return len(ratios) >= _MIN_RESTATED_OVERLAP and all(
        _RESTATED_RATIO_LO <= ratio <= _RESTATED_RATIO_HI for ratio in ratios
    )


def _deep_leg_restated(cached: list[Candle], recent: list[Candle]) -> bool:
    """True if the cached deep leg looks restated vs the fresh recent leg.

    Compares ``close`` on timestamps present in both legs. A stock split (or
    any wholesale provider restatement) rescales the fresh leg's history, so
    the median fresh/cached close ratio jumps far from 1.0; ordinary
    cross-vendor noise and dividend drift stay near 1.0. Returns False when
    the legs share too few timestamps to judge. Never raises.
    """
    ratios = sorted(_overlap_ratios(cached, recent))
    if len(ratios) < _MIN_RESTATED_OVERLAP:
        return False
    median = ratios[len(ratios) // 2]
    coherent = sum(abs(ratio / median - 1) <= 0.05 for ratio in ratios) >= 0.9 * len(ratios)
    return coherent and (median < _RESTATED_RATIO_LO or median > _RESTATED_RATIO_HI)


def merge_prefer_recent(
    deep: list[Candle] | None, recent: list[Candle] | None,
) -> list[Candle]:
    """Merge a deep-history leg with a recent leg; **recent wins on overlap**.

    Thin, intent-revealing wrapper over :func:`disk_cache.merge_candles`
    ("new wins on duplicate date", keeps both sides' non-overlapping bars).
    Passing ``recent`` as ``new`` makes the yfinance leg win every bar it also
    has (full consolidated volume), while Alpaca's older-than-yfinance tail is
    retained. Both legs arrive date-ascending from their fetchers, so
    ``presorted=True``.
    """
    from .. import disk_cache

    return disk_cache.merge_candles(deep, recent, presorted=True)


def _default_deep_loader(ticker: str, interval: str) -> list[Candle] | None:
    from .. import disk_cache

    return disk_cache.load(_DEEP_SOURCE, ticker, interval)


def _default_deep_saver(ticker: str, interval: str, candles: list[Candle]) -> bool | None:
    from .. import disk_cache

    return disk_cache.save(_DEEP_SOURCE, ticker, interval, candles)


def _persist_verified_deep(
    ticker: str, interval: str, fetched: list[Candle], recovery: _Recovery, saver: DeepSaver,
) -> None:
    from .. import disk_cache

    try:
        saved = saver(ticker, interval, fetched) is not False
    except Exception:  # noqa: BLE001
        LOG.warning("hybrid: deep cache save failed for %s/%s", ticker, interval, exc_info=True)
        saved = False
    if saved:
        recovery.verified = None
        recovery.failures = 0
        recovery.retry_at = 0.0
        disk_cache.confirm_history(ticker, interval)
    else:
        recovery.verified = fetched
        recovery.failures = min(recovery.failures + 1, 6)
        delay = min(_RETRY_INITIAL_SECONDS * 2 ** (recovery.failures - 1), _RETRY_MAX_SECONDS)
        recovery.retry_at = time.monotonic() + delay
        LOG.warning("hybrid: verified deep history for %s/%s could not be saved; retry in %ss",
                    ticker, interval, delay)


def _resolve_deep_leg(
    ticker: str,
    interval: str,
    *,
    deep_fetcher: CandleFetcher,
    deep_loader: DeepLoader,
    deep_saver: DeepSaver,
    recovery: _Recovery,
    recent: list[Candle] | None = None,
) -> list[Candle]:
    """Reuse verified history or retry a replacement without publishing guesses."""
    from .. import disk_cache

    def quarantine(reason: str) -> None:
        if not recovery.quarantined:
            recovery.quarantined = True
            recovery.verified = None
            disk_cache.invalidate_history(ticker, interval)
            LOG.warning("hybrid: withholding deep history for %s/%s: %s", ticker, interval, reason)

    try:
        cached = recovery.verified or deep_loader(ticker, interval)
    except Exception:  # noqa: BLE001
        LOG.warning("hybrid: deep cache read failed for %s/%s", ticker, interval, exc_info=True)
        cached = None
    if cached:
        if not recovery.quarantined and (not recent or _deep_leg_compatible(cached, recent)):
            if recovery.verified is not None and time.monotonic() >= recovery.retry_at:
                _persist_verified_deep(ticker, interval, cached, recovery, deep_saver)
            return cached
        quarantine(
            "coherent price restatement" if recent and _deep_leg_restated(cached, recent)
            else "insufficient or inconsistent overlap"
        )
    if time.monotonic() < recovery.retry_at:
        return []
    try:
        fetched = deep_fetcher(ticker, interval) or []
    except Exception:  # noqa: BLE001
        LOG.warning("hybrid: deep refetch failed for %s/%s", ticker, interval, exc_info=True)
        fetched = []
    verified = bool(fetched) and (
        _deep_leg_compatible(fetched, recent) if recent else not recovery.quarantined
    )
    if not verified:
        quarantine("replacement unavailable or not yet on a verifiable price basis")
        recovery.failures = min(recovery.failures + 1, 6)
        delay = min(_RETRY_INITIAL_SECONDS * 2 ** (recovery.failures - 1), _RETRY_MAX_SECONDS)
        recovery.retry_at = time.monotonic() + delay
        LOG.warning("hybrid: deep history for %s/%s remains withheld; retry in %ss", ticker, interval, delay)
        return []
    recovery.quarantined = False
    recovery.failures = 0
    recovery.retry_at = 0.0
    _persist_verified_deep(ticker, interval, fetched, recovery, deep_saver)
    return fetched


def fetch_hybrid_data(
    ticker: str = "AAPL",
    interval: str = "1d",
    *,
    recent_fetcher: CandleFetcher | None = None,
    deep_fetcher: CandleFetcher | None = None,
    deep_loader: DeepLoader | None = None,
    deep_saver: DeepSaver | None = None,
    **_ignored: Any,
) -> list[Candle] | None:
    """Fetch one continuous series: yfinance (recent + live) over Alpaca (deep).

    ``recent_fetcher`` / ``deep_fetcher`` / ``deep_loader`` / ``deep_saver`` are
    injectable seams for offline tests; production defaults are yfinance,
    Alpaca, and the ``alpaca``-keyed disk cache. Extra kwargs (e.g. a stray
    ``start`` / ``end`` from a range-capable call site) are ignored — this
    source is registered period-style.

    Ratio pseudo-symbols (``AMD/NVDA``) short-circuit to the yfinance leg only
    (Alpaca has no ratio concept), matching yfinance's own ratio behaviour.

    Returns the merged list (possibly empty). Returns ``None`` only when the
    yfinance leg hard-failed (``None``) AND Alpaca yielded nothing — so the
    app's usual "``None`` = failed fetch" handling still fires; an Alpaca-only
    result (yfinance down but deep history present) is returned as data.
    """
    ticker = ticker.strip().upper()
    recent_fetcher = recent_fetcher or fetch_live_data
    deep_fetcher = deep_fetcher or fetch_alpaca_data
    deep_loader = deep_loader or _default_deep_loader
    deep_saver = deep_saver or _default_deep_saver

    # Ratio pseudo-symbols are a yfinance-leg concept; Alpaca can't resolve
    # them, so skip the deep leg entirely (avoids a wasted 404 fetch).
    if parse_ratio_symbol(ticker) is not None:
        try:
            return recent_fetcher(ticker, interval)
        except Exception:  # noqa: BLE001
            return None

    from .. import disk_cache

    # Fixed lock stripes bound synchronization state and serialize a key's
    # recent/deep observations, including calls made through Auto.
    with _FETCH_LOCKS[hash((ticker, interval)) % len(_FETCH_LOCKS)]:
        key = (str(disk_cache._cache_dir()), ticker, interval)
        with _RECOVERY_LOCK:
            recovery = _RECOVERY.get(key)
            if recovery is None:
                recovery = _Recovery(quarantined=disk_cache.history_pending(ticker, interval))
                _RECOVERY[key] = recovery
        try:
            recent = recent_fetcher(ticker, interval)
        except Exception:  # noqa: BLE001
            LOG.warning("hybrid: recent fetch failed for %s/%s", ticker, interval, exc_info=True)
            recent = None
        recent_list = recent or []
        deep = _resolve_deep_leg(
            ticker, interval, deep_fetcher=deep_fetcher, deep_loader=deep_loader,
            deep_saver=deep_saver, recovery=recovery, recent=recent_list,
        )
        notice = None
        if recovery.quarantined:
            delay = max(0, math.ceil(recovery.retry_at - time.monotonic()))
            notice = f"{ticker}: deep history withheld; showing verified recent bars only. Retry in {delay}s."
        result = disk_cache.history_snapshot(
            ticker, interval, merge_prefer_recent(deep, recent_list), notice=notice,
        )
        if not result and recent is None:
            return None
        return result


__all__ = ["HYBRID_SOURCE_NAME", "fetch_hybrid_data", "merge_prefer_recent"]
