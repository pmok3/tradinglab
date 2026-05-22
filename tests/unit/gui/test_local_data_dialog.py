"""Unit tests for ``gui/local_data_dialog.py`` (Configure Local Data).

Coverage focuses on the parts that don't require interactive Tk
event loops:
* Settings I/O (load → render rows → save).
* The name validator (alphanumeric + underscore only — no hyphens).
* The ``_refresh_data_registry`` helper that strips & re-registers
  BYOD entries in ``DATA_SOURCES``.
* The "Save and Close" / "Cancel" paradigm — Cancel must not mutate
  settings.

Tk widget interactions are exercised end-to-end where they're tractable
(create dialog → set values → click Save) and skipped otherwise.
"""
from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

from tradinglab import disk_cache
from tradinglab.data import DATA_SOURCES

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def root():
    try:
        r = tk.Tk()
        r.withdraw()
    except tk.TclError:
        pytest.skip("No display available")
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


@pytest.fixture()
def isolated_registry() -> Iterator[None]:
    """Snapshot the DATA_SOURCES + _NO_PERSIST globals."""
    saved_sources = dict(DATA_SOURCES)
    saved_no_persist = set(disk_cache._NO_PERSIST)
    try:
        yield
    finally:
        for k in list(DATA_SOURCES.keys()):
            if k not in saved_sources:
                DATA_SOURCES.pop(k, None)
        for k, v in saved_sources.items():
            DATA_SOURCES[k] = v
        disk_cache._NO_PERSIST.clear()
        disk_cache._NO_PERSIST.update(saved_no_persist)


# ---------------------------------------------------------------------------
# Pure-logic helpers
# ---------------------------------------------------------------------------


class TestRefreshDataRegistry:
    """``_refresh_data_registry`` strips BYOD keys then re-registers."""

    def test_strips_only_byod_keys(
        self, isolated_registry: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tradinglab.gui.local_data_dialog import _refresh_data_registry

        # Inject fake BYOD and built-in entries.
        DATA_SOURCES["my-share"] = lambda t, i: None
        DATA_SOURCES["yfinance"] = DATA_SOURCES.get("yfinance", lambda t, i: None)
        DATA_SOURCES["synthetic-stream"] = DATA_SOURCES.get(
            "synthetic-stream", lambda t, i: None,
        )

        # Stub out register_local_sources so this test stays unit-pure.
        from tradinglab import data as _data
        monkeypatch.setattr(_data, "register_local_sources", lambda: [])
        # Also stub defaults.reload to avoid touching real settings.
        from tradinglab import defaults
        monkeypatch.setattr(defaults, "reload", lambda: None)

        _refresh_data_registry()

        # BYOD key dropped, built-ins survive.
        assert "my-share" not in DATA_SOURCES
        assert "yfinance" in DATA_SOURCES
        assert "synthetic-stream" in DATA_SOURCES

    def test_handles_empty_byod_list(
        self, isolated_registry: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tradinglab import data as _data
        from tradinglab import defaults
        from tradinglab.gui.local_data_dialog import _refresh_data_registry
        monkeypatch.setattr(_data, "register_local_sources", lambda: [])
        monkeypatch.setattr(defaults, "reload", lambda: None)
        # Must not raise on a no-op registry.
        _refresh_data_registry()


# ---------------------------------------------------------------------------
# Dialog lifecycle
# ---------------------------------------------------------------------------


class TestDialogLifecycle:
    def test_opens_and_closes(self, root: tk.Tk) -> None:
        from tradinglab.gui.local_data_dialog import LocalDataDialog
        dlg = LocalDataDialog(root)
        assert dlg.winfo_exists()
        dlg.destroy()

    def test_settings_loaded_into_treeview(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tradinglab.gui import local_data_dialog as ldd

        monkeypatch.setattr(
            ldd, "_load_roots_from_settings",
            lambda: (True, [
                ("share_2024", "C:/data"),
                ("backup", "D:/backup"),
            ]),
        )
        dlg = ldd.LocalDataDialog(root)
        try:
            items = dlg._tree.get_children()
            assert len(items) == 2
            # The enabled checkbox must reflect the loaded value.
            assert dlg._enabled_var.get() is True
        finally:
            dlg.destroy()

    def test_cancel_does_not_invoke_save(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tradinglab.gui import local_data_dialog as ldd

        save_fn = mock.MagicMock()
        monkeypatch.setattr(ldd, "_save_roots_to_settings", save_fn)
        monkeypatch.setattr(
            ldd, "_load_roots_from_settings", lambda: (False, []),
        )

        dlg = ldd.LocalDataDialog(root)
        dlg._on_cancel()
        save_fn.assert_not_called()

    def test_save_invokes_save_and_callback(
        self, root: tk.Tk, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tradinglab.gui import local_data_dialog as ldd

        save_fn = mock.MagicMock()
        monkeypatch.setattr(ldd, "_save_roots_to_settings", save_fn)
        monkeypatch.setattr(
            ldd, "_load_roots_from_settings",
            lambda: (True, [("share", str(tmp_path))]),
        )
        # Stub the registry refresh so we don't need a real data root.
        monkeypatch.setattr(ldd, "_refresh_data_registry", lambda: None)

        callback = mock.MagicMock()
        dlg = ldd.LocalDataDialog(root, on_changed=callback)
        dlg._on_save()

        save_fn.assert_called_once()
        callback.assert_called_once()


# ---------------------------------------------------------------------------
# Name validator
# ---------------------------------------------------------------------------


class TestNameValidator:
    """The name validator must reject hyphens (would confuse the
    ``<root-name>-<subdir>`` combobox parser) but accept underscores
    and alphanumerics."""

    @pytest.mark.parametrize("good", [
        "share", "share_2024", "alpha1", "ABC", "x", "z9",
        "long_name_with_underscores",
    ])
    def test_accepts_valid(self, good: str) -> None:
        from tradinglab.gui.local_data_dialog import _validate_root_name
        assert _validate_root_name(good) is None  # None = valid

    @pytest.mark.parametrize("bad,reason_token", [
        ("share-2024", "hyphen"),
        ("share 2024", "alphanumeric"),
        ("share.2024", "alphanumeric"),
        ("share/2024", "alphanumeric"),
        ("", "required"),
        ("   ", "required"),
    ])
    def test_rejects_invalid(self, bad: str, reason_token: str) -> None:
        from tradinglab.gui.local_data_dialog import _validate_root_name
        err = _validate_root_name(bad)
        assert err is not None
        assert reason_token.lower() in err.lower()
