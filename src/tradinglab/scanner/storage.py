"""Scanner persistence: UUID-keyed JSON files in ``<cache_dir>/scans/``.

Single scan = single file ``<uuid>.json``. The UUID is the
:attr:`ScanDefinition.id`; this means renaming a scan never moves the
file, so external tooling and import/export references stay stable.

Layout::

    <cache_dir>/scans/
        a3f4b2...-....json
        b8e1c2...-....json

Atomic write via temp-file + ``os.replace`` so a crash mid-save can't
leave a half-written JSON. Reads tolerate missing / corrupt files
(skipped + logged) — we never destroy user data on a failed parse.

Public API
----------

- :func:`scans_dir`             — directory path (created on demand)
- :func:`save`                  — atomic write, returns final path
- :func:`load`                  — strict, raises on missing / invalid
- :func:`load_all`              — lenient, skips corrupt files
- :func:`delete`                — returns True iff a file was removed
- :func:`find_by_name`          — case-insensitive name lookup
- :func:`export_scan`           — write to arbitrary path
- :func:`import_scan`           — read from arbitrary path; collision
  resolution delegated to caller via callback (Overwrite / Rename /
  Cancel returned as a :class:`CollisionDecision`)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

from ..core.io_helpers import atomic_write_json
from ..disk_cache import _cache_dir
from .model import SCHEMA_VERSION, ScanDefinition

LOG = logging.getLogger(__name__)

_SCANS_DIR_NAME = "scans"
_FILENAME_RE = re.compile(r"^([0-9a-fA-F-]{8,}|tmpl[A-Za-z0-9_-]*)\.json$")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def scans_dir() -> Path:
    """Return the scans directory, creating it if needed."""
    d = _cache_dir() / _SCANS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def scan_path(scan_id: str) -> Path:
    """Return the on-disk path for a given scan id."""
    if not scan_id:
        raise ValueError("scan_id must be non-empty")
    return scans_dir() / f"{scan_id}.json"


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write ``payload`` as JSON to ``path`` atomically.

    Thin shim over :func:`tradinglab.core.io_helpers.atomic_write_json`
    that fixes the storage-module convention (``indent=2``,
    ``sort_keys=False``, ``ensure_ascii=False``, fsync). Kept as a
    private alias because ``scanner/storage.spec.md`` documents the
    atomic-write contract here, and external callers in this module
    would otherwise need to know the kwarg shape.
    """
    atomic_write_json(path, payload)


def save(scan: ScanDefinition) -> Path:
    """Persist ``scan`` to ``<cache_dir>/scans/<id>.json``. Returns the path.

    Touches ``updated_at`` to "now" before writing. Atomic.
    """
    fresh = scan.touch()
    path = scan_path(fresh.id)
    _atomic_write_json(path, fresh.to_dict())
    return path


def load(scan_id: str) -> ScanDefinition:
    """Load one scan by id. Raises :class:`FileNotFoundError` / :class:`ValueError`."""
    path = scan_path(scan_id)
    if not path.exists():
        raise FileNotFoundError(f"no scan with id={scan_id!r} at {path}")
    return _load_path(path)


def _load_path(path: Path) -> ScanDefinition:
    """Strict load from an arbitrary path. Raises :class:`ValueError`."""
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


def load_all() -> List[ScanDefinition]:
    """Load every scan in ``scans_dir()``. Skips + logs corrupt files.

    Order: alphabetical by ``ScanDefinition.name`` (case-insensitive),
    ties broken by id. Stable across runs.
    """
    out: List[ScanDefinition] = []
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
    path = scan_path(scan_id)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def find_by_name(name: str) -> Optional[ScanDefinition]:
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
    dst_path = Path(dst_path)
    _atomic_write_json(dst_path, scan.to_dict())
    return dst_path


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
    on_collision: Optional[Callable[[ScanDefinition, ScanDefinition], CollisionDecision]] = None,
) -> Optional[ScanDefinition]:
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
