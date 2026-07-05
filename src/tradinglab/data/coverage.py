"""Per-``(source, ticker, interval)`` fetch-coverage record.

A small sidecar (stored next to each ``disk_cache`` JSONL) that records which
date ranges have actually been fetched and how far back the provider's data
goes. It lets the targeted / on-demand intraday fetch (see
``docs/TARGETED_FETCH.md``) anchor its page-span window against the real
data boundary, skip re-fetching already-covered ranges, and tell three states
apart in the UI: *loading* (range not yet fetched), *no bars for range*
(fetched but the provider had an interior gap — halt/holiday), and
*provider-exhausted* (requested older than the discovered ``data_start_ts``).

Timestamps are UTC epoch **seconds**. ``segments`` are merged, sorted, half-open
``[start_ts, end_ts)`` ranges. See ``coverage.spec.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.io_helpers import atomic_write_json, read_json

#: Sidecar schema version (bump on an incompatible on-disk change).
SCHEMA_VERSION = 1

#: Filename suffix for the sidecar, alongside the ``disk_cache`` JSONL.
COVERAGE_SUFFIX = ".coverage.json"

#: A returned-bars start this many seconds later than requested means the
#: provider has nothing older (a real data-start boundary, not a weekend /
#: holiday gap at the left edge). 7 days clears the longest market closures.
_DATA_START_MARGIN_S = 7 * 86_400


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


def _merge_segments(segs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping / adjacent half-open ranges → sorted, non-overlapping."""
    cleaned: list[tuple[int, int]] = []
    for a, b in segs:
        try:
            ia, ib = int(a), int(b)
        except (TypeError, ValueError):
            continue
        if ib > ia:
            cleaned.append((ia, ib))
    if not cleaned:
        return []
    cleaned.sort()
    out: list[list[int]] = [list(cleaned[0])]
    for a, b in cleaned[1:]:
        if a <= out[-1][1]:  # overlap or exactly adjacent
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _coverage_path(source: str, ticker: str, interval: str, *, root: Path | None = None) -> Path:
    """Resolve the sidecar path next to the ``disk_cache`` JSONL for this key."""
    if root is not None:
        base = Path(root)
    else:
        from .. import paths
        base = paths.cache_dir()
    safe_ticker = ticker.replace("/", "_").replace("\\", "_")
    return base / f"{source}__{safe_ticker}__{interval}{COVERAGE_SUFFIX}"


def load(source: str, ticker: str, interval: str, *, root: Path | None = None) -> CoverageRecord:
    """Load the coverage sidecar (empty record when missing/corrupt — never raises)."""
    data = read_json(_coverage_path(source, ticker, interval, root=root), default=None)
    if not isinstance(data, dict):
        return CoverageRecord()
    raw_segs = data.get("segments") or []
    segs: list[tuple[int, int]] = []
    if isinstance(raw_segs, list):
        for pair in raw_segs:
            try:
                a, b = pair
                segs.append((int(a), int(b)))
            except (TypeError, ValueError):
                continue
    ds = data.get("data_start_ts")
    try:
        ds_val: int | None = int(ds) if ds is not None else None
    except (TypeError, ValueError):
        ds_val = None
    try:
        ver = int(data.get("version", SCHEMA_VERSION) or SCHEMA_VERSION)
    except (TypeError, ValueError):
        ver = SCHEMA_VERSION
    return CoverageRecord(
        data_start_ts=ds_val,
        exhausted_start=bool(data.get("exhausted_start", False)),
        segments=_merge_segments(segs),
        version=ver,
    )


def save(
    source: str, ticker: str, interval: str, record: CoverageRecord,
    *, root: Path | None = None,
) -> None:
    """Atomically write the coverage sidecar (best-effort; never raises)."""
    try:
        atomic_write_json(
            _coverage_path(source, ticker, interval, root=root),
            {
                "version": int(record.version),
                "data_start_ts": record.data_start_ts,
                "exhausted_start": bool(record.exhausted_start),
                "segments": [[int(a), int(b)] for a, b in record.segments],
            },
        )
    except OSError:
        pass  # coverage is convenience metadata — never break the caller


def bootstrap_from_cache(
    source: str, ticker: str, interval: str, *, root: Path | None = None,
) -> CoverageRecord:
    """Seed coverage from an existing JSONL cache with no sidecar yet.

    Treat the on-disk series as one fetched segment spanning its min/max bar
    timestamp — so we never re-fetch what is already on disk.
    """
    from .. import disk_cache
    try:
        candles = disk_cache.load(source, ticker, interval)
    except Exception:  # noqa: BLE001
        candles = None
    rec = CoverageRecord()
    if candles:
        try:
            lo = int(candles[0].date.timestamp())
            hi = int(candles[-1].date.timestamp())
            if hi >= lo:
                rec.segments = [(lo, hi + 1)]  # half-open, inclusive of last bar
        except (AttributeError, ValueError, OSError):
            pass
    save(source, ticker, interval, rec, root=root)
    return rec


def record_fetch(
    source: str, ticker: str, interval: str,
    req_start: int, req_end: int,
    returned_start: int | None, returned_end: int | None,
    *, root: Path | None = None,
) -> CoverageRecord:
    """Merge a completed fetch into coverage + persist.

    Adds ``[req_start, req_end)`` to ``segments``. When ``returned_start`` is
    materially later than ``req_start`` (:data:`_DATA_START_MARGIN_S`), learns
    ``data_start_ts`` and sets ``exhausted_start`` — the provider has nothing
    older. Returns the updated record.
    """
    rec = load(source, ticker, interval, root=root)
    rs_i, re_i = int(req_start), int(req_end)
    if re_i > rs_i:
        rec.segments = _merge_segments([*rec.segments, (rs_i, re_i)])
    if returned_start is not None:
        rstart = int(returned_start)
        if rstart - rs_i > _DATA_START_MARGIN_S:
            if rec.data_start_ts is None or rstart < rec.data_start_ts:
                rec.data_start_ts = rstart
            rec.exhausted_start = True
    save(source, ticker, interval, rec, root=root)
    return rec


def missing_ranges(record: CoverageRecord, start: int, end: int) -> list[tuple[int, int]]:
    """Return the sub-ranges of ``[start, end)`` NOT yet covered by ``record``."""
    s, e = int(start), int(end)
    if e <= s:
        return []
    gaps: list[tuple[int, int]] = []
    cursor = s
    for a, b in record.segments:  # sorted + merged by load()
        if b <= cursor:
            continue
        if a >= e:
            break
        if a > cursor:
            gaps.append((cursor, min(a, e)))
        cursor = max(cursor, b)
        if cursor >= e:
            break
    if cursor < e:
        gaps.append((cursor, e))
    return gaps


def covered(record: CoverageRecord, start: int, end: int) -> bool:
    """True iff ``[start, end)`` is fully within ``record.segments``."""
    return not missing_ranges(record, start, end)


def data_start(record: CoverageRecord) -> int | None:
    """The discovered earliest-available bar timestamp, or ``None`` if unknown."""
    return record.data_start_ts


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
