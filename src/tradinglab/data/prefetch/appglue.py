"""Pure ChartApp-integration glue for the prefetch scheduler.

Small, Tk-free helpers that the (flagged) ``ChartApp`` wiring uses so the
app-coupled surface stays thin and testable:

* :func:`scheduler_enabled` / :func:`scheduler_mode` — the feature flag
  (``TRADINGLAB_PREFETCH_SCHEDULER`` env var). Default ON in live mode;
  ``off``/``0``/``false``/``no`` disable; ``shadow`` → observe-only.
* :func:`partition_watchlists` — split the pinned watchlists into the focused
  (active sub-tab) tier and the other-visible tier, deduped.
* :func:`build_context` — normalize app state into a :class:`PrefetchContext`.

See ``PREFETCH_SCHEDULER_DESIGN.md`` (Decisions 6 flag, 13 config) + review.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterable

from .buckets import (
    SourceBucketRegistry,
    global_bucket_registry,
    unlimited_bucket_registry,
)
from .tiers import PrefetchContext

_FLAG_ENV = "TRADINGLAB_PREFETCH_SCHEDULER"
_DISABLED_VALUES = frozenset({"0", "off", "false", "no"})
_SHADOW_VALUES = frozenset({"shadow"})


def _flag_value() -> str:
    return os.environ.get(_FLAG_ENV, "").strip().lower()


def scheduler_enabled() -> bool:
    """True when the background prefetch scheduler is enabled (default True)."""
    return _flag_value() not in _DISABLED_VALUES


def scheduler_mode() -> str:
    """``"shadow"`` when requested, else ``"live"`` (cut-over default)."""
    return "shadow" if _flag_value() in _SHADOW_VALUES else "live"


def bucket_registry_for_mode(mode: str) -> SourceBucketRegistry:
    """Pick the rate-limiter registry for a scheduler ``mode``.

    ``live`` shares the process-wide :func:`global_bucket_registry` — the single
    accounting gate every real fetch path acquires from (Decision 1). Every
    other mode (``shadow``) gets a throwaway :func:`unlimited_bucket_registry`
    so dry-run planning cannot consume real vendor tokens (principal-SWE review
    Must-fix: shadow observation must be genuinely side-effect-free).
    """
    return (
        global_bucket_registry() if mode == "live"
        else unlimited_bucket_registry()
    )


def _norm(symbol: object) -> str:
    return str(symbol or "").strip().upper()


def _norm_seq(seq: Iterable[object] | None) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in seq or ():
        s = _norm(raw)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return tuple(out)


def partition_watchlists(
    active_name: str,
    pinned_names: Iterable[str],
    tickers_of: Callable[[str], Iterable[str]],
) -> tuple[list[str], list[str]]:
    """Split pinned watchlists into (focused, other-visible), deduped + normalized.

    ``focused`` = the tickers of the active sub-tab (when it is pinned);
    ``other`` = every other pinned list's tickers, minus anything already in
    ``focused``. Order preserved.
    """
    pinned = list(pinned_names)
    focused: list[str] = []
    other: list[str] = []
    seen: set[str] = set()

    def _add(dst: list[str], raw: object) -> None:
        s = _norm(raw)
        if s and s not in seen:
            seen.add(s)
            dst.append(s)

    if active_name in pinned:
        for t in tickers_of(active_name):
            _add(focused, t)
    for name in pinned:
        if name == active_name:
            continue
        for t in tickers_of(name):
            _add(other, t)
    return focused, other


def build_context(
    *,
    source: str,
    active_symbol: str,
    active_interval: str,
    compare_symbol: str = "",
    focused_watchlist: Iterable[object] = (),
    other_watchlists: Iterable[object] = (),
    universe: Iterable[object] = (),
) -> PrefetchContext:
    """Normalize raw app state into a :class:`PrefetchContext`."""
    return PrefetchContext(
        source=str(source or ""),
        active_symbol=_norm(active_symbol),
        active_interval=str(active_interval or ""),
        compare_symbol=_norm(compare_symbol),
        focused_watchlist=_norm_seq(focused_watchlist),
        other_watchlists=_norm_seq(other_watchlists),
        universe=_norm_seq(universe),
    )


__all__ = [
    "scheduler_enabled", "scheduler_mode", "bucket_registry_for_mode",
    "partition_watchlists", "build_context",
]
