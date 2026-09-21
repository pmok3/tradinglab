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

Because Alpaca's contribution is sealed history, the deep leg is
reused from the on-disk ``alpaca`` cache after the first fetch, so the live
poll never re-paginates Alpaca — with one exception: the cached tail is
revalidated against the fresh yfinance leg on overlapping bars every fetch.
Both vendors restate history retroactively after a stock split, so a
wholesale price-scale disagreement there means the cache is pre-split and
is refetched + re-saved (otherwise the merged series would keep a permanent
artificial price cliff at the seam).

Registered as :data:`HYBRID_SOURCE_NAME` (``"yfinance+alpaca"``) in
:data:`DATA_SOURCES` only when Alpaca credentials are configured (yfinance is
always available). Registered period-style (no ``supports_range``): the
trailing fetch returns the full merged series.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

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
DeepSaver = Callable[[str, str, "list[Candle]"], None]

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


def _deep_leg_restated(cached: list[Candle], recent: list[Candle]) -> bool:
    """True if the cached deep leg looks restated vs the fresh recent leg.

    Compares ``close`` on timestamps present in both legs. A stock split (or
    any wholesale provider restatement) rescales the fresh leg's history, so
    the median fresh/cached close ratio jumps far from 1.0; ordinary
    cross-vendor noise and dividend drift stay near 1.0. Returns False when
    the legs share too few timestamps to judge. Never raises.
    """
    try:
        recent_close = {c.date: c.close for c in recent}
        ratios = []
        for c in cached:
            fresh = recent_close.get(c.date)
            if fresh and c.close:
                ratios.append(fresh / c.close)
        if len(ratios) < _MIN_RESTATED_OVERLAP:
            return False
        ratios.sort()
        median = ratios[len(ratios) // 2]
        return median < _RESTATED_RATIO_LO or median > _RESTATED_RATIO_HI
    except Exception:  # noqa: BLE001
        return False


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


def _default_deep_saver(ticker: str, interval: str, candles: list[Candle]) -> None:
    from .. import disk_cache

    disk_cache.save(_DEEP_SOURCE, ticker, interval, candles)


def _resolve_deep_leg(
    ticker: str,
    interval: str,
    *,
    deep_fetcher: CandleFetcher,
    deep_loader: DeepLoader,
    deep_saver: DeepSaver,
    recent: list[Candle] | None = None,
) -> list[Candle]:
    """Return Alpaca's deep-history bars, reusing the disk cache when present.

    Alpaca's contribution is the OLD tail (yfinance owns the recent window),
    which is sealed history — so a cached copy is authoritative and the slow
    paginated network fetch is paid only on a cold miss, UNLESS the cache
    fails revalidation: both vendors restate history retroactively after a
    stock split, so when the fresh ``recent`` leg disagrees wholesale with
    the cached tail on overlapping bars (see :func:`_deep_leg_restated`),
    the cache is pre-split and is refetched + re-saved. Without this the
    merged series would keep a permanent artificial price cliff at the seam.

    This keeps the live poll cheap: each tick refetches only the yfinance
    leg plus an in-memory overlap comparison, and re-paginates Alpaca only
    when a restatement is actually detected. Never raises.
    """
    try:
        cached = deep_loader(ticker, interval)
    except Exception:  # noqa: BLE001
        cached = None
    if cached and not (recent and _deep_leg_restated(cached, recent)):
        return cached
    if cached:
        LOG.info(
            "hybrid: deep leg for %s %s looks restated (split?) — refetching",
            ticker, interval,
        )
    try:
        fetched = deep_fetcher(ticker, interval) or []
    except Exception:  # noqa: BLE001
        fetched = []
    if fetched:
        try:
            deep_saver(ticker, interval, fetched)
        except Exception:  # noqa: BLE001
            pass
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

    try:
        recent = recent_fetcher(ticker, interval)
    except Exception:  # noqa: BLE001
        recent = None
    recent_list = recent or []

    deep = _resolve_deep_leg(
        ticker,
        interval,
        deep_fetcher=deep_fetcher,
        deep_loader=deep_loader,
        deep_saver=deep_saver,
        recent=recent_list,
    )

    if not deep and not recent_list:
        # Nothing from either leg: preserve the "None = hard failure" signal
        # when yfinance itself failed; otherwise an empty list (valid: no data).
        return None if recent is None else []
    if not deep:
        return recent_list
    if not recent_list:
        return deep
    return merge_prefer_recent(deep, recent_list)


__all__ = ["HYBRID_SOURCE_NAME", "fetch_hybrid_data", "merge_prefer_recent"]
