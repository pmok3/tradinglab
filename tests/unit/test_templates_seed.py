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
from tradinglab.templates.seed import _is_library_empty


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
# seed_default_templates_if_empty
# ---------------------------------------------------------------------------


def test_first_run_writes_sentinel(isolated_cache_dir: Path) -> None:
    result = seed_default_templates_if_empty()
    assert result["copied"] >= 15
    sentinel = isolated_cache_dir / ".templates_seeded"
    assert sentinel.exists()
    assert "TradingLab" in sentinel.read_text(encoding="utf-8")


def test_second_run_is_noop_when_sentinel_present(
    isolated_cache_dir: Path,
) -> None:
    seed_default_templates_if_empty()
    second = seed_default_templates_if_empty()
    assert second == {"copied": 0, "skipped": 0, "by_kind": {}}


def test_deleting_sentinel_allows_reseed(isolated_cache_dir: Path) -> None:
    seed_default_templates_if_empty()
    (isolated_cache_dir / ".templates_seeded").unlink()
    # Even without the sentinel, libraries are now non-empty so each
    # kind is skipped — but the sentinel is rewritten.
    result = seed_default_templates_if_empty()
    assert result["copied"] == 0
    assert (isolated_cache_dir / ".templates_seeded").exists()


def test_on_seed_callback_is_invoked(isolated_cache_dir: Path) -> None:
    seen: list = []

    def cb(kind: str, path: Path) -> None:
        seen.append((kind, path.name))

    result = seed_default_templates_if_empty(on_seed=cb)
    assert len(seen) == result["copied"]
    assert {k for k, _ in seen} == {"entries", "exits", "scans"}
