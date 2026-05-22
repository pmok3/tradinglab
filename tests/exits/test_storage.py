"""Tests for ``exits.storage``."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from tradinglab.exits import model as model_mod
from tradinglab.exits import storage as storage_mod
from tradinglab.exits.model import (
    CURRENT_SCHEMA_VERSION,
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
    OCOGroup,
    TriggerKind,
)
from tradinglab.exits.storage import (
    BrokenStrategy,
    CollisionDecision,
    delete,
    export_strategy,
    find_by_name,
    import_strategy,
    load,
    load_all,
    save,
    strategy_path,
)


@pytest.fixture
def isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr("tradinglab.disk_cache._cache_dir", lambda: tmp_path)
    monkeypatch.setattr("tradinglab.exits.storage._cache_dir", lambda: tmp_path)
    yield tmp_path


def _bracket(name: str = "bracket-AAPL") -> ExitStrategy:
    pt = ExitLeg(
        id="pt", label="profit-target",
        triggers=[ExitTrigger(kind=TriggerKind.LIMIT, price=200.0)],
    )
    stop = ExitLeg(
        id="stop", label="hard-stop",
        triggers=[ExitTrigger(kind=TriggerKind.STOP, price=180.0)],
    )
    return ExitStrategy(
        name=name,
        legs=[pt, stop],
        oco_groups=[OCOGroup(leg_ids=("pt", "stop"))],
    )


# ---------------------------------------------------------------------------
# Save / load round-trip
# ---------------------------------------------------------------------------


def test_save_and_load(isolated_cache_dir):
    s = _bracket()
    save(s)
    loaded = load(s.id)
    assert loaded.name == s.name
    assert loaded.id == s.id
    assert len(loaded.legs) == 2


def test_save_creates_file_in_strategies_dir(isolated_cache_dir):
    s = _bracket()
    save(s)
    expected = strategy_path(s.id)
    assert expected.exists()
    assert expected.parent.name == "exit_strategies"


def test_load_missing_raises(isolated_cache_dir):
    with pytest.raises(FileNotFoundError):
        load("does-not-exist")


def test_save_invalid_strategy_raises(isolated_cache_dir):
    s = ExitStrategy(name="")  # empty name fails validation
    with pytest.raises(ValueError, match="invalid"):
        save(s)


def test_save_invalid_does_not_create_file(isolated_cache_dir):
    s = ExitStrategy(name="")
    with pytest.raises(ValueError):
        save(s)
    assert not strategy_path(s.id).exists()


# ---------------------------------------------------------------------------
# load_all — strategies + broken
# ---------------------------------------------------------------------------


def test_load_all_empty_dir_returns_empty(isolated_cache_dir):
    strategies, broken = load_all()
    assert strategies == []
    assert broken == []


def test_load_all_returns_saved(isolated_cache_dir):
    save(_bracket("alpha"))
    save(_bracket("beta"))
    strategies, broken = load_all()
    assert len(strategies) == 2
    assert [s.name for s in strategies] == ["alpha", "beta"]
    assert broken == []


def test_load_all_skips_unparseable_files(isolated_cache_dir):
    save(_bracket("good"))
    # Drop a file that doesn't even parse.
    (isolated_cache_dir / "exit_strategies" / "deadbeef-0000-0000-0000-000000000000.json").write_text(
        "not-json{{{"
    )
    strategies, broken = load_all()
    assert len(strategies) == 1
    assert broken == []  # unparseable goes to LOG, not broken


def test_load_all_marks_broken_validation_failure(isolated_cache_dir):
    # Hand-craft a JSON that parses + constructs but fails validation
    # (empty name).
    bad_dict = ExitStrategy(name="x", legs=[ExitLeg(id="L", triggers=[ExitTrigger()])]).to_dict()
    bad_dict["name"] = ""
    bad_dict["id"] = "abcdef00-aaaa-bbbb-cccc-ddddeeeeffff"
    path = isolated_cache_dir / "exit_strategies"
    path.mkdir(exist_ok=True)
    (path / "abcdef00-aaaa-bbbb-cccc-ddddeeeeffff.json").write_text(
        json.dumps(bad_dict)
    )
    strategies, broken = load_all()
    assert strategies == []
    assert len(broken) == 1
    assert broken[0].id == "abcdef00-aaaa-bbbb-cccc-ddddeeeeffff"
    assert "name is empty" in broken[0].reason
    assert isinstance(broken[0].raw_json, dict)


def test_load_all_marks_broken_construction_failure(isolated_cache_dir):
    # Trigger with unknown kind — fails ExitStrategy.from_dict.
    bad = {
        "id": "deadbeef-0000-0000-0000-000000000001",
        "name": "broken-construct",
        "legs": [
            {
                "id": "L1",
                "label": "",
                "enabled": True,
                "triggers": [{"kind": "rocket"}],
            }
        ],
        "oco_groups": [],
        "schema_version": CURRENT_SCHEMA_VERSION,
    }
    path = isolated_cache_dir / "exit_strategies"
    path.mkdir(exist_ok=True)
    (path / "deadbeef-0000-0000-0000-000000000001.json").write_text(json.dumps(bad))
    strategies, broken = load_all()
    assert strategies == []
    assert len(broken) == 1


def test_load_strict_raises_on_future_schema(isolated_cache_dir):
    s = _bracket()
    save(s)
    p = strategy_path(s.id)
    raw = json.loads(p.read_text())
    raw["schema_version"] = CURRENT_SCHEMA_VERSION + 1
    p.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load(s.id)


# ---------------------------------------------------------------------------
# Delete / find_by_name
# ---------------------------------------------------------------------------


def test_delete_existing_returns_true(isolated_cache_dir):
    s = _bracket()
    save(s)
    assert delete(s.id) is True
    assert not strategy_path(s.id).exists()


def test_delete_missing_returns_false(isolated_cache_dir):
    assert delete("never-saved") is False


def test_find_by_name_case_insensitive(isolated_cache_dir):
    save(_bracket("Bracket-AAPL"))
    found = find_by_name("BRACKET-aapl")
    assert found is not None
    assert found.name == "Bracket-AAPL"


def test_find_by_name_returns_none_when_missing(isolated_cache_dir):
    assert find_by_name("nope") is None


# ---------------------------------------------------------------------------
# Export / import
# ---------------------------------------------------------------------------


def test_export_then_import_round_trip(isolated_cache_dir, tmp_path):
    s = _bracket("alpha")
    out = tmp_path / "alpha.json"
    export_strategy(s, out)
    assert out.exists()

    imported = import_strategy(out)
    assert imported is not None
    assert imported.name == "alpha"
    # Was NOT a collision: imported.id == s.id (no local lib first).
    assert imported.id == s.id


def test_import_id_collision_overwrite(isolated_cache_dir, tmp_path):
    s = _bracket("orig")
    save(s)

    # Modify and export
    s2 = _bracket("orig-edited")
    s2.id = s.id  # same id
    out = tmp_path / "out.json"
    export_strategy(s2, out)

    seen = []

    def cb(local, incoming):
        seen.append((local.name, incoming.name))
        return CollisionDecision.OVERWRITE

    imported = import_strategy(out, on_collision=cb)
    assert imported is not None
    assert imported.name == "orig-edited"
    assert seen == [("orig", "orig-edited")]
    # Verify on disk
    loaded = load(s.id)
    assert loaded.name == "orig-edited"


def test_import_id_collision_cancel(isolated_cache_dir, tmp_path):
    s = _bracket("orig")
    save(s)
    out = tmp_path / "out.json"
    s2 = _bracket("renamed")
    s2.id = s.id
    export_strategy(s2, out)

    imported = import_strategy(
        out, on_collision=lambda _l, _i: CollisionDecision.CANCEL
    )
    assert imported is None
    # Original unchanged
    assert load(s.id).name == "orig"


def test_import_id_collision_rename_assigns_new_id(isolated_cache_dir, tmp_path):
    s = _bracket("orig")
    save(s)
    out = tmp_path / "out.json"
    s2 = _bracket("orig")  # same name AND same id below
    s2.id = s.id
    export_strategy(s2, out)

    imported = import_strategy(
        out, on_collision=lambda _l, _i: CollisionDecision.RENAME
    )
    assert imported is not None
    assert imported.id != s.id  # new UUID
    assert imported.name != "orig"  # also bumped to avoid name collision
    # Both exist on disk now
    strategies, _ = load_all()
    assert len(strategies) == 2


def test_import_name_collision_distinct_id_overwrite(isolated_cache_dir, tmp_path):
    """Name collision with different id: OVERWRITE keeps local id."""
    s = _bracket("orig")
    save(s)
    out = tmp_path / "out.json"
    s2 = _bracket("orig")  # same name, fresh id
    export_strategy(s2, out)

    imported = import_strategy(
        out, on_collision=lambda _l, _i: CollisionDecision.OVERWRITE
    )
    assert imported is not None
    # Overwrite-by-name preserves local id (so position bindings survive)
    assert imported.id == s.id
    strategies, _ = load_all()
    assert len(strategies) == 1


def test_import_default_callback_cancels(isolated_cache_dir, tmp_path):
    s = _bracket("orig")
    save(s)
    out = tmp_path / "out.json"
    export_strategy(s, out)
    # No callback → CANCEL
    imported = import_strategy(out)
    assert imported is None


def test_import_invalid_strategy_raises(isolated_cache_dir, tmp_path):
    bad = {
        "id": "deadbeef-0000-0000-0000-000000000002",
        "name": "",  # invalid
        "legs": [],
        "oco_groups": [],
        "schema_version": CURRENT_SCHEMA_VERSION,
    }
    out = tmp_path / "bad.json"
    out.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        import_strategy(out)


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_save_atomic_no_temp_files_left(isolated_cache_dir):
    s = _bracket()
    save(s)
    files = list((isolated_cache_dir / "exit_strategies").iterdir())
    # Only the final UUID file
    assert len(files) == 1
    assert files[0].suffix == ".json"
    assert ".tmp" not in files[0].name
