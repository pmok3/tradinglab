"""Generic JSON-collection storage.

Six subsystems (entries, exits, scanner, watchlists, strategy_tester,
positions) each reimplemented the same ``<dir>/<id>.json`` + ``_index.json``
pattern with identical try/except scaffolding and ``BrokenStrategy``
triage. This module hosts the shared implementation; each subsystem
narrows it via a thin module-level instance + delegating wrappers.

Contract:

* Storage layout: ``<storage_dir>/<id>.json`` per object, plus
  ``_index.json`` mapping ``id -> index_value`` (typically the name).
* Atomic writes via :func:`core.io_helpers.atomic_write_json` with
  ``sort_keys=True`` for byte-stable on-disk files.
* Bulk loads are *lenient*: malformed JSON / parse failure / validation
  failure yield :class:`BrokenRecord` entries (with raw text preserved
  when available) instead of crashing the read.
* Per-id loads are *strict*: missing file → :class:`FileNotFoundError`;
  malformed JSON / failed validation → :class:`ValueError` or whatever
  the supplied parser raises.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

from .io_helpers import atomic_write_json

LOG = logging.getLogger(__name__)

T = TypeVar("T")

__all__ = ["BrokenRecord", "JsonObjectStore"]


@dataclass
class BrokenRecord:
    """A persisted record that failed to parse / validate.

    The raw JSON text is preserved (when readable) so a GUI can render
    "edit raw / delete" actions without losing the user's data.
    """

    path: Path
    error: str
    raw_json: str | None = None


class JsonObjectStore(Generic[T]):
    """Generic per-id JSON store with a flat ``_index.json``.

    Each subsystem builds a module-level instance and exposes thin
    wrappers preserving its historical public surface.
    """

    def __init__(
        self,
        *,
        storage_dir: Callable[[], Path],
        kind_label: str,
        to_dict: Callable[[T], dict[str, Any]],
        from_dict: Callable[[dict[str, Any]], T],
        id_of: Callable[[T], str],
        validate: Callable[[T], None] | None = None,
        index_value_of: Callable[[T], str] | None = None,
        index_filename: str = "_index.json",
    ) -> None:
        self._storage_dir = storage_dir
        self.kind_label = kind_label
        self._to_dict = to_dict
        self._from_dict = from_dict
        self._id_of = id_of
        self._validate = validate
        self._index_value_of = index_value_of or (lambda obj: id_of(obj))
        self._index_filename = index_filename

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _root(self, root: Path | None) -> Path:
        return root if root is not None else self._storage_dir()

    def path_for(self, obj_id: str, *, root: Path | None = None) -> Path:
        if not obj_id:
            raise ValueError(f"{self.kind_label} id must be non-empty")
        return self._root(root) / f"{obj_id}.json"

    def index_path(self, *, root: Path | None = None) -> Path:
        return self._root(root) / self._index_filename

    # ------------------------------------------------------------------
    # Index
    # ------------------------------------------------------------------

    def load_index(self, *, root: Path | None = None) -> dict[str, str]:
        p = self.index_path(root=root)
        if not p.exists():
            return {}
        try:
            with p.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            LOG.warning(
                "%s index corrupt, ignoring: %s", self.kind_label, exc,
            )
        return {}

    def save_index(
        self, index: dict[str, str], *, root: Path | None = None,
    ) -> None:
        atomic_write_json(
            self.index_path(root=root),
            dict(sorted(index.items())),
            sort_keys=True,
        )

    def refresh_index(self, *, root: Path | None = None) -> dict[str, str]:
        """Best-effort rescan of the storage dir → fresh ``_index.json``.

        Files that fail to read / parse are skipped (and logged at
        WARNING) but do not abort the refresh.
        """
        base = self._root(root)
        base.mkdir(parents=True, exist_ok=True)
        out: dict[str, str] = {}
        for entry in base.iterdir():
            if entry.name == self._index_filename:
                continue
            if entry.suffix != ".json":
                continue
            try:
                with entry.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                sid = str(data.get("id") or entry.stem)
                name = str(data.get("name") or "")
                out[sid] = name
            except (OSError, json.JSONDecodeError, ValueError, AttributeError) as exc:
                LOG.warning(
                    "%s refresh: skipping %s (%s)",
                    self.kind_label, entry.name, exc,
                )
                continue
        self.save_index(out, root=root)
        return out

    def list_ids(self, *, root: Path | None = None) -> list[str]:
        """Return all ids known to ``_index.json`` (sorted)."""
        return sorted(self.load_index(root=root).keys())

    # ------------------------------------------------------------------
    # Save / Load / Delete
    # ------------------------------------------------------------------

    def save(self, obj: T, *, root: Path | None = None) -> Path:
        """Validate + write ``obj`` atomically and refresh the index entry."""
        if self._validate is not None:
            self._validate(obj)
        obj_id = self._id_of(obj)
        path = self.path_for(obj_id, root=root)
        atomic_write_json(path, self._to_dict(obj), sort_keys=True)
        index = self.load_index(root=root)
        index[obj_id] = str(self._index_value_of(obj))
        self.save_index(index, root=root)
        return path

    def load(self, obj_id: str, *, root: Path | None = None) -> T:
        """Strict per-id load. Raises :class:`FileNotFoundError` if absent."""
        path = self.path_for(obj_id, root=root)
        if not path.exists():
            raise FileNotFoundError(
                f"{self.kind_label} {obj_id!r} not found at {path}"
            )
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{self.kind_label} {obj_id!r} at {path} is malformed JSON: {exc}"
            ) from exc
        return self._from_dict(data)

    def delete(self, obj_id: str, *, root: Path | None = None) -> bool:
        """Delete the file + index entry. Returns ``True`` if a file was removed."""
        path = self.path_for(obj_id, root=root)
        removed = False
        if path.exists():
            try:
                path.unlink()
                removed = True
            except OSError as exc:
                LOG.warning(
                    "%s delete %s failed: %s", self.kind_label, obj_id, exc,
                )
                return False
        index = self.load_index(root=root)
        if obj_id in index:
            del index[obj_id]
            self.save_index(index, root=root)
        return removed

    # ------------------------------------------------------------------
    # Bulk
    # ------------------------------------------------------------------

    def load_all(
        self, *, root: Path | None = None,
    ) -> tuple[list[T], list[BrokenRecord]]:
        """Lenient bulk load. Returns ``(good, broken)``.

        Files whose JSON is malformed, whose parser raises, or which
        fail :meth:`_validate` are accumulated into ``broken`` with
        raw text preserved when available.
        """
        base = self._root(root)
        base.mkdir(parents=True, exist_ok=True)
        good: list[T] = []
        broken: list[BrokenRecord] = []
        for entry in sorted(base.iterdir()):
            if entry.name == self._index_filename:
                continue
            if entry.suffix != ".json":
                continue
            try:
                with entry.open("r", encoding="utf-8") as fh:
                    raw_text = fh.read()
                data = json.loads(raw_text)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                LOG.warning(
                    "%s load_all: failed to read %s: %s",
                    self.kind_label, entry.name, exc,
                )
                broken.append(BrokenRecord(
                    path=entry,
                    error=f"failed to read JSON: {exc}",
                    raw_json=None,
                ))
                continue
            try:
                obj = self._from_dict(data)
            except (ValueError, TypeError, KeyError) as exc:
                LOG.warning(
                    "%s load_all: failed to parse %s: %s",
                    self.kind_label, entry.name, exc,
                )
                broken.append(BrokenRecord(
                    path=entry,
                    error=f"failed to parse: {exc}",
                    raw_json=raw_text,
                ))
                continue
            if self._validate is not None:
                try:
                    self._validate(obj)
                except (ValueError, TypeError) as exc:
                    LOG.warning(
                        "%s load_all: %s failed validation: %s",
                        self.kind_label, entry.name, exc,
                    )
                    broken.append(BrokenRecord(
                        path=entry,
                        error=str(exc),
                        raw_json=raw_text,
                    ))
                    continue
            good.append(obj)
        return good, broken

    # ------------------------------------------------------------------
    # Import / Export
    # ------------------------------------------------------------------

    def export_to_path(self, obj: T, dst: Path) -> Path:
        """Write ``obj`` to an arbitrary path (no index update)."""
        atomic_write_json(dst, self._to_dict(obj), sort_keys=True)
        return dst

    def import_from_path(
        self,
        src: Path,
        *,
        root: Path | None = None,
        on_id_collision: str = "rename",
        rename_fn: Callable[[T], T] | None = None,
    ) -> T:
        """Load ``src``, validate, then save into the store.

        ``on_id_collision`` is one of ``"rename"`` (mint a fresh id via
        ``rename_fn``), ``"overwrite"`` (replace the existing file), or
        ``"reject"`` (raise :class:`ValueError`). ``rename_fn`` is
        required when ``on_id_collision="rename"`` and a collision
        actually occurs.
        """
        with src.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        obj = self._from_dict(data)
        if self._validate is not None:
            self._validate(obj)
        target = self.path_for(self._id_of(obj), root=root)
        if target.exists():
            if on_id_collision == "rename":
                if rename_fn is None:
                    raise ValueError(
                        f"{self.kind_label} id {self._id_of(obj)!r} "
                        "collides and no rename_fn was provided"
                    )
                obj = rename_fn(obj)
            elif on_id_collision == "reject":
                raise ValueError(
                    f"{self.kind_label} id {self._id_of(obj)!r} "
                    "already exists; refusing to import"
                )
            elif on_id_collision == "overwrite":
                pass
            else:
                raise ValueError(
                    f"unknown on_id_collision={on_id_collision!r}; "
                    "expected 'rename' / 'overwrite' / 'reject'"
                )
        self.save(obj, root=root)
        return obj
