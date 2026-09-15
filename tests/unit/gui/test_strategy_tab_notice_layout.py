"""Clearing report warnings must return their space to the report controls."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tests._application_window_cases import settle
from tests._window_width import assert_window_width, enlarged_fonts, mapped_window
from tests.smoke.test_smoke_window_width import _session_result
from tradinglab.backtest.performance import build_trade_rows
from tradinglab.gui.strategy_tab import StrategyTab
from tradinglab.strategy_tester import storage
from tradinglab.strategy_tester.report import compute_aggregate


def test_report_warning_container_clears_and_returns_without_starving_controls(root, monkeypatch):
    monkeypatch.setattr(storage, "list_runs_with_paths", lambda: [])
    library = SimpleNamespace(load_all=lambda: ([], []))
    rows = build_trade_rows(_session_result())
    clean = compute_aggregate(
        run_id="width-report", rows_by_symbol={"AAPL": rows * 150},
        starting_cash=100000, bootstrap_samples=20,
    )
    warned = compute_aggregate(
        run_id="width-report", rows_by_symbol={"AAPL": rows},
        starting_cash=100000, bootstrap_samples=20,
        interval_overrides=[
            f"Indicator {index} authored at 1m; evaluated at 5m in single-interval mode."
            for index in range(8)
        ],
    )
    assert not clean.insufficient_sample and not clean.low_sample and clean.trade_count == 150
    with enlarged_fonts(root, size=16):
        tab = StrategyTab(
            root, entries_storage=library, exits_storage=library, watchlists_storage=library,
        )
        tab.pack(fill="both", expand=True)
        tab._cfg_frame.master.forget(tab._cfg_frame)
        root.geometry("420x650")
        try:
            with mapped_window(root):
                report = tab._report_frame
                notices = tab._banner_sample.master
                baseline_height = None
                for transition, aggregate in enumerate((clean, warned, clean, warned, clean)):
                    tab._render_aggregate(aggregate, Path("report"))
                    settle(root)
                    assert report.winfo_width() == 420
                    if aggregate is warned:
                        assert notices.winfo_ismapped() and notices.winfo_height() > 100
                        assert tab._banner_sample.winfo_ismapped()
                        assert tab._banner_interval.winfo_ismapped()
                        order = report.pack_slaves()
                        assert order.index(notices) == order.index(tab._lbl_run_id) + 1
                    else:
                        assert not tab._banner_sample.winfo_manager()
                        assert not tab._banner_interval.winfo_manager()
                        assert tab._btn_export_pdf.winfo_ismapped(), (
                            f"warning-free transition {transition} retained "
                            f"{notices.winfo_height()}px of notice allocation"
                        )
                        assert_window_width(report)
                        for control in (tab._tree_symbol, tab._btn_export_pdf):
                            assert control.winfo_ismapped() and control.winfo_height() > 1
                            assert control.winfo_rooty() + control.winfo_height() <= (
                                report.winfo_rooty() + report.winfo_height()
                            )
                        assert tab._tree_symbol.get_children()
                        height = tab._tree_symbol.winfo_height()
                        if baseline_height is None:
                            baseline_height = height
                        assert height == baseline_height
        finally:
            tab.destroy()
