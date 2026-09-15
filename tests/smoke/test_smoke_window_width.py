"""Real application-window widths using the same checker as standard dialogs.

These cases reuse the smoke app's interpreter, but map it for meaningful bounds.
No strategy run, replay session, export, network provider or credential is needed.
The module pauses history dispatch while real worker-pool fences and inbox drains
settle earlier work before snapshots. ChartStack receives deterministic card-only
data; the original main-cache and primary/compare assertions remain unchanged.

Settings, Performance and Strategy use transient Toplevels. Those cases retain
the headless-macOS guard from AGENTS.md sections 5 and 7.1, before construction
can block. Main and Heatmap cases remain enabled there; Windows runs every case.
"""
from __future__ import annotations

import sys
import tkinter as tk
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace

import pytest

from tests._application_window_cases import (
    HEAVY_CASES,
    WindowProbe,
    close_window,
    isolate_geometry,
    settle,
    widgets,
    width_scan,
)
from tests._window_width import assert_window_width, enlarged_fonts, mapped_window

_TRANSIENT_CASE_NAMES = frozenset({"settings", "performance", "strategy"})


def _settle_fetch_workers(app):
    """Fence existing workers, then apply their Tk callbacks before taking snapshots."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from tests.smoke._helpers import _pump_until, _timeout_scale

    service = app._fetch_svc
    executors = {app._executor, app._fetch_executor, service._prefetch_executor}
    timeout = 5 * _timeout_scale()
    for executor in executors:
        assert isinstance(executor, ThreadPoolExecutor), "Width probes require restored application executors"
        release = Event()
        ready = [Event() for _ in range(executor._max_workers)]

        def fence(entered, release=release):
            entered.set()
            assert release.wait(timeout + 1), "Fetch-worker fence was not released"

        try:
            futures = [executor.submit(fence, entered) for entered in ready]
            assert _pump_until(app, lambda ready=ready: all(entered.is_set() for entered in ready), timeout=5), (
                "Earlier application fetches did not finish before the width probe"
            )
            release.set()
            for future in futures:
                future.result(timeout=timeout)
        finally:
            release.set()
    # Future polling is 5ms and worker-inbox delivery is 80ms. Workers have
    # finished above; let those real callbacks apply rather than discarding data.
    settle(app)
    assert _pump_until(app, app._worker_inbox.empty, timeout=5), (
        "Earlier fetch results did not drain before the width probe"
    )


@pytest.fixture(scope="module", autouse=True)
def _pause_width_background_fetches(app):
    """Keep live/history work outside this module's geometry-only measurements."""
    from tests.smoke._helpers import _fake_candles
    from tradinglab.core import reference_data
    from tradinglab.gui.chartstack.controller import CardController

    provider, arrival = reference_data._provider, reference_data._on_arrival
    timers = {name: getattr(app, name) is not None for name in ("_poll_job", "_reload_job")}
    retry = app._poll_retry_count, app._poll_retry_expected_min_ts
    for name in timers:
        job = getattr(app, name)
        if job is not None:
            app.after_cancel(job)
            app._after_jobs.discard(job)
            setattr(app, name, None)
    driver = app._prefetch_driver
    original_card_start = CardController.start
    card_bars = _fake_candles(20)

    def provide_card_data(controller):
        if controller.owner_app is not app:
            return original_card_start(controller)
        if controller.binding is not None:
            app._worker_inbox.put_nowait(("card_stash", (
                controller.slot_index, controller.token, controller.binding.symbol, card_bars,
            )))

    try:
        with pytest.MonkeyPatch.context() as patches:
            reference_data.set_provider(None, on_arrival=arrival)
            for name in (
                "_submit_quant_fetches", "_kick_watchlist_preloads",
                "_preload_watchlist", "_preload_watchlist_events",
                "_preload_watchlist_daily", "_preload_watchlist_signals",
                "_schedule_next_bar_fetch", "_schedule_reload",
            ):
                patches.setattr(app, name, lambda *_args, **_kwargs: None)
            patches.setattr(app._fetch_svc, "prefetch", lambda *_args, **_kwargs: None)
            if driver is not None:
                patches.setattr(driver, "pump", lambda: None)
            patches.setattr(CardController, "start", provide_card_data)
            _settle_fetch_workers(app)
            yield
            _settle_fetch_workers(app)
    finally:
        reference_data.set_provider(provider, on_arrival=arrival)
        if timers["_poll_job"]:
            app._schedule_next_bar_fetch()
            app._poll_retry_count, app._poll_retry_expected_min_ts = retry
        if timers["_reload_job"]:
            app._schedule_reload(0)
        if driver is not None and driver.scheduler.pending_count:
            app._track_after(1, app._prefetch_pump)


