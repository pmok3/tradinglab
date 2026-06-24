"""Tests for indicator-preset auto-persistence (issue: presets not surviving
app restart).

Covers two layers:

1. ``indicators.preset_store`` — the standalone JSON envelope: save/load
   round-trip, missing/corrupt-file degradation, active-pointer
   normalisation.
2. ``IndicatorManager.presets_to_dict`` / ``install_presets`` — the serialize
   + startup-restore pair, including the contract that ``install_presets``
   fires no observer event (so the app's auto-persist subscriber doesn't
   re-write the file on launch) and leaves the active-config list untouched.
3. End-to-end lifecycle mirroring the ChartApp wiring: a persistence
   subscriber writes the file on ``save_preset`` / ``delete_preset``; a fresh
   manager restores from disk and applies the preset.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tradinglab.indicators import preset_store
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager


def _mgr() -> IndicatorManager:
    return IndicatorManager(scheduler=lambda cb=None: None)


def _cfg(length: int) -> IndicatorConfig:
    return IndicatorConfig(kind_id="ema", params={"length": length})


# ---------------------------------------------------------------------------
# preset_store: file round-trip
# ---------------------------------------------------------------------------


def test_save_then_load_round_trip(tmp_path: Path):
    f = tmp_path / "indicator_presets.json"
    presets = {"trend": [_cfg(9).to_dict(), _cfg(21).to_dict()]}
    assert preset_store.save_presets(presets, "trend", path=f) is True
    assert f.exists()
    loaded, active = preset_store.load_presets(path=f)
    assert set(loaded.keys()) == {"trend"}
    assert active == "trend"
    assert len(loaded["trend"]) == 2


def test_load_missing_file_returns_empty(tmp_path: Path):
    loaded, active = preset_store.load_presets(path=tmp_path / "nope.json")
    assert loaded == {}
    assert active is None


def test_load_corrupt_file_returns_empty(tmp_path: Path):
    f = tmp_path / "indicator_presets.json"
    f.write_text("{ not valid json", encoding="utf-8")
    loaded, active = preset_store.load_presets(path=f)
    assert loaded == {}
    assert active is None


def test_load_non_dict_payload_returns_empty(tmp_path: Path):
    f = tmp_path / "indicator_presets.json"
    f.write_text("[1, 2, 3]", encoding="utf-8")
    loaded, active = preset_store.load_presets(path=f)
    assert loaded == {}
    assert active is None


def test_active_pointer_dropped_when_not_a_preset(tmp_path: Path):
    f = tmp_path / "indicator_presets.json"
    preset_store.save_presets({"a": [_cfg(5).to_dict()]}, "ghost", path=f)
    # 'ghost' isn't a saved preset → save normalises it to null, load too.
    loaded, active = preset_store.load_presets(path=f)
    assert set(loaded.keys()) == {"a"}
    assert active is None


def test_save_skips_non_dict_preset_entries_on_load(tmp_path: Path):
    f = tmp_path / "indicator_presets.json"
    import json
    f.write_text(json.dumps({
        "version": 1,
        "active_preset": "x",
        "presets": {"x": [{"kind_id": "ema"}, "garbage", 42]},
    }), encoding="utf-8")
    loaded, active = preset_store.load_presets(path=f)
    assert active == "x"
    # Only the dict entry survives.
    assert loaded["x"] == [{"kind_id": "ema"}]


def test_empty_presets_round_trip(tmp_path: Path):
    f = tmp_path / "indicator_presets.json"
    assert preset_store.save_presets({}, None, path=f) is True
    loaded, active = preset_store.load_presets(path=f)
    assert loaded == {}
    assert active is None


def test_presets_path_under_data_root(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("TRADINGLAB_DATA_DIR", str(tmp_path))
    # paths.app_data_dir caches migration but re-resolves the root each call.
    assert preset_store.presets_path().name == "indicator_presets.json"
    assert preset_store.presets_path().parent == tmp_path


# ---------------------------------------------------------------------------
# IndicatorManager.presets_to_dict / install_presets
# ---------------------------------------------------------------------------


def test_presets_to_dict_matches_to_dict_presets_section():
    mgr = _mgr()
    mgr.add(_cfg(9))
    mgr.save_preset("p1")
    full = mgr.to_dict()
    assert mgr.presets_to_dict() == full["presets"]


def test_install_presets_round_trips_through_store():
    mgr = _mgr()
    mgr.add(_cfg(9))
    mgr.add(_cfg(21))
    mgr.save_preset("trend")
    snapshot = mgr.presets_to_dict()

    fresh = _mgr()
    assert fresh.list_presets() == []
    fresh.install_presets(snapshot, "trend")
    assert fresh.list_presets() == ["trend"]
    assert fresh.active_preset() == "trend"
    # Applying the restored preset rebuilds the active list.
    assert fresh.set_preset("trend") is True
    assert sorted(int(c.params.get("length", 0)) for c in fresh.list()) == [9, 21]


def test_install_presets_does_not_touch_active_configs():
    mgr = _mgr()
    mgr.add(_cfg(50))  # a live indicator the user has on-screen
    before = [c.id for c in mgr.list()]
    mgr.install_presets({"p": [_cfg(9).to_dict()]}, "p")
    after = [c.id for c in mgr.list()]
    assert after == before, "install_presets must not replace the active list"
    assert mgr.list_presets() == ["p"]


def test_install_presets_fires_no_observer_event():
    """Critical: startup restore must not notify, or the app's auto-persist
    subscriber would re-write the file on every launch (and a render would
    be scheduled needlessly)."""
    mgr = _mgr()
    events: list[str] = []
    mgr.subscribe(lambda kind, _cfg: events.append(kind))
    mgr.install_presets({"p": [_cfg(9).to_dict()]}, "p")
    assert events == []


def test_install_presets_drops_active_when_absent():
    mgr = _mgr()
    mgr.install_presets({"p": [_cfg(9).to_dict()]}, "missing")
    assert mgr.active_preset() is None


def test_install_presets_skips_malformed_entries():
    mgr = _mgr()
    # A structurally-broken entry shouldn't abort the whole install.
    mgr.install_presets({"p": [_cfg(9).to_dict(), {"bogus": True}]}, "p")
    assert "p" in mgr.list_presets()


# ---------------------------------------------------------------------------
# End-to-end: persistence subscriber (mirrors ChartApp wiring)
# ---------------------------------------------------------------------------


def _mgr_with_persistence(path: Path) -> IndicatorManager:
    mgr = _mgr()

    def _persist(event_kind: str, _cfg) -> None:
        if event_kind in {"preset_saved", "preset_deleted", "preset_loaded", "loaded"}:
            preset_store.save_presets(mgr.presets_to_dict(), mgr.active_preset(), path=path)

    mgr.subscribe(_persist)
    return mgr


def test_save_preset_persists_and_survives_restart(tmp_path: Path):
    f = tmp_path / "indicator_presets.json"

    # Session 1: save a preset → auto-persisted to disk.
    m1 = _mgr_with_persistence(f)
    m1.add(_cfg(9))
    m1.add(_cfg(21))
    m1.save_preset("scalping")
    assert f.exists(), "save_preset must auto-persist to disk"

    # Session 2 (restart): fresh manager restores from disk.
    m2 = _mgr_with_persistence(f)
    assert m2.list_presets() == []
    presets, active = preset_store.load_presets(path=f)
    m2.install_presets(presets, active)
    assert m2.list_presets() == ["scalping"]
    assert m2.set_preset("scalping") is True
    assert sorted(int(c.params.get("length", 0)) for c in m2.list()) == [9, 21]


def test_delete_preset_persists(tmp_path: Path):
    f = tmp_path / "indicator_presets.json"
    m = _mgr_with_persistence(f)
    m.add(_cfg(9))
    m.save_preset("a")
    m.save_preset("b")
    m.delete_preset("a")
    presets, _ = preset_store.load_presets(path=f)
    assert set(presets.keys()) == {"b"}, "delete must persist to disk"


def test_set_preset_persists_active_pointer(tmp_path: Path):
    f = tmp_path / "indicator_presets.json"
    m = _mgr_with_persistence(f)
    m.add(_cfg(9))
    m.save_preset("a")
    m.add(_cfg(21))
    m.save_preset("b")
    assert m.set_preset("a") is True
    _, active = preset_store.load_presets(path=f)
    assert active == "a", "active-preset pointer must persist"
