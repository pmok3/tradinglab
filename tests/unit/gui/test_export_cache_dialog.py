"""Unit tests for ``gui/export_cache_dialog.py`` (Export Bars to CSV…).

Coverage focuses on the logic that doesn't require interactive Tk:
* All entries checked by default.
* Select All / Select None toggle the selection map.
* Export button refuses to run without a destination.
* End-to-end export through to ``local_export.export_entries``.
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
from unittest import mock

import pytest

from tradinglab.models import Candle


_ET = timezone(timedelta(hours=-4))


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


def _make_candles(n: int = 3) -> List[Candle]:
    start = datetime(2024, 3, 15, 9, 30, tzinfo=_ET)
    return [
        Candle(
            date=start + timedelta(minutes=5 * i),
            open=100.0 + i, high=101.0 + i, low=99.5 + i, close=100.5 + i,
            volume=1000 + 100 * i, session="regular",
        )
        for i in range(n)
    ]


def _stub_cache_index(
    monkeypatch: pytest.MonkeyPatch, entries: list[tuple[str, str, str]],
) -> None:
    from tradinglab.gui import export_cache_dialog as ecd
    monkeypatch.setattr(ecd, "_load_cache_index", lambda: list(entries))


def _stub_cache_candles(
    monkeypatch: pytest.MonkeyPatch, candles: list[Candle],
) -> None:
    from tradinglab.gui import export_cache_dialog as ecd
    monkeypatch.setattr(ecd, "_load_cache_candles", lambda *a, **k: list(candles))


# ---------------------------------------------------------------------------
# Default state
# ---------------------------------------------------------------------------


class TestDefaultState:
    def test_empty_cache_message(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_cache_index(monkeypatch, [])
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            # The dialog must still exist (no tree, just the message).
            assert dlg.winfo_exists()
        finally:
            dlg.destroy()

    def test_all_entries_selected_by_default(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_cache_index(monkeypatch, [
            ("yfinance", "AAPL", "5m"),
            ("polygon", "AAPL", "5m"),
            ("yfinance", "MSFT", "1d"),
        ])
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            assert all(dlg._selected.values())
            assert len(dlg._selected) == 3
        finally:
            dlg.destroy()


# ---------------------------------------------------------------------------
# Select All / Select None
# ---------------------------------------------------------------------------


class TestSelectionToggles:
    def test_select_none_clears_all(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_cache_index(monkeypatch, [
            ("yfinance", "AAPL", "5m"),
            ("polygon", "AAPL", "5m"),
        ])
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            dlg._select_none()
            assert not any(dlg._selected.values())
        finally:
            dlg.destroy()

    def test_select_all_restores(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_cache_index(monkeypatch, [
            ("yfinance", "AAPL", "5m"),
            ("polygon", "AAPL", "5m"),
        ])
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            dlg._select_none()
            dlg._select_all()
            assert all(dlg._selected.values())
        finally:
            dlg.destroy()


# ---------------------------------------------------------------------------
# Export gating
# ---------------------------------------------------------------------------


class TestExportGating:
    def test_refuses_export_without_destination(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_cache_index(monkeypatch, [("yfinance", "AAPL", "5m")])
        _stub_cache_candles(monkeypatch, _make_candles(3))
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            dlg._destination = None
            dlg._on_export()
            # Status message should mention "destination".
            assert "destination" in dlg._status_var.get().lower()
        finally:
            dlg.destroy()

    def test_refuses_export_with_no_selection(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        _stub_cache_index(monkeypatch, [("yfinance", "AAPL", "5m")])
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            dlg._destination = tmp_path
            dlg._select_none()
            dlg._on_export()
            assert "nothing" in dlg._status_var.get().lower()
        finally:
            dlg.destroy()


# ---------------------------------------------------------------------------
# Happy-path export
# ---------------------------------------------------------------------------


class TestEndToEndExport:
    def test_writes_selected_entries(
        self, root: tk.Tk,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _stub_cache_index(monkeypatch, [
            ("yfinance", "AAPL", "5m"),
            ("polygon", "MSFT", "1d"),
        ])
        _stub_cache_candles(monkeypatch, _make_candles(2))

        # Suppress the messagebox the real dialog pops on completion.
        from tradinglab.gui import export_cache_dialog as ecd
        monkeypatch.setattr(ecd, "messagebox", mock.MagicMock())

        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            dlg._destination = tmp_path
            dlg._on_export()
        finally:
            try:
                dlg.destroy()
            except tk.TclError:
                pass

        # Both selected entries should land in the subfolder-per-source
        # layout the importer expects.
        assert (tmp_path / "yfinance" / "AAPL_5m.csv").exists()
        assert (tmp_path / "polygon" / "MSFT_1d.csv").exists()

    def test_skipped_entries_not_written(
        self, root: tk.Tk,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        _stub_cache_index(monkeypatch, [
            ("yfinance", "AAPL", "5m"),
            ("polygon", "MSFT", "1d"),
        ])
        _stub_cache_candles(monkeypatch, _make_candles(2))

        from tradinglab.gui import export_cache_dialog as ecd
        monkeypatch.setattr(ecd, "messagebox", mock.MagicMock())

        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            dlg._destination = tmp_path
            # Uncheck the polygon entry.
            key = dlg._key("polygon", "MSFT", "1d")
            dlg._selected[key] = False
            dlg._on_export()
        finally:
            try:
                dlg.destroy()
            except tk.TclError:
                pass

        assert (tmp_path / "yfinance" / "AAPL_5m.csv").exists()
        assert not (tmp_path / "polygon" / "MSFT_1d.csv").exists()
