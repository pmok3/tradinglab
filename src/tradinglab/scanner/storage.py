"""Scanner persistence: UUID-keyed JSON files in ``<cache_dir>/scans/``.

Delegates per-id save / load / delete / path-resolution / export to
:class:`tradinglab.core.json_collection_store.JsonObjectStore` — the
shared generic store. Scanner-specific concerns kept here:

* ``schema_version`` refusal on future-version files (raised from the
  custom ``from_dict`` wrapper so both :func:`load` and :func:`_load_path`
  enforce it),
* ``_FILENAME_RE`` filter for ``load_all`` (only UUID-style or
  ``tmpl*`` files participate; ``_index.json`` / ``README.txt`` ignored),
* :class:`CollisionDecision` two-phase id/name collision handling for
  :func:`import_scan`,
* :func:`find_by_name` case-insensitive lookup,
* :meth:`ScanDefinition.touch` on every save (refreshes ``updated_at``).

Single scan = single file ``<uuid>.json``. The UUID is the
:attr:`ScanDefinition.id`; renaming a scan never moves the file, so
external tooling and import/export references stay stable.

Public API surface is preserved verbatim (function signatures, error
types, ``CollisionDecision`` semantics) so callers and tests need no
edits.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Any

from ..core.io_helpers import atomic_write_json
from ..core.json_collection_store import JsonObjectStore
from ..disk_cache import _cache_dir
from .model import SCHEMA_VERSION, ScanDefinition

LOG = logging.getLogger(__name__)

_SCANS_DIR_NAME = "scans"
_FILENAME_RE = re.compile(r"^([0-9a-fA-F-]{8,}|tmpl[A-Za-z0-9_-]*)\.json$")


__all__ = [
    "CollisionDecision",
    "scans_dir",
    "scan_path",
    "save",
    "load",
    "load_all",
    "delete",
    "find_by_name",
    "export_scan",
    "import_scan",
]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def scans_dir() -> Path:
    """Return the scans directory, creating it if needed."""
    d = _cache_dir() / _SCANS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Back-compat shim. Prefer ``core.io_helpers.atomic_write_json``.

    The generic store writes with ``sort_keys=True``; this shim
    preserves the historical scanner default (``indent=2``,
    ``sort_keys=False``, ``ensure_ascii=False``) for any caller that
    imported the private name directly.
    """
    atomic_write_json(path, payload)


def _from_dict_checked(d: Any) -> ScanDefinition:
    """``ScanDefinition.from_dict`` + future-schema refusal.

    Used as the ``from_dict`` callback on the generic store so both
    :func:`load` (delegating) and :func:`_load_path` (scanner-specific)
    enforce the same schema-version guard.
    """
    if not isinstance(d, dict):
        raise ValueError("scan payload is not a JSON object")
    version = int(d.get("schema_version", 1))
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"scan schema_version {version} > supported {SCHEMA_VERSION}; "
            f"created by a newer build of the app"
        )
    try:
        return ScanDefinition.from_dict(d)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"scan failed to deserialize: {e}") from e


_STORE: JsonObjectStore[ScanDefinition] = JsonObjectStore(
    storage_dir=scans_dir,
    kind_label="scan",
    to_dict=lambda s: s.to_dict(),
    from_dict=_from_dict_checked,
    id_of=lambda s: s.id,
    index_value_of=lambda s: s.name,
)


def scan_path(scan_id: str) -> Path:
    """Return the on-disk path for a given scan id."""
    return _STORE.path_for(scan_id)


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------


def save(scan: ScanDefinition) -> Path:
    """Persist ``scan`` to ``<cache_dir>/scans/<id>.json``. Returns the path.

    Touches ``updated_at`` to "now" before writing. Atomic.

    Uses the generic store for path resolution but writes the file
    directly — scanner historically does NOT maintain an ``_index.json``
    (see ``storage.spec.md`` Layout section), and callers / tests
    assert that no auxiliary files appear in ``scans_dir()`` after a
    save. ``_STORE.save`` would auto-write an index entry which would
    break that invariant.
    """
    fresh = scan.touch()
    path = _STORE.path_for(fresh.id)
    atomic_write_json(path, fresh.to_dict())
    return path


def load(scan_id: str) -> ScanDefinition:
    """Load one scan by id. Raises :class:`FileNotFoundError` / :class:`ValueError`."""
    return _STORE.load(scan_id)


