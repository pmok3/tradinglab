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

Because Auto's cache namespace is the opaque literal ``"Auto"``, nothing in a
cache key records WHICH concrete source produced the bars on screen. That is
what :func:`last_resolved_source` is for: every delegation records its target,
so the UI can notice that a credential save just changed Auto's answer and
reload instead of silently serving the old provider's cached series until the
next app restart.
"""
from __future__ import annotations

from typing import Any

from ..models import Candle

#: The registry key + user-facing dropdown label for the auto-select source.
AUTO_SOURCE_NAME = "Auto"

#: Ultimate fallback when nothing else is registered (yfinance is always on).
_FALLBACK_SOURCE = "yfinance"

#: Concrete source the most recent Auto delegation actually used — i.e. the
#: provenance of whatever Auto-keyed data is currently cached / on screen.
#: Seeded at boot by ``data/__init__`` and rewritten by every
#: :func:`fetch_auto_data` call. Plain module global: writes are single
#: assignments (atomic under the GIL) and come from fetch workers.
_last_resolved: str | None = None


def note_resolved_source(name: str | None) -> None:
    """Record ``name`` as the source Auto most recently resolved to."""
    global _last_resolved
    _last_resolved = name or None


def last_resolved_source() -> str | None:
    """Concrete source behind the currently-cached Auto data (``None`` if unknown).

    Compare against a fresh :func:`resolve_auto_source` to detect that Auto's
    answer moved — e.g. the user just saved Alpaca credentials, so the
    ``yfinance+alpaca`` composite outranks the plain yfinance series already
    cached under the ``"Auto"`` key.
    """
    return _last_resolved


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

    Records the delegate via :func:`note_resolved_source` **before** dispatch,
    so the recorded provenance matches the bars this call is about to cache
    even when the delegate errors out.
    """
    from .base import DATA_SOURCES

    best = resolve_auto_source()
    if best == AUTO_SOURCE_NAME:  # defensive: never dispatch to ourselves
        best = _FALLBACK_SOURCE
    note_resolved_source(best)
    fetcher = DATA_SOURCES.get(best)
    if fetcher is None:
        return None
    try:
        return fetcher(ticker, interval)
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "AUTO_SOURCE_NAME",
    "resolve_auto_source",
    "fetch_auto_data",
    "last_resolved_source",
    "note_resolved_source",
]
