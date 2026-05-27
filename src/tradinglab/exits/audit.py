"""JSONL audit log for the exit-strategies subsystem.

One file per UTC day at ``<cache_dir>/exits/audit/<YYYY-MM-DD>.jsonl``.
Every line is one record; every record is a self-contained JSON object.
Schema (per the v1 plan):

    {
        "ts":           "2025-01-15T12:34:56.789012+00:00",
        "kind":         "fire",        # one of KNOWN_KINDS
        "strategy_id":  "uuid|null",
        "position_id":  "uuid|null",
        "leg_id":       "uuid|null",
        "trigger_id":   "uuid|null",
        "qty":          1.0,           # optional float
        "price":        180.5,         # optional float
        "meta":         { ... }        # optional free-form dict
    }

Atomicity / durability — single-writer invariant
-------------------------------------------------
There is no OS-level guarantee that a multi-byte append is atomic on
Windows; therefore we do **not** rely on it. Instead we enforce that
``append`` is only ever called from the Tk main thread (via
``@require_tk_thread``). With a single writer that flushes after every
line, the file can only be corrupted on a crash *mid-line*. Readers
(``tail`` / ``read_date``) tolerate partial trailing lines and skip
them with a logged warning.

The reader path runs on any thread (including a Tk-driven UI refresh
that wants to display the last 100 records) and therefore must NOT
mutate state — its only side effect is reading bytes from disk.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..core.thread_guard import require_tk_thread
from ..disk_cache import _cache_dir

LOG = logging.getLogger(__name__)

_DIR_NAME = "exits/audit"
_DATE_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")

KNOWN_KINDS = frozenset(
    {
        "arm",
        "disarm",
        "fire",
        "cancel",
        "submit",
        "fill",
        "eod_kill_switch_fired",
        "strategy_attach",
        "strategy_detach",
        "position_open",
        "position_close",
        "panic_flatten",
        "broken_strategy_load",
    }
)


__all__ = [
    "KNOWN_KINDS",
    "AuditLog",
    "audit_dir",
]


def audit_dir() -> Path:
    """Return ``<cache_dir>/exits/audit`` (created if missing)."""
    d = _cache_dir() / _DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _utc_now() -> datetime:
    """Default clock — UTC with microsecond precision."""
    return datetime.now(timezone.utc)


def _date_path(root: Path, day: date) -> Path:
    return root / f"{day.isoformat()}.jsonl"


def _serialise_record(record: dict[str, Any]) -> str:
    """Render one record as a single JSONL line.

    Sort keys to keep the on-disk format deterministic per record (the
    overall file is still ordered by append time). ``json.dumps`` with
    ``ensure_ascii=False`` keeps non-ASCII metadata (e.g. unicode notes
    a user pasted into a strategy name) round-trippable.
    """
    body = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    if "\n" in body:  # paranoia — should be impossible after json.dumps
        body = body.replace("\n", " ")
    return body + "\n"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read every JSON record from a JSONL file.

    Skips blank lines silently and corrupt (un-parseable) lines with a
    warning — see the single-writer invariant in the module docstring.
    """
    from ..core.io_helpers import read_jsonl
    return read_jsonl(path, default=[], log=LOG, log_label="audit log") or []


def _tail_jsonl(path: Path, n: int) -> list[dict[str, Any]]:
    """Return the last ``n`` records from a JSONL file (oldest-first)."""
    if n <= 0 or not path.exists():
        return []
    # We could seek-from-end for huge files, but daily exit-audit files
    # are tiny (kilobytes). Reading the whole file is simpler and fully
    # tolerates partial trailing lines via ``_read_jsonl``.
    records = _read_jsonl(path)
    if len(records) <= n:
        return records
    return records[-n:]


