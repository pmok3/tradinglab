"""JSON storage for entry strategies.

Mirrors :mod:`tradinglab.exits.storage`:

- Strategies live in ``<cache_dir>/entry_strategies/<uuid>.json``.
- An ``_index.json`` maps id -> name for fast listing without parsing
  every file (rebuilt on demand if missing or stale).
- Atomic writes via tmp + rename + fsync (matches exits storage).
- Loads tolerate corrupt or schema-mismatched files: they're surfaced
  as :class:`BrokenStrategy` records with the raw JSON preserved, so
  the GUI can show "Recover/Delete" actions instead of swallowing the
  user's data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.io_helpers import atomic_write_json
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

    The raw JSON is preserved so the GUI can render "edit raw / delete"
    actions without losing the user's data.
    """

    path: Path
    error: str
    raw_json: str | None = None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _path_for(strategy_id: str, *, root: Path | None = None) -> Path:
    if not strategy_id:
        raise ValueError("strategy_id must be non-empty")
    base = root if root is not None else storage_dir()
    return base / f"{strategy_id}.json"


def _index_path(*, root: Path | None = None) -> Path:
    base = root if root is not None else storage_dir()
    return base / _INDEX_NAME


def _atomic_write_json(path: Path, obj: Any) -> None:
    """Write ``obj`` as JSON to ``path`` via tmp + rename + fsync.

    Thin shim over :func:`core.io_helpers.atomic_write_json` with
    ``sort_keys=True`` so on-disk strategy files are byte-stable
    (re-saving the same object yields identical bytes — useful for
    diff-friendly inspection and avoids spurious mtime churn).
    """
    atomic_write_json(path, obj, sort_keys=True)


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


def _load_index(*, root: Path | None = None) -> dict[str, str]:
    p = _index_path(root=root)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover
        LOG.warning("entry_strategies index corrupt, ignoring: %s", exc)
    return {}


def _save_index(index: dict[str, str], *, root: Path | None = None) -> None:
    _atomic_write_json(_index_path(root=root), dict(sorted(index.items())))


def _refresh_index(*, root: Path | None = None) -> dict[str, str]:
    base = root if root is not None else storage_dir()
    out: dict[str, str] = {}
    for entry in base.iterdir():
        if entry.name == _INDEX_NAME:
            continue
        if entry.suffix != ".json":
            continue
        try:
            with entry.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            sid = str(data.get("id") or entry.stem)
            name = str(data.get("name") or "")
            out[sid] = name
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
    _save_index(out, root=root)
    return out


# ---------------------------------------------------------------------------
# Save / Load / Delete
# ---------------------------------------------------------------------------


def save(strategy: EntryStrategy, *, root: Path | None = None) -> Path:
    """Validate + write a strategy. Refuses to write invalid strategies.

    Returns the on-disk path. Updates ``_index.json``.
    """
    errs = validate_strategy(strategy)
    if errs:
        raise ValueError(
            f"refusing to save invalid strategy {strategy.name!r}: "
            + "; ".join(errs)
        )
    path = _path_for(strategy.id, root=root)
    _atomic_write_json(path, strategy.to_dict())
    index = _load_index(root=root)
    index[strategy.id] = strategy.name
    _save_index(index, root=root)
    return path


def load(
    strategy_id: str, *, root: Path | None = None,
) -> EntryStrategy:
    """Load and parse one strategy by id. Raises :class:`FileNotFoundError`."""
    path = _path_for(strategy_id, root=root)
    if not path.exists():
        raise FileNotFoundError(f"entry strategy {strategy_id!r} not found at {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return EntryStrategy.from_dict(data)


def load_all(
    *, root: Path | None = None,
) -> tuple[list[EntryStrategy], list[BrokenStrategy]]:
    """Load every strategy in the directory.

    Returns a pair ``(good, broken)``. Files whose JSON is malformed,
    whose schema_version is unknown, or whose contents fail
    :func:`validate_strategy` end up in ``broken`` with their raw text
    preserved.
    """
    base = root if root is not None else storage_dir()
    base.mkdir(parents=True, exist_ok=True)
    good: list[EntryStrategy] = []
    broken: list[BrokenStrategy] = []
    for entry in sorted(base.iterdir()):
        if entry.name == _INDEX_NAME:
            continue
        if entry.suffix != ".json":
            continue
        try:
            with entry.open("r", encoding="utf-8") as fh:
                raw_text = fh.read()
            data = json.loads(raw_text)
        except (OSError, json.JSONDecodeError) as exc:
            broken.append(BrokenStrategy(
                path=entry,
                error=f"failed to read JSON: {exc}",
                raw_json=None,
            ))
            continue
        try:
            strat = EntryStrategy.from_dict(data)
        except (ValueError, TypeError) as exc:
            broken.append(BrokenStrategy(
                path=entry,
                error=f"failed to parse: {exc}",
                raw_json=raw_text,
            ))
            continue
        errs = validate_strategy(strat)
        if errs:
            broken.append(BrokenStrategy(
                path=entry,
                error="; ".join(errs),
                raw_json=raw_text,
            ))
            continue
        good.append(strat)
    return good, broken


def delete(strategy_id: str, *, root: Path | None = None) -> bool:
    """Delete the strategy file + index entry. Returns True if removed."""
    path = _path_for(strategy_id, root=root)
    removed = False
    if path.exists():
        try:
            path.unlink()
            removed = True
        except OSError:  # pragma: no cover
            return False
    index = _load_index(root=root)
    if strategy_id in index:
        del index[strategy_id]
        _save_index(index, root=root)
    return removed


# ---------------------------------------------------------------------------
# Import / Export
# ---------------------------------------------------------------------------


def export_to_path(
    strategy: EntryStrategy, path: Path,
) -> Path:
    """Write ``strategy`` to a user-specified path (no index update)."""
    _atomic_write_json(path, strategy.to_dict())
    return path


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
    with src.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    strat = EntryStrategy.from_dict(data)
    errs = validate_strategy(strat)
    if errs:
        raise ValueError(
            f"refusing to import invalid strategy {strat.name!r}: "
            + "; ".join(errs)
        )
    target = _path_for(strat.id, root=root)
    if target.exists():
        if on_id_collision == "rename":
            from .model import _new_id
            strat.id = _new_id()
            if " (imported)" not in strat.name:
                strat.name = f"{strat.name} (imported)"
        elif on_id_collision == "reject":
            raise ValueError(
                f"strategy id {strat.id!r} already exists; refusing to import"
            )
        elif on_id_collision == "overwrite":
            pass
        else:
            raise ValueError(
                f"unknown on_id_collision={on_id_collision!r}; expected "
                "'rename' / 'overwrite' / 'reject'"
            )
    save(strat, root=root)
    return strat
