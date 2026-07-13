"""Ordering value types for the prefetch scheduler.

The scheduler orders all work by a single totally-ordered
:class:`PriorityKey` = ``(band_index, tier_rank, interval_rank, seq)`` —
**pure band-major** (breadth-first: finish the most-recent max-request band
across every tier before stepping back in history), with tier as the secondary
sort and interval + FIFO ``seq`` as tiebreaks.

:class:`FetchJob` is the immutable work unit. It carries identity (for dedup /
promotion / attach-to-in-flight) plus the priority inputs and the per-tier
``generation`` used to drop stale jobs after a context switch.

Pure — no Tk / IO. See ``PREFETCH_SCHEDULER_DESIGN.md`` §4 and Decisions 2, 3, 9.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

#: Sentinel band for a user-blocking (foreground) fetch. A negative band sorts
#: before every background band (0, 1, …) regardless of tier — so a foreground
#: request always wins the queue (it also runs on the dedicated foreground pool;
#: see Decision 2).
FOREGROUND_BAND = -1


@dataclass(frozen=True, order=True)
class PriorityKey:
    """Total order for scheduling. Lower = fetched sooner.

    Field order IS the precedence: ``band_index`` first (breadth-first),
    then ``tier_rank`` (gap-ranked: active 10 < compare 20 < … < universe 90),
    then ``interval_rank`` (on-screen 0 < escape-hatch 1 < …), then ``seq``
    (monotonic FIFO tiebreak).
    """

    band_index: int
    tier_rank: int
    interval_rank: int
    seq: int


@dataclass(frozen=True)
class FetchJob:
    """One unit of prefetch work (one max-request band of one series)."""

    source: str
    symbol: str
    interval: str
    band_index: int
    tier_rank: int
    interval_rank: int
    generation: int
    seq: int = 0

    @property
    def dedup_key(self) -> tuple[str, str, str, int]:
        """Identity for queue dedup / promotion — includes the band."""
        return (self.source, self.symbol, self.interval, self.band_index)

    @property
    def series_key(self) -> tuple[str, str, str]:
        """Band-independent identity of the ``(source, symbol, interval)``
        series — used to attach a request to an in-flight fetch of the same
        series and to key the per-series window planner."""
        return (self.source, self.symbol, self.interval)

    @property
    def is_foreground(self) -> bool:
        return self.band_index <= FOREGROUND_BAND

    def priority(self) -> PriorityKey:
        return PriorityKey(
            self.band_index, self.tier_rank, self.interval_rank, self.seq
        )

    def with_seq(self, seq: int) -> FetchJob:
        """Return a copy with a new FIFO ``seq`` (assigned at enqueue time)."""
        return replace(self, seq=seq)


__all__ = ["FOREGROUND_BAND", "PriorityKey", "FetchJob"]
