"""Exit-strategy persistence: UUID-keyed JSON files in
``<cache_dir>/exit_strategies/``.

Mirrors :mod:`scanner.storage` deliberately — same atomic-write pattern,
same UUID-keyed filename convention, same import-collision protocol —
so the user-facing import/export experience matches between scans and
exit strategies. The single substantive difference is in
:func:`load_all`, which returns ``(strategies, broken)``: a strategy
that JSON-parses but fails :func:`exits.model.validate_strategy`
becomes a :class:`BrokenStrategy` rather than getting silently dropped.
This matters because an open position may *reference* a strategy id
on disk; if that strategy has gone broken (e.g. the user edited the
JSON by hand and broke the OCO disjointness rule, or upgraded the app
to a build with stricter validation), we want a "needs attention"
banner, not a silent re-arm with default settings.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.io_helpers import atomic_write_json
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

    The full raw JSON is preserved so the GUI can offer "open in editor
    to repair" without losing data.
    """

    id: str
    name: str
    reason: str
    raw_json: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def exit_strategies_dir() -> Path:
    """Return the strategies directory, creating it if needed."""
    d = _cache_dir() / _DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def strategy_path(strategy_id: str) -> Path:
    """Return the on-disk path for a given strategy id."""
    if not strategy_id:
        raise ValueError("strategy_id must be non-empty")
    return exit_strategies_dir() / f"{strategy_id}.json"


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Thin shim over :func:`core.io_helpers.atomic_write_json`.

    Kept as a private alias to preserve the call-site shape used
    throughout this module; the storage convention defaults
    (``indent=2``, ``sort_keys=False``, ``ensure_ascii=False``,
    fsync) live in the shared helper.
    """
    atomic_write_json(path, payload)


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------


def save(strategy: ExitStrategy) -> Path:
    """Persist ``strategy`` to ``<cache_dir>/exit_strategies/<id>.json``.

    Refuses-to-save invalid strategies — calls :func:`validate_strategy`
    first and raises :class:`ValueError` listing all errors.
    """
    errs = validate_strategy(strategy)
    if errs:
        raise ValueError(
            f"refusing to save invalid ExitStrategy {strategy.name!r}: "
            + "; ".join(errs)
        )
    path = strategy_path(strategy.id)
    _atomic_write_json(path, strategy.to_dict())
    return path


def load(strategy_id: str) -> ExitStrategy:
    """Load one strategy by id. Raises on missing / invalid."""
    path = strategy_path(strategy_id)
    if not path.exists():
        raise FileNotFoundError(f"no exit strategy with id={strategy_id!r} at {path}")
    return _load_path(path)


def _load_path(path: Path) -> ExitStrategy:
    """Strict load. Raises :class:`ValueError` on JSON / schema problems."""
    raw = _read_json(path)
    return _from_raw(raw, path)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"failed to read strategy from {path}: {e}") from e
    if not isinstance(d, dict):
        raise ValueError(f"strategy file {path} is not a JSON object")
    return d


def _from_raw(d: Dict[str, Any], path: Path) -> ExitStrategy:
    version = int(d.get("schema_version", CURRENT_SCHEMA_VERSION))
    if version > CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"strategy {path} schema_version {version} > supported "
            f"{CURRENT_SCHEMA_VERSION}; created by a newer build"
        )
    try:
        return ExitStrategy.from_dict(d)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"strategy {path} failed to deserialize: {e}") from e


def load_all() -> Tuple[List[ExitStrategy], List[BrokenStrategy]]:
    """Load every strategy in :func:`exit_strategies_dir`.

    Returns ``(strategies, broken)``:

    - ``strategies``: validated, ready-to-use :class:`ExitStrategy` list,
      sorted by name (case-insensitive), id as tiebreaker.
    - ``broken``: strategies that JSON-parsed but failed validation.
      Position-strategy resolution can use the ids to surface a
      "needs attention" banner without losing data.

    Files that fail to even JSON-parse (totally corrupt) are skipped
    with a warning log — these typically only happen if the user
    crashed mid-write or the disk was full. The atomic-write path
    makes this very rare.
    """
    strategies: List[ExitStrategy] = []
    broken: List[BrokenStrategy] = []

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
        # Step 1: try to parse JSON. Hard skip on parse failure.
        try:
            raw = _read_json(entry)
        except ValueError as e:
            LOG.warning("exits.storage: skipping unparseable strategy %s: %s", entry, e)
            continue
        # Step 2: try to construct ExitStrategy.
        try:
            strat = _from_raw(raw, entry)
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
        # Step 3: structural validation.
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
    """Delete the strategy file. Returns True if a file was removed."""
    path = strategy_path(strategy_id)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def find_by_name(name: str) -> Optional[ExitStrategy]:
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
    dst_path = Path(dst_path)
    _atomic_write_json(dst_path, strategy.to_dict())
    return dst_path


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
    on_collision: Optional[
        Callable[[ExitStrategy, ExitStrategy], CollisionDecision]
    ] = None,
) -> Optional[ExitStrategy]:
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
    incoming = _load_path(src_path)
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
