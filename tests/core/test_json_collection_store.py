"""Tests for the generic JsonObjectStore."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from tradinglab.core.json_collection_store import (
    BrokenRecord,
    JsonObjectStore,
)


@dataclass
class Item:
    id: str
    name: str
    value: int = 0

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "value": self.value}

    @classmethod
    def from_dict(cls, data: dict) -> Item:
        if "id" not in data or "name" not in data:
            raise ValueError("missing id/name")
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            value=int(data.get("value", 0)),
        )


def _validate(item: Item) -> None:
    if not item.name:
        raise ValueError("name must be non-empty")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    d = tmp_path / "items"
    d.mkdir()
    return d


@pytest.fixture
def store(root) -> JsonObjectStore[Item]:
    return JsonObjectStore[Item](
        storage_dir=lambda: root,
        kind_label="item",
        to_dict=lambda x: x.to_dict(),
        from_dict=Item.from_dict,
        id_of=lambda x: x.id,
        validate=_validate,
        index_value_of=lambda x: x.name,
    )


class TestSaveLoad:
    def test_round_trip(self, store, root):
        item = Item(id="a1", name="Alpha", value=42)
        path = store.save(item)
        assert path.exists() and path.suffix == ".json"
        out = store.load("a1")
        assert out == item

    def test_save_invalid_raises(self, store):
        with pytest.raises(ValueError, match="non-empty"):
            store.save(Item(id="bad", name="", value=0))

    def test_load_missing_raises(self, store):
        with pytest.raises(FileNotFoundError):
            store.load("ghost")

    def test_load_malformed_raises(self, store, root):
        (root / "x1.json").write_text("not json", encoding="utf-8")
        with pytest.raises(ValueError, match="malformed"):
            store.load("x1")

    def test_save_writes_index(self, store, root):
        store.save(Item(id="i1", name="One"))
        idx = json.loads((root / "_index.json").read_text(encoding="utf-8"))
        assert idx == {"i1": "One"}

    def test_path_for_empty_id(self, store):
        with pytest.raises(ValueError, match="non-empty"):
            store.path_for("")


class TestDelete:
    def test_existing_returns_true(self, store, root):
        store.save(Item(id="d1", name="X"))
        assert store.delete("d1") is True
        assert not (root / "d1.json").exists()
        idx = json.loads((root / "_index.json").read_text(encoding="utf-8"))
        assert "d1" not in idx

    def test_missing_returns_false(self, store):
        assert store.delete("ghost") is False


class TestLoadAll:
    def test_empty_dir(self, store):
        good, broken = store.load_all()
        assert good == [] and broken == []

    def test_loads_multiple(self, store):
        store.save(Item(id="a", name="A"))
        store.save(Item(id="b", name="B"))
        good, broken = store.load_all()
        assert {x.id for x in good} == {"a", "b"}
        assert broken == []

    def test_malformed_json_becomes_broken(self, store, root):
        (root / "bad.json").write_text("not json", encoding="utf-8")
        good, broken = store.load_all()
        assert good == []
        assert len(broken) == 1
        assert isinstance(broken[0], BrokenRecord)
        assert broken[0].path == root / "bad.json"
        assert "JSON" in broken[0].error or "json" in broken[0].error.lower()

    def test_parse_failure_becomes_broken_with_raw(self, store, root):
        (root / "missing.json").write_text(
            json.dumps({"value": 1}), encoding="utf-8",
        )
        good, broken = store.load_all()
        assert good == []
        assert len(broken) == 1
        assert broken[0].raw_json is not None
        assert "parse" in broken[0].error.lower()

    def test_validation_failure_becomes_broken_with_raw(self, store, root):
        store.save(Item(id="x", name="ok"))
        # Tamper: blank the name → fails validate
        path = root / "x.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["name"] = ""
        path.write_text(json.dumps(data), encoding="utf-8")
        good, broken = store.load_all()
        assert good == []
        assert len(broken) == 1
        assert "non-empty" in broken[0].error
        assert broken[0].raw_json is not None

    def test_mixed_good_and_broken(self, store, root):
        store.save(Item(id="ok1", name="One"))
        store.save(Item(id="ok2", name="Two"))
        (root / "bust.json").write_text("oops", encoding="utf-8")
        good, broken = store.load_all()
        assert len(good) == 2
        assert len(broken) == 1

    def test_index_file_skipped(self, store, root):
        store.save(Item(id="i", name="I"))
        good, broken = store.load_all()
        assert len(good) == 1 and broken == []

    def test_non_json_files_skipped(self, store, root):
        store.save(Item(id="z", name="Z"))
        (root / "notes.txt").write_text("hi", encoding="utf-8")
        good, _ = store.load_all()
        assert len(good) == 1


class TestImportExport:
    def test_round_trip(self, store, tmp_path):
        item = Item(id="e1", name="Exported", value=7)
        store.save(item)
        dst = tmp_path / "out.json"
        store.export_to_path(item, dst)
        assert dst.exists()

        # Import to a fresh root → id preserved
        new_root = tmp_path / "imported"
        new_root.mkdir()
        imported = store.import_from_path(dst, root=new_root)
        assert imported.id == "e1"
        assert imported.name == "Exported"

    def test_import_collision_rename(self, store, tmp_path, root):
        store.save(Item(id="c1", name="Orig"))
        dst = tmp_path / "x.json"
        store.export_to_path(Item(id="c1", name="Orig"), dst)

        def rename(obj: Item) -> Item:
            obj.id = "c1-new"
            obj.name = f"{obj.name} (imported)"
            return obj

        imported = store.import_from_path(
            dst, on_id_collision="rename", rename_fn=rename,
        )
        assert imported.id == "c1-new"
        assert "(imported)" in imported.name
        good, _ = store.load_all()
        assert {x.id for x in good} == {"c1", "c1-new"}

    def test_import_collision_rename_missing_fn_raises(self, store, tmp_path):
        store.save(Item(id="c1", name="Orig"))
        dst = tmp_path / "x.json"
        store.export_to_path(Item(id="c1", name="Orig"), dst)
        with pytest.raises(ValueError, match="rename_fn"):
            store.import_from_path(dst, on_id_collision="rename")

    def test_import_collision_reject(self, store, tmp_path):
        store.save(Item(id="c1", name="Orig"))
        dst = tmp_path / "x.json"
        store.export_to_path(Item(id="c1", name="Orig"), dst)
        with pytest.raises(ValueError, match="already exists"):
            store.import_from_path(dst, on_id_collision="reject")

    def test_import_collision_overwrite(self, store, tmp_path):
        store.save(Item(id="c1", name="Orig", value=1))
        dst = tmp_path / "x.json"
        store.export_to_path(Item(id="c1", name="New", value=99), dst)
        imported = store.import_from_path(dst, on_id_collision="overwrite")
        assert imported.value == 99
        assert store.load("c1").value == 99

    def test_import_unknown_collision_raises(self, store, tmp_path):
        store.save(Item(id="c1", name="Orig"))
        dst = tmp_path / "x.json"
        store.export_to_path(Item(id="c1", name="Orig"), dst)
        with pytest.raises(ValueError, match="unknown on_id_collision"):
            store.import_from_path(dst, on_id_collision="weird")

    def test_import_invalid_raises(self, store, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"id": "b", "name": ""}), encoding="utf-8")
        with pytest.raises(ValueError, match="non-empty"):
            store.import_from_path(bad)


class TestIndex:
    def test_load_index_missing_returns_empty(self, store):
        assert store.load_index() == {}

    def test_load_index_corrupt_returns_empty(self, store, root):
        (root / "_index.json").write_text("not json", encoding="utf-8")
        assert store.load_index() == {}

    def test_refresh_rebuilds_from_files(self, store, root):
        # Write files directly (bypassing the index).
        (root / "a.json").write_text(
            json.dumps({"id": "a", "name": "A", "value": 0}),
            encoding="utf-8",
        )
        (root / "b.json").write_text(
            json.dumps({"id": "b", "name": "B", "value": 0}),
            encoding="utf-8",
        )
        idx = store.refresh_index()
        assert idx == {"a": "A", "b": "B"}
        on_disk = json.loads(
            (root / "_index.json").read_text(encoding="utf-8")
        )
        assert on_disk == {"a": "A", "b": "B"}

    def test_refresh_tolerates_broken_file(self, store, root):
        (root / "good.json").write_text(
            json.dumps({"id": "good", "name": "G"}), encoding="utf-8",
        )
        (root / "bad.json").write_text("xx", encoding="utf-8")
        idx = store.refresh_index()
        assert idx == {"good": "G"}

    def test_list_ids_sorted(self, store):
        store.save(Item(id="zzz", name="Z"))
        store.save(Item(id="aaa", name="A"))
        store.save(Item(id="mmm", name="M"))
        assert store.list_ids() == ["aaa", "mmm", "zzz"]


class TestWithoutValidate:
    def test_no_validate_skipped(self, root):
        store = JsonObjectStore[Item](
            storage_dir=lambda: root,
            kind_label="item",
            to_dict=lambda x: x.to_dict(),
            from_dict=Item.from_dict,
            id_of=lambda x: x.id,
            validate=None,
        )
        # Empty name would normally fail validate; without it, save succeeds.
        store.save(Item(id="x", name=""))
        good, broken = store.load_all()
        assert len(good) == 1 and broken == []