@contextmanager
def _preserve_app_layout(app, monkeypatch):
    from tradinglab import settings

    geometry, minimum, maximum = app.geometry(), app.minsize(), app.maxsize()
    selected = app._notebook.select()
    tab_states = {tab: app._notebook.tab(tab, "state") for tab in app._notebook.tabs()}
    paned = app._main_paned
    sashes = [paned.sashpos(i) for i in range(len(paned.panes()) - 1)]
    chartstack = app._chartstack
    chartstack_visible = app._chartstack_currently_visible(paned)
    chartstack_setting = settings.get("chartstack.enabled", False)
    axes = [(ax, ax.get_xlim(), ax.get_ylim()) for ax in app._figure.axes]
    original_status = app.status.get()
    primary, compare = list(app._primary), list(app._compare)
    candles = {key: list(value) for key, value in app._full_cache.items()}
    geometry_store = getattr(app, "_geometry_store", None)
    saved_windows = dict(geometry_store._windows) if geometry_store is not None else None
    if geometry_store is not None:
        monkeypatch.setattr(geometry_store, "save", lambda: None)
    try:
        yield
    finally:
        app._toggle_chartstack(target=chartstack_visible)
        settings.set("chartstack.enabled", chartstack_setting)
        if chartstack is None and app._chartstack is not None:
            app._chartstack.destroy()
            app._chartstack = None
        for tab, state in tab_states.items():
            app._notebook.tab(tab, state=state)
        app._notebook.select(selected)
        app.minsize(*minimum)
        app.maxsize(*maximum)
        app.geometry(geometry)
        settle(app)
        for index, sash in enumerate(sashes):
            paned.sashpos(index, sash)
        for ax, xlim, ylim in axes:
            if ax in app._figure.axes:
                ax.set_xlim(xlim)
                ax.set_ylim(ylim)
        app.status.set(original_status)
        if geometry_store is not None:
            for job in tuple(geometry_store._pending_after.values()):
                app.after_cancel(job)
            geometry_store._pending_after.clear()
            geometry_store._windows.clear()
            geometry_store._windows.update(saved_windows)
        assert app._primary == primary and app._compare == compare, "Width probes changed chart bars"
        assert dict(app._full_cache) == candles, "Width probes changed the chart candle cache"


def _session_result():
    from tradinglab.backtest.journal import DecisionRecord, PostTradeReview
    from tradinglab.backtest.session import SessionResult, SessionSpec

    clock = 1_717_423_200  # 2024-06-03 14:00 UTC.
    return SessionResult(
        spec=SessionSpec(deck_seed=1, tickers=("AAPL",), start_clock_iso="",
                         slippage_bps=0, commission=0),
        post_trades=[PostTradeReview(
            symbol="AAPL", side="buy", quantity=10, entry_price=100, exit_price=101,
            entry_ts=clock, exit_ts=clock + 300, pnl=10, pnl_pct=1,
            mae=0, mfe=10, mae_pct=0, mfe_pct=1, ref_pre_trade_id="width-trade",
        )],
        decisions=[DecisionRecord(
            ts=clock - 60, symbol="AAPL", action="watch", confidence=4,
            setup_tag="Opening range", note="Waiting for confirmation",
        )],
        day_notes={"2024-06-03": "A populated daily journal"},
        equity_curve=[(clock, 100000), (clock + 300, 100010)],
        final_cash=100010,
    )


