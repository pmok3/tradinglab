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
import os
import tempfile
from pathlib import Path
from typing import Any


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
