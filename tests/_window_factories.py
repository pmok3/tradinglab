"""One fixture roster for the native-theme and standard window-width probes."""
from __future__ import annotations

import tkinter as tk
from types import SimpleNamespace

from tradinglab.constants import DARK_THEME
from tradinglab.gui import (
    dialogs,
    exits_dialog,
    pre_trade_dialog,
    sandbox_panel,
    sandbox_review_dialog,
    scanner_tab,
)


class _FakeWatchlists:
    MAX_PINNED = 5

    def __init__(self) -> None:
        self._wl = SimpleNamespace(tickers=["AAPL", "MSFT"])

    def list_names(self) -> list[str]:
        return ["Momentum"]

    def pinned_names(self) -> list[str]:
        return []

    def get(self, _name: str):
        return self._wl


class _FakeSandboxController:
    app = SimpleNamespace(_display_tz="", ticker_var=None)
    focus_symbol = "AAPL"
    blind = False

    def set_post_trade_callback(self, _callback) -> None:
        return None

    def clock_ts(self) -> int:
        return 1_700_000_000

    def cash(self) -> float:
        return 100_000.0

    def is_active(self) -> bool:
        return True

    def tickers(self) -> list[str]:
        return ["AAPL", "MSFT"]

    def positions_snapshot(self) -> list[dict[str, object]]:
        return []


class _FakeTagStore:
    def list(self) -> list[str]:
        return ["Gap", "Pullback"]


def _build_doc_viewer(root, _monkeypatch):
    from tradinglab.gui.doc_viewer import DocViewerDialog
    return DocViewerDialog(root)


def _build_watchlist(root, _monkeypatch):
    root._watchlists = _FakeWatchlists()
    return dialogs._WatchlistDialog(root)


def _build_exits(root, monkeypatch):
    monkeypatch.setattr(exits_dialog._exits_storage, "load_all", lambda: ([], []))
    return exits_dialog.ExitsDialog(root)


def _build_sandbox_panel(root, _monkeypatch):
    return sandbox_panel.SandboxPanel(root, _FakeSandboxController())


def _build_post_trade_review(root, _monkeypatch):
    post = SimpleNamespace(
        side="long", symbol="AAPL", quantity=1.0,
        entry_ts=1_700_000_000, exit_ts=1_700_000_060,
        entry_price=100.0, exit_price=101.0, pnl=1.0, pnl_pct=0.01,
        mae=0.5, mae_pct=0.005, mfe=1.5, mfe_pct=0.015,
    )
    return sandbox_review_dialog.PostTradeReviewDialog(root, post)


def _build_decision_log(root, _monkeypatch):
    return sandbox_review_dialog.DecisionLogDialog(root, "AAPL", setup_tags=["Gap"])


def _build_tags_editor(root, _monkeypatch):
    return sandbox_review_dialog.TagsEditorDialog(root, _FakeTagStore())


def _build_load_scan(root, _monkeypatch):
    return scanner_tab._LoadScanDialog(
        root, [("scan-1", SimpleNamespace(name="Breakout"))],
    )


def _build_pre_trade(root, _monkeypatch):
    return pre_trade_dialog.PreTradeFormDialog(root, "AAPL", setup_tags=["Gap"])


def _build_color_chooser(root, _monkeypatch):
    from tradinglab.gui.color_palette import ThemedColorChooser
    return ThemedColorChooser(root, initial="#1f77b4")


def _build_chartstack_settings(root, _monkeypatch):
    from tradinglab.gui.chartstack_settings_dialog import ChartStackSettingsDialog
    return ChartStackSettingsDialog(root)


def _build_credentials(root, _monkeypatch):
    from tradinglab.gui.credentials_dialog import CredentialsDialog
    return CredentialsDialog(root)


def _build_export_cache(root, monkeypatch):
    from tradinglab.gui import export_cache_dialog
    monkeypatch.setattr(export_cache_dialog, "_load_cache_index", lambda: [
        ("yfinance", "SPY", "1d"), ("yfinance", "AAPL", "5m"),
    ])
    return export_cache_dialog.ExportCacheDialog(root)


def _build_local_data(root, monkeypatch):
    from tradinglab.gui import local_data_dialog
    monkeypatch.setattr(local_data_dialog, "_load_roots_from_settings", lambda: (False, []))
    return local_data_dialog.LocalDataDialog(root)


def _build_bracket(root, _monkeypatch):
    from tradinglab.gui.exits_dialog_widgets import _BracketDialog
    return _BracketDialog(root)


def _build_watchlist_columns(root, _monkeypatch):
    from tradinglab.gui.watchlist_columns_dialog import WatchlistColumnsDialog
    from tradinglab.watchlists.columns import default_columns
    return WatchlistColumnsDialog(
        root, watchlist_name="Momentum", columns=list(default_columns()), on_apply=lambda _cols: None,
    )


def _build_operand(root, _monkeypatch):
    from tradinglab.gui.expression_builder import _OperandDialog
    return _OperandDialog(root, ref=None)


def _build_fieldref_param(root, _monkeypatch):
    from tradinglab.gui.scanner_block_editor import _FieldRefParamDialog
    from tradinglab.scanner.model import FieldRef
    return _FieldRefParamDialog(root, ref=FieldRef.indicator("rsi", params={"length": 14}))


def _build_entries(root, monkeypatch):
    from tradinglab.entries import storage
    from tradinglab.gui.entries_dialog import EntriesDialog
    monkeypatch.setattr(storage, "load_all", lambda: ([], []))
    return EntriesDialog(root)


def _build_universe_prepare(root, _monkeypatch):
    from tradinglab.gui.universe_prepare_dialog import UniversePrepareDialog
    return UniversePrepareDialog(root, source_name="yfinance", fetcher=lambda _s, _i: [])