@contextmanager
def _populated_main_data(app, monkeypatch):
    from tradinglab.entries.model import EntryStrategy
    from tradinglab.exits.model import ExitLeg, ExitStrategy, ExitTrigger, TriggerKind
    from tradinglab.positions.model import Position
    from tradinglab.watchlists import WatchlistManager

    entries, exits, scanner = app._entries_tab, app._exits_tab, app._scanner_tab
    original_filter = entries._filter_var.get()
    original_selection = entries._tree.selection()
    original_scans, open_scans = scanner.get_library(), list(scanner._open_ids)
    selected_scan = scanner.current_scan_id()
    original_watchlist = app.watchlist_var.get()
    original_sort = dict(app._watchlist_sort_by_name)
    quant = app._quant_tab
    quant_values = {item: quant.tree.item(item, "values") for item in quant._rows_by_item}
    exit_strategy = ExitStrategy(
        id="width-exit", name="Width exit",
        legs=[ExitLeg(id="width-leg", label="Protective stop",
                      triggers=[ExitTrigger(id="width-stop", kind=TriggerKind.STOP, price=95)])],
    )
    position = Position(
        id="width-position", symbol="AAPL", side="long", qty_initial=10, qty_open=10,
        avg_entry_price=100, entry_time=datetime(2024, 6, 3, tzinfo=timezone.utc),
        source="paper", last_price=101,
    )
    attached = {"value": True}
    manager = WatchlistManager()
    manager.create("Width watchlist", ["AAPL", "MSFT"])
    manager.pin("Width watchlist")
    try:
        with monkeypatch.context() as patches:
            patches.setattr(app, "_watchlists", manager)
            patches.setattr(app, "_watchlist_row_cache", {})
            patches.setattr(app, "_watchlist_snapshot", {
                "AAPL": {"last": 101.0, "change_1d": 1.0, "pct_1d": 1.0},
                "MSFT": {"last": 202.0, "change_1d": -2.0, "pct_1d": -1.0},
            })
            patches.setattr(app, "_kick_watchlist_preloads", lambda: None)
            patches.setattr(app, "_schedule_reload", lambda *_args, **_kwargs: None)
            patches.setattr(app, "_apply_theme", lambda: None)
            patches.setattr(scanner, "_on_subtab_change", lambda _sub: None)
            patches.setattr(entries, "_library", [EntryStrategy(id="width-entry", name="Width entry")])
            patches.setattr(exits, "_library", [exit_strategy])
            patches.setattr(exits, "_last_prices", dict(exits._last_prices))
            patches.setattr(exits, "_tracker", SimpleNamespace(list_open=lambda: [position]))
            patches.setattr(exits, "_evaluator", SimpleNamespace(
                attached_strategy=lambda _id: exit_strategy if attached["value"] else None,
                trigger_state=lambda *_args: None,
            ))
            entries._filter_var.set("all")
            entries._refresh_tree()
            exits._refresh_attach_panel()
            exits._refresh_status_tree()
            scanner.set_library({"width-probe": width_scan()})
            app._rebuild_watchlist_subtabs()
            quant.set_last_values({symbol: "123.45" for symbol in quant.symbols()})
            assert entries._tree.get_children() and exits._tree.get_children()
            assert app._watchlist_trees["Width watchlist"].get_children()
            assert scanner._sub_tabs["width-probe"].scan.all_conditions()
            yield attached
    finally:
        entries._filter_var.set(original_filter)
        entries._refresh_tree()
        if original_selection:
            entries._tree.selection_set(original_selection)
        exits._refresh_attach_panel()
        exits._refresh_status_tree()
        scanner._open_ids = open_scans
        scanner.set_library(original_scans)
        if selected_scan:
            scanner.open_scan(selected_scan)
        app.watchlist_var.set(original_watchlist)
        app._watchlist_sort_by_name = original_sort
        with monkeypatch.context() as patches:
            patches.setattr(app, "_kick_watchlist_preloads", lambda: None)
            patches.setattr(app, "_apply_theme", lambda: None)
            app._rebuild_watchlist_subtabs()
        for item, values in quant_values.items():
            quant.tree.item(item, values=values)


