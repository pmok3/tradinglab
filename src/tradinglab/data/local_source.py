"""Local-file data source — Bring Your Own Data (BYOD).

Reads CSV files the user has placed on disk and turns them into
``List[Candle]`` the rest of the app consumes identically to any other
source. The contract is **strict, schema-validated, and lossless on
round-trip with the matching exporter** (see :mod:`.local_export`).

Design summary (see ``docs/LOCAL_DATA.md``):

* Each registered local "root" points at a directory whose top-level
  subfolders represent original sources (``yfinance/``, ``polygon/``,
  ``alpaca/`` ...). Each subfolder becomes one entry in
  :data:`DATA_SOURCES` named ``"<root_name>-<subfolder>"``.
* Inside a subfolder, files are flat: ``<TICKER>_<INTERVAL>.csv``.
* The CSV header MUST be exactly ``timestamp,open,high,low,close,volume``
  (lowercase). Reject anything else with an actionable error.
* ``timestamp`` MUST be ISO-8601 with explicit timezone offset (e.g.
  ``2024-03-15T09:30:00-04:00`` or ``...Z``). Naive timestamps are
  rejected — they're the #1 source of silent tz-drift bugs.
* Bars are NOT transformed: whatever the upstream source originally
  produced is what gets written and what gets loaded back.
* No disk-cache participation — the file IS the cache. In-memory
  ``_full_cache`` (LRU) is used identically to remote sources and
  entries are immutable within a session (re-read on app restart).

The fetcher factory :func:`make_local_fetcher` closes over the
subfolder path so the same ``DataFetcher`` callable can resolve every
``(ticker, interval)`` request for that source.
"""

from __future__ import annotations

import csv
import logging
import zipfile
from collections.abc import Callable
from datetime import datetime
from io import StringIO
from pathlib import Path, PurePosixPath

from ..constants import classify_session, is_intraday
from ..models import Candle
from .base import DataFetcher

LOG = logging.getLogger(__name__)

# Canonical strict header — match exactly, in this order. We sort the
# parsed columns by header index, not by name, so the contract on disk
# is "headers are these six tokens, comma-separated, lowercase, no
# leading spaces, in this exact order".
CANONICAL_HEADER: tuple[str, ...] = (
    "timestamp", "open", "high", "low", "close", "volume",
)

# Public sentinel for the docs link printed in error messages. Keep in
# sync with the path written by :mod:`.local_export`.
DOCS_HINT = "see docs/LOCAL_DATA.md"


class LocalDataError(Exception):
    """Raised internally by the strict parser; never propagated.

    The fetcher catches this and returns ``None`` so the caller's
    contract (``Optional[List[Candle]]``) is preserved. The exception
    message is logged via the module logger so users can diagnose via
    the status-history view.
    """


def _path_for(root: Path, ticker: str, interval: str) -> Path:
    """Resolve ``(ticker, interval)`` to a flat ``<TICKER>_<INTERVAL>.csv`` path.

    The ticker is uppercased to match the export convention; intervals
    are passed through verbatim (``5m``, ``1h``, ``1d``).
    """
    safe_ticker = ticker.upper().replace("/", "_").replace("\\", "_")
    safe_interval = interval.replace("/", "_").replace("\\", "_")
    return root / f"{safe_ticker}_{safe_interval}.csv"


def _parse_iso_with_tz(raw: str, line_no: int) -> datetime:
    """Parse an ISO-8601 timestamp that MUST carry an explicit tz offset.

    Accepts the ``...Z`` shorthand for UTC. Rejects naive timestamps
    (no offset, no ``Z``) — that's the contract.
    """
    s = (raw or "").strip()
    if not s:
        raise LocalDataError(
            f"row {line_no}: empty timestamp ({DOCS_HINT})"
        )
    # Python <3.11 fromisoformat doesn't accept "Z"; normalize.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise LocalDataError(
            f"row {line_no}: timestamp {raw!r} unparseable — "
            f"expected ISO-8601 with timezone (e.g. "
            f"'2024-03-15T09:30:00-04:00') ({DOCS_HINT}); {e}"
        ) from e
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise LocalDataError(
            f"row {line_no}: timestamp {raw!r} has no timezone — naive "
            f"timestamps are rejected; add an explicit offset "
            f"(e.g. '-04:00' or 'Z') ({DOCS_HINT})"
        )
    return dt