def _load_path(path: Path) -> ScanDefinition:
    """Strict load from an arbitrary path. Raises :class:`ValueError`.

    Kept scanner-specific (not delegated) so the error message includes
    the path — callers reading corrupt files want to see which one.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"failed to read scan from {path}: {e}") from e
    if not isinstance(d, dict):
        raise ValueError(f"scan file {path} is not a JSON object")
    version = int(d.get("schema_version", 1))
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"scan {path} schema_version {version} > supported {SCHEMA_VERSION}; "
            f"created by a newer build of the app"
        )
    try:
        return ScanDefinition.from_dict(d)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"scan {path} failed to deserialize: {e}") from e


def load_all() -> list[ScanDefinition]:
    """Load every scan in ``scans_dir()``. Skips + logs corrupt files.

    Order: alphabetical by ``ScanDefinition.name`` (case-insensitive),
    ties broken by id. Stable across runs.

    Kept scanner-specific (not delegated to ``_STORE.load_all``) so the
    ``_FILENAME_RE`` filter (allows UUIDs + ``tmpl*``, excludes everything
    else) and the historical "skipping corrupt scan file" warning text
    are preserved.
    """
    out: list[ScanDefinition] = []
    d = scans_dir()
    if not d.exists():
        return out
    for entry in sorted(d.iterdir()):
        if not entry.is_file():
            continue
        if entry.name == "_index.json":
            continue  # reserved for future v1.1 fast-listing
        if not _FILENAME_RE.match(entry.name):
            continue
        try:
            out.append(_load_path(entry))
        except ValueError as e:
            LOG.warning("scanner.storage: skipping corrupt scan file %s: %s",
                        entry, e)
    out.sort(key=lambda s: (s.name.lower(), s.id))
    return out


def delete(scan_id: str) -> bool:
    """Delete the scan file. Returns True if a file was removed."""
    return _STORE.delete(scan_id)


def find_by_name(name: str) -> ScanDefinition | None:
    """Return the first scan whose name matches ``name`` case-insensitively."""
    target = (name or "").strip().lower()
    if not target:
        return None
    for s in load_all():
        if s.name.lower() == target:
            return s
    return None


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------


class CollisionDecision(str, Enum):
    """Outcome of an import collision callback."""

    OVERWRITE = "overwrite"
    RENAME = "rename"
    CANCEL = "cancel"


def export_scan(scan: ScanDefinition, dst_path: Path) -> Path:
    """Write ``scan`` to ``dst_path`` (atomic). Returns ``dst_path``."""
    return _STORE.export_to_path(scan, Path(dst_path))


def _make_unique_name(base: str) -> str:
    """Return ``base`` with `(2)` / `(3)` suffix until it doesn't clash."""
    existing = {s.name.lower() for s in load_all()}
    if base.lower() not in existing:
        return base
    i = 2
    while True:
        candidate = f"{base} ({i})"
        if candidate.lower() not in existing:
            return candidate
        i += 1


def import_scan(
    src_path: Path,
    on_collision: Callable[[ScanDefinition, ScanDefinition], CollisionDecision] | None = None,
) -> ScanDefinition | None:
    """Import a scan from ``src_path`` into the local library.

    Two collision dimensions are checked, in order:

    1. **id collision** — a local scan exists with the same UUID. Most
       likely a re-import of one previously exported.
    2. **name collision** — a *different* local scan has the same
       (case-insensitive) name.

    On either collision, ``on_collision(local, incoming)`` is called.
    The default behavior (if no callback is given) is to *cancel* on
    any collision — UI code should always pass an explicit callback.

    Returns the saved :class:`ScanDefinition`, or ``None`` if the
    operation was cancelled.
    """
    src_path = Path(src_path)
    incoming = _load_path(src_path)

    locals_ = load_all()
    by_id = {s.id: s for s in locals_}
    by_name = {s.name.lower(): s for s in locals_}

    callback = on_collision or (lambda _local, _inc: CollisionDecision.CANCEL)

    # 1) Same UUID locally?
    if incoming.id in by_id:
        local = by_id[incoming.id]
        decision = callback(local, incoming)
        if decision == CollisionDecision.CANCEL:
            return None
        if decision == CollisionDecision.OVERWRITE:
            save(incoming)
            return incoming
        if decision == CollisionDecision.RENAME:
            renamed = replace(
                incoming,
                id=ScanDefinition(name="x", root=incoming.root).id,  # fresh UUID
                name=_make_unique_name(incoming.name),
            )
            save(renamed)
            return renamed

    # 2) Different UUID, same name?
    name_clash = by_name.get(incoming.name.lower())
    if name_clash is not None:
        decision = callback(name_clash, incoming)
        if decision == CollisionDecision.CANCEL:
            return None
        if decision == CollisionDecision.OVERWRITE:
            # Replace the local scan with the incoming one but keep the
            # local scan's id so existing references (open tabs, etc.)
            # don't get orphaned.
            merged = replace(incoming, id=name_clash.id)
            delete(incoming.id)  # no-op if not present
            save(merged)
            return merged
        if decision == CollisionDecision.RENAME:
            renamed = replace(incoming, name=_make_unique_name(incoming.name))
            save(renamed)
            return renamed

    # No collision — straight save.
    save(incoming)
    return incoming
