"""The "Auto" data source — resolves to the globally best available source.

Selecting **Auto** (the startup default) means "use the best real source I have
configured, per the global tier-aware priority" (``data/source_ranking.py`):

    alpaca (paid)  >  schwab  >  yfinance  >  alpaca (free)

Auto is registered as a first-class delegating source (like the hybrid): the
whole app keys off ``source_var == "Auto"`` — cache keys, drilldown, prefetch,
persistence — and :func:`fetch_auto_data` resolves + delegates to the concrete
best source at fetch time (dynamic ``DATA_SOURCES`` lookup so a test stub or a
newly-registered vendor is picked up automatically). Extensible: adding Schwab
later needs no change here — it slots into the ranking and Auto starts choosing
it once registered.

Auto is **always live-capable** by construction: the free/IEX Alpaca feed ranks
below yfinance, and yfinance is always available, so Auto never resolves to
free-Alpaca-as-a-live-source. It resolves to paid-Alpaca (SIP), yfinance, or the
yfinance+alpaca composite — all real-time on their live edge.
"""
from __future__ import annotations

from typing import Any

from ..models import Candle

#: The registry key + user-facing dropdown label for the auto-select source.
AUTO_SOURCE_NAME = "Auto"

#: Ultimate fallback when nothing else is registered (yfinance is always on).
_FALLBACK_SOURCE = "yfinance"


def resolve_auto_source(*, candidates: list[str] | None = None) -> str:
    """Return the concrete source ``"Auto"`` currently resolves to.

    The globally best real source (via ``source_ranking.best_source``) among the
    user-visible candidates, **excluding "Auto" itself** (so it never recurses)
    and any internal source (already filtered by ``user_visible_sources``).
    Falls back to :data:`_FALLBACK_SOURCE` when no real source is available.
    ``candidates`` defaults to ``data.base.user_visible_sources()``.
    """
    from .base import user_visible_sources
    from .source_ranking import best_source

    if candidates is None:
        candidates = user_visible_sources()
    reals = [s for s in candidates if s and s != AUTO_SOURCE_NAME]
    return best_source(reals) or _FALLBACK_SOURCE


def fetch_auto_data(
    ticker: str = "AAPL", interval: str = "1d", **_ignored: Any,
) -> list[Candle] | None:
    """``DataFetcher`` for ``"Auto"`` — delegate to the resolved best source.

    Resolves the concrete source per :func:`resolve_auto_source` and dispatches
    through the live ``DATA_SOURCES`` registry (so a test stub or a
    freshly-registered vendor is honoured). Extra kwargs (a stray range
    ``start`` / ``end``) are ignored — Auto is registered period-style. Returns
    the delegate's result verbatim (``None`` on a hard failure), so the app's
    usual handling is unchanged. Never raises.
    """
    from .base import DATA_SOURCES

    best = resolve_auto_source()
    if best == AUTO_SOURCE_NAME:  # defensive: never dispatch to ourselves
        best = _FALLBACK_SOURCE
    fetcher = DATA_SOURCES.get(best)
    if fetcher is None:
        return None
    try:
        return fetcher(ticker, interval)
    except Exception:  # noqa: BLE001
        return None


__all__ = ["AUTO_SOURCE_NAME", "resolve_auto_source", "fetch_auto_data"]