def _parse_float(raw: str, *, field: str, line_no: int) -> float:
    """Parse an OHLC field; reject NaN / inf / negative values."""
    s = (raw or "").strip()
    if not s:
        raise LocalDataError(
            f"row {line_no}: empty {field!r} value ({DOCS_HINT})"
        )
    try:
        v = float(s)
    except ValueError as e:
        raise LocalDataError(
            f"row {line_no}: {field}={raw!r} is not a number "
            f"({DOCS_HINT}); {e}"
        ) from e
    # OHLC must be finite + non-negative. NaN/Inf would silently poison
    # every indicator downstream.
    if v != v:  # NaN check (avoids math.isnan import)
        raise LocalDataError(
            f"row {line_no}: {field}=NaN is not allowed ({DOCS_HINT})"
        )
    if v in (float("inf"), float("-inf")):
        raise LocalDataError(
            f"row {line_no}: {field}={v!r} is not finite ({DOCS_HINT})"
        )
    if v < 0.0:
        raise LocalDataError(
            f"row {line_no}: {field}={v!r} is negative ({DOCS_HINT})"
        )
    return v


def _parse_volume(raw: str, *, line_no: int) -> int:
    """Parse volume; blank → 0; reject negatives; coerce float-strings."""
    s = (raw or "").strip()
    if not s:
        return 0
    try:
        # Accept "1234", "1234.0", "1.234e3" — broker exports vary.
        v = int(float(s))
    except ValueError as e:
        raise LocalDataError(
            f"row {line_no}: volume={raw!r} is not a number "
            f"({DOCS_HINT}); {e}"
        ) from e
    if v < 0:
        raise LocalDataError(
            f"row {line_no}: volume={v} is negative ({DOCS_HINT})"
        )
    return v


def _validate_header(header_row: list[str], *, file_path: Path) -> None:
    """Raise :class:`LocalDataError` if the header doesn't match exactly."""
    if not header_row:
        raise LocalDataError(
            f"{file_path.name}: file is empty or missing header "
            f"({DOCS_HINT})"
        )
    cleaned = tuple(h.strip() for h in header_row)
    if cleaned != CANONICAL_HEADER:
        expected = ",".join(CANONICAL_HEADER)
        got = ",".join(cleaned) if cleaned else "(empty)"
        raise LocalDataError(
            f"{file_path.name}: header mismatch — expected "
            f"{expected!r}, got {got!r} ({DOCS_HINT})"
        )


def _parse_candles_strict(
    rows_iter, *, label: str, interval: str,
) -> list[Candle]:
    """Strict CSV → ``List[Candle]`` from any iterator of split rows.

    ``rows_iter`` yields lists-of-strings (header first, then data).
    ``label`` is a human-readable source identifier used in error
    messages (file name, or ``"<zip.zip>/<arcname>"``).

    Raises :class:`LocalDataError` on any schema violation.
    """
    iterator = iter(rows_iter)
    try:
        header_row = next(iterator)
    except StopIteration:
        raise LocalDataError(
            f"{label}: file is completely empty ({DOCS_HINT})"
        ) from None
    cleaned = tuple(h.strip() for h in header_row)
    if cleaned != CANONICAL_HEADER:
        expected = ",".join(CANONICAL_HEADER)
        got = ",".join(cleaned) if cleaned else "(empty)"
        raise LocalDataError(
            f"{label}: header mismatch — expected "
            f"{expected!r}, got {got!r} ({DOCS_HINT})"
        )

    candles: list[Candle] = []
    intraday = is_intraday(interval)
    # Line number starts at 2 (1 was the header) so error messages
    # match what the user sees in their text editor.
    for line_no, row in enumerate(iterator, start=2):
        if not row or all((not c or not c.strip()) for c in row):
            continue  # tolerate trailing blank lines
        if len(row) != len(CANONICAL_HEADER):
            raise LocalDataError(
                f"row {line_no}: expected {len(CANONICAL_HEADER)} "
                f"columns, got {len(row)} ({DOCS_HINT})"
            )
        dt = _parse_iso_with_tz(row[0], line_no)
        o = _parse_float(row[1], field="open",  line_no=line_no)
        h = _parse_float(row[2], field="high",  line_no=line_no)
        lo = _parse_float(row[3], field="low",   line_no=line_no)
        c = _parse_float(row[4], field="close", line_no=line_no)
        v = _parse_volume(row[5], line_no=line_no)
        sess = classify_session(dt.hour, dt.minute) if intraday else "regular"
        candles.append(Candle(
            date=dt, open=o, high=h, low=lo, close=c,
            volume=v, session=sess,
        ))

    if not candles:
        raise LocalDataError(
            f"{label}: file has a valid header but zero data rows "
            f"({DOCS_HINT})"
        )

    candles.sort(key=lambda candle: candle.date)
    deduped: list[Candle] = []
    last_ts: datetime | None = None
    for candle in candles:
        if last_ts is not None and candle.date == last_ts:
            LOG.warning(
                "%s: duplicate timestamp %s — keeping first",
                label, candle.date.isoformat(),
            )
            continue
        deduped.append(candle)
        last_ts = candle.date
    return deduped


