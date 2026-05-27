"""First-run seeding of bundled starter templates.

See :mod:`tradinglab.templates` package docstring for the high-
level contract. This module is the implementation:

- :func:`seed_default_templates_if_empty` — the safe entry point
  that runs at app startup. No-op once the sentinel exists.
- :func:`seed_default_templates` — unconditional seed; respects
  per-storage-dir "library is empty" guards (set
  ``force=True`` to overwrite existing files of the same id).
- :func:`bundled_templates_dir` — resolves a bundled template
  directory under ``data/`` for both source checkouts and frozen
  PyInstaller builds.

The seeder is intentionally conservative:

- Per-library, we ONLY seed when the target dir is empty (no
  visible JSON files). If the user has already created their own
  strategies, we never touch their library.
- Each individual file copy is wrapped in try/except — one bad
  template can't block the others, and a missing
  ``data/<dir>`` (e.g. partial wheel) simply skips that library.
- The sentinel is written ONCE at the end on best effort; if it
  fails to write, the next launch re-seeds (idempotent because the
  empty-library guard still holds).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from .._resources import resource_path
from ..disk_cache import _cache_dir

LOG = logging.getLogger(__name__)

_SENTINEL_NAME = ".templates_seeded"

# Mapping: (bundled_subdir_under_data, target_storage_dir_callable).
# We import storage helpers lazily inside _seed_one() to keep this
# module import-cheap (templates package is touched at startup).
_TEMPLATE_KINDS: tuple[tuple[str, str], ...] = (
    ("entry_strategy_templates", "entries"),
    ("exit_strategy_templates", "exits"),
    ("scanner_templates", "scans"),
)


def bundled_templates_dir(kind_subdir: str) -> Path:
    """Return the bundled-templates directory under ``data/`` for ``kind_subdir``.

    Works in both source and frozen builds via
    :func:`tradinglab._resources.resource_path`.
    """
    return resource_path("data", kind_subdir)


def _target_storage_dir(kind: str) -> Path:
    """Resolve the user-local storage dir for the given template kind.

    Lazy-imports the per-kind storage module so this file stays
    cheap to import at startup. New template kinds register here.
    """
    try:
        return _STORAGE_DIR_RESOLVERS[kind]()
    except KeyError:
        raise ValueError(f"unknown template kind: {kind!r}") from None


def _entries_storage_dir() -> Path:
    from ..entries.storage import storage_dir
    return storage_dir()


def _exits_storage_dir() -> Path:
    from ..exits.storage import exit_strategies_dir
    return exit_strategies_dir()


def _scans_storage_dir() -> Path:
    from ..scanner.storage import scans_dir
    return scans_dir()


#: Per-kind resolver registry. Each resolver is a zero-arg callable
#: that lazy-imports the target storage module and returns its dir.
#: New template kinds add one entry here + one resolver function.
_STORAGE_DIR_RESOLVERS: dict[str, Callable[[], Path]] = {
    "entries": _entries_storage_dir,
    "exits":   _exits_storage_dir,
    "scans":   _scans_storage_dir,
}


def _is_library_empty(target_dir: Path) -> bool:
    """True iff ``target_dir`` has no visible ``*.json`` files."""
    try:
        for entry in target_dir.iterdir():
            if entry.is_file() and entry.suffix.lower() == ".json":
                # _index.json (entries / exits) is not a user strategy
                # by itself, but we treat ANY .json other than the
                # known meta-file as "library has content".
                if entry.name == "_index.json":
                    continue
                return False
        return True
    except OSError:
        return True


def _copy_json(src: Path, dst: Path) -> None:
    """Atomically copy ``src`` to ``dst`` preserving JSON formatting.

    We re-serialize through :mod:`json` to ensure the target byte
    layout matches the storage convention (``indent=2``,
    ``ensure_ascii=False``) rather than copying bytes verbatim.
    Falls back to byte copy if the source isn't valid JSON, so a
    one-off oddity won't lose the user a template.
    """
    try:
        with src.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        LOG.warning(
            "templates.seed: %s isn't valid JSON (%s); copying bytes",
            src, e,
        )
        dst.write_bytes(src.read_bytes())
        return
    # Match the storage modules' atomic-write conventions.
    from ..core.io_helpers import atomic_write_json
    atomic_write_json(dst, payload)


def _seed_one(
    kind: str,
    bundled_subdir: str,
    *,
    force: bool,
    on_seed: Callable[[str, Path], None] | None,
) -> tuple[int, int]:
    """Seed one template kind. Returns ``(copied, skipped)``.

    - ``copied`` = files written this run.
    - ``skipped`` = bundled files NOT written (because the user's
      library wasn't empty and ``force=False``, or because a
      destination of the same name already existed).
    """
    src_dir = bundled_templates_dir(bundled_subdir)
    if not src_dir.exists() or not src_dir.is_dir():
        LOG.debug("templates.seed: bundled dir missing %s; skipping", src_dir)
        return (0, 0)
    try:
        target_dir = _target_storage_dir(kind)
    except Exception as e:  # noqa: BLE001
        LOG.warning("templates.seed: couldn't resolve target for %s: %s",
                    kind, e)
        return (0, 0)

    if not force and not _is_library_empty(target_dir):
        LOG.debug("templates.seed: %s library not empty; skipping seed",
                  kind)
        return (0, 0)

    copied = 0
    skipped = 0
    for src in sorted(src_dir.glob("*.json")):
        dst = target_dir / src.name
        if dst.exists() and not force:
            skipped += 1
            continue
        try:
            _copy_json(src, dst)
            copied += 1
            if on_seed is not None:
                try:
                    on_seed(kind, dst)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            LOG.warning("templates.seed: failed to copy %s → %s: %s",
                        src, dst, e)
            skipped += 1
    return (copied, skipped)


def seed_default_templates(
    *,
    force: bool = False,
    on_seed: Callable[[str, Path], None] | None = None,
) -> dict:
    """Copy bundled starter templates into the user-local libraries.

    Parameters
    ----------
    force
        If True, overwrite existing files of the same name and ignore
        the "library is empty" guard. Use this only when the user
        explicitly asks for "Restore Default Templates".
    on_seed
        Optional callback invoked with ``(kind, dst_path)`` after each
        successful file copy. Exceptions from the callback are
        swallowed.

    Returns
    -------
    dict
        ``{"copied": int, "skipped": int, "by_kind": {kind: (c, s)}}``.
    """
    by_kind: dict = {}
    total_copied = 0
    total_skipped = 0
    for bundled_subdir, kind in _TEMPLATE_KINDS:
        copied, skipped = _seed_one(
            kind, bundled_subdir, force=force, on_seed=on_seed,
        )
        by_kind[kind] = (copied, skipped)
        total_copied += copied
        total_skipped += skipped
    return {
        "copied": total_copied,
        "skipped": total_skipped,
        "by_kind": by_kind,
    }


def _sentinel_path() -> Path:
    return _cache_dir() / _SENTINEL_NAME


def seed_default_templates_if_empty(
    *,
    on_seed: Callable[[str, Path], None] | None = None,
) -> dict:
    """First-run safe wrapper around :func:`seed_default_templates`.

    No-op if the sentinel file already exists. Otherwise runs the
    seeder (which itself per-library guards on "is empty"), then
    writes the sentinel.

    Returns the same dict as :func:`seed_default_templates`, or
    ``{"copied": 0, "skipped": 0, "by_kind": {}}`` on no-op.
    """
    try:
        sentinel = _sentinel_path()
    except Exception as e:  # noqa: BLE001
        LOG.warning("templates.seed: couldn't resolve sentinel path: %s", e)
        return {"copied": 0, "skipped": 0, "by_kind": {}}
    if sentinel.exists():
        return {"copied": 0, "skipped": 0, "by_kind": {}}

    result = seed_default_templates(force=False, on_seed=on_seed)
    try:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(
            "TradingLab starter-pack templates seeded.\n"
            "Delete this file to re-seed on next launch.\n",
            encoding="utf-8",
        )
    except OSError as e:
        LOG.warning("templates.seed: couldn't write sentinel %s: %s",
                    sentinel, e)
    if result["copied"]:
        LOG.info("templates.seed: seeded %d starter templates "
                 "(by kind: %s)",
                 result["copied"], result["by_kind"])
    return result


__all__ = [
    "bundled_templates_dir",
    "seed_default_templates",
    "seed_default_templates_if_empty",
]
