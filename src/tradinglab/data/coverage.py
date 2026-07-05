"""Per-``(source, ticker, interval)`` fetch-coverage record — **API skeleton
(implementation pending)**.

A small sidecar (stored next to each ``disk_cache`` JSONL) that records which
date ranges have actually been fetched and how far back the provider's data
goes. It lets the targeted / on-demand intraday fetch (see
``docs/TARGETED_FETCH.md``) anchor its page-span window against the real
data boundary, skip re-fetching already-covered ranges, and tell three states
apart in the UI: *loading* (range not yet fetched), *no bars for range*
(fetched but the provider had an interior gap — halt/holiday), and
*provider-exhausted* (requested older than the discovered ``data_start_ts``).

Behavioral functions raise :class:`NotImplementedError` until built; the
dataclass + constants define the documented shape. See ``coverage.spec.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Sidecar schema version (bump on an incompatible on-disk change).
SCHEMA_VERSION = 1

#: Filename suffix for the sidecar, alongside the ``disk_cache`` JSONL.
COVERAGE_SUFFIX = ".coverage.json"


@dataclass
class CoverageRecord:
    """Fetch coverage for one ``(source, ticker, interval)`` key.

    ``segments`` are merged, sorted, half-open ``[start_ts, end_ts)`` epoch-
    second ranges that have been fetched (whether or not bars existed in them).
    ``data_start_ts`` is the earliest bar the provider is known to hold (learned
    when a fetch returns bars starting later than requested); ``exhausted_start``
    is set once a request older than that watermark came back empty on the left.
    """

    data_start_ts: int | None = None
    exhausted_start: bool = False
    segments: list[tuple[int, int]] = field(default_factory=list)
    version: int = SCHEMA_VERSION


def _coverage_path(source: str, ticker: str, interval: str, *, root: Path | None = None) -> Path:
    """Resolve the sidecar path next to the ``disk_cache`` JSONL for this key."""
    raise NotImplementedError


def load(source: str, ticker: str, interval: str, *, root: Path | None = None) -> CoverageRecord:
    """Load the coverage sidecar (empty record when missing/corrupt — never raises)."""
    raise NotImplementedError


def save(
    source: str, ticker: str, interval: str, record: CoverageRecord,
    *, root: Path | None = None,
) -> None:
    """Atomically write the coverage sidecar (best-effort; never raises)."""
    raise NotImplementedError


def bootstrap_from_cache(
    source: str, ticker: str, interval: str, *, root: Path | None = None,
) -> CoverageRecord:
    """Seed coverage from an existing JSONL cache with no sidecar yet.

    Treat the on-disk series as one fetched segment spanning its min/max bar
    timestamp — so we never re-fetch what is already on disk.
    """
    raise NotImplementedError


def record_fetch(
    source: str, ticker: str, interval: str,
    req_start: int, req_end: int,
    returned_start: int | None, returned_end: int | None,
    *, root: Path | None = None,
) -> CoverageRecord:
    """Merge a completed fetch into coverage + persist.

    Adds ``[req_start, req_end)`` to ``segments`` (merging overlaps). When
    ``returned_start`` is materially later than ``req_start``, learns
    ``data_start_ts`` and sets ``exhausted_start`` (the provider has nothing
    older). Returns the updated record.
    """
    raise NotImplementedError


def missing_ranges(record: CoverageRecord, start: int, end: int) -> list[tuple[int, int]]:
    """Return the sub-ranges of ``[start, end)`` NOT yet covered by ``record``."""
    raise NotImplementedError


def covered(record: CoverageRecord, start: int, end: int) -> bool:
    """True iff ``[start, end)`` is fully within ``record.segments``."""
    raise NotImplementedError


def data_start(record: CoverageRecord) -> int | None:
    """The discovered earliest-available bar timestamp, or ``None`` if unknown."""
    raise NotImplementedError


__all__ = (
    "SCHEMA_VERSION",
    "COVERAGE_SUFFIX",
    "CoverageRecord",
    "load",
    "save",
    "bootstrap_from_cache",
    "record_fetch",
    "missing_ranges",
    "covered",
    "data_start",
)
