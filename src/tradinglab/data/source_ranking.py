"""Global data-source priority ranking — the authoritative preference order.

A single, fixed, **tier-aware** order of which registered source the app should
prefer when several are available. The owner's stated order:

    alpaca (paid)  >  schwab  >  yfinance  >  alpaca (free)

Alpaca appears at BOTH ends because its *tier* flips its quality: the paid
"Algo Trader Plus" SIP feed is full-volume, real-time and effectively unlimited
(the single best source), while the free "Basic" IEX feed is partial-volume and
15-minute delayed (the worst real market source). The tier is resolved live from
credentials (``alpaca_source.is_live_capable`` — paid & not header-downgraded).

This module is the ONE source of truth for source preference. ``data/quality.py``
keeps the volume-quality metadata + partial-volume warning; its ranking helpers
now delegate here. Consumers: the sandbox source chooser
(``quality.preferred_source`` → here) and any "best available source" decision.

Sources not named in :data:`GLOBAL_SOURCE_PRIORITY` (a BYOD/local source, a
future vendor, the internal synthetic sources) fall into a single trailing band,
ordered by name for determinism — so a new source ranks sensibly without editing
this list.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

#: The authoritative global priority, **best first**. Entries are *tier-resolved
#: tokens* (see :func:`resolve_priority_token`), not raw source names, because
#: Alpaca's rank depends on its tier.
#:
#: The owner specified the four core sources (``alpaca@paid`` > ``schwab`` >
#: ``yfinance`` > ``alpaca@free``). ``polygon`` and the ``yfinance+alpaca``
#: composite are slotted into that spine by quality: polygon is a full-volume
#: deep vendor (peer of schwab, below it on the adjusted tiebreak); the hybrid's
#: live edge is full-volume yfinance PLUS Alpaca's deep tail, so it is never
#: worse than plain yfinance → ranked just above it. Reorder freely — this tuple
#: is the single knob.
GLOBAL_SOURCE_PRIORITY: tuple[str, ...] = (
    "alpaca@paid",       # owner #1 — SIP: full volume, real-time, unlimited
    "schwab",            # owner #2 — full volume + deep history (adjusted)
    "polygon",           # (inferred) full volume + deep history (raw)
    "yfinance+alpaca",   # (inferred) hybrid: yfinance live edge + Alpaca deep tail
    "yfinance",          # owner #3 — full volume, ~60-day intraday cap
    "alpaca@free",       # owner #4 — IEX: partial volume, 15-min delayed
)

_PRIORITY_INDEX: dict[str, int] = {tok: i for i, tok in enumerate(GLOBAL_SOURCE_PRIORITY)}

#: Rank assigned to any token not in the explicit priority (BYOD / future
#: vendor / synthetic) — after every named source, tie-broken by name.
_UNLISTED_RANK: int = len(GLOBAL_SOURCE_PRIORITY)

#: Tier-resolved tokens for Alpaca.
_ALPACA_PAID_TOKEN = "alpaca@paid"
_ALPACA_FREE_TOKEN = "alpaca@free"


def _alpaca_is_paid() -> bool:
    """True when the configured Alpaca account is on the paid (SIP) tier.

    Reuses ``alpaca_source.is_live_capable`` — which is precisely
    ``tier == "paid" and not header-auto-detected-free`` — so a free key that a
    persisted ``tier="paid"`` can't rescue ranks as free, matching the rate/feed
    clamp. Lazy import keeps this module free of an import cycle and offline-
    testable (callers can inject ``alpaca_paid`` to bypass credentials). Never
    raises.
    """
    try:
        from .alpaca_source import is_live_capable

        return bool(is_live_capable())
    except Exception:  # noqa: BLE001
        return False


def resolve_priority_token(source: str, *, alpaca_paid: bool | None = None) -> str:
    """Map a registered source *name* to its tier-resolved priority *token*.

    Only Alpaca is tier-resolved (``"alpaca"`` → ``"alpaca@paid"`` /
    ``"alpaca@free"``); every other source is its own token. ``alpaca_paid``
    overrides the live credential check (for offline tests); ``None`` = resolve
    from credentials.
    """
    s = (source or "").strip().lower()
    if s == "alpaca":
        paid = _alpaca_is_paid() if alpaca_paid is None else bool(alpaca_paid)
        return _ALPACA_PAID_TOKEN if paid else _ALPACA_FREE_TOKEN
    return s


def global_rank(source: str, *, alpaca_paid: bool | None = None) -> int:
    """Global priority index for ``source`` (**lower = better**).

    Named sources return their position in :data:`GLOBAL_SOURCE_PRIORITY`;
    anything unlisted returns :data:`_UNLISTED_RANK` (after every named source).
    """
    token = resolve_priority_token(source, alpaca_paid=alpaca_paid)
    return _PRIORITY_INDEX.get(token, _UNLISTED_RANK)


def rank_sources(
    candidates: Iterable[str], *, alpaca_paid: bool | None = None
) -> list[str]:
    """Return ``candidates`` sorted best-first by the global priority.

    De-dupes case-insensitively (keeping the first-seen spelling) and breaks
    ties (equal rank, e.g. two unlisted sources) by lowercased name for a
    deterministic result.
    """
    seen: set[str] = set()
    uniq: list[str] = []
    for name in candidates:
        clean = (name or "").strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            uniq.append(clean)
    return sorted(uniq, key=lambda s: (global_rank(s, alpaca_paid=alpaca_paid), s.lower()))


def best_source(
    candidates: Iterable[str], *, alpaca_paid: bool | None = None
) -> str | None:
    """The single highest-priority source in ``candidates`` (or ``None``)."""
    ranked = rank_sources(candidates, alpaca_paid=alpaca_paid)
    return ranked[0] if ranked else None


def preferred_source(
    active_source: str,
    *,
    candidates: list[str] | None = None,
    alpaca_paid: bool | None = None,
    candidates_fn: Callable[[], list[str]] | None = None,
) -> str:
    """Best global source to load from, respecting explicit non-standard choices.

    Contract (unchanged from the historical ``quality.preferred_source``):

    * If ``active_source`` is NOT among the candidates (an internal
      ``synthetic`` source, a test stub, an unregistered name), it is returned
      unchanged — never override a deliberate offline/scaffolding choice.
    * Otherwise the globally best-ranked candidate (which includes
      ``active_source``) is returned — an upgrade among real, user-visible
      sources only.

    ``candidates`` defaults to ``data.base.user_visible_sources()``
    (override the resolver via ``candidates_fn`` for tests).
    """
    if candidates is None:
        if candidates_fn is not None:
            candidates = candidates_fn()
        else:
            from .base import user_visible_sources

            candidates = user_visible_sources()
    if active_source not in candidates:
        return active_source
    return best_source(candidates, alpaca_paid=alpaca_paid) or active_source


__all__ = [
    "GLOBAL_SOURCE_PRIORITY",
    "resolve_priority_token",
    "global_rank",
    "rank_sources",
    "best_source",
    "preferred_source",
]
