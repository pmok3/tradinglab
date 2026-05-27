"""Tests for :class:`tradinglab.core.json_list_store.JsonListStore`."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pytest

from tradinglab.core.json_list_store import JsonListStore


@dataclass
class _Item:
    id: str
    value: int

    def to_dict(self) -> dict:
        return {"id": self.id, "value": self.value}

    @classmethod
    def from_dict(cls, data: dict) -> _Item:
        return cls(id=str(data["id"]), value=int(data["value"]))


def _store(tmp_path: Path, **overrides) -> JsonListStore[_Item]:
    kwargs = dict(
        path=lambda: tmp_path / "items.json",
        items_key="items",
        to_dict=lambda i: i.to_dict(),
        from_dict=_Item.from_dict,
        schema_version=1,
        kind_label="items",
    )
    kwargs.update(overrides)
    return JsonListStore(**kwargs)


# --- empty / missing --------------------------------------------------------

def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _store(tmp_path).load() == []


def test_load_with_extras_on_missing_returns_extras_none(tmp_path: Path) -> None:
    s = _store(tmp_path, extra_keys=("pinned",))
    items, extras = s.load_with_extras()
    assert items == []
    assert extras == {"pinned": None}


# --- round-trip -------------------------------------------------------------

def test_save_load_round_trip(tmp_path: Path) -> None:
    s = _store(tmp_path)
    items = [_Item("a", 1), _Item("b", 2)]
    p = s.save(items)
    assert p.exists()
    loaded = s.load()
    assert [(i.id, i.value) for i in loaded] == [("a", 1), ("b", 2)]


def test_envelope_shape_on_disk(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.save([_Item("a", 1)])
    with (tmp_path / "items.json").open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    assert raw == {"schema_version": 1, "items": [{"id": "a", "value": 1}]}


# --- bad envelope -----------------------------------------------------------

def test_load_non_object_root_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "items.json").write_text("[1, 2, 3]", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert _store(tmp_path).load() == []
    assert any("not a JSON object" in r.message for r in caplog.records)


def test_load_malformed_json_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "items.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert _store(tmp_path).load() == []


def test_load_invalid_schema_version_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "items.json").write_text(
        json.dumps({"schema_version": "not-int", "items": []}),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        assert _store(tmp_path).load() == []
    assert any("invalid schema_version" in r.message for r in caplog.records)


def test_load_individual_bad_record_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "items.json").write_text(
        json.dumps({
            "schema_version": 1,
            "items": [{"id": "a", "value": 1}, {"id": "b"}, {"id": "c", "value": 3}],
        }),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        loaded = _store(tmp_path).load()
    assert [(i.id, i.value) for i in loaded] == [("a", 1), ("c", 3)]
    assert any("malformed record" in r.message for r in caplog.records)


# --- future-version refuse --------------------------------------------------

def test_load_future_version_refused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "items.json").write_text(
        json.dumps({"schema_version": 999, "items": [{"id": "a", "value": 1}]}),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        assert _store(tmp_path).load() == []
    assert any("too new" in r.message for r in caplog.records)


# --- migration --------------------------------------------------------------

def test_migrate_hook_transforms_older_version(tmp_path: Path) -> None:
    # On-disk v0: items had key "v" instead of "value".
    (tmp_path / "items.json").write_text(
        json.dumps({"schema_version": 0, "items": [{"id": "a", "v": 7}]}),
        encoding="utf-8",
    )

    def migrate(envelope: dict, on_disk: int) -> dict:
        assert on_disk == 0
        return {
            "schema_version": 1,
            "items": [
                {"id": rec["id"], "value": rec["v"]}
                for rec in envelope.get("items", [])
            ],
        }

    s = _store(tmp_path, schema_version=1, migrate=migrate)
    loaded = s.load()
    assert [(i.id, i.value) for i in loaded] == [("a", 7)]


def test_migrate_hook_exception_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "items.json").write_text(
        json.dumps({"schema_version": 0, "items": []}),
        encoding="utf-8",
    )

    def bad_migrate(envelope: dict, on_disk: int) -> dict:
        raise RuntimeError("boom")

    s = _store(tmp_path, schema_version=1, migrate=bad_migrate)
    with caplog.at_level(logging.WARNING):
        assert s.load() == []
    assert any("migration" in r.message for r in caplog.records)


# --- extras pass-through ----------------------------------------------------

def test_save_with_extras_round_trip(tmp_path: Path) -> None:
    s = _store(tmp_path, extra_keys=("pinned",))
    s.save_with_extras([_Item("a", 1)], {"pinned": ["X", "Y"]})
    items, extras = s.load_with_extras()
    assert [(i.id, i.value) for i in items] == [("a", 1)]
    assert extras == {"pinned": ["X", "Y"]}


def test_save_with_extras_rejects_reserved_key(tmp_path: Path) -> None:
    s = _store(tmp_path, extra_keys=("items",))
    with pytest.raises(ValueError, match="reserved envelope key"):
        s.save_with_extras([_Item("a", 1)], {"items": "nope"})
    with pytest.raises(ValueError, match="reserved envelope key"):
        s.save_with_extras([_Item("a", 1)], {"schema_version": 99})


def test_load_with_extras_missing_key_is_none(tmp_path: Path) -> None:
    # File exists with no `pinned` key in envelope.
    (tmp_path / "items.json").write_text(
        json.dumps({"schema_version": 1, "items": []}),
        encoding="utf-8",
    )
    s = _store(tmp_path, extra_keys=("pinned",))
    items, extras = s.load_with_extras()
    assert items == []
    assert extras == {"pinned": None}


# --- clear ------------------------------------------------------------------

def test_clear_existing_returns_true(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.save([_Item("a", 1)])
    assert s.clear() is True
    assert not (tmp_path / "items.json").exists()


def test_clear_missing_returns_false(tmp_path: Path) -> None:
    assert _store(tmp_path).clear() is False


# --- root override (test sandboxing) ---------------------------------------

def test_root_override_redirects_file(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()
    s = JsonListStore(
        path=lambda: primary / "items.json",
        items_key="items",
        to_dict=lambda i: i.to_dict(),
        from_dict=_Item.from_dict,
        kind_label="items",
    )
    s.save([_Item("z", 9)], root=secondary)
    assert (secondary / "items.json").exists()
    assert not (primary / "items.json").exists()
    loaded = s.load(root=secondary)
    assert [(i.id, i.value) for i in loaded] == [("z", 9)]