@dataclass
class AuditLog:
    """Append-only JSONL audit log with day rotation.

    The constructor takes an optional ``root`` (default ``audit_dir()``)
    and an optional ``clock`` callable returning ``datetime`` instances
    (default UTC ``now()``). Tests inject a fake clock to drive day
    rollover deterministically.

    The instance is **single-writer**: ``append`` is decorated with
    :func:`require_tk_thread`. Readers (``tail`` / ``read_date`` /
    ``list_dates``) have no thread restriction.
    """

    root: Path = field(default_factory=audit_dir)
    clock: Callable[[], datetime] = field(default=_utc_now)
    _current_date: date | None = field(default=None, init=False, repr=False)
    _current_handle: io.TextIOWrapper | None = field(default=None, init=False, repr=False)
    _open_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Writer path
    # ------------------------------------------------------------------

    @require_tk_thread
    def append(
        self,
        kind: str,
        *,
        strategy_id: str | None = None,
        position_id: str | None = None,
        leg_id: str | None = None,
        trigger_id: str | None = None,
        qty: float | None = None,
        price: float | None = None,
        meta: dict[str, Any] | None = None,
        ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Append one record. Returns the persisted record dict.

        ``kind`` must be a member of :data:`KNOWN_KINDS` — adding a new
        kind requires editing this module so we never write a typo to
        disk. ``ts`` defaults to ``clock()``. ``meta`` is shallow-copied
        so the caller's dict can mutate without polluting the on-disk
        record.

        Raises :class:`TkThreadViolation` from a non-Tk thread,
        :class:`ValueError` for unknown ``kind``.
        """
        if kind not in KNOWN_KINDS:
            raise ValueError(
                f"audit log: unknown kind {kind!r}; "
                f"add it to KNOWN_KINDS or use one of {sorted(KNOWN_KINDS)}"
            )
        when = ts if ts is not None else self.clock()
        if when.tzinfo is None:
            # Treat naive timestamps as UTC. Avoids ambiguity in the
            # JSON output.
            when = when.replace(tzinfo=timezone.utc)
        record: dict[str, Any] = {
            "ts": when.isoformat(),
            "kind": kind,
            "strategy_id": strategy_id,
            "position_id": position_id,
            "leg_id": leg_id,
            "trigger_id": trigger_id,
        }
        if qty is not None:
            record["qty"] = float(qty)
        if price is not None:
            record["price"] = float(price)
        if meta is not None:
            record["meta"] = dict(meta)
        line = _serialise_record(record)
        self._write_line(when.date(), line)
        return record

    def _write_line(self, day: date, line: str) -> None:
        """Append one already-serialised JSONL line, rotating per UTC day."""
        # The lock guards open/close transitions. Concurrent writers are
        # already forbidden by `@require_tk_thread` on `append`; the lock
        # is purely defensive against a future caller invoking
        # _write_line on a thread we didn't anticipate (the reader API
        # never touches the writer handle).
        with self._open_lock:
            if self._current_date != day or self._current_handle is None:
                self._rotate_to(day)
            assert self._current_handle is not None
            self._current_handle.write(line)
            self._current_handle.flush()
            try:
                os.fsync(self._current_handle.fileno())
            except (OSError, ValueError):  # pragma: no cover - on closed handles
                pass

    def _rotate_to(self, day: date) -> None:
        if self._current_handle is not None:
            try:
                self._current_handle.close()
            except OSError:  # pragma: no cover - filesystem race
                pass
            self._current_handle = None
        path = _date_path(self.root, day)
        # ``a`` mode: append; create if missing. UTF-8 + newline='' so
        # Windows doesn't translate ``\n`` into ``\r\n`` and produce
        # unparseable JSONL on round-trip.
        self._current_handle = path.open("a", encoding="utf-8", newline="")
        self._current_date = day

    # ------------------------------------------------------------------
    # Reader path (any thread)
    # ------------------------------------------------------------------

    def tail(self, n: int) -> list[dict[str, Any]]:
        """Return the last ``n`` records across all dates, oldest-first.

        Walks date files newest-to-oldest until ``n`` records are
        gathered, then reverses to caller's preferred orientation.
        """
        if n <= 0:
            return []
        gathered: deque[dict[str, Any]] = deque()
        for day_path in self._date_paths_newest_first():
            file_records = _read_jsonl(day_path)
            # Append in reverse so we see newest first per file.
            for rec in reversed(file_records):
                gathered.append(rec)
                if len(gathered) >= n:
                    break
            if len(gathered) >= n:
                break
        # Currently newest-first; reverse for oldest-first to match the
        # natural append order presented in tooling like `tail -f`.
        out = list(reversed(gathered))
        return out

    def list_dates(self) -> list[str]:
        """Return ``YYYY-MM-DD`` strings sorted **newest first**."""
        try:
            files = self.root.iterdir()
        except OSError:
            return []
        out: list[str] = []
        for entry in files:
            m = _DATE_FILE_RE.match(entry.name)
            if m:
                out.append(m.group(1))
        out.sort(reverse=True)
        return out

    def read_date(self, date_str: str) -> list[dict[str, Any]]:
        """Return all records for a specific ``YYYY-MM-DD`` (oldest-first)."""
        if not _DATE_FILE_RE.match(f"{date_str}.jsonl"):
            raise ValueError(
                f"audit log: invalid date string {date_str!r}; expected YYYY-MM-DD"
            )
        return _read_jsonl(self.root / f"{date_str}.jsonl")

    def _date_paths_newest_first(self) -> Iterable[Path]:
        for day_str in self.list_dates():
            yield self.root / f"{day_str}.jsonl"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Flush + close the current write handle.

        Safe to call multiple times. After ``close`` a subsequent
        ``append`` reopens the file transparently.
        """
        with self._open_lock:
            if self._current_handle is not None:
                try:
                    self._current_handle.flush()
                    self._current_handle.close()
                except OSError:  # pragma: no cover - filesystem race
                    pass
                self._current_handle = None
                self._current_date = None