def _read_candles_strict(path: Path, *, interval: str) -> list[Candle]:
    """Strict CSV → ``List[Candle]`` from a file path on disk.

    Thin wrapper around :func:`_parse_candles_strict` that handles
    the file I/O (utf-8-sig + newline='').
    """
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        return _parse_candles_strict(
            reader, label=path.name, interval=interval,
        )


def _read_candles_from_zip(
    zip_path: Path, arcname: str, *, interval: str,
) -> list[Candle]:
    """Strict CSV → ``List[Candle]`` from a member inside a zip archive.

    ``arcname`` uses forward slashes per the PKZIP spec — typically
    ``<SOURCE>/<TICKER>_<INTERVAL>.csv``. Raises :class:`LocalDataError`
    if the member is missing or fails schema validation.
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            try:
                with zf.open(arcname, "r") as raw:
                    # ZipFile streams return bytes; decode to text with
                    # the same utf-8-sig contract the disk path uses so
                    # BOMs from Excel exports parse cleanly.
                    text = raw.read().decode("utf-8-sig")
            except KeyError as e:
                raise LocalDataError(
                    f"{zip_path.name}!{arcname}: member missing from "
                    f"archive ({DOCS_HINT}); {e}"
                ) from e
    except zipfile.BadZipFile as e:
        raise LocalDataError(
            f"{zip_path.name}: not a valid zip archive "
            f"({DOCS_HINT}); {e}"
        ) from e

    reader = csv.reader(StringIO(text))
    label = f"{zip_path.name}!{arcname}"
    return _parse_candles_strict(reader, label=label, interval=interval)


def make_local_fetcher(root: Path) -> DataFetcher:
    """Build a :data:`DataFetcher` closure that reads from ``root``.

    The returned callable matches the
    ``(ticker, interval) -> Optional[List[Candle]]`` protocol every
    other source obeys. Errors return ``None`` and are logged via the
    module logger; never propagated.
    """
    root = Path(root)

    def fetch_local_data(ticker: str, interval: str) -> list[Candle] | None:
        path = _path_for(root, ticker, interval)
        if not path.is_file():
            LOG.info(
                "local: %s/%s not found at %s (%s)",
                ticker.upper(), interval, path, DOCS_HINT,
            )
            return None
        try:
            candles = _read_candles_strict(path, interval=interval)
        except LocalDataError as e:
            LOG.warning("local: %s/%s: %s", ticker.upper(), interval, e)
            return None
        except (OSError, UnicodeDecodeError) as e:
            LOG.warning(
                "local: %s/%s: cannot read %s — %s (%s)",
                ticker.upper(), interval, path.name, e, DOCS_HINT,
            )
            return None
        except Exception as e:  # noqa: BLE001
            LOG.warning(
                "local: %s/%s: unexpected error reading %s — %s (%s)",
                ticker.upper(), interval, path.name, e, DOCS_HINT,
            )
            return None
        LOG.info(
            "local: %s/%s: %d bars loaded from %s",
            ticker.upper(), interval, len(candles), path.name,
        )
        return candles

    return fetch_local_data


def make_local_zip_fetcher(zip_path: Path, source_subdir: str) -> DataFetcher:
    """Build a :data:`DataFetcher` that reads from inside a zip archive.

    Audit ``local-source-zip``: lets a zip produced by
    :func:`tradinglab.data.local_export.export_entries_zip` be added
    directly as a Configure Local Data root without the user having
    to unzip it first. Each ``source_subdir`` inside the archive
    (top-level directory entry under the zip) becomes one combobox
    source.

    Resolution: ``<source_subdir>/<TICKER>_<INTERVAL>.csv`` inside the
    archive, using forward slashes (PKZIP spec).
    """
    zip_path = Path(zip_path)
    safe_subdir = (source_subdir or "").strip().rstrip("/").rstrip("\\")

    def fetch_local_zip_data(
        ticker: str, interval: str,
    ) -> list[Candle] | None:
        safe_ticker = ticker.upper().replace("/", "_").replace("\\", "_")
        safe_interval = interval.replace("/", "_").replace("\\", "_")
        # Use PurePosixPath to enforce forward slashes — Tk on Windows
        # may have given us back paths with backslashes, but PKZIP
        # arcnames are always /.
        arcname = str(
            PurePosixPath(safe_subdir) / f"{safe_ticker}_{safe_interval}.csv"
        )
        if not zip_path.is_file():
            LOG.info(
                "local: %s/%s: zip archive missing at %s (%s)",
                ticker.upper(), interval, zip_path, DOCS_HINT,
            )
            return None
        try:
            candles = _read_candles_from_zip(
                zip_path, arcname, interval=interval,
            )
        except LocalDataError as e:
            LOG.warning("local: %s/%s: %s", ticker.upper(), interval, e)
            return None
        except (OSError, UnicodeDecodeError) as e:
            LOG.warning(
                "local: %s/%s: cannot read %s!%s — %s (%s)",
                ticker.upper(), interval, zip_path.name, arcname, e,
                DOCS_HINT,
            )
            return None
        except Exception as e:  # noqa: BLE001
            LOG.warning(
                "local: %s/%s: unexpected error reading %s!%s — %s (%s)",
                ticker.upper(), interval, zip_path.name, arcname, e,
                DOCS_HINT,
            )
            return None
        LOG.info(
            "local: %s/%s: %d bars loaded from %s!%s",
            ticker.upper(), interval, len(candles), zip_path.name, arcname,
        )
        return candles

    return fetch_local_zip_data


def list_symbols(root: Path) -> list[tuple[str, str]]:
    """List ``(ticker, interval)`` pairs available in ``root``.

    Discovery rule: any file matching ``<TICKER>_<INTERVAL>.csv`` where
    ``<TICKER>`` is uppercase A–Z 0–9 . - and ``<INTERVAL>`` is a
    non-empty token. Files that don't match the pattern are ignored
    silently — users may keep README or notes alongside their data.

    Returns a list sorted by ticker then interval. Used by the
    Configure Local Data dialog to preview what's actually loadable.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for entry in root.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix.lower() != ".csv":
            continue
        stem = entry.stem  # AAPL_5m
        # Split on the LAST underscore — interval is always one token,
        # ticker may contain underscores (rare, but BRK_B-style names).
        if "_" not in stem:
            continue
        ticker, _, interval = stem.rpartition("_")
        if not ticker or not interval:
            continue
        out.append((ticker.upper(), interval))
    out.sort()
    return out


