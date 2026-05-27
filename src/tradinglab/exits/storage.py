"""JSON storage for exit strategies.

Delegates the path/atomic-write/per-id strict-load mechanics to
:class:`tradinglab.core.json_collection_store.JsonObjectStore`. Public
surface is preserved verbatim (function signatures, ``BrokenStrategy``
dataclass shape, ``CollisionDecision`` enum, two-tier collision
semantics in :func:`import_strategy`) so callers and existing tests
need no edits.

Exits intentionally does NOT maintain an ``_index.json`` (see
``storage.spec.md`` — strategy counts are O(10-50), directory scan
is cheap, and the index file would be visible to atomicity-checking
tests). The generic store is therefore used as a helper for
``path_for`` / ``load`` / ``delete`` / ``export_to_path`` but the
``save`` / ``load_all`` / ``import_strategy`` paths remain bespoke
to preserve exit-strategy-specific semantics (schema-version
rejection, filename regex filter, dict-shaped raw_json in broken
records, two-tier id-then-name collision protocol).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..core.io_helpers import atomic_write_json
from ..core.json_collection_store import JsonObjectStore
from ..disk_cache import _cache_dir
from .model import (
    CURRENT_SCHEMA_VERSION,
    ExitStrategy,
    validate_strategy,
)

LOG = logging.getLogger(__name__)

_DIR_NAME = "exit_strategies"
_FILENAME_RE = re.compile(r"^([0-9a-fA-F-]{8,}|tmpl[A-Za-z0-9_-]*)\.json$")


__all__ = [
    "BrokenStrategy",
    "CollisionDecision",
    "exit_strategies_dir",
    "strategy_path",
    "save",
    "load",
    "load_all",
    "delete",
    "find_by_name",
    "export_strategy",
    "import_strategy",
]


@dataclass
class BrokenStrategy:
    """A strategy that was JSON-loadable but failed validation.

    The full raw JSON dict is preserved so the GUI can offer "open
    in editor to repair" without losing data.
    """

    id: str
    name: str
    reason: str
    raw_json: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def exit_strategies_dir() -> Path:
    """Return the strategies directory, creating it if needed."""
    d = _cache_dir() / _DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _from_raw(d: dict[str, Any], where: str) -> ExitStrategy:
    """Schema-version-guarded constructor used by both load + load_all."""
    version = int(d.get("schema_version", CURRENT_SCHEMA_VERSION))
    if version > CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"strategy {where} schema_version {version} > supported "
            f"{CURRENT_SCHEMA_VERSION}; created by a newer build"
        )
    try:
        return ExitStrategy.from_dict(d)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"strategy {where} failed to deserialize: {e}") from e


def _validate(strategy: ExitStrategy) -> None:
    errs = validate_strategy(strategy)
    if errs:
        raise ValueError(
            f"refusing to save invalid ExitStrategy {strategy.name!r}: "
            + "; ".join(errs)
        )


_STORE: JsonObjectStore[ExitStrategy] = JsonObjectStore(
    storage_dir=exit_strategies_dir,
    kind_label="exit strategy",
    to_dict=lambda s: s.to_dict(),
    from_dict=lambda d: _from_raw(d, "<load>"),
    id_of=lambda s: s.id,
    validate=_validate,
    index_value_of=lambda s: s.name,
)


def strategy_path(strategy_id: str) -> Path:
    """Return the on-disk path for a given strategy id."""
    return _STORE.path_for(strategy_id)


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Back-compat shim. Prefer :func:`core.io_helpers.atomic_write_json`."""
    atomic_write_json(path, payload)


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------


def save(strategy: ExitStrategy) -> Path:
    """Persist ``strategy`` to ``<cache_dir>/exit_strategies/<id>.json``.

    Refuses-to-save invalid strategies. Does NOT write an
    ``_index.json`` (intentional — see module docstring).
    """
    _validate(strategy)
    path = strategy_path(strategy.id)
    atomic_write_json(path, strategy.to_dict())
    return path


def load(strategy_id: str) -> ExitStrategy:
    """Load one strategy by id. Raises on missing / invalid / future schema."""
    path = strategy_path(strategy_id)
    if not path.exists():
        raise FileNotFoundError(f"no exit strategy with id={strategy_id!r} at {path}")
    raw = _read_json(path)
    return _from_raw(raw, str(path))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"failed to read strategy from {path}: {e}") from e
    if not isinstance(d, dict):
        raise ValueError(f"strategy file {path} is not a JSON object")
    return d


