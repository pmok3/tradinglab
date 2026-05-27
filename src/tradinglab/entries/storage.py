"""JSON storage for entry strategies.

Delegates to :class:`tradinglab.core.json_collection_store.JsonObjectStore`
— the shared generic implementation. Public surface is preserved
verbatim (function signatures, ``BrokenStrategy`` dataclass shape, error
messages) so callers and tests need no edits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..core.io_helpers import atomic_write_json
from ..core.json_collection_store import JsonObjectStore
from ..disk_cache import _cache_dir
from .model import EntryStrategy, validate_strategy

LOG = logging.getLogger(__name__)

_DIR_NAME = "entry_strategies"
_INDEX_NAME = "_index.json"


__all__ = [
    "BrokenStrategy",
    "storage_dir",
    "save",
    "load",
    "load_all",
    "delete",
    "import_from_path",
    "export_to_path",
]


def storage_dir() -> Path:
    """Return the directory where entry-strategy JSONs live."""
    d = _cache_dir() / _DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class BrokenStrategy:
    """A strategy that failed to parse / validate.

    Identical shape to :class:`core.json_collection_store.BrokenRecord`;
    preserved as a distinct name for back-compat with callers that
    `from tradinglab.entries.storage import BrokenStrategy`.
    """

    path: Path
    error: str
    raw_json: str | None = None


def _atomic_write_json(path: Path, obj) -> None:
    """Back-compat shim. Prefer ``core.io_helpers.atomic_write_json``."""
    atomic_write_json(path, obj, sort_keys=True)


def _validate(strategy: EntryStrategy) -> None:
    errs = validate_strategy(strategy)
    if errs:
        raise ValueError(
            f"invalid strategy {strategy.name!r}: " + "; ".join(errs)
        )


_STORE: JsonObjectStore[EntryStrategy] = JsonObjectStore(
    storage_dir=storage_dir,
    kind_label="entry strategy",
    to_dict=lambda s: s.to_dict(),
    from_dict=EntryStrategy.from_dict,
    id_of=lambda s: s.id,
    validate=_validate,
    index_value_of=lambda s: s.name,
    index_filename=_INDEX_NAME,
)


# ---------------------------------------------------------------------------
# Path helpers (preserved for back-compat with anything that imported them)
# ---------------------------------------------------------------------------


def _path_for(strategy_id: str, *, root: Path | None = None) -> Path:
    return _STORE.path_for(strategy_id, root=root)


def _index_path(*, root: Path | None = None) -> Path:
    return _STORE.index_path(root=root)


def _load_index(*, root: Path | None = None) -> dict[str, str]:
    return _STORE.load_index(root=root)


def _save_index(index: dict[str, str], *, root: Path | None = None) -> None:
    _STORE.save_index(index, root=root)


def _refresh_index(*, root: Path | None = None) -> dict[str, str]:
    return _STORE.refresh_index(root=root)


# ---------------------------------------------------------------------------
# Public API — thin delegators over _STORE
# ---------------------------------------------------------------------------


def save(strategy: EntryStrategy, *, root: Path | None = None) -> Path:
    """Validate + write a strategy. Refuses to write invalid strategies."""
    return _STORE.save(strategy, root=root)


def load(strategy_id: str, *, root: Path | None = None) -> EntryStrategy:
    """Load and parse one strategy by id. Raises :class:`FileNotFoundError`."""
    return _STORE.load(strategy_id, root=root)


def load_all(
    *, root: Path | None = None,
) -> tuple[list[EntryStrategy], list[BrokenStrategy]]:
    """Lenient bulk load — returns ``(good, broken)``."""
    good, broken = _STORE.load_all(root=root)
    # Repackage BrokenRecord → BrokenStrategy for back-compat with callers
    # that isinstance-check the dataclass type.
    broken_compat = [
        BrokenStrategy(path=b.path, error=b.error, raw_json=b.raw_json)
        for b in broken
    ]
    return good, broken_compat


def delete(strategy_id: str, *, root: Path | None = None) -> bool:
    """Delete the strategy file + index entry. Returns True if removed."""
    return _STORE.delete(strategy_id, root=root)


def export_to_path(strategy: EntryStrategy, path: Path) -> Path:
    """Write ``strategy`` to a user-specified path (no index update)."""
    return _STORE.export_to_path(strategy, path)


def _rename_on_import(strat: EntryStrategy) -> EntryStrategy:
    from .model import _new_id
    strat.id = _new_id()
    if " (imported)" not in strat.name:
        strat.name = f"{strat.name} (imported)"
    return strat


def import_from_path(
    src: Path,
    *,
    root: Path | None = None,
    on_id_collision: str = "rename",
) -> EntryStrategy:
    """Load + validate a strategy from an arbitrary path, then save it.

    ``on_id_collision``: ``"rename"`` mints a fresh id (and appends
    " (imported)" to the name to disambiguate), ``"overwrite"`` keeps
    the source id (overwriting any existing strategy), ``"reject"``
    raises :class:`ValueError`.
    """
    return _STORE.import_from_path(
        src,
        root=root,
        on_id_collision=on_id_collision,
        rename_fn=_rename_on_import,
    )
