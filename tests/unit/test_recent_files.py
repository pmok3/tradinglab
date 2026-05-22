"""Unit tests for :mod:`tradinglab.recent_files`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradinglab import recent_files


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch, tmp_path):
    """Re-route ``app_data_dir`` to a tmpdir so the test stays hermetic."""
    monkeypatch.setattr(
        "tradinglab.paths.app_data_dir", lambda: tmp_path,
    )
    yield tmp_path


def test_list_recent_empty_by_default(_isolated_data_dir):
    assert recent_files.list_recent("configs") == []
    assert recent_files.list_recent("watchlists") == []


def test_push_single_entry(_isolated_data_dir, tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text("{}", encoding="utf-8")
    result = recent_files.push_recent("configs", p)
    assert len(result) == 1
    assert str(p.resolve()) in result[0]
    # Persistence — reading back returns the same list.
    assert recent_files.list_recent("configs") == result


def test_push_dedupes_and_promotes(_isolated_data_dir, tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.touch(); b.touch()
    recent_files.push_recent("configs", a)
    recent_files.push_recent("configs", b)
    recent_files.push_recent("configs", a)  # re-push moves to front
    result = recent_files.list_recent("configs")
    assert result[0].endswith("a.json")
    assert result[1].endswith("b.json")
    assert len(result) == 2


def test_push_caps_at_max(_isolated_data_dir, tmp_path):
    paths = [tmp_path / f"f{i}.json" for i in range(recent_files.MAX_RECENT + 3)]
    for p in paths:
        p.touch()
        recent_files.push_recent("configs", p)
    result = recent_files.list_recent("configs")
    assert len(result) == recent_files.MAX_RECENT
    # Newest first.
    assert result[0].endswith(f"f{recent_files.MAX_RECENT + 2}.json")


def test_two_kinds_are_independent(_isolated_data_dir, tmp_path):
    cfg = tmp_path / "settings.json"
    wl = tmp_path / "watchlist.json"
    cfg.touch(); wl.touch()
    recent_files.push_recent("configs", cfg)
    recent_files.push_recent("watchlists", wl)
    assert len(recent_files.list_recent("configs")) == 1
    assert len(recent_files.list_recent("watchlists")) == 1
    # Pushing a config doesn't bleed into watchlists.
    recent_files.push_recent("configs", cfg)
    assert len(recent_files.list_recent("watchlists")) == 1


def test_remove_recent_drops_entry(_isolated_data_dir, tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.touch(); b.touch()
    recent_files.push_recent("configs", a)
    recent_files.push_recent("configs", b)
    pruned = recent_files.remove_recent("configs", a)
    assert all(not p.endswith("a.json") for p in pruned)
    assert any(p.endswith("b.json") for p in pruned)


def test_clear_recent_kind(_isolated_data_dir, tmp_path):
    a = tmp_path / "a.json"; b = tmp_path / "b.json"
    a.touch(); b.touch()
    recent_files.push_recent("configs", a)
    recent_files.push_recent("watchlists", b)
    recent_files.clear_recent("configs")
    assert recent_files.list_recent("configs") == []
    # Other kind preserved.
    assert len(recent_files.list_recent("watchlists")) == 1


def test_clear_recent_all(_isolated_data_dir, tmp_path):
    a = tmp_path / "a.json"; b = tmp_path / "b.json"
    a.touch(); b.touch()
    recent_files.push_recent("configs", a)
    recent_files.push_recent("watchlists", b)
    recent_files.clear_recent()
    assert recent_files.list_recent("configs") == []
    assert recent_files.list_recent("watchlists") == []


def test_corrupt_file_treated_as_empty(_isolated_data_dir, tmp_path):
    (tmp_path / "recent_files.json").write_text("not json", encoding="utf-8")
    assert recent_files.list_recent("configs") == []
    # Pushing into a corrupt file still works (writes a fresh dict).
    p = tmp_path / "fresh.json"; p.touch()
    recent_files.push_recent("configs", p)
    assert len(recent_files.list_recent("configs")) == 1


def test_display_label_short_path_unchanged():
    label = recent_files.display_label("C:\\Users\\me\\cfg.json")
    assert label.endswith("cfg.json")


def test_display_label_long_path_truncated():
    long_parent = "C:\\" + "\\".join(["very_long_dir"] * 10)
    long_path = long_parent + "\\thefile.json"
    label = recent_files.display_label(long_path, max_len=40)
    assert label.endswith("thefile.json")
    assert len(label) <= 41  # may be slightly over due to ellipsis grammar


def test_unknown_kind_returns_empty(_isolated_data_dir):
    assert recent_files.list_recent("unknown") == []


def test_preserves_unknown_kinds_on_push(_isolated_data_dir, tmp_path):
    # Simulate a newer build wrote a 3rd kind ("layouts") — pushing
    # configs must not nuke the unknown slot.
    (tmp_path / "recent_files.json").write_text(
        json.dumps({"configs": [], "layouts": ["/a.json", "/b.json"]}),
        encoding="utf-8",
    )
    p = tmp_path / "f.json"; p.touch()
    recent_files.push_recent("configs", p)
    raw = json.loads(
        (tmp_path / "recent_files.json").read_text(encoding="utf-8")
    )
    assert raw["layouts"] == ["/a.json", "/b.json"]
    assert len(raw["configs"]) == 1
