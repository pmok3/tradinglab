"""First-run seeding of bundled starter templates.

See :mod:`tradinglab.templates` package docstring for the high-
level contract. This module is the implementation:

- :func:`seed_default_templates_if_empty` — the startup entry point.
  Offers each bundled template to the user's library **exactly once
  ever**, tracked in a JSON ledger (``.templates_seeded``). This means
  newly-shipped catalog templates reach EXISTING users on upgrade —
  not just brand-new installs — while user edits/deletions are never
  clobbered or resurrected. (Name retained for back-compat; before the
  ledger it seeded only once, gated on an empty library + a binary
  sentinel, so the 15 templates added after the original 5-template
  starter pack never reached upgraders.)
- :func:`seed_default_templates` — unconditional seed used by the
  "Restore Default Templates" menu; respects per-storage-dir "library
  is empty" guards (set ``force=True`` to overwrite existing files of
  the same id).
- :func:`bundled_templates_dir` — resolves a bundled template
  directory under ``data/`` for both source checkouts and frozen
  PyInstaller builds.

The startup seeder is conservative but **additive**:

- A bundled template is offered (copied) once, then its filename is
  recorded in the per-kind ledger. A recorded template is never
  re-offered, so deleting a seeded template makes it stay deleted.
- A bundled file is NEVER overwritten if a same-named file already
  exists on disk (the user may have edited it); it is just recorded.
- The legacy plain-text sentinel (pre-ledger) is treated as "nothing
  recorded yet", so the first launch after upgrading fills the library
  up to the full bundled set without clobbering existing files.
- Each copy is wrapped in try/except — one bad template can't block the
  others, and a missing ``data/<dir>`` (e.g. partial wheel) simply
  skips that library.
- The ledger is written best-effort at the end; if it fails to write,
  the next launch re-offers (idempotent because the per-file existence
  check still skips files already on disk).
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
#: Ledger format version. The ``.templates_seeded`` file is a JSON
#: document ``{"version": 1, "seeded": {kind: [filenames]}}`` recording
#: which bundled templates have already been offered to the user. A
#: pre-ledger plain-text sentinel parses as "nothing recorded yet" so
#: upgraders get the templates added since their last install.
_LEDGER_VERSION = 1

# Mapping: (bundled_subdir_under_data, target_storage_dir_callable).
# We import storage helpers lazily inside _seed_one() to keep this
# module import-cheap (templates package is touched at startup).
_TEMPLATE_KINDS: tuple[tuple[str, str], ...] = (
    ("entry_strategy_templates", "entries"),
    ("exit_strategy_templates", "exits"),
    ("scanner_templates", "scans"),
)

#: Indicator presets are handled out-of-band from ``_TEMPLATE_KINDS``
#: because they persist in a single JSON *envelope*
#: (``indicators.preset_store``), not one file per record like the
#: strategy / scan libraries. Same bundled-``data/`` + ledger machinery,
#: different merge target. ``_INDICATOR_PRESETS_KIND`` is the ledger key.
_INDICATOR_PRESETS_SUBDIR = "indicator_presets"
_INDICATOR_PRESETS_KIND = "indicator_presets"


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
    # Indicator presets (single-envelope store). With ``force`` this
    # overwrites same-named presets; otherwise it skips them.
    ip_copied, ip_skipped, _ = _seed_indicator_presets_additive(
        set(), force=force, on_seed=on_seed,
    )
    by_kind[_INDICATOR_PRESETS_KIND] = (ip_copied, ip_skipped)
    total_copied += ip_copied
    total_skipped += ip_skipped
    return {
        "copied": total_copied,
        "skipped": total_skipped,
        "by_kind": by_kind,
    }


def _sentinel_path() -> Path:
    return _cache_dir() / _SENTINEL_NAME


def _load_ledger() -> dict[str, set[str]]:
    """Return ``{kind: set(seeded_filenames)}`` from the on-disk ledger.

    Returns an empty mapping when the ledger is missing, unreadable, or
    in the legacy plain-text format (pre-ledger sentinel). The empty
    result is what makes an upgrade additive: every currently-bundled
    template is treated as "not yet offered" and gets delivered (subject
    to the per-file existence check) the first time the new build runs.
    """
    try:
        raw = _sentinel_path().read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Legacy plain-text sentinel or a corrupt ledger → migrate by
        # treating it as "nothing recorded yet".
        return {}
    if not isinstance(data, dict):
        return {}
    seeded = data.get("seeded", {})
    out: dict[str, set[str]] = {}
    if isinstance(seeded, dict):
        for kind, names in seeded.items():
            if isinstance(names, list):
                out[str(kind)] = {str(n) for n in names}
    return out


def _write_ledger(ledger: dict[str, set[str]]) -> None:
    """Persist the seeded-template ledger (best effort)."""
    path = _sentinel_path()
    payload = {
        "version": _LEDGER_VERSION,
        "_comment": (
            "TradingLab template-seed ledger. Records which bundled "
            "templates have been offered to your library so new catalog "
            "templates reach you on upgrade and your deletions are not "
            "resurrected. Delete this file to re-offer every bundled "
            "template on the next launch."
        ),
        "seeded": {k: sorted(v) for k, v in sorted(ledger.items())},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        from ..core.io_helpers import atomic_write_json
        atomic_write_json(path, payload)
    except OSError as e:
        LOG.warning("templates.seed: couldn't write ledger %s: %s", path, e)


def _seed_one_additive(
    kind: str,
    bundled_subdir: str,
    seeded_names: set[str],
    *,
    on_seed: Callable[[str, Path], None] | None,
) -> tuple[int, int, set[str]]:
    """Offer bundled templates of ``kind`` that haven't been offered yet.

    A bundled file is copied only when its name is NOT already in
    ``seeded_names`` AND no same-named file exists in the target library
    (so user edits are never clobbered). Returns
    ``(copied, skipped, considered)`` where ``considered`` is every
    bundled filename seen this run — the caller folds it into the ledger
    so each template is offered exactly once over the app's lifetime.
    """
    src_dir = bundled_templates_dir(bundled_subdir)
    if not src_dir.exists() or not src_dir.is_dir():
        LOG.debug("templates.seed: bundled dir missing %s; skipping", src_dir)
        return (0, 0, set())
    try:
        target_dir = _target_storage_dir(kind)
    except Exception as e:  # noqa: BLE001
        LOG.warning("templates.seed: couldn't resolve target for %s: %s",
                    kind, e)
        return (0, 0, set())

    copied = 0
    skipped = 0
    considered: set[str] = set()
    for src in sorted(src_dir.glob("*.json")):
        considered.add(src.name)
        if src.name in seeded_names:
            # Already offered before — respect prior state (incl. a
            # deletion). Never re-offer.
            continue
        dst = target_dir / src.name
        if dst.exists():
            # User already has a file by this name (seeded earlier by a
            # build that predates the ledger, or hand-placed). Don't
            # clobber it; just record it as offered.
            skipped += 1
            continue
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
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
    return (copied, skipped, considered)


def _seed_indicator_presets_additive(
    seeded_names: set[str],
    *,
    force: bool,
    on_seed: Callable[[str, Path], None] | None,
) -> tuple[int, int, set[str]]:
    """Offer bundled indicator presets that haven't been offered yet.

    Indicator presets don't use the per-file storage the strategy / scan
    libraries do — they live in a single JSON *envelope* owned by
    :mod:`indicators.preset_store`. So instead of copying files we parse
    each bundled ``data/indicator_presets/<slug>.json`` (translating the
    compact starter schema to canonical config dicts via
    :func:`preset_store.read_bundled_preset`) and merge it into the user's
    preset table, keyed by the preset's display name.

    Ledger + skip-if-exists semantics mirror :func:`_seed_one_additive`:
    a preset already offered (its *filename* is in ``seeded_names``) is
    never re-offered — so a preset the user later deleted stays deleted —
    and a preset *name* the user already has is not clobbered unless
    ``force``. Returns ``(copied, skipped, considered)`` where
    ``considered`` is every bundled filename seen this run (folded into
    the ledger so each is offered exactly once).
    """
    from ..indicators import preset_store
    src_dir = bundled_templates_dir(_INDICATOR_PRESETS_SUBDIR)
    if not src_dir.exists() or not src_dir.is_dir():
        LOG.debug("templates.seed: bundled dir missing %s; skipping", src_dir)
        return (0, 0, set())
    try:
        presets, active = preset_store.load_presets()
    except Exception:  # noqa: BLE001
        presets, active = {}, None

    copied = 0
    skipped = 0
    considered: set[str] = set()
    for src in sorted(src_dir.glob("*.json")):
        considered.add(src.name)
        if not force and src.name in seeded_names:
            # Already offered before — respect prior state (incl. a
            # deletion). Never re-offer.
            continue
        parsed = preset_store.read_bundled_preset(src)
        if parsed is None:
            skipped += 1
            continue
        name, config_dicts = parsed
        if not name or not config_dicts:
            skipped += 1
            continue
        if name in presets and not force:
            # User already has a preset by this name (seeded earlier or
            # hand-saved). Don't clobber; just record it as offered.
            skipped += 1
            continue
        presets[name] = config_dicts
        copied += 1
        if on_seed is not None:
            try:
                on_seed(_INDICATOR_PRESETS_KIND, src)
            except Exception:  # noqa: BLE001
                pass
    if copied:
        try:
            preset_store.save_presets(presets, active)
        except Exception:  # noqa: BLE001
            LOG.warning("templates.seed: failed to persist seeded presets",
                        exc_info=True)
    return (copied, skipped, considered)


def seed_default_templates_if_empty(
    *,
    on_seed: Callable[[str, Path], None] | None = None,
) -> dict:
    """Offer any not-yet-offered bundled templates to the user libraries.

    Runs at every app startup (cheap: it globs a few dozen bundled JSON
    files and reads a small ledger). For each template kind it copies
    every bundled template the user has never been offered — recorded in
    the ``.templates_seeded`` ledger by filename — skipping any file that
    already exists on disk so user edits are preserved. Newly-shipped
    catalog templates therefore reach EXISTING users on upgrade, while
    templates the user has already seen (and possibly deleted) are never
    re-offered.

    Name kept for backward compatibility; the historical "no-op once the
    sentinel exists / only seed an empty library" behaviour was the bug
    that left upgraders stuck with the original 5-template starter pack.

    Returns ``{"copied": int, "skipped": int, "by_kind": {kind: (c, s)}}``.
    """
    ledger = _load_ledger()
    by_kind: dict = {}
    total_copied = 0
    total_skipped = 0
    changed = False
    for bundled_subdir, kind in _TEMPLATE_KINDS:
        seeded_names = ledger.get(kind, set())
        copied, skipped, considered = _seed_one_additive(
            kind, bundled_subdir, seeded_names, on_seed=on_seed,
        )
        new_names = seeded_names | considered
        if copied or new_names != seeded_names:
            changed = True
        ledger[kind] = new_names
        by_kind[kind] = (copied, skipped)
        total_copied += copied
        total_skipped += skipped

    # Indicator presets: single-envelope store, dedicated additive seeder
    # (same ledger semantics keyed by ``_INDICATOR_PRESETS_KIND``).
    ip_seeded = ledger.get(_INDICATOR_PRESETS_KIND, set())
    ip_copied, ip_skipped, ip_considered = _seed_indicator_presets_additive(
        ip_seeded, force=False, on_seed=on_seed,
    )
    ip_new = ip_seeded | ip_considered
    if ip_copied or ip_new != ip_seeded:
        changed = True
    ledger[_INDICATOR_PRESETS_KIND] = ip_new
    by_kind[_INDICATOR_PRESETS_KIND] = (ip_copied, ip_skipped)
    total_copied += ip_copied
    total_skipped += ip_skipped

    # Persist the ledger when it changed, or when the file doesn't exist
    # yet / is still in the legacy plain-text format (so we migrate it to
    # JSON exactly once).
    try:
        needs_write = changed
        if not needs_write:
            try:
                json.loads(_sentinel_path().read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                needs_write = True
        if needs_write:
            _write_ledger(ledger)
    except Exception as e:  # noqa: BLE001
        LOG.warning("templates.seed: ledger persistence check failed: %s", e)

    if total_copied:
        LOG.info("templates.seed: offered %d new template(s) "
                 "(by kind: %s)",
                 total_copied, by_kind)
    return {
        "copied": total_copied,
        "skipped": total_skipped,
        "by_kind": by_kind,
    }


__all__ = [
    "bundled_templates_dir",
    "seed_default_templates",
    "seed_default_templates_if_empty",
]
