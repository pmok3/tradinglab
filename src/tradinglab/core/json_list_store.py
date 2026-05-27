"""Single-file JSON-collection store with versioned envelope.

For the on-disk shape ``{"schema_version": N, "<items_key>": [...]}``
where the items are a homogeneous list of records. Sibling to
:class:`core.json_collection_store.JsonObjectStore` which handles the
"one file per record" shape. Migration target for
``positions/storage.py`` (pilot) and potentially
``watchlists/storage.py`` later.

Key contract:

* Read returns ``[]`` for missing file / unreadable / bad envelope /
  on-disk ``schema_version`` newer than ``self.schema_version``.
* Write replaces the whole envelope atomically (via
  :func:`core.io_helpers.atomic_write_json`).
* The ``schema_version`` field lets callers do future-proof migration
  on read via the optional ``migrate`` hook.
* Optional "extras" keys (e.g. ``pinned`` alongside ``watchlists``)
  are passed through opaquely — the store never inspects their shape.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Generic, TypeVar

from .io_helpers import atomic_write_json, read_json

T = TypeVar("T")

LOG = logging.getLogger(__name__)

__all__ = ["JsonListStore"]


class JsonListStore(Generic[T]):
    """Bounded-shape JSON list persistence with a versioned envelope.

    Args:
        path: Callable returning the on-disk path (lazy-resolved so
            tests can sandbox via ``tmp_path``).
        items_key: The dict key inside the envelope that holds the
            list of records (e.g. ``"positions"``).
        to_dict: ``(T) -> dict`` per-record serializer.
        from_dict: ``(dict) -> T`` per-record parser.
        schema_version: Current envelope version. On read, an envelope
            with ``schema_version > self.schema_version`` is REFUSED
            (returns ``[]``); equal-or-lower is accepted (caller's
            responsibility to handle older shapes via ``migrate``).
        migrate: Optional ``(dict_envelope, on_disk_version) ->
            dict_envelope`` hook called when the on-disk version is
            older than ``schema_version``. Default: identity.
        kind_label: Short human-readable name for log messages
            (e.g. ``"open positions"``).
        extra_keys: Additional top-level envelope keys to preserve on
            read/write (e.g. ``("pinned",)`` for watchlists). Values
            are opaque pass-through.
    """

    def __init__(
        self,
        *,
        path: Callable[[], Path],
        items_key: str,
        to_dict: Callable[[T], dict],
        from_dict: Callable[[dict], T],
        schema_version: int = 1,
        migrate: Callable[[dict, int], dict] | None = None,
        kind_label: str = "list-store",
        extra_keys: tuple[str, ...] = (),
    ) -> None:
        self._path = path
        self._items_key = items_key
        self._to_dict = to_dict
        self._from_dict = from_dict
        self.schema_version = int(schema_version)
        self._migrate = migrate
        self.kind_label = kind_label
        self._extra_keys = tuple(extra_keys)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def path_for(self, *, root: Path | None = None) -> Path:
        if root is not None:
            return root / self._path().name
        return self._path()

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def _read_envelope(self, *, root: Path | None = None) -> dict | None:
        """Return a validated envelope dict, or ``None`` for empty/refused."""
        p = self.path_for(root=root)
        data = read_json(p, default=None, log=LOG, log_label=self.kind_label)
        if data is None:
            return None
        if not isinstance(data, dict):
            LOG.warning(
                "%s: %s is not a JSON object; ignoring", self.kind_label, p,
            )
            return None
        try:
            on_disk_version = int(data.get("schema_version", 1))
        except (TypeError, ValueError):
            LOG.warning(
                "%s: %s has invalid schema_version; ignoring",
                self.kind_label, p,
            )
            return None
        if on_disk_version > self.schema_version:
            LOG.warning(
                "%s: %s schema_version=%d too new (current=%d); ignoring",
                self.kind_label, p, on_disk_version, self.schema_version,
            )
            return None
        if on_disk_version < self.schema_version and self._migrate is not None:
            try:
                data = self._migrate(data, on_disk_version)
            except Exception as exc:  # noqa: BLE001
                LOG.warning(
                    "%s: migration from v%d failed: %s; ignoring",
                    self.kind_label, on_disk_version, exc,
                )
                return None
            if not isinstance(data, dict):
                LOG.warning(
                    "%s: migrate hook returned non-dict; ignoring",
                    self.kind_label,
                )
                return None
        return data

    def _parse_items(self, envelope: dict) -> list[T]:
        raw_items = envelope.get(self._items_key, []) or []
        out: list[T] = []
        for raw in raw_items:
            try:
                out.append(self._from_dict(raw))
            except Exception as exc:  # noqa: BLE001
                LOG.warning(
                    "%s: skipping malformed record: %s",
                    self.kind_label, exc,
                )
        return out

    def load(self, *, root: Path | None = None) -> list[T]:
        envelope = self._read_envelope(root=root)
        if envelope is None:
            return []
        return self._parse_items(envelope)

    def load_with_extras(
        self, *, root: Path | None = None,
    ) -> tuple[list[T], dict[str, Any]]:
        envelope = self._read_envelope(root=root)
        if envelope is None:
            return [], {key: None for key in self._extra_keys}
        items = self._parse_items(envelope)
        extras = {key: envelope.get(key) for key in self._extra_keys}
        return items, extras

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def _build_envelope(
        self, items: list[T], extras: dict[str, Any] | None,
    ) -> dict:
        envelope: dict[str, Any] = {
            "schema_version": self.schema_version,
            self._items_key: [self._to_dict(item) for item in items],
        }
        if extras:
            for key, value in extras.items():
                if key in ("schema_version", self._items_key):
                    raise ValueError(
                        f"{self.kind_label}: extras key {key!r} collides "
                        "with reserved envelope key",
                    )
                envelope[key] = value
        return envelope

    def save(
        self, items: list[T], *, root: Path | None = None,
    ) -> Path:
        path = self.path_for(root=root)
        atomic_write_json(path, self._build_envelope(items, None))
        return path

    def save_with_extras(
        self,
        items: list[T],
        extras: dict[str, Any],
        *,
        root: Path | None = None,
    ) -> Path:
        path = self.path_for(root=root)
        atomic_write_json(path, self._build_envelope(items, extras))
        return path

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def clear(self, *, root: Path | None = None) -> bool:
        """Remove the file. Returns ``True`` iff a file existed."""
        try:
            self.path_for(root=root).unlink()
            return True
        except FileNotFoundError:
            return False
