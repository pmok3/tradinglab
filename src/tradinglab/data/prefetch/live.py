"""Live-mode fetch translation for the prefetch scheduler.

Maps a scheduler :class:`~.planner.FetchWindow` to a concrete fetch against the
data-source registry and derives the ``oldest_ts`` the scheduler needs for
deepening. The only side effect is the (monkeypatch-friendly) ``DATA_SOURCES`` /
``fetch_page`` dispatch — no Tk, no threads. The app's live ``_prefetch_submit``
seam runs :func:`fetch_window` on the dedicated prefetch worker pool and feeds
:func:`oldest_ts` into ``PrefetchDriver.complete``.

See ``PREFETCH_SCHEDULER_DESIGN.md`` + the live-integration review.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .planner import FetchWindow

#: ``(bars, error, retry_after_s)`` — the outcome of one live fetch.
FetchOutcome = tuple[list[Any], BaseException | None, float | None]


def oldest_ts(bars: Sequence[Any] | None) -> float | None:
    """Epoch-seconds timestamp of the OLDEST bar, or ``None``.

    Input is expected ascending (the fetchers + ``fetch_alpaca_page`` return
    ascending), but we take the ``min`` defensively so a mis-ordered page still
    yields the true oldest bar for the deepening step-back. Any bar lacking a
    ``.date.timestamp()`` → ``None`` (treated as "no progress" → exhausted)."""
    if not bars:
        return None
    try:
        return min(float(b.date.timestamp()) for b in bars)
    except Exception:  # noqa: BLE001
        return None


def fetch_window(
    source: str, symbol: str, interval: str, window: FetchWindow | None,
) -> FetchOutcome:
    """Fetch the bars for one scheduler ``FetchWindow``. Never raises.

    * **range** kind → ``base.fetch_page`` (one HTTP page: newest ``limit`` bars
      strictly before ``end``). ``ok``/``empty`` return bars/``[]``; ``error``
      propagates ``(error, retry_after_s)`` so the scheduler owns retry/poison;
      ``unsupported`` falls through to the trailing fetcher.
    * **period** kind (and the range-unsupported fallback) → the source's
      trailing ``DATA_SOURCES[source](symbol, interval)`` window.

    Returns ``(bars, error, retry_after_s)``.
    """
    from ..base import DATA_SOURCES, fetch_page

    if window is not None and window.kind == "range":
        res = fetch_page(
            source, symbol, interval,
            end_ts=window.end, limit=window.limit or 10_000,
        )
        if res.status == "ok":
            return (list(res.bars or []), None, None)
        if res.status == "empty":
            return ([], None, None)
        if res.status == "error":
            return ([], res.error, res.retry_after_s)
        # "unsupported" → fall through to the trailing fetcher below.

    fetcher = DATA_SOURCES.get(source)
    if fetcher is None:
        return ([], None, None)
    try:
        bars = fetcher(symbol, interval) or []
    except Exception as exc:  # noqa: BLE001 — scheduler owns retry/poison
        return ([], exc, None)
    return (list(bars), None, None)


__all__ = ["FetchOutcome", "oldest_ts", "fetch_window"]
