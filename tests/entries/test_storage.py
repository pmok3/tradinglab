"""Tests for tradinglab.entries.storage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    SizingKind,
    SizingRule,
    TriggerKind,
    Universe,
)
from tradinglab.entries import storage


def _good(name: str = "Test", **overrides) -> EntryStrategy:
    s = EntryStrategy(
        name=name,
        direction=Direction.LONG,
        universe=Universe(symbols=("AAPL",)),
        trigger=EntryTrigger(kind=TriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=100),
    )
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


@pytest.fixture
def root(tmp_path: Path) -> Path:
    d = tmp_path / "entry_strategies"
    d.mkdir()
    return d


class TestSaveLoad:
    def test_round_trip(self, root):
        s = _good("VWAP reclaim")
        path = storage.save(s, root=root)
        assert path.exists()
        assert path.suffix == ".json"
        out = storage.load(s.id, root=root)
        assert out.id == s.id
        assert out.name == s.name

    def test_save_invalid_raises(self, root):
        s = _good("")  # empty name -> invalid
        with pytest.raises(ValueError, match="invalid"):
            storage.save(s, root=root)

    def test_load_missing_raises(self, root):
        with pytest.raises(FileNotFoundError):
            storage.load("nonexistent-id", root=root)

    def test_save_creates_index(self, root):
        s = _good("Indexed")
        storage.save(s, root=root)
        idx_file = root / "_index.json"
        assert idx_file.exists()
        idx = json.loads(idx_file.read_text(encoding="utf-8"))
        assert idx[s.id] == "Indexed"


class TestLoadAll:
    def test_empty_dir(self, root):
        good, broken = storage.load_all(root=root)
        assert good == [] and broken == []

    def test_loads_multiple(self, root):
        a = _good("A")
        b = _good("B")
        storage.save(a, root=root)
        storage.save(b, root=root)
        good, broken = storage.load_all(root=root)
        assert len(good) == 2
        assert {s.name for s in good} == {"A", "B"}
        assert broken == []

    def test_corrupt_json_surfaced_as_broken(self, root):
        bad = root / "abc-bad.json"
        bad.write_text("not json", encoding="utf-8")
        good, broken = storage.load_all(root=root)
        assert good == []
        assert len(broken) == 1
        assert "JSON" in broken[0].error or "json" in broken[0].error.lower()
        assert broken[0].path == bad

    def test_invalid_strategy_surfaced_as_broken(self, root):
        s = _good("orig")
        storage.save(s, root=root)
        # Tamper: replace name with empty.
        path = root / f"{s.id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["name"] = ""
        path.write_text(json.dumps(data), encoding="utf-8")
        good, broken = storage.load_all(root=root)
        assert good == []
        assert len(broken) == 1
        assert "name" in broken[0].error
        assert broken[0].raw_json is not None  # original text preserved

    def test_index_file_skipped(self, root):
        s = _good("S")
        storage.save(s, root=root)
        good, broken = storage.load_all(root=root)
        assert len(good) == 1


class TestDelete:
    def test_removes_file_and_index_entry(self, root):
        s = _good("Doomed")
        storage.save(s, root=root)
        assert storage.delete(s.id, root=root) is True
        assert not (root / f"{s.id}.json").exists()
        idx = json.loads((root / "_index.json").read_text(encoding="utf-8"))
        assert s.id not in idx

    def test_delete_nonexistent_returns_false(self, root):
        assert storage.delete("missing", root=root) is False


class TestImportExport:
    def test_export_and_reimport(self, root, tmp_path):
        s = _good("Exported")
        storage.save(s, root=root)
        export_path = tmp_path / "exported.json"
        storage.export_to_path(s, export_path)
        assert export_path.exists()

        # Use an empty target dir to avoid id collision.
        new_root = tmp_path / "import_target"
        new_root.mkdir()
        imported = storage.import_from_path(export_path, root=new_root)
        assert imported.id == s.id  # no collision -> id preserved
        assert imported.name == "Exported"

    def test_import_collision_rename(self, root, tmp_path):
        s = _good("Original")
        storage.save(s, root=root)
        # Export then re-import into the SAME root.
        export_path = tmp_path / "x.json"
        storage.export_to_path(s, export_path)
        imported = storage.import_from_path(
            export_path, root=root, on_id_collision="rename",
        )
        assert imported.id != s.id
        assert "(imported)" in imported.name
        # Both strategies survive.
        good, _ = storage.load_all(root=root)
        assert len(good) == 2

    def test_import_collision_reject(self, root, tmp_path):
        s = _good("Original")
        storage.save(s, root=root)
        export_path = tmp_path / "x.json"
        storage.export_to_path(s, export_path)
        with pytest.raises(ValueError, match="already exists"):
            storage.import_from_path(export_path, root=root, on_id_collision="reject")

    def test_import_invalid_raises(self, root, tmp_path):
        bad_path = tmp_path / "bad.json"
        bad_path.write_text(json.dumps({"name": "", "schema_version": 1}), encoding="utf-8")
        with pytest.raises((ValueError, KeyError, TypeError)):
            storage.import_from_path(bad_path, root=root)
