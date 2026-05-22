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
import io
import logging
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

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
    first_ts: str | None   # ISO-8601 with tz, or None for empty entry
    last_ts: str | None


def _sanitize_segment(seg: str) -> str:
    """Strip path separators and trim whitespace from a folder/file name segment.

    We use this on both source and ticker tokens because both come from
    untrusted on-disk cache keys (which themselves came from user
    input). A malicious or just-buggy source name with ``..`` or path
    separators must never escape the destination directory.
    """
    s = (seg or "").strip()
    return s.replace("/", "_").replace("\\", "_").replace("..", "_")


def format_csv(candles: Sequence[Candle]) -> str:
    """Return the canonical strict-schema CSV text for ``candles``.

    Pure in-memory variant of :func:`write_csv` — used by the zip
    exporter (which needs the bytes to feed into ``ZipFile.writestr``)
    and by tests that want to assert on content without touching disk.

    Raises :class:`LocalExportError` if any candle has a naive
    timestamp.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
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
    return buf.getvalue()


def _count_rows(csv_text: str) -> int:
    """Return the data-row count in ``csv_text`` (excludes the header)."""
    if not csv_text:
        return 0
    # Header line + N data lines, all terminated with \n. An empty
    # csv (header only) has one trailing newline → split yields
    # ["header", ""].
    parts = csv_text.split("\n")
    # Strip the header and any trailing empty line.
    rows = [p for p in parts[1:] if p]
    return len(rows)


def write_csv(path: Path, candles: Sequence[Candle]) -> int:
    """Write ``candles`` to ``path`` in the canonical strict schema.

    Returns the number of rows written. Creates parent directories as
    needed. Raises :class:`LocalExportError` if any candle has a naive
    timestamp.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = format_csv(candles)
    rows_written = _count_rows(content)
    # Write to a temp file then rename, so a crash mid-export can't
    # leave a half-baked file that the importer would later reject.
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8", newline="")
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
) -> list[tuple[str, str, str, int, str | None]]:
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

    results: list[tuple[str, str, str, int, str | None]] = []
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


def export_entries_zip(
    entries: Iterable[tuple[str, str, str, Sequence[Candle]]],
    zip_path: Path,
) -> list[tuple[str, str, str, int, str | None]]:
    """Export selected ``(source, ticker, interval, candles)`` tuples into a zip.

    Each entry becomes a member ``<SOURCE>/<TICKER>_<INTERVAL>.csv``
    inside ``zip_path``. The zip uses forward slashes for arcnames per
    the PKZIP spec so the file extracts cleanly on every OS.

    Audit ``local-export-zip`` — replaces the legacy "one folder of
    loose CSVs" mode in the export dialog. The single-archive output
    saves transfer space (deflate compresses OHLCV text ~4x) and gives
    the user a single file to share / back up.

    Returns the same per-entry result list as :func:`export_entries`.
    The function never raises for a single bad entry; the dialog
    reports per-row outcomes. If the zip itself can't be opened or
    written, raises :class:`LocalExportError`.
    """
    zip_path = Path(zip_path)
    if zip_path.is_dir():
        raise LocalExportError(
            f"zip destination {zip_path} is a directory — pick a file path"
        )
    if not zip_path.parent.exists():
        raise LocalExportError(
            f"destination parent does not exist: {zip_path.parent}"
        )

    results: list[tuple[str, str, str, int, str | None]] = []
    # Stream to a tmp path then rename so a crash mid-export can't
    # leave a corrupt archive at the user's chosen filename.
    tmp = zip_path.with_suffix(zip_path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(
            tmp, mode="w", compression=zipfile.ZIP_DEFLATED,
        ) as zf:
            for source, ticker, interval, candles in entries:
                src_safe = _sanitize_segment(source)
                tkr_safe = _sanitize_segment(ticker).upper()
                intv_safe = _sanitize_segment(interval)
                if not src_safe or not tkr_safe or not intv_safe:
                    results.append(
                        (source, ticker, interval, 0,
                         "empty source/ticker/interval after sanitization")
                    )
                    continue
                arcname = f"{src_safe}/{tkr_safe}_{intv_safe}.csv"
                try:
                    content = format_csv(candles)
                except LocalExportError as e:
                    results.append((source, ticker, interval, 0, str(e)))
                    continue
                except Exception as e:  # noqa: BLE001
                    results.append((source, ticker, interval, 0,
                                    f"unexpected error: {e}"))
                    continue
                try:
                    zf.writestr(arcname, content)
                except Exception as e:  # noqa: BLE001
                    results.append((source, ticker, interval, 0,
                                    f"zip write error: {e}"))
                    continue
                results.append((source, ticker, interval,
                                _count_rows(content), None))
                LOG.info(
                    "local_export: %s/%s/%s -> %s!%s (%d bars)",
                    source, ticker, interval, zip_path, arcname,
                    _count_rows(content),
                )
        import os
        os.replace(tmp, zip_path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        raise
    return results


def default_zip_filename(today=None) -> str:
    """Return the dialog's prepopulated default zip filename.

    Format: ``tradinglab-export-YYYY-MM-DD.zip``. Uses local date so
    the user's filename matches what they see on their wall clock.
    The ``today`` injection keeps the function unit-testable.
    """
    from datetime import date
    d = today if today is not None else date.today()
    return f"tradinglab-export-{d.isoformat()}.zip"
