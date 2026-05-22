"""Storage layer tests: UUID-keyed JSON files, atomic write, collision handling.

All tests redirect ``disk_cache._cache_dir`` to a pytest tmp_path so we
never touch the user's real cache directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tradinglab.scanner import storage
from tradinglab.scanner.model import (
    OP_GT,
    Condition,
    FieldRef,
    Group,
    ScanDefinition,
)


@pytest.fixture(autouse=True)
def _redirect_cache(monkeypatch, tmp_path):
    """Point ``_cache_dir()`` at a fresh tmp path for every test."""
    monkeypatch.setattr(
        "tradinglab.scanner.storage._cache_dir",
        lambda: tmp_path,
    )
    yield tmp_path


def _make_scan(name: str = "Test Scan") -> ScanDefinition:
    return ScanDefinition(
        name=name,
        root=Group(combinator="and", children=[
            Condition(
                left=FieldRef.builtin("close"),
                op=OP_GT,
                params={"right": FieldRef.literal(100.0)},
                interval="5m",
            ),
        ]),
    )


# ---------------------------------------------------------------------------
# Save / load round-trip
# ---------------------------------------------------------------------------


def test_save_creates_uuid_named_file(tmp_path):
    scan = _make_scan()
    path = storage.save(scan)
    assert path.name == f"{scan.id}.json"
    assert path.exists()


def test_load_round_trip():
    scan = _make_scan("Original")
    storage.save(scan)
    loaded = storage.load(scan.id)
    assert loaded.id == scan.id
    assert loaded.name == "Original"
    assert len(loaded.root.children) == 1


def test_load_missing_raises():
    with pytest.raises(FileNotFoundError):
        storage.load("nonexistent-id")


def test_load_corrupt_json_raises_value_error(tmp_path):
    target = storage.scans_dir() / "deadbeef-0000-4000-8000-000000000000.json"
    target.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(ValueError):
        storage._load_path(target)


def test_load_future_schema_version_raises(tmp_path):
    scan = _make_scan()
    p = storage.save(scan)
    import json
    d = json.loads(p.read_text())
    d["schema_version"] = 99
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ValueError, match="newer build"):
        storage._load_path(p)


def test_save_atomic_no_temp_files_left(tmp_path):
    scan = _make_scan()
    storage.save(scan)
    leftovers = [p for p in storage.scans_dir().iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_save_touches_updated_at():
    scan = _make_scan()
    original_updated = scan.updated_at
    # Force a different timestamp by mutating after construction.
    object.__setattr__(scan, "updated_at", "2020-01-01T00:00:00Z")
    storage.save(scan)
    loaded = storage.load(scan.id)
    assert loaded.updated_at != "2020-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# load_all
# ---------------------------------------------------------------------------


def test_load_all_empty():
    assert storage.load_all() == []


def test_load_all_returns_alphabetical():
    a = _make_scan("Zebra")
    b = _make_scan("Apple")
    c = _make_scan("mango")
    storage.save(a); storage.save(b); storage.save(c)
    names = [s.name for s in storage.load_all()]
    assert names == ["Apple", "mango", "Zebra"]


def test_load_all_skips_corrupt_files(caplog):
    a = _make_scan("Good")
    storage.save(a)
    bad = storage.scans_dir() / "00000000-0000-4000-8000-000000000000.json"
    bad.write_text("not json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        scans = storage.load_all()
    assert [s.name for s in scans] == ["Good"]
    assert any("skipping corrupt scan file" in r.message for r in caplog.records)


def test_load_all_ignores_unrelated_files():
    storage.save(_make_scan())
    (storage.scans_dir() / "_index.json").write_text("{}", encoding="utf-8")
    (storage.scans_dir() / "README.txt").write_text("notes", encoding="utf-8")
    scans = storage.load_all()
    assert len(scans) == 1


# ---------------------------------------------------------------------------
# Delete / find
# ---------------------------------------------------------------------------


def test_delete_removes_file():
    s = _make_scan()
    storage.save(s)
    assert storage.delete(s.id) is True
    assert not storage.scan_path(s.id).exists()


def test_delete_missing_returns_false():
    assert storage.delete("nonexistent-id") is False


def test_find_by_name_case_insensitive():
    s = _make_scan("Strong RVOL Setup")
    storage.save(s)
    found = storage.find_by_name("strong rvol setup")
    assert found is not None and found.id == s.id


def test_find_by_name_returns_none_when_missing():
    assert storage.find_by_name("missing") is None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_writes_arbitrary_path(tmp_path):
    s = _make_scan("Exported")
    dst = tmp_path / "exported.json"
    out = storage.export_scan(s, dst)
    assert out == dst and dst.exists()
    import json
    d = json.loads(dst.read_text(encoding="utf-8"))
    assert d["name"] == "Exported"


# ---------------------------------------------------------------------------
# Import collision handling
# ---------------------------------------------------------------------------


def test_import_no_collision_saves_directly(tmp_path):
    s = _make_scan("Fresh")
    src = tmp_path / "src.json"
    storage.export_scan(s, src)
    decision_calls = []

    result = storage.import_scan(src, on_collision=lambda l, i: decision_calls.append(1) or storage.CollisionDecision.CANCEL)
    assert result is not None
    assert result.id == s.id
    assert decision_calls == []  # no collision callback fired
    assert storage.load(s.id).name == "Fresh"


def test_import_id_collision_overwrite(tmp_path):
    """Same UUID locally → overwrite replaces it."""
    local = _make_scan("OldName")
    storage.save(local)
    incoming = _make_scan("NewName")
    object.__setattr__(incoming, "id", local.id)
    src = tmp_path / "src.json"
    storage.export_scan(incoming, src)
    result = storage.import_scan(src,
                                 on_collision=lambda l, i: storage.CollisionDecision.OVERWRITE)
    assert result is not None
    assert storage.load(local.id).name == "NewName"


def test_import_id_collision_cancel(tmp_path):
    local = _make_scan("Untouched")
    storage.save(local)
    incoming = _make_scan("Replacement")
    object.__setattr__(incoming, "id", local.id)
    src = tmp_path / "src.json"
    storage.export_scan(incoming, src)
    result = storage.import_scan(src,
                                 on_collision=lambda l, i: storage.CollisionDecision.CANCEL)
    assert result is None
    assert storage.load(local.id).name == "Untouched"


def test_import_id_collision_rename_creates_new_uuid(tmp_path):
    local = _make_scan("Original")
    storage.save(local)
    incoming = _make_scan("Original")  # also collides on name
    object.__setattr__(incoming, "id", local.id)
    src = tmp_path / "src.json"
    storage.export_scan(incoming, src)
    result = storage.import_scan(src,
                                 on_collision=lambda l, i: storage.CollisionDecision.RENAME)
    assert result is not None
    assert result.id != local.id
    assert result.name == "Original (2)"
    # Both files exist.
    assert storage.scan_path(local.id).exists()
    assert storage.scan_path(result.id).exists()


def test_import_name_collision_different_id_rename(tmp_path):
    local = _make_scan("Setup")
    storage.save(local)
    incoming = _make_scan("Setup")  # different UUID, same name
    src = tmp_path / "src.json"
    storage.export_scan(incoming, src)
    result = storage.import_scan(src,
                                 on_collision=lambda l, i: storage.CollisionDecision.RENAME)
    assert result is not None
    assert result.id == incoming.id
    assert result.name == "Setup (2)"


def test_import_name_collision_overwrite_keeps_local_id(tmp_path):
    """Overwriting on a name-collision must keep the local id so open tabs survive."""
    local = _make_scan("Setup")
    storage.save(local)
    incoming = _make_scan("Setup")
    src = tmp_path / "src.json"
    storage.export_scan(incoming, src)
    result = storage.import_scan(src,
                                 on_collision=lambda l, i: storage.CollisionDecision.OVERWRITE)
    assert result is not None
    assert result.id == local.id  # local id preserved
    # Only one file on disk.
    files = [p for p in storage.scans_dir().iterdir() if p.suffix == ".json"]
    assert len(files) == 1


def test_import_default_callback_cancels_on_collision(tmp_path):
    """No callback supplied → safe default is CANCEL."""
    local = _make_scan("Setup")
    storage.save(local)
    incoming = _make_scan("Setup")
    src = tmp_path / "src.json"
    storage.export_scan(incoming, src)
    result = storage.import_scan(src)
    assert result is None