def _build_sandbox_start(root, _monkeypatch):
    import datetime as dt

    from tradinglab.gui.sandbox_dialog import SandboxStartDialog
    return SandboxStartDialog(
        root, reference_symbol="SPY", intervals=["1m", "5m", "15m", "1h"],
        eligible_dates_provider=lambda _itv, _src: [dt.date(2024, 6, 3)],
    )


def _build_drawing(root, _monkeypatch):
    from tradinglab.drawings.model import make_hline_drawing
    from tradinglab.drawings.store import DrawingStore
    from tradinglab.gui.drawing_dialog import DrawingDialog
    store = DrawingStore(autosave=False)
    drawing = make_hline_drawing(ticker="AAPL", price=150.0, color="#2962ff")
    store.add(drawing)
    return DrawingDialog(root, store=store, drawing=drawing)


def _indicator_app(root):
    from tradinglab.indicators.config import IndicatorConfig, IndicatorManager
    mgr = IndicatorManager()
    mgr.add(IndicatorConfig(kind_id="sma", params={"length": 20}, display_name="SMA(20)"))
    root._indicator_manager = mgr
    root._indicator_dialog = None
    root._per_indicator_dialogs = {}
    # This dialog resolves app._theme rather than _theme_ctrl.
    root._theme = dict(DARK_THEME)
    if not hasattr(root, "interval_var"):
        root.interval_var = tk.StringVar(root, value="1d")
    root._on_menu_save_config = lambda *a, **k: None
    return mgr


def _build_indicator_dialog(root, _monkeypatch):
    from tradinglab.gui.indicator_dialog import IndicatorDialog
    _indicator_app(root)
    return IndicatorDialog(root)


def _build_per_indicator(root, _monkeypatch):
    from tradinglab.gui.per_indicator_dialog import _PerIndicatorDialog
    mgr = _indicator_app(root)
    return _PerIndicatorDialog(root, mgr.list()[0].id, slot="primary")


def _build_custom_indicator(root, _monkeypatch):
    import tempfile
    from pathlib import Path

    from tradinglab.gui.custom_indicator_dialog import CustomIndicatorDialog
    directory = tempfile.TemporaryDirectory(prefix="tl_custom_ind_")
    dialog = CustomIndicatorDialog(root, directory=Path(directory.name))
    dialog.bind("<Destroy>", lambda event: directory.cleanup() if event.widget is dialog else None, add="+")
    return dialog


def _build_theme_editor(root, _monkeypatch):
    from tradinglab.gui.theme_editor import ThemeEditorDialog
    root._theme_overrides = {"light": {}, "dark": {}}
    if not hasattr(root, "dark_var"):
        root.dark_var = tk.BooleanVar(master=root, value=True)
    root.set_theme_override = lambda *a, **k: None
    root.clear_theme_overrides = lambda *a, **k: None
    root.replace_theme_overrides = lambda *a, **k: None
    root._apply_theme = lambda *a, **k: None
    return ThemeEditorDialog(root)


WINDOW_FACTORIES = {
    "tradinglab.gui.doc_viewer.DocViewerDialog": _build_doc_viewer,
    "tradinglab.gui.dialogs._WatchlistDialog": _build_watchlist,
    "tradinglab.gui.exits_dialog.ExitsDialog": _build_exits,
    "tradinglab.gui.sandbox_panel.SandboxPanel": _build_sandbox_panel,
    "tradinglab.gui.sandbox_review_dialog.DecisionLogDialog": _build_decision_log,
    "tradinglab.gui.sandbox_review_dialog.PostTradeReviewDialog": _build_post_trade_review,
    "tradinglab.gui.sandbox_review_dialog.TagsEditorDialog": _build_tags_editor,
    "tradinglab.gui.scanner_tab._LoadScanDialog": _build_load_scan,
    "tradinglab.gui.pre_trade_dialog.PreTradeFormDialog": _build_pre_trade,
    "tradinglab.gui.color_palette.ThemedColorChooser": _build_color_chooser,
    "tradinglab.gui.chartstack_settings_dialog.ChartStackSettingsDialog": _build_chartstack_settings,
    "tradinglab.gui.credentials_dialog.CredentialsDialog": _build_credentials,
    "tradinglab.gui.export_cache_dialog.ExportCacheDialog": _build_export_cache,
    "tradinglab.gui.local_data_dialog.LocalDataDialog": _build_local_data,
    "tradinglab.gui.exits_dialog_widgets._BracketDialog": _build_bracket,
    "tradinglab.gui.watchlist_columns_dialog.WatchlistColumnsDialog": _build_watchlist_columns,
    "tradinglab.gui.expression_builder._OperandDialog": _build_operand,
    "tradinglab.gui.scanner_block_editor._FieldRefParamDialog": _build_fieldref_param,
    "tradinglab.gui.entries_dialog.EntriesDialog": _build_entries,
    "tradinglab.gui.universe_prepare_dialog.UniversePrepareDialog": _build_universe_prepare,
    "tradinglab.gui.sandbox_dialog.SandboxStartDialog": _build_sandbox_start,
    "tradinglab.gui.drawing_dialog.DrawingDialog": _build_drawing,
    "tradinglab.gui.indicator_dialog.IndicatorDialog": _build_indicator_dialog,
    "tradinglab.gui.per_indicator_dialog._PerIndicatorDialog": _build_per_indicator,
    "tradinglab.gui.custom_indicator_dialog.CustomIndicatorDialog": _build_custom_indicator,
    "tradinglab.gui.theme_editor.ThemeEditorDialog": _build_theme_editor,
}