def _zip_top_level_dirs(zip_path: Path) -> list[str]:
    """Return the sorted top-level "directory" prefixes inside a zip.

    A directory exists implicitly in a zip whenever a member's arcname
    contains a forward slash. We treat the first segment of each
    arcname as a subsource — matching the layout the exporter writes
    (``<SOURCE>/<TICKER>_<INTERVAL>.csv``).
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return []
    out = set()
    for name in names:
        if "/" not in name:
            continue
        head, _, tail = name.partition("/")
        if not head or not tail:
            continue
        # Skip files masquerading as directories (e.g. macOS metadata
        # entries starting with "__MACOSX/" — the user almost never
        # wants those as a combobox source).
        if head.startswith(".") or head.startswith("__"):
            continue
        out.add(head)
    return sorted(out)


def discover_subsources(
    root_path: Path, root_name: str,
) -> list[tuple[str, Path, Callable[..., list[Candle] | None]]]:
    """Walk a root, yield ``(combobox_key, subdir_path, fetcher)`` per subfolder.

    Two root shapes are supported:

    1. **Directory root** — for each top-level subdirectory
       ``<root_path>/<subdir>``, produce a combobox key
       ``"<root_name>-<subdir>"`` and a fetcher closed over the subdir.
    2. **Zip root** (audit ``local-source-zip``) — for each top-level
       directory prefix inside ``root_path`` (a ``.zip`` file), produce
       a combobox key and a fetcher that resolves
       ``<SOURCE>/<TICKER>_<INTERVAL>.csv`` inside the archive. The
       returned "subdir_path" carries the zip-path for diagnostic
       display; callers that need the actual on-disk path of a CSV
       must accept it doesn't exist as a real file.

    Returns an empty list when ``root_path`` doesn't exist or contains
    no subdirectories — the caller treats that as "nothing to register"
    and the source-selector combobox stays as-is.
    """
    root_path = Path(root_path)
    out: list[tuple[str, Path, Callable[..., list[Candle] | None]]] = []

    if root_path.is_file() and root_path.suffix.lower() == ".zip":
        for subdir in _zip_top_level_dirs(root_path):
            key = f"{root_name}-{subdir}"
            out.append((key, root_path, make_local_zip_fetcher(root_path, subdir)))
        return out

    if not root_path.is_dir():
        return []

    for entry in sorted(root_path.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            # Skip hidden dirs (e.g. .DS_Store on macOS, .git, etc.)
            continue
        key = f"{root_name}-{entry.name}"
        out.append((key, entry, make_local_fetcher(entry)))
    return out
