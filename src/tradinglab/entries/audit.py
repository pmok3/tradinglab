"""JSONL audit log for the entry-strategies subsystem.

Mirrors :mod:`tradinglab.exits.audit` precisely — same atomic-write
pattern, same Tk-thread invariant, same on-disk record schema. The only
differences are the directory name (``entries/audit`` instead of
``exits/audit``) and the :data:`KNOWN_KINDS` frozenset (which lists
entry-flavored kinds: ``entry_arm``, ``entry_fire``, etc.).

Per the rev-2 plan we deliberately keep this as a duplicate of the exits
audit module instead of promoting both to a shared ``core/audit_log.py``.
The duplication is small (~150 LOC) and bounded; the alternative would
have meant refactoring already-green exits-v1 code, which is too much
regression blast radius for a v1 ship.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional

from ..core.thread_guard import require_tk_thread
from ..disk_cache import _cache_dir

LOG = logging.getLogger(__name__)

_DIR_NAME = "entries/audit"
_DATE_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")

KNOWN_KINDS = frozenset(
    {
        "entry_arm",
        "entry_disarm",
        "entry_disarm_all",
        "entry_fire",
        "entry_submit",
        "entry_fill",
        "entry_cancel",
        "entry_blocked",      # risk-gate or other guard refused
        "entry_cooldown",     # gate suppressed due to cooldown
        "entry_dedup_skipped",
        "entry_bind_failed",  # an on_fill_exit_id couldn't be bound
        "entry_modal_requested",
        "entry_broken_strategy_load",
    }
)


__all__ = [
    "KNOWN_KINDS",
    "AuditLog",
    "audit_dir",
]


def audit_dir() -> Path:
    """Return ``<cache_dir>/entries/audit`` (created if missing)."""
    d = _cache_dir() / _DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _date_path(root: Path, day: date) -> Path:
    return root / f"{day.isoformat()}.jsonl"


def _serialise_record(record: Dict[str, Any]) -> str:
    body = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
    if "\n" in body:
        body = body.replace("\n", " ")
    return body + "\n"


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    LOG.warning(
                        "entries audit log: corrupt line skipped: %s:%d",
                        path,
                        lineno,
                    )
                    continue
                if not isinstance(record, dict):
                    LOG.warning(
                        "entries audit log: non-object record skipped: %s:%d",
                        path,
                        lineno,
                    )
                    continue
                out.append(record)
    except OSError as exc:  # pragma: no cover - filesystem race
        LOG.warning("entries audit log: failed to read %s: %s", path, exc)
        return []
    return out


@dataclass
class AuditLog:
    """Append-only JSONL audit log with day rotation, scoped to entries.

    Identical contract to :class:`tradinglab.exits.audit.AuditLog` —
    constructor takes optional ``root`` and ``clock``; ``append`` is
    Tk-thread-only; readers may run on any thread.
    """

    root: Path = field(default_factory=audit_dir)
    clock: Callable[[], datetime] = field(default=_utc_now)
    _current_date: Optional[date] = field(default=None, init=False, repr=False)
    _current_handle: Optional[io.TextIOWrapper] = field(default=None, init=False, repr=False)
    _open_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    @require_tk_thread
    def append(
        self,
        kind: str,
        *,
        strategy_id: Optional[str] = None,
        symbol: Optional[str] = None,
        position_id: Optional[str] = None,
        trigger_id: Optional[str] = None,
        order_id: Optional[str] = None,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        meta: Optional[Dict[str, Any]] = None,
        ts: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Append one record. Returns the persisted record dict.

        ``kind`` must be a member of :data:`KNOWN_KINDS`. ``symbol`` and
        ``order_id`` are entry-specific keys (the exit audit log doesn't
        carry symbol because exits resolve symbol via position_id).
        """
        if kind not in KNOWN_KINDS:
            raise ValueError(
                f"entries audit log: unknown kind {kind!r}; "
                f"add it to KNOWN_KINDS or use one of {sorted(KNOWN_KINDS)}"
            )
        when = ts if ts is not None else self.clock()
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        record: Dict[str, Any] = {
            "ts": when.isoformat(),
            "kind": kind,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "position_id": position_id,
            "trigger_id": trigger_id,
            "order_id": order_id,
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
        with self._open_lock:
            if self._current_date != day or self._current_handle is None:
                self._rotate_to(day)
            assert self._current_handle is not None
            self._current_handle.write(line)
            self._current_handle.flush()
            try:
                os.fsync(self._current_handle.fileno())
            except (OSError, ValueError):  # pragma: no cover
                pass

    def _rotate_to(self, day: date) -> None:
        if self._current_handle is not None:
            try:
                self._current_handle.close()
            except OSError:  # pragma: no cover
                pass
            self._current_handle = None
        path = _date_path(self.root, day)
        self._current_handle = path.open("a", encoding="utf-8", newline="")
        self._current_date = day

    def tail(self, n: int) -> List[Dict[str, Any]]:
        """Return the last ``n`` records across all dates, oldest-first."""
        if n <= 0:
            return []
        gathered: Deque[Dict[str, Any]] = deque()
        for day_path in self._date_paths_newest_first():
            file_records = _read_jsonl(day_path)
            for rec in reversed(file_records):
                gathered.append(rec)
                if len(gathered) >= n:
                    break
            if len(gathered) >= n:
                break
        return list(reversed(gathered))

    def list_dates(self) -> List[str]:
        try:
            files = self.root.iterdir()
        except OSError:
            return []
        out: List[str] = []
        for entry in files:
            m = _DATE_FILE_RE.match(entry.name)
            if m:
                out.append(m.group(1))
        out.sort(reverse=True)
        return out

    def read_date(self, date_str: str) -> List[Dict[str, Any]]:
        if not _DATE_FILE_RE.match(f"{date_str}.jsonl"):
            raise ValueError(
                f"entries audit log: invalid date string {date_str!r}; "
                "expected YYYY-MM-DD"
            )
        return _read_jsonl(self.root / f"{date_str}.jsonl")

    def _date_paths_newest_first(self) -> Iterable[Path]:
        for day_str in self.list_dates():
            yield self.root / f"{day_str}.jsonl"

    def close(self) -> None:
        with self._open_lock:
            if self._current_handle is not None:
                try:
                    self._current_handle.flush()
                    self._current_handle.close()
                except OSError:  # pragma: no cover
                    pass
                self._current_handle = None
                self._current_date = None
