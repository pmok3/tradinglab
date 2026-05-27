"""Tiny I/O primitives shared across the codebase.

Currently exposes :func:`atomic_write_json` — a single canonical
"write JSON to disk so a crash mid-write can't leave a corrupt
file" implementation. Six storage modules previously inlined the
same ``tempfile.mkstemp`` + ``os.fdopen`` + ``json.dump`` +
``os.fsync`` + ``os.replace`` + cleanup-on-failure scaffolding;
they now all delegate here.

Out of scope (different shapes):
* ``data/_dpapi.py`` writes DPAPI-encrypted bytes, not JSON.
* ``events/cache.py`` writes pickle.
* ``disk_cache.py`` writes pickle.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


def atomic_write_json(
    path: Path,
    obj: Any,
    *,
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
    fsync: bool = True,
) -> None:
    """Write ``obj`` as JSON to ``path`` atomically.

    Strategy: ``tempfile.mkstemp`` in the destination directory →
    ``json.dump`` → ``fh.flush()`` → optional ``os.fsync`` →
    ``os.replace`` (atomic rename on POSIX & Windows). On any
    failure the temp file is best-effort unlinked and the
    exception re-raises.

    Defaults match the most common caller (storage modules):
    ``indent=2``, ``sort_keys=False``, ``ensure_ascii=False``,
    ``fsync=True``. Callers that need byte-stable output should
    pass ``sort_keys=True``.

    Parent directories are created on demand (``parents=True,
    exist_ok=True``).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                obj,
                fh,
                ensure_ascii=ensure_ascii,
                indent=indent,
                sort_keys=sort_keys,
            )
            fh.flush()
            if fsync:
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json(
    path: Path,
    *,
    default: T | None = None,
    log: logging.Logger | None = None,
    log_label: str = "",
) -> T | None:
    """Read a JSON document from ``path``; return ``default`` on any failure.

    Returns ``default`` (typically ``None``, ``{}`` or ``[]``) when the
    file is missing, unreadable (``OSError``), or unparsable
    (``json.JSONDecodeError`` / ``ValueError``). When ``log`` is
    provided, a single ``WARNING`` is emitted per failure that the
    file actually existed for; missing-file is silent (it's a normal
    first-run condition, not an error).

    ``log_label`` is the short subsystem name (e.g. ``"settings"``,
    ``"geometry_store"``) used to disambiguate warnings in the shared
    log file. When omitted, the path itself is the only identifier.
    """
    p = Path(path)
    if not p.exists():
        return default
    try:
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        if log is not None:
            label = log_label or "read_json"
            log.warning("%s: failed to read %s: %s", label, p, exc)
        return default


def read_jsonl(
    path: Path,
    *,
    default: list | None = None,
    log: logging.Logger | None = None,
    log_label: str = "",
) -> list[dict] | None:
    """Read a newline-delimited JSON document; return ``list[dict]``.

    Symmetric to :func:`read_json` but for JSONL. Returns ``default``
    (typically ``None`` or ``[]``) when the file is missing or
    unreadable. A file that exists but is empty returns ``[]``.
    Malformed lines and non-object records are skipped individually
    (each emits one ``WARNING`` if ``log`` is provided) so a single
    torn write doesn't lose the rest of the file.

    Blank lines are skipped silently.
    """
    p = Path(path)
    if not p.exists():
        return default
    label = log_label or "read_jsonl"
    out: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    if log is not None:
                        log.warning(
                            "%s: corrupt line skipped: %s:%d: %s",
                            label, p, lineno, exc,
                        )
                    continue
                if not isinstance(record, dict):
                    if log is not None:
                        log.warning(
                            "%s: non-object record skipped: %s:%d",
                            label, p, lineno,
                        )
                    continue
                out.append(record)
    except OSError as exc:
        if log is not None:
            log.warning("%s: failed to read %s: %s", label, p, exc)
        return default
    return out
