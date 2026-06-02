"""Storage-pattern meta-test.

Pins the contract from AGENTS.md §7.22: every subsystem `storage.py`
under `src/tradinglab/` uses the generic
``core.json_collection_store.JsonObjectStore[T]`` primitive instead of
hand-rolling the index/load/save/triage pattern (~150 LOC each, ~900
LOC total).

Deferred subsystems per §7.22 are grandfathered with the documented
reason; the preferred direction is to migrate them by introducing the
sibling primitives they need (e.g. `JsonEnvelopeStore` for watchlists,
`JsonListStore[T]` for positions).

Audit ``storage-pattern``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "tradinglab"


# Subsystems whose `storage.py` cannot use `JsonObjectStore[T]` because
# their on-disk layout doesn't match the "one record per file" pattern.
# Each entry documents the divergence + the sibling primitive that
# would be needed to migrate (per AGENTS.md §7.22).
_JSON_OBJECT_STORE_EXEMPTIONS: dict[str, str] = {
    "watchlists/storage.py": (
        "Single consolidated JSON envelope {version, watchlists, "
        "pinned} — generic assumes one-record-per-file. Migration "
        "needs a sibling JsonEnvelopeStore primitive."
    ),
    "strategy_tester/storage.py": (
        "Directory-per-Run layout (config + manifest + per-symbol "
        "JSONs + aggregate.json + trades.csv + screenshots/ + report). "
        "Generic assumes save(obj) -> one file."
    ),
    "positions/storage.py": (
        "Two singleton blob files (open.json containing a list, "
        "trail_state.json containing an opaque dict). Migration "
        "needs a sibling JsonListStore[T] primitive."
    ),
}


def test_every_storage_module_uses_json_object_store_or_is_exempt():
    """Per AGENTS.md §7.22: every subsystem `storage.py` uses
    ``core.json_collection_store.JsonObjectStore[T]`` or is explicitly
    exempt with a documented reason (a layout the generic primitive
    doesn't model — needs its own sibling primitive).

    Audit ``storage-pattern``.
    """
    stores = sorted(_SRC.rglob("storage.py"))
    missing: list[str] = []
    for store in stores:
        rel = store.relative_to(_SRC).as_posix()
        if rel in _JSON_OBJECT_STORE_EXEMPTIONS:
            continue
        text = store.read_text(encoding="utf-8")
        # Look for use of the shared primitive — either via direct
        # JsonObjectStore[T](...) instantiation or via importing
        # from core.json_collection_store.
        if "JsonObjectStore" in text or "json_collection_store" in text:
            continue
        missing.append(f"  - {rel}")
    if missing:
        pytest.fail(
            "Storage modules NOT using JsonObjectStore[T] "
            "(§7.22 hand-rolling reintroduces drift between "
            "subsystems):\n\n" + "\n".join(missing) + "\n\n"
            "Migrate to `from ..core.json_collection_store import "
            "JsonObjectStore` (see entries/storage.py as the canonical "
            "example) OR add to _JSON_OBJECT_STORE_EXEMPTIONS with a "
            "documented reason."
        )


def test_json_object_store_exemptions_correspond_to_real_files():
    """Catch stale entries in :data:`_JSON_OBJECT_STORE_EXEMPTIONS`."""
    actual = {p.relative_to(_SRC).as_posix() for p in _SRC.rglob("storage.py")}
    stale = sorted(
        f for f in _JSON_OBJECT_STORE_EXEMPTIONS if f not in actual
    )
    assert not stale, (
        "Stale entries in _JSON_OBJECT_STORE_EXEMPTIONS:\n  - "
        + "\n  - ".join(stale)
    )
