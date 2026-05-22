"""Unit tests for :mod:`tradinglab.gui.geometry_store`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradinglab.gui import geometry_store as gs


def test_round_trip_save_and_load(tmp_path: Path) -> None:
    store = gs.GeometryStore(path=tmp_path / "geometry.json")
    store.load()  # missing file -> empty cache
    store.set_window("main", "1280x800+100+100")
    store.set_sash("main_paned", [220, 980])
    store.set("kv.demo", {"answer": 42})  # set() auto-saves

    raw = json.loads((tmp_path / "geometry.json").read_text(encoding="utf-8"))
    assert raw["version"] == gs.SCHEMA_VERSION
    assert raw["windows"]["main"] == "1280x800+100+100"
    assert raw["sashes"]["main_paned"] == [220, 980]
    assert raw["kv"]["kv.demo"] == {"answer": 42}

    other = gs.GeometryStore(path=tmp_path / "geometry.json")
    other.load()
    assert other.get_window("main") == "1280x800+100+100"
    assert other.get_sash("main_paned") == [220, 980]
    assert other.get("kv.demo") == {"answer": 42}


def test_clamp_to_screen_accepts_in_bounds() -> None:
    assert gs._clamp_to_screen("1280x800+100+100", 1920, 1080) == "1280x800+100+100"
    # Slightly off-screen (within 100 px slack) — still accepted.
    assert gs._clamp_to_screen("1280x800-50-50", 1920, 1080) == "1280x800-50-50"


def test_clamp_to_screen_rejects_off_screen() -> None:
    default = "1024x768+0+0"
    # Far-left disconnected monitor.
    assert gs._clamp_to_screen("1280x800-9999-9999", 1920, 1080, default=default) == default
    # Bottom-right past virtual bounds.
    assert gs._clamp_to_screen("1280x800+5000+5000", 1920, 1080, default=default) == default
    # Bogus geometry string.
    assert gs._clamp_to_screen("not-a-geometry", 1920, 1080, default=default) == default


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    store = gs.GeometryStore(path=tmp_path / "nope.json")
    store.load()  # must not raise
    assert store.get_window("main") is None
    assert store.get_sash("main_paned") is None
    assert store.get("anything") is None


def test_future_version_treated_as_missing(tmp_path: Path) -> None:
    p = tmp_path / "geometry.json"
    p.write_text(
        json.dumps(
            {
                "version": 9999,
                "windows": {"main": "9999x9999+9999+9999"},
                "sashes": {},
                "kv": {},
            }
        ),
        encoding="utf-8",
    )
    store = gs.GeometryStore(path=p)
    store.load()  # must not raise
    assert store.get_window("main") is None  # ignored, not loaded


def test_save_writes_schema_version_1(tmp_path: Path) -> None:
    p = tmp_path / "geometry.json"
    store = gs.GeometryStore(path=p)
    store.load()
    store.save()
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert "windows" in raw and "sashes" in raw and "kv" in raw


def test_save_swallows_permission_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "no" / "such" / "dir" / "geometry.json"
    store = gs.GeometryStore(path=bad)
    store.set_window("main", "100x100+0+0")
    # The directory will be created by atomic_write_json; force a failure
    # by using a path that points through a regular file.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    bad2 = blocker / "geometry.json"
    store2 = gs.GeometryStore(path=bad2)
    store2.set_window("main", "100x100+0+0")
    store2.save()  # must not raise
    err = capsys.readouterr().err
    assert "geometry_store" in err


# --------------------------------------------------------- restore_sash sanity --

class _FakePaned:
    """Minimal duck-typed PanedWindow stub.

    Records the sash positions actually applied by ``restore_sash``.
    Implements just enough of the Tk surface for the test:
    ``after_idle`` / ``after`` invoke their callbacks synchronously,
    ``winfo_width`` returns a configurable width, and ``sashpos``
    accepts (idx, value) writes.
    """

    def __init__(self, width: int) -> None:
        self._width = int(width)
        self.applied: list[tuple[int, int]] = []

    def winfo_width(self) -> int:
        return self._width

    def sashpos(self, idx: int, value: int) -> None:
        self.applied.append((int(idx), int(value)))

    def after_idle(self, fn) -> None:
        fn()

    def after(self, _ms: int, fn) -> None:
        fn()


def test_restore_sash_uses_stored_when_within_minimums(tmp_path: Path) -> None:
    store = gs.GeometryStore(path=tmp_path / "geometry.json")
    store.load()
    store.set_sash("main_paned_2pane", [900])  # chart=900, notebook=380 in a 1280 wide paned

    paned = _FakePaned(width=1280)
    store.restore_sash(
        paned, "main_paned_2pane",
        default_positions=[700],
        min_pane_widths=[500, 280],
    )
    assert paned.applied == [(0, 900)]


def test_restore_sash_falls_back_when_chart_too_small(tmp_path: Path) -> None:
    store = gs.GeometryStore(path=tmp_path / "geometry.json")
    store.load()
    # Stored sash leaves only 506 px for the chart pane
    store.set_sash("main_paned_2pane", [506])

    paned = _FakePaned(width=1382)
    store.restore_sash(
        paned, "main_paned_2pane",
        default_positions=[967],
        min_pane_widths=[700, 280],  # chart_min=700 > 506 → reject
    )
    assert paned.applied == [(0, 967)]


def test_restore_sash_falls_back_when_notebook_too_small(tmp_path: Path) -> None:
    store = gs.GeometryStore(path=tmp_path / "geometry.json")
    store.load()
    # Stored sash leaves only 100 px for the notebook pane (1380 - 1280)
    store.set_sash("main_paned_2pane", [1280])

    paned = _FakePaned(width=1380)
    store.restore_sash(
        paned, "main_paned_2pane",
        default_positions=[966],
        min_pane_widths=[500, 280],  # notebook_min=280 > 100 → reject
    )
    assert paned.applied == [(0, 966)]


def test_restore_sash_3pane_falls_back_when_chartstack_too_thin(tmp_path: Path) -> None:
    store = gs.GeometryStore(path=tmp_path / "geometry.json")
    store.load()
    # Stored sash collapses chartstack to 30 px
    store.set_sash("main_paned_3pane", [30, 1100])

    paned = _FakePaned(width=1500)
    store.restore_sash(
        paned, "main_paned_3pane",
        default_positions=[220, 1116],
        min_pane_widths=[180, 500, 280],  # chartstack_min=180 > 30 → reject
    )
    assert paned.applied == [(0, 220), (1, 1116)]


def test_restore_sash_skips_minimum_check_when_no_stored_value(tmp_path: Path) -> None:
    """Defaults bypass the minimum check (caller is trusted)."""
    store = gs.GeometryStore(path=tmp_path / "geometry.json")
    store.load()
    # No stored sash — should apply defaults verbatim.
    paned = _FakePaned(width=1380)
    store.restore_sash(
        paned, "main_paned_2pane",
        default_positions=[100],  # silly default; we don't reject defaults
        min_pane_widths=[500, 280],
    )
    assert paned.applied == [(0, 100)]


def test_restore_sash_no_min_pane_widths_keeps_legacy_behavior(tmp_path: Path) -> None:
    """Without min_pane_widths the old "trust the stored value" behavior holds."""
    store = gs.GeometryStore(path=tmp_path / "geometry.json")
    store.load()
    store.set_sash("main_paned_2pane", [50])

    paned = _FakePaned(width=1380)
    store.restore_sash(
        paned, "main_paned_2pane",
        default_positions=[967],
        # no min_pane_widths kwarg
    )
    assert paned.applied == [(0, 50)]