@contextmanager
def _heavy_probe(case, app, monkeypatch, directory: Path) -> Iterator[WindowProbe]:
    with ExitStack() as cleanup:
        if case.name == "main":
            from tests._window_factories import _FakeSandboxController
            from tradinglab.gui.sandbox_panel import SandboxPanel

            panel = SandboxPanel(app._sandbox_tab_frame, _FakeSandboxController())
            panel.pack(fill="both", expand=True)
            cleanup.callback(panel.destroy)
            attached = cleanup.enter_context(_populated_main_data(app, monkeypatch))
            window = app

            def states():
                for tab in app._notebook.tabs():
                    app._notebook.tab(tab, state="normal")
                    app._notebook.select(tab)
                    settle(app)
                    yield str(app._notebook.tab(tab, "text"))
                app._notebook.select(app._exits_tab)
                attached["value"] = False
                app._exits_tab._refresh_attach_panel()
                yield "unprotected-position"
                app._notebook.select(app._entries_tab)
                app._entries_tab._filter_var.set("active")
                app._entries_tab._on_filter_change()
                yield "empty-active-entry-filter"
                app._notebook.select(app._watchlist_outer_frame)
                app._toggle_chartstack(target=True)
                settle(app)
                yield "chartstack-visible"

        elif case.name == "strategy":
            from tradinglab.gui import strategy_tab
            from tradinglab.strategy_tester import storage
            from tradinglab.strategy_tester.model import DatePreset, UniverseKind

            monkeypatch.setattr(strategy_tab._entries_storage, "load_all", lambda: ([], []))
            monkeypatch.setattr(strategy_tab._exits_storage, "load_all", lambda: ([], []))
            monkeypatch.setattr(
                strategy_tab._watchlists_storage, "load_all",
                lambda: ([SimpleNamespace(name="Width watchlist")], []),
            )
            monkeypatch.setattr(storage, "list_runs_with_paths", lambda: [])
            monkeypatch.setattr(app, "_strategy_dialog", None)
            monkeypatch.setattr(app, "_strategy_tab", None)
            app._on_open_strategy_dialog()
            window = app._strategy_dialog
            assert isinstance(window, tk.Toplevel)
            tab = app._strategy_tab
            assert isinstance(tab, strategy_tab.StrategyTab)

            def states():
                yield "initial-configure"
                from tradinglab.backtest.performance import build_trade_rows
                from tradinglab.strategy_tester.report import compute_aggregate

                rows = build_trade_rows(_session_result())
                aggregate = compute_aggregate(
                    run_id="width-report", rows_by_symbol={"AAPL": rows},
                    starting_cash=100000, bootstrap_samples=20,
                    interval_overrides=["AAPL condition authored at 1m; evaluated at 5m"],
                )
                tab._render_aggregate(aggregate, directory)
                assert tab._tree_symbol.get_children() and tab._tree_year.get_children()
                yield "populated-report"
                tab._var_advanced_open.set(True)
                tab._on_advanced_toggle()
                tab._var_date_preset.set(DatePreset.CUSTOM.value)
                tab._on_date_preset_change()
                for kind in UniverseKind:
                    tab._var_universe_kind.set(kind.value)
                    tab._on_universe_kind_change()
                    yield f"advanced-{kind.value}"
                for notebook in (w for w in widgets(window) if isinstance(w, ttk.Notebook)):
                    for page in notebook.tabs():
                        notebook.select(page)
                        yield f"report-{notebook.tab(page, 'text')}"
                clean = compute_aggregate(
                    run_id="width-report", rows_by_symbol={"AAPL": rows * 150},
                    starting_cash=100000, bootstrap_samples=20,
                )
                tab._render_aggregate(clean, directory)
                yield "warnings-cleared"
                tab._render_aggregate(aggregate, directory)
                yield "warnings-restored"

        elif case.name == "settings":
            from tradinglab.gui.dialogs import _SettingsDialog

            window = _SettingsDialog(app)

            def states():
                window._form_canvas.yview_moveto(0)
                yield "top"
                window._on_capture_current_as_default()
                window._form_canvas.yview_moveto(1)
                yield "captured-defaults-and-footer"
                window._on_reset_startup_defaults()
                yield "reset-defaults"

        elif case.name == "performance":
            from tradinglab.gui.performance_view import PerformanceView

            window = PerformanceView(app, _session_result(), title="Width probe")

            def states():
                assert window._trades.get_children() and window._journal_tree.get_children()
                assert window._equity_canvas is not None
                yield "populated"
                window._journal_blind_var.set(True)
                window._populate_journal()
                window._show_mtm_var.set(False)
                window._on_toggle_equity_lines()
                yield "blind-journal-and-realized-equity"

        elif case.name == "heatmap":
            from tradinglab.backtest.heatmap_provider import HeatmapProvider
            from tradinglab.data.shares_sources import SharesFact
            from tradinglab.gui.sandbox_heatmap import SandboxHeatmapWindow

            clock = 1_717_423_200
            provider = HeatmapProvider(
                meta={
                    "AAPL": {"sector": "Technology", "industry": "Hardware", "cik": "1", "date_added_ts": 0},
                    "MSFT": {"sector": "Technology", "industry": "Software", "cik": "2", "date_added_ts": 0},
                },
                shares_fetcher=lambda _symbol: [SharesFact(clock - 86400, clock - 86400, 1000)],
                splits_fetcher=lambda _symbol: [], cache_dir=directory,
            )
            provider.prime(["AAPL", "MSFT"])
            controller = SimpleNamespace(
                clock_ts=lambda: clock, is_active=lambda: True,
                current_session_date=lambda: "2024-06-03",
                positions_snapshot=lambda: [], focus_symbol="AAPL", blind=False,
                engine=SimpleNamespace(clock=SimpleNamespace(index=42)),
                market_state=lambda: "open",
            )
            window = SandboxHeatmapWindow(
                app, controller, provider=provider, price_source=lambda _symbol, _clock: (101, 100),
            )

            def states():
                assert len(window._tiles) == 2
                yield "replay"
                controller.blind = True
                window.refresh()
                yield "blind-replay"
                controller.blind = False
                window._live = True
                window.refresh()
                yield "live-cached-prices"
                tile = window._tiles[0]
                window._on_motion(SimpleNamespace(
                    inaxes=window._ax, xdata=tile.x + tile.w / 2, ydata=tile.y + tile.h / 2,
                ))
                yield "hover-detail"

        else:
            raise AssertionError(f"No heavy builder for {case.name}")
        if window is not app:
            cleanup.callback(close_window, window)
        yield WindowProbe(window, states)


