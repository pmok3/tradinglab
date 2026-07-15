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

Because Alpaca's contribution is immutable sealed history, the deep leg is
reused from the on-disk ``alpaca`` cache after the first fetch, so the live
poll never re-paginates Alpaca.

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
) -> list[Candle]:
    """Return Alpaca's deep-history bars, reusing the disk cache when present.

    Alpaca's contribution is the OLD tail (yfinance owns the recent window),
    which is immutable once sealed — so a cached copy is authoritative and the
    slow paginated network fetch is paid only on a cold miss. This keeps the
    live poll cheap: each tick refetches only the yfinance leg and reuses this
    cached tail. Never raises.
    """
    try:
        cached = deep_loader(ticker, interval)
    except Exception:  # noqa: BLE001
        cached = None
    if cached:
        return cached
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