def load_all() -> tuple[list[ExitStrategy], list[BrokenStrategy]]:
    """Load every strategy in :func:`exit_strategies_dir`.

    Returns ``(strategies, broken)``. Files that fail to even
    JSON-parse are skipped with a warning log; files that parse
    but fail construction or validation become :class:`BrokenStrategy`
    records with the raw JSON dict preserved.
    """
    strategies: list[ExitStrategy] = []
    broken: list[BrokenStrategy] = []

    d = exit_strategies_dir()
    if not d.exists():
        return strategies, broken

    for entry in sorted(d.iterdir()):
        if not entry.is_file():
            continue
        if entry.name == "_index.json":
            continue
        if not _FILENAME_RE.match(entry.name):
            continue
        try:
            raw = _read_json(entry)
        except ValueError as e:
            LOG.warning("exits.storage: skipping unparseable strategy %s: %s", entry, e)
            continue
        try:
            strat = _from_raw(raw, str(entry))
        except ValueError as e:
            broken.append(
                BrokenStrategy(
                    id=str(raw.get("id", entry.stem)),
                    name=str(raw.get("name", entry.stem)),
                    reason=str(e),
                    raw_json=raw,
                )
            )
            continue
        errs = validate_strategy(strat)
        if errs:
            broken.append(
                BrokenStrategy(
                    id=strat.id,
                    name=strat.name,
                    reason="; ".join(errs),
                    raw_json=raw,
                )
            )
            continue
        strategies.append(strat)

    strategies.sort(key=lambda s: (s.name.lower(), s.id))
    broken.sort(key=lambda b: (b.name.lower(), b.id))
    return strategies, broken


def delete(strategy_id: str) -> bool:
    """Delete the strategy file. Returns True if a file was removed.

    Delegates to :meth:`JsonObjectStore.delete`; safe wrt the no-index
    policy because the generic only touches ``_index.json`` if the id
    is present in a non-empty in-memory index (we never write one,
    so the on-disk index is always missing and ``load_index`` returns
    ``{}``).
    """
    return _STORE.delete(strategy_id)


def find_by_name(name: str) -> ExitStrategy | None:
    """Case-insensitive name lookup; returns the first match (broken excluded)."""
    target = (name or "").strip().lower()
    if not target:
        return None
    strategies, _broken = load_all()
    for s in strategies:
        if s.name.lower() == target:
            return s
    return None


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------


class CollisionDecision(str, Enum):
    OVERWRITE = "overwrite"
    RENAME = "rename"
    CANCEL = "cancel"


def export_strategy(strategy: ExitStrategy, dst_path: Path) -> Path:
    """Write ``strategy`` to ``dst_path`` (atomic). Returns the path."""
    return _STORE.export_to_path(strategy, Path(dst_path))


def _make_unique_name(base: str, existing_names: set) -> str:
    if base.lower() not in existing_names:
        return base
    i = 2
    while True:
        candidate = f"{base} ({i})"
        if candidate.lower() not in existing_names:
            return candidate
        i += 1


def import_strategy(
    src_path: Path,
    on_collision: Callable[[ExitStrategy, ExitStrategy], CollisionDecision] | None = None,
) -> ExitStrategy | None:
    """Import a strategy from ``src_path`` into the local library.

    Two collision dimensions, checked in order:

    1. **id collision** — local strategy exists with the same UUID.
    2. **name collision** — a *different* local strategy has the same
       (case-insensitive) name.

    Default (if no callback) is ``CANCEL`` on any collision —
    GUI code must always pass an explicit callback.

    Returns the saved strategy, or ``None`` if cancelled.
    """
    src_path = Path(src_path)
    raw = _read_json(src_path)
    incoming = _from_raw(raw, str(src_path))
    errs = validate_strategy(incoming)
    if errs:
        raise ValueError(
            f"refusing to import invalid ExitStrategy {incoming.name!r}: "
            + "; ".join(errs)
        )

    locals_, _broken = load_all()
    by_id = {s.id: s for s in locals_}
    by_name = {s.name.lower(): s for s in locals_}
    existing_names = set(by_name.keys())

    callback = on_collision or (lambda _local, _inc: CollisionDecision.CANCEL)

    # 1) UUID collision
    if incoming.id in by_id:
        local = by_id[incoming.id]
        decision = callback(local, incoming)
        if decision == CollisionDecision.CANCEL:
            return None
        if decision == CollisionDecision.OVERWRITE:
            save(incoming)
            return incoming
        if decision == CollisionDecision.RENAME:
            renamed = dataclasses.replace(
                incoming,
                id=ExitStrategy(name="x").id,  # fresh UUID
                name=_make_unique_name(incoming.name, existing_names),
            )
            save(renamed)
            return renamed

    # 2) Name collision (different UUID)
    if incoming.name.lower() in by_name and by_name[incoming.name.lower()].id != incoming.id:
        local = by_name[incoming.name.lower()]
        decision = callback(local, incoming)
        if decision == CollisionDecision.CANCEL:
            return None
        if decision == CollisionDecision.OVERWRITE:
            # Overwriting "by name" replaces the local entry's *content*
            # but keeps its id (so live position bindings keep working).
            replaced = dataclasses.replace(incoming, id=local.id)
            save(replaced)
            return replaced
        if decision == CollisionDecision.RENAME:
            renamed = dataclasses.replace(
                incoming,
                name=_make_unique_name(incoming.name, existing_names),
            )
            save(renamed)
            return renamed

    # No collision
    save(incoming)
    return incoming