def check_w0_application_window_width(app, case, scenario, font_size, monkeypatch, tmp_path):
    if sys.platform == "darwin" and case.name in _TRANSIENT_CASE_NAMES:
        pytest.skip(
            f"{case.name}: Tk transient() can deadlock during construction on headless macOS "
            "(AGENTS.md sections 5 and 7.1); the full width case runs on Windows"
        )
    store = isolate_geometry(monkeypatch, tmp_path, case, scenario)
    _settle_fetch_workers(app)
    fonts = enlarged_fonts(app, size=font_size) if font_size else nullcontext()
    failures = []
    with _preserve_app_layout(app, monkeypatch), mapped_window(app), fonts:
        if case.name == "main":
            store.restore_window(app, "main", default=app._initial_geometry, min_size=app.minsize())
        with _heavy_probe(case, app, monkeypatch, tmp_path) as probe, mapped_window(probe.window):
            if scenario == "minimum":
                width, _height = probe.window.minsize()
                probe.window.geometry(f"{width}x{max(800, probe.window.winfo_height())}")
            for state in probe.states():
                settle(probe.window)
                elided = {}
                if case.name == "main":
                    elided[app._status_label] = "Single-line status; full message opens in Status History."
                try:
                    assert_window_width(probe.window, elided_labels=elided)
                except AssertionError as exc:
                    failures.append(f"{state}: {exc}")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("case,scenario", [
    pytest.param(
        case, scenario, id=f"{scenario}-{case.name}",
        marks=pytest.mark.window_width(window_id=case.window_id),
    )
    for case in HEAVY_CASES
    for scenario in ("default", "minimum", "saved")
    if scenario != "saved" or case.geometry_key is not None
])
@pytest.mark.parametrize("font_size", [None, 16], ids=["normal-font", "large-font"])
def test_application_window_width(app, case, scenario, font_size, monkeypatch, tmp_path):
    check_w0_application_window_width(app, case, scenario, font_size, monkeypatch, tmp_path)


