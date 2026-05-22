"""Export disk-cached candles to CSV files for BYOD round-trip.

Companion to :mod:`.local_source`. The exporter writes one CSV per
``(source, ticker, interval)`` tuple selected by the user, in the
exact strict schema the importer accepts:

    timestamp,open,high,low,close,volume
    2024-03-15T09:30:00-04:00,172.50,172.85,172.31,172.62,1245300

Layout on disk:

    <destination>/
        <SOURCE>/
            <TICKER>_<INTERVAL>.csv

This matches :func:`tradinglab.data.local_source.discover_subsources`
so the user can drop the destination folder into Configure Local Data,
add it as a root, and immediately load back exactly the data they
exported (modulo any candles missing from the cache at export time).

Timestamps are formatted via ``datetime.isoformat()``. A timezone is
**required** — if the source produced naive timestamps, this function
raises :class:`LocalExportError` so the bug shows up at export time,
not at re-import time on someone else's machine.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from ..models import Candle

LOG = logging.getLogger(__name__)


class LocalExportError(Exception):
    """Raised when an export cannot be completed (bad data, I/O error)."""


@dataclass(frozen=True)
class ExportEntry:
    """One row in the export dialog's selection table.

    ``candles`` is loaded lazily via :func:`load_cache_entries`; the
    dialog displays metadata (count, range) before the user clicks
    Export so they can decide which entries to include.
    """
    source: str
    ticker: str
    interval: str
    bar_count: int
    first_ts: Optional[str]   # ISO-8601 with tz, or None for empty entry
    last_ts: Optional[str]


def _sanitize_segment(seg: str) -> str:
    """Strip path separators and trim whitespace from a folder/file name segment.

    We use this on both source and ticker tokens because both come from
    untrusted on-disk cache keys (which themselves came from user
    input). A malicious or just-buggy source name with ``..`` or path
    separators must never escape the destination directory.
    """
    s = (seg or "").strip()
    return s.replace("/", "_").replace("\\", "_").replace("..", "_")


def write_csv(path: Path, candles: Sequence[Candle]) -> int:
    """Write ``candles`` to ``path`` in the canonical strict schema.

    Returns the number of rows written. Creates parent directories as
    needed. Raises :class:`LocalExportError` if any candle has a naive
    timestamp.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rows_written = 0
    # Write to a temp file then rename, so a crash mid-export can't
    # leave a half-baked file that the importer would later reject.
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for candle in candles:
                dt = candle.date
                if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
                    raise LocalExportError(
                        f"candle at {dt!r} has no timezone — exporter "
                        f"requires tz-aware timestamps so the file round-trips"
                    )
                writer.writerow([
                    dt.isoformat(),
                    f"{candle.open:.6f}".rstrip("0").rstrip("."),
                    f"{candle.high:.6f}".rstrip("0").rstrip("."),
                    f"{candle.low:.6f}".rstrip("0").rstrip("."),
                    f"{candle.close:.6f}".rstrip("0").rstrip("."),
                    str(int(candle.volume)),
                ])
                rows_written += 1
        # Atomic publish — the importer will never see a partial file.
        import os
        os.replace(tmp, path)
    except Exception:
        # Clean up temp on failure so the destination doesn't accumulate
        # orphan .tmp files across repeated failed exports.
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        raise
    return rows_written


def export_entries(
    entries: Iterable[tuple[str, str, str, Sequence[Candle]]],
    destination: Path,
) -> List[tuple[str, str, str, int, Optional[str]]]:
    """Export selected ``(source, ticker, interval, candles)`` tuples.

    Each entry becomes a file at
    ``<destination>/<SOURCE>/<TICKER>_<INTERVAL>.csv`` (uppercase
    ticker; source and interval passed through after sanitization).

    Returns a per-entry result list ``(source, ticker, interval,
    rows_written, error)`` where ``error`` is ``None`` on success or a
    human-readable message on failure. The function never raises for a
    single bad entry — the dialog wants to report per-row outcomes.
    """
    destination = Path(destination)
    if not destination.parent.exists():
        # Refuse to create a deep tree out of thin air — the user
        # should have picked a real folder. Mkdir on a single missing
        # leaf is fine and matches typical "save as" semantics.
        raise LocalExportError(
            f"destination parent does not exist: {destination.parent}"
        )
    destination.mkdir(parents=True, exist_ok=True)

    results: List[tuple[str, str, str, int, Optional[str]]] = []
    for source, ticker, interval, candles in entries:
        src_safe = _sanitize_segment(source)
        tkr_safe = _sanitize_segment(ticker).upper()
        intv_safe = _sanitize_segment(interval)
        if not src_safe or not tkr_safe or not intv_safe:
            results.append((source, ticker, interval, 0,
                            "empty source/ticker/interval after sanitization"))
            continue
        subdir = destination / src_safe
        out_path = subdir / f"{tkr_safe}_{intv_safe}.csv"
        try:
            n = write_csv(out_path, candles)
        except LocalExportError as e:
            results.append((source, ticker, interval, 0, str(e)))
            continue
        except OSError as e:
            results.append((source, ticker, interval, 0, f"I/O error: {e}"))
            continue
        except Exception as e:  # noqa: BLE001
            results.append((source, ticker, interval, 0,
                            f"unexpected error: {e}"))
            continue
        results.append((source, ticker, interval, n, None))
        LOG.info("local_export: %s/%s/%s -> %s (%d bars)",
                 source, ticker, interval, out_path, n)
    return results
