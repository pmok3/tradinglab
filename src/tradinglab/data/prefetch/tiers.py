"""Relevance ladder + context snapshot for the prefetch scheduler.

A :class:`PrefetchContext` is a frozen snapshot of the app state the scheduler
needs to decide *what* to warm. A :class:`TierProvider` is a small, pure
``context -> symbols`` rule registered at a gap-spaced ``rank``; adding a tier
later is one registration (open/closed). :func:`expand_all` walks the providers
in rank order and produces band-0 :class:`FetchJob`s, applying:

* the shared **dual-interval** policy per symbol (on-screen + escape-hatch), and
* **dedup-by-highest-tier** — a symbol appearing in several tiers is emitted
  once, under its highest-priority (lowest-rank) tier.

Pure — no Tk / IO. See ``PREFETCH_SCHEDULER_DESIGN.md`` §5 and Decisions 5, 9, 15.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .intervals import dual_interval
from .priority import FetchJob

#: Standard tier ranks — gap-spaced so a future tier (e.g. recently-viewed MRU
#: at 25, open-position underlyings at 15) slots in without renumbering.
TIER_ACTIVE = 10
TIER_COMPARE = 20
TIER_FOCUSED_WL = 30
TIER_OTHER_WL = 40
TIER_UNIVERSE = 90


@dataclass(frozen=True)
class PrefetchContext:
    """Immutable snapshot of the app state that drives tier expansion."""

    source: str
    active_symbol: str
    active_interval: str
    compare_symbol: str = ""
    focused_watchlist: tuple[str, ...] = ()
    other_watchlists: tuple[str, ...] = ()
    universe: tuple[str, ...] = ()


#: ``(ctx, symbol) -> ordered intervals`` — the per-tier interval policy.
IntervalPolicy = Callable[[PrefetchContext, str], list[str]]
#: ``(ctx) -> symbols`` — a tier's symbol selector.
SymbolSelector = Callable[[PrefetchContext], Iterable[str]]


@dataclass(frozen=True)
class TierProvider:
    """A priority tier: which symbols are relevant, at which rank."""

    rank: int
    name: str
    symbols: SymbolSelector
    #: ``None`` -> shared dual-interval policy (Decision 15). Override for a
    #: tier that wants a different interval set (e.g. universe 1d-only).
    interval_policy: IntervalPolicy | None = None


def _default_policy(ctx: PrefetchContext, symbol: str) -> list[str]:
    return dual_interval(ctx.active_interval)


def standard_tiers() -> list[TierProvider]:
    """The five approved tiers (Decision 5 ladder), gap-ranked."""
    return [
        TierProvider(TIER_ACTIVE, "active", lambda c: [c.active_symbol]),
        TierProvider(TIER_COMPARE, "compare", lambda c: [c.compare_symbol]),
        TierProvider(TIER_FOCUSED_WL, "focused_watchlist",
                     lambda c: c.focused_watchlist),
        TierProvider(TIER_OTHER_WL, "other_watchlists",
                     lambda c: c.other_watchlists),
        TierProvider(TIER_UNIVERSE, "universe", lambda c: c.universe),
    ]


def expand_all(
    providers: Iterable[TierProvider],
    ctx: PrefetchContext,
    *,
    gen_of: Callable[[int], int] = lambda rank: 0,
) -> list[FetchJob]:
    """Expand every provider into band-0 jobs (dedup-by-highest-tier).

    Providers are visited in ascending ``rank``. Each symbol is normalized
    (upper/strip), skipped if blank or already claimed by a higher tier, then
    emitted once per interval from its tier's policy. ``gen_of(rank)`` stamps
    the per-tier generation onto that tier's jobs (Decision 3). Jobs carry a
    monotonic ``seq`` in emission order; the scheduler re-stamps a global ``seq``
    at enqueue to preserve FIFO across expansions.
    """
    claimed: set[str] = set()
    jobs: list[FetchJob] = []
    seq = 0
    for provider in sorted(providers, key=lambda p: p.rank):
        policy = provider.interval_policy or _default_policy
        generation = int(gen_of(provider.rank))
        for raw in provider.symbols(ctx):
            sym = (raw or "").strip().upper()
            if not sym or sym in claimed:
                continue
            claimed.add(sym)
            for interval_rank, iv in enumerate(policy(ctx, sym)):
                jobs.append(FetchJob(
                    source=ctx.source, symbol=sym, interval=iv,
                    band_index=0, tier_rank=provider.rank,
                    interval_rank=interval_rank, generation=generation,
                    seq=seq,
                ))
                seq += 1
    return jobs


__all__ = [
    "PrefetchContext", "TierProvider", "IntervalPolicy", "SymbolSelector",
    "standard_tiers", "expand_all",
    "TIER_ACTIVE", "TIER_COMPARE", "TIER_FOCUSED_WL", "TIER_OTHER_WL",
    "TIER_UNIVERSE",
]