@pytest.mark.window_width(window_id="tradinglab.app.ChartApp")
def test_main_window_width_with_cold_cache_and_prior_fetch(app, monkeypatch, tmp_path):
    from threading import Event

    from tests.smoke._helpers import _fake_candles, _pump_until
    from tradinglab.data import DATA_SOURCES
    from tradinglab.gui import quant_app

    assert _pump_until(
        app, lambda: not app._quant_fetch_inflight and app._worker_inbox.empty(), timeout=5,
    )
    original_cache = dict(app._full_cache)
    original_snapshot = dict(app._watchlist_snapshot)
    original_visible = app._quant_visible_var.get()
    original_timer = app._quant_refresh_job is not None
    case = next(case for case in HEAVY_CASES if case.name == "main")
    source = app.source_var.get()
    bars = _fake_candles(20)
    prior_key = (source, "WIDTHPRIOR", "1d")
    started, release = Event(), Event()
    requests = []
    fetch_quant = app._fetch_quant_last

    def record_quant(symbol, src):
        requests.append((symbol, src))
        fetch_quant(symbol, src)

    def prior_fetch():
        started.set()
        try:
            assert release.wait(5), "Prior width-fixture fetch was never released"
            app._worker_inbox.put_nowait(("stash", (prior_key, bars)))
        finally:
            app._quant_fetch_inflight.discard("WIDTHPRIOR")

    future = None
    release_job = None
    try:
        with monkeypatch.context() as patches:
            patches.setitem(DATA_SOURCES, source, lambda _symbol, _interval: bars)
            patches.setattr(quant_app, "QUANT_REFRESH_MS", 20)
            patches.setattr(app._quant_tab, "symbols", lambda: ["SPY", "QQQ"])
            patches.setattr(app, "_fetch_quant_last", record_quant)
            app._full_cache.clear()
            app._quant_fetch_inflight.add("WIDTHPRIOR")
            future = app._fetch_executor.submit(prior_fetch)
            assert started.wait(2), "Prior fetch did not enter its worker"
            release_job = app.after(20, release.set)
            app._quant_visible_var.set(True)
            app._start_quant_refresh_loop()
            check_w0_application_window_width(app, case, "default", None, patches, tmp_path)
            assert requests == [], "Width-only Quant selection started history fetches"
            assert dict(app._full_cache) == {prior_key: bars}, (
                "Prior work must settle before the snapshot; width probes must leave cold Quant keys absent"
            )
    finally:
        release.set()
        app._stop_quant_refresh_loop()
        if release_job is not None:
            app.after_cancel(release_job)
        try:
            if future is not None:
                future.result(timeout=5)
            else:
                app._quant_fetch_inflight.discard("WIDTHPRIOR")
            assert _pump_until(
                app, lambda: not app._quant_fetch_inflight and app._worker_inbox.empty(), timeout=5,
            )
        finally:
            app._quant_visible_var.set(original_visible)
            app._full_cache.clear()
            app._full_cache.update(original_cache)
            app._watchlist_snapshot.clear()
            app._watchlist_snapshot.update(original_snapshot)
            assert dict(app._full_cache) == original_cache, (
                "Cold-cache fixture failed to restore its temporary data"
            )
            assert app._watchlist_snapshot == original_snapshot
            if original_timer:
                app._quant_refresh_job = app.after(quant_app.QUANT_REFRESH_MS, app._quant_refresh_tick)


def test_width_cache_guard_still_rejects_new_stashes(app, monkeypatch):
    from tests.smoke._helpers import _fake_candles, _pump_until

    _settle_fetch_workers(app)
    original = dict(app._full_cache)
    key = next(iter(original), ("yfinance", "WIDTHUNEXPECTED", "1d"))
    changed = [*original.get(key, ()), *_fake_candles(1)]
    try:
        with pytest.raises(AssertionError, match="Width probes changed the chart candle cache"):
            with _preserve_app_layout(app, monkeypatch):
                app._worker_inbox.put_nowait(("stash", (key, changed)))
                assert _pump_until(app, lambda: app._full_cache.get(key) == changed, timeout=5), (
                    "The controlled cache mutation was not applied inside the guarded scope"
                )
    finally:
        _settle_fetch_workers(app)
        app._full_cache.clear()
        app._full_cache.update(original)
        assert dict(app._full_cache) == original
