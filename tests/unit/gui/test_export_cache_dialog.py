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


def _make_candles(n: int = 3) -> list[Candle]:
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
            # Status message should prompt for the zip file.
            assert "zip" in dlg._status_var.get().lower()
        finally:
            dlg.destroy()

    def test_refuses_export_with_no_selection(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        _stub_cache_index(monkeypatch, [("yfinance", "AAPL", "5m")])
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            dlg._destination = tmp_path / "out.zip"
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
        zip_path = tmp_path / "out.zip"
        try:
            dlg._destination = zip_path
            dlg._on_export()
        finally:
            try:
                dlg.destroy()
            except tk.TclError:
                pass

        # Audit ``local-export-zip``: dialog now writes a single zip,
        # not a folder of loose CSVs.
        import zipfile
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            names = sorted(zf.namelist())
        assert names == sorted([
            "yfinance/AAPL_5m.csv",
            "polygon/MSFT_1d.csv",
        ])

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
        zip_path = tmp_path / "out.zip"
        try:
            dlg._destination = zip_path
            # Uncheck the polygon entry.
            key = dlg._key("polygon", "MSFT", "1d")
            dlg._selected[key] = False
            dlg._on_export()
        finally:
            try:
                dlg.destroy()
            except tk.TclError:
                pass

        import zipfile
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert "yfinance/AAPL_5m.csv" in names
        assert "polygon/MSFT_1d.csv" not in names


# ---------------------------------------------------------------------------
# Quant-only filter
# ---------------------------------------------------------------------------
#
# The filter classifies on the CANONICAL symbol key, not the literal string,
# because the two sides of the comparison spell index symbols differently: the
# quant catalog says `VIX`, while the disk cache holds whatever the fetching
# source used (`^VIX` on yfinance, `$VIX` on Schwab). It is also a pure view
# filter — Select All / Select None / Export all act on `visible_entries()`,
# so the visible selection is exactly what gets written.
#
# See `gui/export_cache_dialog.spec.md`.


class TestIsQuantEntry:
    """The classifier, independent of any Tk widget."""

    def test_catalog_shorthand_matches(self) -> None:
        from tradinglab.gui.export_cache_dialog import is_quant_entry
        assert is_quant_entry("VIX")

    @pytest.mark.parametrize("form", ["^VIX", "$VIX", "I:VIX"])
    def test_every_vendor_spelling_matches(self, form: str) -> None:
        """The cache holds the resolved form; the catalog holds shorthand."""
        from tradinglab.gui.export_cache_dialog import is_quant_entry
        assert is_quant_entry(form)

    def test_plain_etf_leg_matches(self) -> None:
        from tradinglab.gui.export_cache_dialog import is_quant_entry
        assert is_quant_entry("SPY")
        assert is_quant_entry("HYG")

    def test_leg_only_reachable_through_a_ratio_matches(self) -> None:
        """LQD is never a row on its own — only HYG/LQD's denominator."""
        from tradinglab.gui.export_cache_dialog import is_quant_entry
        assert is_quant_entry("LQD")

    def test_ordinary_equity_does_not_match(self) -> None:
        from tradinglab.gui.export_cache_dialog import is_quant_entry
        assert not is_quant_entry("AAPL")

    def test_empty_does_not_match(self) -> None:
        from tradinglab.gui.export_cache_dialog import is_quant_entry
        assert not is_quant_entry("")

    def test_ratio_does_not_match(self) -> None:
        """Ratios are never persisted, so they can never be listed."""
        from tradinglab.gui.export_cache_dialog import is_quant_entry
        assert not is_quant_entry("RSP/SPY")

    def test_every_catalog_leg_matches_in_every_vocabulary(self) -> None:
        from tradinglab.data.index_aliases import resolve_symbol
        from tradinglab.gui.export_cache_dialog import is_quant_entry
        from tradinglab.quant.catalog import quant_leg_symbols

        for sym in quant_leg_symbols():
            for source in ("yfinance", "schwab", "polygon"):
                assert is_quant_entry(resolve_symbol(sym, source)), (sym, source)


_MIXED_CACHE = [
    ("yfinance", "AAPL", "5m"),
    ("yfinance", "^VIX", "1d"),
    ("yfinance", "SPY", "5m"),
    ("yfinance", "MSFT", "1d"),
]


class TestQuantOnlyFilter:
    def test_off_by_default(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_cache_index(monkeypatch, _MIXED_CACHE)
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            assert not dlg._quant_only_var.get()
            assert len(dlg.visible_entries()) == 4
        finally:
            dlg.destroy()

    def test_narrows_to_quant_entries(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_cache_index(monkeypatch, _MIXED_CACHE)
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            dlg._quant_only_var.set(True)
            tickers = {t for _s, t, _i in dlg.visible_entries()}
            assert tickers == {"^VIX", "SPY"}
        finally:
            dlg.destroy()

    def test_select_all_only_touches_visible_rows(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A filtered Select All must not silently re-arm hidden rows."""
        _stub_cache_index(monkeypatch, _MIXED_CACHE)
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            dlg._select_none()
            dlg._quant_only_var.set(True)
            dlg._select_all()
            assert dlg._selected[dlg._key("yfinance", "^VIX", "1d")]
            assert dlg._selected[dlg._key("yfinance", "SPY", "5m")]
            assert not dlg._selected[dlg._key("yfinance", "AAPL", "5m")]
        finally:
            dlg.destroy()

    def test_select_none_only_touches_visible_rows(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_cache_index(monkeypatch, _MIXED_CACHE)
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            dlg._quant_only_var.set(True)
            dlg._select_none()
            assert not dlg._selected[dlg._key("yfinance", "^VIX", "1d")]
            assert dlg._selected[dlg._key("yfinance", "AAPL", "5m")]
        finally:
            dlg.destroy()

    def test_count_label_is_scoped_to_the_filter(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_cache_index(monkeypatch, _MIXED_CACHE)
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            assert dlg._selected_count_text() == "4 of 4 selected"
            dlg._quant_only_var.set(True)
            assert dlg._selected_count_text() == "2 of 2 selected"
        finally:
            dlg.destroy()

    def test_export_writes_only_the_visible_selection(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """What you see is what gets written — the invariant, end to end."""
        _stub_cache_index(monkeypatch, _MIXED_CACHE)
        _stub_cache_candles(monkeypatch, _make_candles())
        monkeypatch.setattr(
            "tkinter.messagebox.showinfo", lambda *a, **k: None)
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        zip_path = tmp_path / "quant.zip"
        try:
            dlg._destination = zip_path
            dlg._quant_only_var.set(True)
            dlg._on_export()
        finally:
            try:
                dlg.destroy()
            except tk.TclError:
                pass

        import zipfile
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        assert names == {"yfinance/^VIX_1d.csv", "yfinance/SPY_5m.csv"}

    def test_checkbox_disabled_when_no_quant_entries_cached(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_cache_index(monkeypatch, [("yfinance", "AAPL", "5m")])
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            assert "disabled" in dlg._quant_check.state()
        finally:
            dlg.destroy()

    def test_empty_cache_does_not_build_the_checkbox(
        self, root: tk.Tk, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The empty-cache branch returns before the toggle row is built."""
        _stub_cache_index(monkeypatch, [])
        from tradinglab.gui.export_cache_dialog import ExportCacheDialog
        dlg = ExportCacheDialog(root)
        try:
            assert dlg.winfo_exists()
            assert dlg.visible_entries() == []
        finally:
            dlg.destroy()

