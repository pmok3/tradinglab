"""Unit tests for :mod:`tradinglab.templates.seed`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradinglab.templates import (
    bundled_templates_dir,
    seed_default_templates,
    seed_default_templates_if_empty,
)
from tradinglab.templates.seed import _is_library_empty, _load_ledger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point TRADINGLAB_DATA_DIR at a fresh tmp dir."""
    monkeypatch.setenv("TRADINGLAB_DATA_DIR", str(tmp_path))
    # Force any modules that already imported the cache-dir resolver
    # to pick up the env var.
    monkeypatch.delenv("TRADINGLAB_CACHE_DIR", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# bundled_templates_dir
# ---------------------------------------------------------------------------


def test_bundled_dirs_exist_for_three_kinds() -> None:
    for sub in (
        "entry_strategy_templates",
        "exit_strategy_templates",
        "scanner_templates",
    ):
        d = bundled_templates_dir(sub)
        assert d.exists(), f"{d} should be shipped"
        assert d.is_dir()
        jsons = list(d.glob("*.json"))
        assert len(jsons) >= 5, (
            f"{sub} should have at least 5 JSON templates, got {len(jsons)}"
        )


# ---------------------------------------------------------------------------
# _is_library_empty
# ---------------------------------------------------------------------------


def test_is_library_empty_true_for_missing_dir(tmp_path: Path) -> None:
    assert _is_library_empty(tmp_path / "nonexistent")


def test_is_library_empty_true_for_empty_dir(tmp_path: Path) -> None:
    d = tmp_path / "lib"
    d.mkdir()
    assert _is_library_empty(d)


def test_is_library_empty_false_with_user_strategy(tmp_path: Path) -> None:
    d = tmp_path / "lib"
    d.mkdir()
    (d / "mine.json").write_text("{}", encoding="utf-8")
    assert not _is_library_empty(d)


def test_is_library_empty_ignores_index_meta_file(tmp_path: Path) -> None:
    d = tmp_path / "lib"
    d.mkdir()
    (d / "_index.json").write_text("{}", encoding="utf-8")
    assert _is_library_empty(d)


# ---------------------------------------------------------------------------
# seed_default_templates
# ---------------------------------------------------------------------------


def test_seed_default_templates_writes_each_kind(isolated_cache_dir: Path) -> None:
    result = seed_default_templates()
    assert result["copied"] >= 15
    # All three kinds should have copied at least 5.
    for kind in ("entries", "exits", "scans"):
        copied, _skipped = result["by_kind"][kind]
        assert copied >= 5, (
            f"{kind} should have at least 5 seeded files; got {copied}"
        )


def test_seed_default_templates_writes_to_storage_dirs(
    isolated_cache_dir: Path,
) -> None:
    seed_default_templates()
    from tradinglab.entries.storage import storage_dir as entries_dir
    from tradinglab.exits.storage import exit_strategies_dir
    from tradinglab.scanner.storage import scans_dir

    assert len(list(entries_dir().glob("*.json"))) >= 5
    assert len(list(exit_strategies_dir().glob("*.json"))) >= 5
    assert len(list(scans_dir().glob("*.json"))) >= 5


def test_seed_default_templates_skips_when_library_not_empty(
    isolated_cache_dir: Path,
) -> None:
    from tradinglab.exits.storage import exit_strategies_dir
    target = exit_strategies_dir()
    target.mkdir(parents=True, exist_ok=True)
    (target / "user.json").write_text(
        json.dumps({"id": "user", "name": "user"}),
        encoding="utf-8",
    )
    result = seed_default_templates()
    # Exits should have been skipped (user.json is there).
    copied, _ = result["by_kind"]["exits"]
    assert copied == 0
    # Entries / scans still seeded.
    assert result["by_kind"]["entries"][0] >= 5
    assert result["by_kind"]["scans"][0] >= 5


def test_seed_default_templates_force_overwrites(
    isolated_cache_dir: Path,
) -> None:
    seed_default_templates()
    # Mutate one of the seeded files; re-seeding without force should
    # leave the mutation; with force, should overwrite.
    from tradinglab.exits.storage import exit_strategies_dir
    d = exit_strategies_dir()
    sample = sorted(d.glob("*.json"))[0]
    sample.write_text('{"id": "user-edit"}', encoding="utf-8")

    # No-force: library is now non-empty (it has both mutated +
    # bundled files), so all kinds are skipped.
    no_force = seed_default_templates()
    assert no_force["by_kind"]["exits"][0] == 0
    # Mutation preserved.
    assert json.loads(sample.read_text(encoding="utf-8")) == {"id": "user-edit"}

    # Force: should overwrite the same path with bundled content.
    forced = seed_default_templates(force=True)
    assert forced["by_kind"]["exits"][0] >= 5
    overwritten = json.loads(sample.read_text(encoding="utf-8"))
    assert "legs" in overwritten or "name" in overwritten


# ---------------------------------------------------------------------------
# seed_default_templates_if_empty — additive per-template ledger
# ---------------------------------------------------------------------------


def _bundled_names(sub: str) -> set[str]:
    return {p.name for p in bundled_templates_dir(sub).glob("*.json")}


def _entries_dir():
    from tradinglab.entries.storage import storage_dir
    return storage_dir()


def test_first_run_offers_all_bundled_and_writes_json_ledger(
    isolated_cache_dir: Path,
) -> None:
    result = seed_default_templates_if_empty()
    expected = (
        len(_bundled_names("entry_strategy_templates"))
        + len(_bundled_names("exit_strategy_templates"))
        + len(_bundled_names("scanner_templates"))
    )
    assert result["copied"] == expected
    # The entries library now holds every bundled entry template.
    present = {p.name for p in _entries_dir().glob("tmpl-*.json")}
    assert present == _bundled_names("entry_strategy_templates")
    # The sentinel is now a JSON ledger recording the offered filenames.
    ledger_path = isolated_cache_dir / ".templates_seeded"
    assert ledger_path.exists()
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert data["version"] >= 1
    assert set(data["seeded"]["entries"]) == _bundled_names(
        "entry_strategy_templates"
    )


def test_second_run_is_idempotent(isolated_cache_dir: Path) -> None:
    seed_default_templates_if_empty()
    second = seed_default_templates_if_empty()
    assert second["copied"] == 0
    assert second["by_kind"]["entries"][0] == 0


def test_upgrade_delivers_newly_bundled_templates(
    isolated_cache_dir: Path,
) -> None:
    """Legacy text sentinel + the original starter pack → fill to full set.

    Reproduces the reported bug: a user who installed before the catalog
    expansion has only the original 5 entries plus the pre-ledger plain-
    text sentinel. The first launch on a build with the ledger must
    deliver the newly-bundled templates without re-copying the existing
    ones.
    """
    # Legacy plain-text sentinel (pre-ledger).
    (isolated_cache_dir / ".templates_seeded").write_text(
        "TradingLab starter-pack templates seeded.\n", encoding="utf-8",
    )
    ent = _entries_dir()
    ent.mkdir(parents=True, exist_ok=True)
    bundled = sorted(
        bundled_templates_dir("entry_strategy_templates").glob("*.json")
    )
    for src in bundled[:5]:
        (ent / src.name).write_bytes(src.read_bytes())
    assert len({p.name for p in ent.glob("tmpl-*.json")}) == 5

    result = seed_default_templates_if_empty()

    present = {p.name for p in ent.glob("tmpl-*.json")}
    assert present == {p.name for p in bundled}, "all bundled entries delivered"
    # The 5 pre-existing files were skipped (recorded), not re-copied.
    copied, _skipped = result["by_kind"]["entries"]
    assert copied == len(bundled) - 5
    # Ledger migrated to JSON and records the full set.
    data = json.loads(
        (isolated_cache_dir / ".templates_seeded").read_text(encoding="utf-8")
    )
    assert set(data["seeded"]["entries"]) == {p.name for p in bundled}


def test_deletion_is_respected_after_offer(isolated_cache_dir: Path) -> None:
    """A template deleted after being offered is NOT resurrected."""
    seed_default_templates_if_empty()
    ent = _entries_dir()
    victim = sorted(ent.glob("tmpl-*.json"))[0]
    victim_name = victim.name
    victim.unlink()
    result = seed_default_templates_if_empty()
    assert result["copied"] == 0
    assert not (ent / victim_name).exists(), "deleted template stays deleted"


def test_user_edited_bundled_file_is_not_clobbered(
    isolated_cache_dir: Path,
) -> None:
    ent = _entries_dir()
    ent.mkdir(parents=True, exist_ok=True)
    sample = sorted(
        bundled_templates_dir("entry_strategy_templates").glob("*.json")
    )[0]
    (ent / sample.name).write_text('{"id": "user-edit"}', encoding="utf-8")
    seed_default_templates_if_empty()
    assert json.loads((ent / sample.name).read_text(encoding="utf-8")) == {
        "id": "user-edit"
    }
    # Still recorded as offered so it's never reconsidered.
    assert sample.name in _load_ledger()["entries"]


def test_new_template_on_later_upgrade_is_offered(
    isolated_cache_dir: Path,
) -> None:
    """A ledger missing one bundled name → only that one is offered."""
    bundled = _bundled_names("entry_strategy_templates")
    seed_default_templates_if_empty()
    ent = _entries_dir()
    # Simulate "this template shipped in a newer build": drop it from the
    # ledger AND from the library, as if it never existed for this user.
    newcomer = sorted(bundled)[0]
    (ent / newcomer).unlink(missing_ok=True)
    ledger = _load_ledger()
    ledger["entries"].discard(newcomer)
    from tradinglab.templates.seed import _write_ledger
    _write_ledger(ledger)

    result = seed_default_templates_if_empty()
    assert result["by_kind"]["entries"][0] == 1
    assert (ent / newcomer).exists()


def test_on_seed_callback_is_invoked(isolated_cache_dir: Path) -> None:
    seen: list = []

    def cb(kind: str, path: Path) -> None:
        seen.append((kind, path.name))

    result = seed_default_templates_if_empty(on_seed=cb)
    assert len(seen) == result["copied"]
    assert {k for k, _ in seen} == {"entries", "exits", "scans"}
