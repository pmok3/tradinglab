"""Tests for ``tradinglab.positions.storage``: open positions + trail state."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tradinglab.positions import storage
from tradinglab.positions.model import Position


@pytest.fixture
def isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect ``tradinglab.disk_cache._cache_dir`` to a tmp path.

    ``positions.storage`` imports ``_cache_dir`` locally, so we have to
    patch BOTH the source module (for any other consumer that might
    re-import) and the storage module's local binding.
    """
    monkeypatch.setattr("tradinglab.disk_cache._cache_dir", lambda: tmp_path)
    monkeypatch.setattr("tradinglab.positions.storage._cache_dir", lambda: tmp_path)
    yield tmp_path


def _make_position(**overrides) -> Position:
    base = dict(
        id="pos-1", symbol="AAPL", side="long",
        qty_initial=100.0, qty_open=100.0,
        avg_entry_price=175.0,
        entry_time=datetime(2026, 5, 4, 14, 30, tzinfo=timezone.utc),
        source="manual",
    )
    base.update(overrides)
    return Position(**base)  # type: ignore[arg-type]


# ---- open positions --------------------------------------------------------

def test_save_and_load_roundtrip_open_positions(isolated_cache_dir):
    positions = [
        _make_position(id="p1", symbol="AAPL"),
        _make_position(id="p2", symbol="MSFT", side="short", avg_entry_price=400.0),
    ]
    storage.save_open_positions(positions)
    loaded = storage.load_open_positions()
    assert {p.id for p in loaded} == {"p1", "p2"}
    assert {p.symbol for p in loaded} == {"AAPL", "MSFT"}


def test_load_open_positions_returns_empty_when_no_file(isolated_cache_dir):
    assert storage.load_open_positions() == []


def test_load_open_positions_lenient_on_corrupt_json(isolated_cache_dir):
    p = storage.open_positions_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json", encoding="utf-8")
    assert storage.load_open_positions() == []


def test_load_open_positions_skips_malformed_entry_keeps_others(isolated_cache_dir):
    raw = {
        "schema_version": 1,
        "positions": [
            {"id": "good", "symbol": "AAPL", "side": "long",
             "qty_initial": 1, "qty_open": 1, "avg_entry_price": 100,
             "entry_time": datetime(2026, 5, 4, tzinfo=timezone.utc).isoformat(),
             "source": "manual"},
            {"id": "bad"},
        ],
    }
    p = storage.open_positions_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw), encoding="utf-8")
    loaded = storage.load_open_positions()
    assert [pos.id for pos in loaded] == ["good"]


def test_load_open_positions_rejects_future_schema(isolated_cache_dir):
    raw = {"schema_version": 999, "positions": []}
    p = storage.open_positions_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert storage.load_open_positions() == []


def test_save_open_positions_is_atomic_via_replace(isolated_cache_dir):
    """The temp file should never be left behind after a successful save."""
    storage.save_open_positions([_make_position()])
    files = list(isolated_cache_dir.joinpath("positions").iterdir())
    # Only the persisted .json should remain — no .tmp leftovers.
    assert all(not f.name.endswith(".tmp") for f in files)


# ---- trail state -----------------------------------------------------------

def test_trail_state_round_trip(isolated_cache_dir):
    blob = {
        "pos-1": {"hwm": 180.5, "activated": True, "trail": 178.5},
        "pos-2": {"hwm": 0.0, "activated": False, "trail": None},
    }
    storage.save_trail_state(blob)
    loaded = storage.load_trail_state()
    assert loaded == blob


def test_load_trail_state_empty_when_missing(isolated_cache_dir):
    assert storage.load_trail_state() == {}


def test_load_trail_state_lenient_on_corrupt(isolated_cache_dir):
    p = storage.trail_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("garbage", encoding="utf-8")
    assert storage.load_trail_state() == {}


def test_clear_trail_state_removes_file(isolated_cache_dir):
    storage.save_trail_state({"pos-1": {"hwm": 180.0}})
    assert storage.trail_state_path().exists()
    assert storage.clear_trail_state() is True
    assert not storage.trail_state_path().exists()


def test_clear_trail_state_returns_false_when_no_file(isolated_cache_dir):
    assert storage.clear_trail_state() is False


def test_load_trail_state_rejects_future_schema(isolated_cache_dir):
    raw = {"schema_version": 999, "trail": {}}
    p = storage.trail_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert storage.load_trail_state() == {}
