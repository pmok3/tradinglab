"""Unit tests for the BYOD registration block in ``tradinglab.data.__init__``.

Validates that :func:`register_local_sources` honours the settings
gate, parses the root list, and registers one
``<root_name>-<subdir>`` entry per top-level subfolder. Also verifies
the disk-cache opt-out is wired correctly via :mod:`disk_cache`.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tradinglab import disk_cache
from tradinglab.data import (
    DATA_SOURCES,
    register_local_sources,
)


@pytest.fixture
def isolated_data_registry() -> Iterator[None]:
    """Snapshot DATA_SOURCES + _NO_PERSIST so tests don't leak state.

    The data registry is module-level mutable global state shared across
    the whole process — without this fixture, leftover BYOD entries
    from one test would taint the next.
    """
    saved_sources = dict(DATA_SOURCES)
    saved_no_persist = set(disk_cache._NO_PERSIST)
    try:
        yield
    finally:
        # Drop anything added during the test, restore originals.
        for k in list(DATA_SOURCES.keys()):
            if k not in saved_sources:
                DATA_SOURCES.pop(k, None)
        for k, v in saved_sources.items():
            DATA_SOURCES[k] = v
        disk_cache._NO_PERSIST.clear()
        disk_cache._NO_PERSIST.update(saved_no_persist)


def _set_local_data_settings(
    monkeypatch: pytest.MonkeyPatch, value: object,
) -> None:
    """Force ``defaults.get('local_data')`` to return ``value`` for this test."""
    from tradinglab import defaults

    def fake_get(key: str) -> object:
        if key == "local_data":
            return value
        # Delegate to the real implementation for anything else.
        return defaults.get.__wrapped__(key) if hasattr(defaults.get, "__wrapped__") else None

    monkeypatch.setattr(defaults, "get", fake_get)


class TestRegisterLocalSourcesGating:
    def test_disabled_returns_empty(
        self, isolated_data_registry: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _set_local_data_settings(
            monkeypatch, {"enabled": False, "roots": [{"name": "x", "path": "/tmp"}]},
        )
        assert register_local_sources() == []

    def test_no_roots_returns_empty(
        self, isolated_data_registry: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _set_local_data_settings(monkeypatch, {"enabled": True, "roots": []})
        assert register_local_sources() == []

    def test_non_dict_returns_empty(
        self, isolated_data_registry: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _set_local_data_settings(monkeypatch, "not a dict")
        assert register_local_sources() == []

    def test_non_list_roots_returns_empty(
        self, isolated_data_registry: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _set_local_data_settings(monkeypatch, {"enabled": True, "roots": "nope"})
        assert register_local_sources() == []


class TestRegisterLocalSourcesHappyPath:
    def test_registers_one_source_per_subfolder(
        self, tmp_path: Path,
        isolated_data_registry: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Lay out: root/yfinance/, root/polygon/
        root = tmp_path / "root"
        root.mkdir()
        (root / "yfinance").mkdir()
        (root / "polygon").mkdir()
        _set_local_data_settings(monkeypatch, {
            "enabled": True,
            "roots": [{"name": "share", "path": str(root)}],
        })
        registered = register_local_sources()
        assert sorted(registered) == ["share-polygon", "share-yfinance"]
        assert "share-yfinance" in DATA_SOURCES
        assert "share-polygon" in DATA_SOURCES

    def test_combobox_naming_includes_root_name_with_hyphen(
        self, tmp_path: Path,
        isolated_data_registry: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "root"
        root.mkdir()
        (root / "yfinance").mkdir()
        _set_local_data_settings(monkeypatch, {
            "enabled": True,
            "roots": [{"name": "share_2024_11", "path": str(root)}],
        })
        registered = register_local_sources()
        assert registered == ["share_2024_11-yfinance"]

    def test_missing_root_dir_skipped(
        self, tmp_path: Path,
        isolated_data_registry: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _set_local_data_settings(monkeypatch, {
            "enabled": True,
            "roots": [{"name": "ghost", "path": str(tmp_path / "does_not_exist")}],
        })
        assert register_local_sources() == []

    def test_multiple_roots_namespaced_independently(
        self, tmp_path: Path,
        isolated_data_registry: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        r1 = tmp_path / "r1"
        r1.mkdir()
        (r1 / "yfinance").mkdir()
        r2 = tmp_path / "r2"
        r2.mkdir()
        (r2 / "yfinance").mkdir()
        _set_local_data_settings(monkeypatch, {
            "enabled": True,
            "roots": [
                {"name": "alice", "path": str(r1)},
                {"name": "bob", "path": str(r2)},
            ],
        })
        registered = sorted(register_local_sources())
        # Same subdir name ("yfinance") on both roots, but namespaced
        # via the root name so they don't collide.
        assert registered == ["alice-yfinance", "bob-yfinance"]


class TestDiskCacheBypass:
    def test_registered_sources_marked_no_persist(
        self, tmp_path: Path,
        isolated_data_registry: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "root"
        root.mkdir()
        (root / "yfinance").mkdir()
        _set_local_data_settings(monkeypatch, {
            "enabled": True,
            "roots": [{"name": "share", "path": str(root)}],
        })
        register_local_sources()
        assert disk_cache.is_no_persist("share-yfinance")
        # Built-ins must never get marked.
        assert not disk_cache.is_no_persist("yfinance")
        assert not disk_cache.is_no_persist("synthetic")

    def test_re_registration_clears_then_remarks(
        self, tmp_path: Path,
        isolated_data_registry: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # First registration: one source.
        r1 = tmp_path / "r1"
        r1.mkdir()
        (r1 / "yfinance").mkdir()
        _set_local_data_settings(monkeypatch, {
            "enabled": True,
            "roots": [{"name": "old", "path": str(r1)}],
        })
        register_local_sources()
        assert disk_cache.is_no_persist("old-yfinance")

        # Second registration: different source. The first should be
        # dropped from _NO_PERSIST (idempotent re-registration).
        r2 = tmp_path / "r2"
        r2.mkdir()
        (r2 / "polygon").mkdir()
        _set_local_data_settings(monkeypatch, {
            "enabled": True,
            "roots": [{"name": "new", "path": str(r2)}],
        })
        register_local_sources()
        assert disk_cache.is_no_persist("new-polygon")
        assert not disk_cache.is_no_persist("old-yfinance")


class TestDiskCacheNoPersist:
    """Direct tests for the no-persist mechanism in :mod:`disk_cache`."""

    def test_save_is_noop_for_no_persist_source(
        self, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from datetime import datetime, timezone

        from tradinglab.models import Candle

        # Re-route the cache dir so we don't pollute the user's cache.
        monkeypatch.setenv("TRADINGLAB_DATA_DIR", str(tmp_path))
        disk_cache.mark_no_persist("test-byod")
        try:
            disk_cache.save("test-byod", "AAPL", "5m", [
                Candle(
                    date=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    open=1, high=2, low=1, close=1.5, volume=100,
                    session="regular",
                ),
            ])
            # Save was a no-op → load returns None.
            assert disk_cache.load("test-byod", "AAPL", "5m") is None
            # The on-disk file must NOT have been written.
            from tradinglab.disk_cache import _path_for
            assert not _path_for("test-byod", "AAPL", "5m").exists()
        finally:
            disk_cache.unmark_no_persist("test-byod")

    def test_load_returns_none_for_no_persist_source(
        self, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from datetime import datetime, timezone

        from tradinglab.models import Candle

        # Write a file the OLD way (no opt-out), then mark opt-out and
        # verify load skips the file even though it exists on disk.
        monkeypatch.setenv("TRADINGLAB_DATA_DIR", str(tmp_path))
        c = [Candle(
            date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=1, high=2, low=1, close=1.5, volume=100,
            session="regular",
        )]
        disk_cache.save("test-byod-2", "AAPL", "5m", c)
        # Sanity: without opt-out we can read it back.
        assert disk_cache.load("test-byod-2", "AAPL", "5m") is not None
        # Now opt out — load must return None even though the file
        # is sitting right there.
        disk_cache.mark_no_persist("test-byod-2")
        try:
            assert disk_cache.load("test-byod-2", "AAPL", "5m") is None
        finally:
            disk_cache.unmark_no_persist("test-byod-2")

    def test_mark_idempotent(self) -> None:
        disk_cache.mark_no_persist("x-y")
        disk_cache.mark_no_persist("x-y")
        assert disk_cache.is_no_persist("x-y")
        disk_cache.unmark_no_persist("x-y")
        assert not disk_cache.is_no_persist("x-y")

    def test_unmark_unknown_is_noop(self) -> None:
        # Must not raise.
        disk_cache.unmark_no_persist("never-marked")

    def test_clear_drops_everything(self) -> None:
        disk_cache.mark_no_persist("a-b")
        disk_cache.mark_no_persist("c-d")
        disk_cache.clear_no_persist()
        assert not disk_cache.is_no_persist("a-b")
        assert not disk_cache.is_no_persist("c-d")

    def test_empty_source_name_ignored(self) -> None:
        disk_cache.mark_no_persist("")
        assert not disk_cache.is_no_persist("")
