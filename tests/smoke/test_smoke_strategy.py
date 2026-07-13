"""Per-feature smoke check: Strategy Tester PR1 kernel.

Standalone (no ``app`` fixture) — the strategy tester runner is Tk-free
at the orchestration layer, so this exercises the full pipeline
without a GUI: ``TestConfig`` → ``runner.run`` → on-disk artifacts.

The check stubs the yfinance source so no network is touched; uses
synthetic deterministic candles via ``_fake_candles``; and runs
across three synthetic tickers under a temporary cache directory.

Acceptance: every symbol completes, ``RunStatus.DONE`` is set on the
final manifest, ``config.json`` and one ``per_symbol/<SYM>.json`` are
on disk, and the total runtime stays under ~3 seconds.
"""

from __future__ import annotations

import gc
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

# Ensure the smoke env is in place before importing tradinglab modules.
import tests.smoke._helpers as _h  # noqa: F401


def _make_test_config():
    from tradinglab.entries.model import (
        Direction,
        EntryStrategy,
        EntryTrigger,
        ShareRounding,
        SizingKind,
        SizingRule,
    )
    from tradinglab.entries.model import (
        TriggerKind as EntryTriggerKind,
    )
    from tradinglab.entries.model import Universe as EntryUniverse
    from tradinglab.exits.model import (
        ExitLeg,
        ExitStrategy,
        ExitTrigger,
    )
    from tradinglab.exits.model import (
        TriggerKind as ExitTriggerKind,
    )
    from tradinglab.strategy_tester import (
        CostModel,
        DatePreset,
        TestConfig,
        UniverseKind,
        UniverseSpec,
    )

    entry = EntryStrategy(
        id="entry-st0",
        name="ST0 Market Long",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("STRAT_A", "STRAT_B", "STRAT_C")),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET, label="open-on-arm"),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY,
            qty=10.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )

    exit_strat = ExitStrategy(
        id="exit-st0",
        name="ST0 5pct Stop + 20pct Take",
        legs=[
            ExitLeg(
                id="leg-stop",
                label="stop",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.STOP,
                        offset_pct=5.0,
                        qty_pct=100.0,
                    )
                ],
            ),
            ExitLeg(
                id="leg-tp",
                label="take-profit",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.LIMIT,
                        offset_pct=20.0,
                        qty_pct=100.0,
                    )
                ],
            ),
        ],
        eod_kill_switch=True,
    )

    cfg = TestConfig(
        entry_strategy_id=entry.id,
        exit_strategy_id=exit_strat.id,
        universe=UniverseSpec(
            kind=UniverseKind.SYMBOLS,
            symbols=("STRAT_A", "STRAT_B", "STRAT_C"),
        ),
        start_date="2020-01-01",
        end_date="2030-01-01",
        interval="5m",
        starting_cash=100_000.0,
        cost_model=CostModel(),
        date_preset=DatePreset.CUSTOM,
        user_label="ST0 smoke kernel",
        # Synthetic candles are tz-naive and not RTH-aligned in ET;
        # opt into extended-hours so the RTH filter doesn't drop them all.
        include_extended_hours=True,
    )

    return entry, exit_strat, cfg


def _fake_fetcher(symbol: str, interval: str):
    return _h._fake_candles(
        120,
        start_price=100.0 + (hash(symbol) % 50),
        step_min=5,
        session_pattern="regular",
    )


def check_st0_kernel_only(tmp_cache_root: Path) -> None:
    """Run the strategy_tester kernel over 3 synthetic tickers and validate output."""
    from tradinglab.strategy_tester import (
        AcceptanceToken,
        RunStatus,
    )
    from tradinglab.strategy_tester import (
        run as run_strategy_test,
    )

    entry, exit_strat, cfg = _make_test_config()
    entries_by_id = {entry.id: entry}
    exits_by_id = {exit_strat.id: exit_strat}

    t0 = time.monotonic()
    result = run_strategy_test(
        cfg,
        cancel_token=AcceptanceToken(),
        candles_fetcher=_fake_fetcher,
        entry_loader=lambda sid: entries_by_id[sid],
        exit_loader=lambda sid: exits_by_id[sid],
        max_workers=2,
    )
    elapsed = time.monotonic() - t0

    # Status + counters
    assert result.test_run.status is RunStatus.DONE, (
        f"expected DONE status, got {result.test_run.status} "
        f"with error={result.test_run.error!r}"
    )
    assert result.test_run.symbol_count_total == 3
    assert result.test_run.symbol_count_done == 3
    assert all(o.ok for o in result.outcomes), (
        f"unexpected per-symbol errors: "
        f"{[(o.symbol, o.error) for o in result.outcomes if not o.ok]}"
    )

    # Disk artifacts
    run_dir = result.run_dir
    assert (run_dir / "config.json").exists(), "config.json should have been written"
    assert (run_dir / "manifest.json").exists(), "manifest.json should have been written"
    assert (run_dir / "per_symbol").is_dir(), "per_symbol/ should have been created"
    assert (run_dir / "screenshots").is_dir(), "screenshots/ should have been created"

    # screenshot_spec defaulted to None → no PNGs and screenshot_count==0
    assert all(o.screenshot_count == 0 for o in result.outcomes), (
        "expected no screenshots without explicit ScreenshotSpec"
    )
    assert not list((run_dir / "screenshots").iterdir()), (
        "screenshots/ should be empty when screenshot_spec=None"
    )

    # Each symbol's SessionResult parses + contains at least one fill (MARKET
    # entry fires on the second bar, EOD kill-switch ensures a close on the
    # last bar, so trade_count >= 2 per symbol).
    for sym in ("STRAT_A", "STRAT_B", "STRAT_C"):
        path = run_dir / "per_symbol" / f"{sym}.json"
        assert path.exists(), f"per_symbol/{sym}.json should exist"
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["spec"]["tickers"] == [sym]
        assert payload["spec"]["engine_version"] == "sandbox-1d"
        fills = payload.get("fills") or []
        assert len(fills) >= 1, f"{sym}: expected >=1 fill, got {len(fills)}"

    # Manifest fingerprint reproducibility
    from tradinglab.backtest.session import ENGINE_VERSION
    from tradinglab.strategy_tester import make_run_id
    expected_run_id = make_run_id(cfg, engine_version=ENGINE_VERSION)
    assert result.test_run.run_id == expected_run_id

    # Hard runtime budget (synthetic data; matrix CI should easily pass).
    assert elapsed < 10.0, f"check_st0_kernel_only took {elapsed:.2f}s (>10s)"


def check_st1_screenshots_written(tmp_cache_root: Path) -> None:
    """Same flow as st0, but with screenshot_spec=ScreenshotSpec() → PNGs on disk."""
    from tradinglab.strategy_tester import (
        AcceptanceToken,
        RunStatus,
        ScreenshotSpec,
    )
    from tradinglab.strategy_tester import (
        run as run_strategy_test,
    )

    entry, exit_strat, cfg = _make_test_config()
    entries_by_id = {entry.id: entry}
    exits_by_id = {exit_strat.id: exit_strat}

    # Reasonably small for fast smoke runtime — still validates the full
    # pipeline (slicing, annotation placement, PNG write).
    spec = ScreenshotSpec(width_in=8.0, height_in=4.5, dpi=72)

    t0 = time.monotonic()
    result = run_strategy_test(
        cfg,
        cancel_token=AcceptanceToken(),
        candles_fetcher=_fake_fetcher,
        entry_loader=lambda sid: entries_by_id[sid],
        exit_loader=lambda sid: exits_by_id[sid],
        max_workers=2,
        screenshot_spec=spec,
    )
    elapsed = time.monotonic() - t0

    assert result.test_run.status is RunStatus.DONE, (
        f"expected DONE status, got {result.test_run.status} "
        f"with error={result.test_run.error!r}"
    )

    run_dir = result.run_dir
    screenshots_dir = run_dir / "screenshots"
    png_files = sorted(screenshots_dir.glob("*.png"))
    assert png_files, (
        "expected at least one PNG when screenshot_spec is passed; "
        f"screenshots/ is empty under {screenshots_dir}"
    )
    # Each PNG is a real (non-empty) file.
    for f in png_files:
        assert f.stat().st_size > 1024, f"PNG {f.name} looks empty"
    # screenshot_count totals across symbols matches PNG file count.
    total_shots = sum(o.screenshot_count for o in result.outcomes)
    assert total_shots == len(png_files), (
        f"outcome screenshot_count={total_shots} but found {len(png_files)} PNGs"
    )
    # Filenames follow the <SYM>_<order_id>_post.png convention.
    for f in png_files:
        assert f.name.endswith("_post.png"), f"unexpected filename: {f.name}"

    # Generous runtime: 3 symbols × ~5-10 trades each × ~50ms/PNG ≈ a few seconds.
    assert elapsed < 30.0, f"check_st1_screenshots_written took {elapsed:.2f}s (>30s)"


def check_st2_aggregate_and_csv(tmp_cache_root: Path) -> None:
    """Verify the runner auto-writes aggregate.json + trades.csv on DONE."""
    from tradinglab.strategy_tester import (
        AcceptanceToken,
        RunStatus,
        load_aggregate,
    )
    from tradinglab.strategy_tester import (
        run as run_strategy_test,
    )

    entry, exit_strat, cfg = _make_test_config()
    entries_by_id = {entry.id: entry}
    exits_by_id = {exit_strat.id: exit_strat}

    result = run_strategy_test(
        cfg,
        cancel_token=AcceptanceToken(),
        candles_fetcher=_fake_fetcher,
        entry_loader=lambda sid: entries_by_id[sid],
        exit_loader=lambda sid: exits_by_id[sid],
        max_workers=2,
    )
    assert result.test_run.status is RunStatus.DONE

    run_dir = result.run_dir
    agg_path = run_dir / "aggregate.json"
    csv_path = run_dir / "trades.csv"
    assert agg_path.exists(), f"aggregate.json missing under {run_dir}"
    assert csv_path.exists(), f"trades.csv missing under {run_dir}"
    assert agg_path.stat().st_size > 50, "aggregate.json looks empty"
    assert csv_path.stat().st_size > 50, "trades.csv looks empty"

    # Aggregate round-trips back through load_aggregate cleanly.
    agg = load_aggregate(run_dir)
    assert agg is not None, "load_aggregate should produce a RunAggregate"
    assert agg.run_id == result.test_run.run_id
    # 3 symbols × ≥1 trade each = ≥3 trades total
    assert agg.trade_count >= 3, (
        f"expected at least 3 trades across 3 symbols, got {agg.trade_count}"
    )
    # The equity curve is populated and monotonic in ts.
    assert agg.equity_curve, "equity_curve should be non-empty"
    ts_seq = [p[0] for p in agg.equity_curve]
    assert ts_seq == sorted(ts_seq), "equity_curve should be sorted by ts"

    # CSV has the canonical column layout (header + at least one data row).
    # Column count is asserted dynamically against CSV_COLUMNS so the
    # smoke test doesn't have to be edited every time the schema evolves.
    from tradinglab.backtest.performance import CSV_COLUMNS
    csv_lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert len(csv_lines) >= 2, "trades.csv should have header + data rows"
    header_cols = csv_lines[0].split(",")
    assert len(header_cols) == len(CSV_COLUMNS), (
        f"expected {len(CSV_COLUMNS)}-column trades.csv header "
        f"(matches CSV_COLUMNS); got {len(header_cols)}"
    )
    assert tuple(header_cols) == CSV_COLUMNS, (
        f"trades.csv header drifted from CSV_COLUMNS: got {header_cols}"
    )


def check_st3_strategy_tab_end_to_end(tmp_cache_root: Path) -> None:
    """Smoke-check the GUI StrategyTab through a full Configure → Run → Render flow.

    We bypass the live notebook (no ChartApp boot) and exercise the
    widget standalone in a hidden Tk root. The widget's worker thread
    invokes the (stubbed) strategy_tester.run kernel and the tab's
    poll loop renders the resulting aggregate.

    Validates:
    - Tab widget constructs without error in a fresh Tk root.
    - refresh() reads entries/exits/watchlist libraries (injected
      fake storages keep the test offline from disk).
    - _build_config_from_ui returns a TestConfig.
    - The full Run flow completes with status DONE and the per-symbol
      Treeview gets populated.
    """
    import sys
    import tkinter as tk

    if sys.platform == "darwin":
        # ttk widget operations on a hidden root can deadlock on
        # headless macos-15-arm64 CI runners (same Tk transient()
        # quirk that affects modal dialogs). Skip on darwin.
        print("[SKIP] check_st3 — Tk widget hang risk on headless macos-15-arm64")
        return

    from tradinglab.gui.strategy_tab import StrategyTab
    from tradinglab.strategy_tester import RunStatus

    entry, exit_strat, _cfg = _make_test_config()

    # Build fake storage modules so the tab's refresh() can find the
    # test strategies without touching disk. The synthetic ticker
    # names ("STRAT_A" etc.) wouldn't pass the entry-storage symbol
    # validator, so we bypass storage entirely.
    class _FakeEntriesStorage:
        @staticmethod
        def load_all():
            return ([entry], [])

    class _FakeExitsStorage:
        @staticmethod
        def load_all():
            return ([exit_strat], [])

    class _FakeWatchlistsStorage:
        @staticmethod
        def load_all():
            return ([], [])

    # Build a hidden Tk root + StrategyTab against the fake libraries.
    # Stub candles_fetcher to deterministic synthetic bars.
    root = tk.Tk()
    root.withdraw()
    try:
        tab = StrategyTab(
            root,
            entries_storage=_FakeEntriesStorage(),
            exits_storage=_FakeExitsStorage(),
            watchlists_storage=_FakeWatchlistsStorage(),
            candles_fetcher=_fake_fetcher,
        )
        tab.pack(fill="both", expand=True)
        # Drive Tk so widget creates internals.
        for _ in range(5):
            root.update()

        # Seed the UI to use the 3 synthetic tickers via SYMBOLS mode.
        tab._var_universe_kind.set("symbols")
        tab._var_universe_symbols.set("STRAT_A, STRAT_B, STRAT_C")
        tab._on_universe_kind_change()
        tab._var_date_preset.set("custom")
        tab._on_date_preset_change()
        tab._var_start_date.set("2020-01-01")
        tab._var_end_date.set("2030-01-01")
        tab._var_interval.set("5m")
        tab._var_screenshots.set(False)
        # Synthetic candles aren't RTH-aligned in ET — opt into
        # extended-hours so the RTH filter doesn't drop them all.
        tab._var_include_extended_hours.set(True)

        # Pickers should auto-populate to the first available entry/exit
        # since refresh() ran in __init__. Validate the selection now.
        assert tab._selected_entry() is not None, (
            f"selected_entry returned None; "
            f"entry_combo={tab._var_entry_id.get()!r}, "
            f"library names: {[e.name for e in tab._entries]}"
        )
        assert tab._selected_exit() is not None

        cfg = tab._build_config_from_ui()
        assert cfg is not None, "TestConfig should build from a valid UI"
        assert cfg.entry_strategy_id == entry.id
        assert cfg.exit_strategy_id == exit_strat.id
        assert cfg.universe.symbols == ("STRAT_A", "STRAT_B", "STRAT_C")

        # Trigger the Run. The worker thread runs the real kernel; the
        # candles_fetcher is the deterministic stub, so the whole flow
        # is offline + synthetic.
        tab._on_run_clicked()

        # Drive Tk until the worker finishes + poll callback renders.
        # Hard cap at ~30 seconds (typical: <3s).
        import time as _time
        deadline = _time.monotonic() + 30.0
        while _time.monotonic() < deadline:
            root.update()
            if tab._worker is None and tab._current_aggregate is not None:
                break
            _time.sleep(0.05)
        else:
            raise AssertionError(
                "StrategyTab Run did not complete within 30 seconds"
            )

        # Validate the final state.
        assert tab._current_aggregate is not None
        assert tab._current_aggregate.trade_count >= 3, (
            f"expected >=3 trades, got {tab._current_aggregate.trade_count}"
        )
        # Per-symbol Treeview should have 3 rows.
        sym_rows = tab._tree_symbol.get_children()
        assert len(sym_rows) == 3, (
            f"expected 3 per-symbol rows, got {len(sym_rows)}"
        )

        # The worker_result must carry a successful Run.
        worker_status = tab._worker_result.get("result")
        assert worker_status is not None
        assert worker_status.test_run.status is RunStatus.DONE

    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
        # Drain Tk Variable.__del__ on the main thread *now*, while we
        # still own it. Without this, leftover IntVar / StringVar / etc.
        # objects can be collected from a worker thread in the next
        # check (check_st4 spawns ThreadPoolExecutor workers), and
        # Tkinter's "main thread is not in main loop" RuntimeError
        # escalates to Tcl_AsyncDelete → SIGABRT on Linux CPython 3.11.
        gc.collect()


def check_st4_export_html_pdf(tmp_cache_root: Path) -> None:
    """Validate the export module + StrategyTab Export buttons end-to-end.

    Runs the kernel once to produce a real run_dir with aggregate.json,
    then exercises export_html + export_pdf directly. Validates the
    files are written, non-empty, and (for PDF) carry the PDF magic
    bytes header.
    """
    from tradinglab.strategy_tester import (
        AcceptanceToken,
        RunStatus,
        export_html,
        export_pdf,
    )
    from tradinglab.strategy_tester import (
        run as run_strategy_test,
    )

    entry, exit_strat, cfg = _make_test_config()
    entries_by_id = {entry.id: entry}
    exits_by_id = {exit_strat.id: exit_strat}

    result = run_strategy_test(
        cfg,
        cancel_token=AcceptanceToken(),
        candles_fetcher=_fake_fetcher,
        entry_loader=lambda sid: entries_by_id[sid],
        exit_loader=lambda sid: exits_by_id[sid],
        max_workers=2,
    )
    assert result.test_run.status is RunStatus.DONE
    run_dir = result.run_dir

    # HTML export — written into run_dir by default; relative
    # screenshot links work without modification.
    html_path = export_html(run_dir)
    assert html_path == run_dir / "report.html"
    assert html_path.exists()
    html_body = html_path.read_text(encoding="utf-8")
    assert "Strategy Tester Run" in html_body
    assert result.test_run.run_id in html_body
    # 3 synthetic tickers should appear in the per-symbol table.
    for sym in ("STRAT_A", "STRAT_B", "STRAT_C"):
        assert sym in html_body, (
            f"per-symbol table should include {sym} in {html_path}"
        )

    # PDF export — file exists, has PDF magic bytes, non-empty.
    pdf_path = export_pdf(run_dir)
    assert pdf_path == run_dir / "report.pdf"
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 2000, (
        f"PDF should be non-trivial ({pdf_path.stat().st_size} bytes)"
    )
    with pdf_path.open("rb") as f:
        head = f.read(8)
    assert head.startswith(b"%PDF-"), (
        f"PDF file missing %PDF- magic bytes (got {head!r})"
    )


def check_st5_recent_runs_sidebar(tmp_cache_root: Path) -> None:
    """Validate StrategyTab's Recent Runs sidebar populates + loads prior runs.

    Bypasses the GUI when possible — directly invokes
    storage.list_runs_with_paths() to confirm the helper works, then
    spins up the tab in a hidden Tk root and checks the Treeview gets
    populated after refresh.
    """
    import sys

    from tradinglab.strategy_tester import (
        AcceptanceToken,
        RunStatus,
        list_runs_with_paths,
    )
    from tradinglab.strategy_tester import (
        run as run_strategy_test,
    )

    entry, exit_strat, cfg = _make_test_config()
    entries_by_id = {entry.id: entry}
    exits_by_id = {exit_strat.id: exit_strat}

    result = run_strategy_test(
        cfg,
        cancel_token=AcceptanceToken(),
        candles_fetcher=_fake_fetcher,
        entry_loader=lambda sid: entries_by_id[sid],
        exit_loader=lambda sid: exits_by_id[sid],
        max_workers=2,
    )
    assert result.test_run.status is RunStatus.DONE

    # The helper itself sees this run.
    pairs = list_runs_with_paths()
    assert pairs, "list_runs_with_paths should report at least one run"
    paths = [p for p, _r in pairs]
    assert result.run_dir in paths

    if sys.platform == "darwin":
        print("[SKIP] check_st5 GUI portion — Tk hang risk on headless darwin")
        return

    # GUI smoke: tab built standalone, sidebar populated.
    import tkinter as tk

    from tradinglab.gui.strategy_tab import StrategyTab

    class _FakeEntriesStorage:
        @staticmethod
        def load_all():
            return ([entry], [])

    class _FakeExitsStorage:
        @staticmethod
        def load_all():
            return ([exit_strat], [])

    class _FakeWatchlistsStorage:
        @staticmethod
        def load_all():
            return ([], [])

    root = tk.Tk()
    root.withdraw()
    try:
        tab = StrategyTab(
            root,
            entries_storage=_FakeEntriesStorage(),
            exits_storage=_FakeExitsStorage(),
            watchlists_storage=_FakeWatchlistsStorage(),
        )
        tab.pack(fill="both", expand=True)
        for _ in range(5):
            root.update()

        sidebar_rows = tab._tree_recent.get_children()
        assert sidebar_rows, (
            "Recent Runs sidebar Treeview should contain at least one "
            "row after refresh_recent_runs"
        )

        # Pick the first row and exercise the load path — should populate
        # the report pane without raising.
        tab._tree_recent.selection_set(sidebar_rows[0])
        tab._on_recent_run_select()
        tab._on_load_recent_run()
        for _ in range(5):
            root.update()
        assert tab._current_aggregate is not None
        assert tab._current_aggregate.run_id == result.test_run.run_id
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
        # See check_st3 for why this is needed (Tk Variable.__del__ +
        # threading + Python 3.11).
        gc.collect()


def check_st6_universe_kind_layout(app) -> None:
    """Switching universe kind (Symbols/Watchlist/Preset) toggles exactly
    one body widget without overlapping the screenshot separator below.

    Regression test for the bug where ``_on_universe_kind_change``
    grid()'d the Watchlist + Preset sub-frames at hardcoded outer-grid
    rows 14/15, overlaying the "Generate per-trade screenshots"
    ``ttk.Separator`` and clipping it horizontally.

    The post-fix layout uses a ``_frame_universe_body`` sub-frame that
    owns a single outer-grid row, and the four sub-widgets are toggled
    with ``pack`` / ``pack_forget`` inside it.

    Uses the session ``app`` fixture instead of a fresh ``tk.Tk()`` —
    on Windows CPython 3.12, creating ≥4 Tk roots in one process can
    trip "Can't find a usable init.tcl" because the Tcl library state
    gets corrupted across destroy cycles.
    """
    import sys
    import tkinter as tk

    if sys.platform == "darwin":
        print("[SKIP] check_st6 — Tk widget hang risk on headless macos-15-arm64")
        return

    from tradinglab.gui.strategy_tab import StrategyTab

    entry, exit_strat, _cfg = _make_test_config()

    class _FakeEntriesStorage:
        @staticmethod
        def load_all():
            return ([entry], [])

    class _FakeExitsStorage:
        @staticmethod
        def load_all():
            return ([exit_strat], [])

    class _FakeWatchlistsStorage:
        @staticmethod
        def load_all():
            return ([], [])

    top = tk.Toplevel(app)
    top.withdraw()
    try:
        tab = StrategyTab(
            top,
            entries_storage=_FakeEntriesStorage(),
            exits_storage=_FakeExitsStorage(),
            watchlists_storage=_FakeWatchlistsStorage(),
            candles_fetcher=_fake_fetcher,
        )
        tab.pack(fill="both", expand=True)
        for _ in range(5):
            app.update()

        # The universe body must own a single outer-grid row.
        body_grid_info = tab._frame_universe_body.grid_info()
        assert body_grid_info, (
            "_frame_universe_body must be grid'd into the outer Configure "
            "layout (single owning row)"
        )

        # SYMBOLS: only _frame_symbols should be packed.
        tab._var_universe_kind.set("symbols")
        tab._on_universe_kind_change()
        for _ in range(3):
            app.update()
        assert tab._frame_symbols.winfo_manager() == "pack"
        assert tab._frame_watchlist.winfo_manager() == ""
        assert tab._frame_preset.winfo_manager() == ""
        assert tab._banner_survivorship.winfo_manager() == ""

        # WATCHLIST: only _frame_watchlist should be packed.
        tab._var_universe_kind.set("watchlist")
        tab._on_universe_kind_change()
        for _ in range(3):
            app.update()
        assert tab._frame_symbols.winfo_manager() == ""
        assert tab._frame_watchlist.winfo_manager() == "pack"
        assert tab._frame_preset.winfo_manager() == ""
        assert tab._banner_survivorship.winfo_manager() == ""

        # PRESET: _frame_preset + _banner_survivorship packed.
        tab._var_universe_kind.set("preset")
        tab._on_universe_kind_change()
        for _ in range(3):
            app.update()
        assert tab._frame_symbols.winfo_manager() == ""
        assert tab._frame_watchlist.winfo_manager() == ""
        assert tab._frame_preset.winfo_manager() == "pack"
        assert tab._banner_survivorship.winfo_manager() == "pack"

        # Back to SYMBOLS — confirms toggle path is round-trip safe.
        tab._var_universe_kind.set("symbols")
        tab._on_universe_kind_change()
        for _ in range(3):
            app.update()
        assert tab._frame_symbols.winfo_manager() == "pack"
        assert tab._frame_watchlist.winfo_manager() == ""
        assert tab._frame_preset.winfo_manager() == ""
    finally:
        try:
            top.destroy()
        except Exception:  # noqa: BLE001
            pass
        gc.collect()


def check_st7_failed_status_surfaces_error(app) -> None:
    """``_on_poll`` surfaces ``test_run.error`` when the runner returns
    a FAILED status, instead of complaining about a missing aggregate.

    Regression test for the bug where a single-symbol failure (e.g.
    ``UnsupportedTriggerKind``, empty candles) sets ``status=FAILED``
    in the runner — which intentionally skips the ``aggregate.json``
    write (runner.py:507) — and the GUI then blindly called
    ``load_aggregate`` and reported "Run finished but aggregate.json
    is missing" with no further diagnostics.

    Uses the session ``app`` fixture instead of a fresh ``tk.Tk()`` —
    same rationale as check_st6 (Windows CPython 3.12 chokes on the
    4th+ Tk root creation in a process).
    """
    import sys
    import tkinter as tk
    from dataclasses import dataclass

    if sys.platform == "darwin":
        print("[SKIP] check_st7 — Tk widget hang risk on headless macos-15-arm64")
        return

    from tradinglab.gui import strategy_tab as _strategy_tab_mod
    from tradinglab.gui.strategy_tab import StrategyTab
    from tradinglab.strategy_tester import RunStatus

    entry, exit_strat, _cfg = _make_test_config()

    class _FakeEntriesStorage:
        @staticmethod
        def load_all():
            return ([entry], [])

    class _FakeExitsStorage:
        @staticmethod
        def load_all():
            return ([exit_strat], [])

    class _FakeWatchlistsStorage:
        @staticmethod
        def load_all():
            return ([], [])

    top = tk.Toplevel(app)
    top.withdraw()

    # Capture messagebox.showerror calls without popping a real dialog.
    showerror_calls: list[tuple[str, str]] = []

    def _fake_showerror(title, message, **_kwargs):
        showerror_calls.append((title, message))
        return "ok"

    # Track load_aggregate calls — the FAILED branch must short-circuit
    # BEFORE invoking it.
    load_aggregate_calls: list[Path] = []

    def _fake_load_aggregate(run_dir):
        load_aggregate_calls.append(run_dir)
        return None

    original_showerror = _strategy_tab_mod.messagebox.showerror
    original_load_aggregate = _strategy_tab_mod.load_aggregate
    _strategy_tab_mod.messagebox.showerror = _fake_showerror  # type: ignore[assignment]
    _strategy_tab_mod.load_aggregate = _fake_load_aggregate  # type: ignore[assignment]
    try:
        tab = StrategyTab(
            top,
            entries_storage=_FakeEntriesStorage(),
            exits_storage=_FakeExitsStorage(),
            watchlists_storage=_FakeWatchlistsStorage(),
            candles_fetcher=_fake_fetcher,
        )
        tab.pack(fill="both", expand=True)
        for _ in range(5):
            app.update()

        # Build a fake FAILED RunResult by hand. We don't need the
        # runner to actually fail; we're testing the _on_poll branch.
        @dataclass
        class _FakeTestRun:
            run_id: str = "00000000abcd"
            status: RunStatus = RunStatus.FAILED
            error: str = "UnsupportedTriggerKind: foo"
            symbol_count_done: int = 0
            symbol_count_total: int = 1

        @dataclass
        class _FakeRunResult:
            run_dir: Path
            test_run: _FakeTestRun

        import tempfile as _tempfile
        run_dir = Path(_tempfile.mkdtemp(prefix="st7_fakerun_"))
        tab._worker_result["result"] = _FakeRunResult(
            run_dir=run_dir, test_run=_FakeTestRun(),
        )
        tab._worker_result["error"] = None
        # _on_poll early-returns if _worker is None, so install a real
        # thread that's already finished — that's the "worker just
        # completed" state the function expects.
        import threading as _threading
        finished_thread = _threading.Thread(target=lambda: None)
        finished_thread.start()
        finished_thread.join()
        tab._worker = finished_thread

        tab._on_poll()
        for _ in range(3):
            app.update()

        assert showerror_calls, (
            "_on_poll should call messagebox.showerror on FAILED status"
        )
        title, msg = showerror_calls[0]
        assert "UnsupportedTriggerKind" in msg, (
            f"showerror message must include the captured error; got {msg!r}"
        )
        assert run_dir.name in msg, (
            f"showerror message must include the run_dir path; got {msg!r}"
        )
        # load_aggregate must NOT be called on FAILED status.
        assert not load_aggregate_calls, (
            "FAILED status must short-circuit before load_aggregate; "
            f"got {len(load_aggregate_calls)} unexpected call(s)"
        )
        # Status bar surfaces the error too.
        assert "UnsupportedTriggerKind" in tab._var_status.get()
    finally:
        _strategy_tab_mod.messagebox.showerror = original_showerror  # type: ignore[assignment]
        _strategy_tab_mod.load_aggregate = original_load_aggregate  # type: ignore[assignment]
        try:
            top.destroy()
        except Exception:  # noqa: BLE001
            pass
        gc.collect()




def check_st8_ema_cross_gui_e2e_screenshots(app) -> None:
    """END-TO-END: EMA(3)/EMA(8) cross on a synthetic SPY 5m chart, driven
    through the **real StrategyTab Run button**, producing per-trade PNG
    screenshots.

    User request: exercise the entire flow of a simple strategy (EMA 3/8
    cross on the SPY 5-minute chart) via the actual Run button and assert:

    * >= 1 trade (we engineer >= 2 so the uniqueness check below is
      meaningful),
    * >= 1 screenshot,
    * each screenshot filename encodes the trade's entry timestamp
      (``SPY_t<epoch>_post.png``) which decodes to the correct ET
      trading date,
    * each rendered PNG's title shows the correct ET date for that trade
      (validated via the exact ``_draw_title_and_labels`` path the
      renderer uses — no brittle OCR), and
    * each screenshot's raw-byte content hash (sha256 of the file) is
      pairwise-unique.

    Offline + deterministic: smoke tests never hit the network, so "SPY"
    is a synthetic RTH-aligned ET 5-minute series engineered to make
    EMA(3) cross above / below EMA(8) several times (lead-in downtrend
    seats EMA3<EMA8, then sawtooth cycles), yielding multiple round-trip
    trades -> multiple distinct screenshots.
    """
    import hashlib
    import re
    import sys
    import time as _time
    import tkinter as tk
    from datetime import date as _date
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from zoneinfo import ZoneInfo

    if sys.platform == "darwin":
        # ttk widget operations on a hidden root can deadlock on headless
        # macos-15-arm64 CI runners (same Tk transient() quirk as the modal
        # dialogs). The runner/screenshot pipeline itself is Tk-free and is
        # unit-tested on every platform; skip only the GUI-driven wrapper.
        print("[SKIP] check_st8 — Tk widget hang risk on headless macos-15-arm64")
        return

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    import tradinglab.indicators  # noqa: F401 — register the EMA factory
    from tradinglab.backtest.performance import build_trade_rows
    from tradinglab.backtest.session import SessionResult
    from tradinglab.entries.model import (
        Direction,
        EntryStrategy,
        EntryTrigger,
        ShareRounding,
        SizingKind,
        SizingRule,
    )
    from tradinglab.entries.model import TriggerKind as EntryTriggerKind
    from tradinglab.entries.model import Universe as EntryUniverse
    from tradinglab.exits.model import ExitLeg, ExitStrategy, ExitTrigger
    from tradinglab.exits.model import TriggerKind as ExitTriggerKind
    from tradinglab.gui import strategy_tab as _strategy_tab_mod
    from tradinglab.gui.strategy_tab import StrategyTab
    from tradinglab.models import Candle
    from tradinglab.scanner.model import (
        OP_CROSSES_ABOVE,
        OP_CROSSES_BELOW,
        Condition,
        FieldRef,
        Group,
    )
    from tradinglab.strategy_tester import RunStatus
    from tradinglab.strategy_tester.screenshot import (
        _draw_title_and_labels,
        _format_et_timestamp_from_ms,
        build_candle_timestamp_index,
    )

    _ET = ZoneInfo("America/New_York")
    _TRADING_DAY = _date(2026, 1, 5)  # a Monday, RTH

    def _ema(length: int) -> FieldRef:
        return FieldRef(kind="indicator", id="ema", params={"length": length})

    def _cross(op: str) -> Group:
        # EMA(3) crosses_above / crosses_below EMA(8), lookback=1 — the
        # canonical 3/8 EMA cross condition (mirrors the on-disk
        # tmpl-ema-3-8-cross-long.json + the evaluator regression tests).
        return Group(combinator="and", children=[Condition(
            left=_ema(3), op=op,
            params={"right": _ema(8), "lookback": 1},
            interval="5m",
        )])

    entry = EntryStrategy(
        id="entry-ema38-smoke", name="3/8 EMA cross (long)",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("SPY",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.INDICATOR,
                             condition=_cross(OP_CROSSES_ABOVE), interval="5m"),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=10.0,
                          share_rounding=ShareRounding.DOWN),
        # Default cap is 1/day (the historical 1-trade-per-symbol landmine);
        # raise it so the strategy re-enters on every up-cross of the day.
        max_fires_per_session_per_symbol=10,
    )
    exit_strat = ExitStrategy(
        id="exit-ema38-smoke", name="3/8 EMA cross (exit)",
        legs=[ExitLeg(id="leg", triggers=[ExitTrigger(
            kind=ExitTriggerKind.INDICATOR, condition=_cross(OP_CROSSES_BELOW),
            interval="5m", qty_pct=100.0,
        )])],
        eod_kill_switch=True,  # flatten any residual position at EOD
    )

    def _spy_candles() -> list:
        # Lead-in downtrend (seats EMA3 below EMA8), then sawtooth cycles.
        # Each up-leg makes EMA(3) cross above EMA(8) (entry); each down-leg
        # crosses below (exit). All bars are RTH-aligned ET on one trading
        # day, so include_extended_hours=False keeps them all.
        closes = [100.0 - i for i in range(10)]  # 100 .. 91
        price = closes[-1]
        for _ in range(5):
            for _ in range(6):
                price += 4.0
                closes.append(price)
            for _ in range(6):
                price -= 4.0
                closes.append(price)
        out = []
        t = _dt(2026, 1, 5, 9, 30, tzinfo=_ET)
        prev = closes[0]
        for i, cl in enumerate(closes):
            op = prev
            hi = max(op, cl) + 0.5
            lo = min(op, cl) - 0.5
            out.append(Candle(date=t, open=op, high=hi, low=lo, close=cl,
                              volume=1000 + i, session="regular"))
            prev = cl
            t = t + _td(minutes=5)
        return out

    spy_candles = _spy_candles()

    def _fetch(_symbol: str, _interval: str) -> list:
        return list(spy_candles)

    class _FakeEntries:
        @staticmethod
        def load_all():
            return ([entry], [])

    class _FakeExits:
        @staticmethod
        def load_all():
            return ([exit_strat], [])

    class _FakeWatchlists:
        @staticmethod
        def load_all():
            return ([], [])

    # Capture any messagebox so a surprise dialog can't hang the headless
    # runner (EMA/5m is interval-compatible, so none is expected).
    orig_info = _strategy_tab_mod.messagebox.showinfo
    orig_err = _strategy_tab_mod.messagebox.showerror
    _strategy_tab_mod.messagebox.showinfo = lambda *a, **k: "ok"  # type: ignore[assignment]
    _strategy_tab_mod.messagebox.showerror = lambda *a, **k: "ok"  # type: ignore[assignment]

    top = tk.Toplevel(app)
    top.withdraw()
    try:
        tab = StrategyTab(
            top,
            entries_storage=_FakeEntries(),
            exits_storage=_FakeExits(),
            watchlists_storage=_FakeWatchlists(),
            candles_fetcher=_fetch,
        )
        tab.pack(fill="both", expand=True)
        for _ in range(5):
            app.update()

        tab._var_universe_kind.set("symbols")
        tab._var_universe_symbols.set("SPY")
        tab._on_universe_kind_change()
        tab._var_date_preset.set("custom")
        tab._on_date_preset_change()
        tab._var_start_date.set("2026-01-01")
        tab._var_end_date.set("2026-01-31")
        tab._var_interval.set("5m")
        tab._var_screenshots.set(True)               # render per-trade PNGs
        tab._var_include_extended_hours.set(False)   # candles are RTH-aligned ET

        assert tab._selected_entry() is not None, "EMA-cross entry must auto-select"
        assert tab._selected_exit() is not None, "EMA-cross exit must auto-select"

        # Click the REAL Run button (command=_on_run_clicked).
        tab._btn_run.invoke()

        deadline = _time.monotonic() + 60.0
        while _time.monotonic() < deadline:
            app.update()
            if tab._worker is None and tab._current_aggregate is not None:
                break
            _time.sleep(0.05)
        else:
            raise AssertionError(
                "StrategyTab EMA-cross Run did not complete within 60 seconds"
            )

        result = tab._worker_result.get("result")
        assert result is not None, f"worker error: {tab._worker_result.get('error')!r}"
        assert result.test_run.status is RunStatus.DONE, (
            f"expected DONE, got {result.test_run.status} "
            f"(error={result.test_run.error!r})"
        )
        agg = tab._current_aggregate
        assert agg is not None, "aggregate should have rendered after the Run"
        assert agg.trade_count >= 2, (
            f"expected >=2 trades from the SPY 3/8 EMA cross, got {agg.trade_count}"
        )

        run_dir = tab._current_run_dir
        assert run_dir is not None, "Run should have set _current_run_dir"
        shots_dir = run_dir / "screenshots"
        pngs = sorted(shots_dir.glob("*.png"))
        assert len(pngs) >= 2, (
            f"expected >=2 per-trade screenshots, got {len(pngs)} in {shots_dir}"
        )

        # Load the SPY trades so we can cross-check filenames + titles.
        sr = SessionResult.from_dict(json.loads(
            (run_dir / "per_symbol" / "SPY.json").read_text(encoding="utf-8")
        ))
        rows = build_trade_rows(sr)
        assert len(rows) >= 2, f"expected >=2 SPY trade rows, got {len(rows)}"
        trade_entry_ts = {int(r.post.entry_ts) for r in rows}

        # (a) Filename encodes the entry timestamp, decoding to the right date.
        name_re = re.compile(r"^SPY_t(\d+)_post\.png$")
        for p in pngs:
            assert p.stat().st_size > 1024, f"{p.name} looks empty ({p.stat().st_size} B)"
            m = name_re.match(p.name)
            assert m, f"filename must be SPY_t<epoch>_post.png; got {p.name!r}"
            ts = int(m.group(1))
            assert ts in trade_entry_ts, (
                f"{p.name}: entry_ts {ts} not among SPY trade entries {trade_entry_ts}"
            )
            got_date = _dt.fromtimestamp(ts, _ET).date()
            assert got_date == _TRADING_DAY, (
                f"{p.name}: filename entry date {got_date} != trading day {_TRADING_DAY}"
            )

        # (b) Rendered PNG title shows the correct ET date per trade. Re-derive
        #     via the EXACT helper the renderer uses (render_trade_screenshot ->
        #     _draw_title_and_labels) so this is faithful, not brittle OCR.
        index = build_candle_timestamp_index(spy_candles)
        date_str = _TRADING_DAY.strftime("%Y-%m-%d")
        for r in rows:
            fig = Figure(figsize=(6.0, 3.5), dpi=72)
            FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            _draw_title_and_labels(
                fig, ax, r, candles=spy_candles,
                entry_index=index.index_of(r.post.entry_ts),
                entry_strategy=entry,
            )
            title = ax.get_title(loc="left")
            assert "SPY" in title, f"title missing symbol: {title!r}"
            assert date_str in title, (
                f"title missing correct ET date {date_str!r}: {title!r}"
            )
            # Sanity: the timestamp-derived ET date matches the same day too.
            assert _format_et_timestamp_from_ms(int(r.post.entry_ts)).startswith(
                date_str
            ), f"entry_ts {r.post.entry_ts} did not map to {date_str}"

        # (c) Each screenshot's raw-byte content hash is pairwise-unique.
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in pngs]
        assert len(set(hashes)) == len(hashes), (
            f"screenshot byte-hashes are NOT unique: {len(pngs)} PNGs but "
            f"only {len(set(hashes))} distinct sha256 digests "
            f"(files={[p.name for p in pngs]})"
        )
    finally:
        _strategy_tab_mod.messagebox.showinfo = orig_info  # type: ignore[assignment]
        _strategy_tab_mod.messagebox.showerror = orig_err  # type: ignore[assignment]
        try:
            top.destroy()
        except Exception:  # noqa: BLE001
            pass
        # Drain Tk Variable.__del__ on the main thread now (a later check
        # spawning worker threads could otherwise GC leftover Vars off-thread
        # → Tcl_AsyncDelete → SIGABRT on Linux CPython 3.11).
        gc.collect()


@pytest.fixture
def tmp_cache_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    return tmp_path


def check_st9_ema_cross_real_data_e2e(app) -> None:
    """END-TO-END on REAL market data: EMA 3/8 cross across SPY / AMD / NVDA /
    INTC / MSFT / AAPL 5m, driven through the StrategyTab Run button, with
    per-trade screenshots.

    Uses the committed ``testdata`` fixture (5 RTH trading days of real
    yfinance 5m bars — see ``tests/_fixtures/market_data.py`` +
    ``tools/fetch_test_fixtures.py``) as the ``candles_fetcher``, so the flow
    exercises genuine market microstructure (real EMA crosses, gaps, RTH
    boundaries, volume) rather than only the engineered series in
    ``check_st8``. Offline + deterministic (sealed OHLCV bars are immutable).

    Asserts the Run completes DONE across all 6 symbols and produces many
    per-trade PNGs whose filenames encode each trade's real entry date (within
    the fixture's trading week) and whose raw-byte content hashes are unique.
    """
    import hashlib
    import re
    import sys
    import time as _time
    import tkinter as tk
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    if sys.platform == "darwin":
        print("[SKIP] check_st9 — Tk widget hang risk on headless macos-15-arm64")
        return

    from tests._fixtures import market_data as _md

    if not _md.available("SPY"):
        print("[SKIP] check_st9 — committed testdata fixtures not present")
        return

    import tradinglab.indicators  # noqa: F401 — register the EMA factory
    from tradinglab.backtest.session import SessionResult
    from tradinglab.entries.model import (
        Direction,
        EntryStrategy,
        EntryTrigger,
        ShareRounding,
        SizingKind,
        SizingRule,
    )
    from tradinglab.entries.model import TriggerKind as EntryTriggerKind
    from tradinglab.entries.model import Universe as EntryUniverse
    from tradinglab.exits.model import ExitLeg, ExitStrategy, ExitTrigger
    from tradinglab.exits.model import TriggerKind as ExitTriggerKind
    from tradinglab.gui import strategy_tab as _strategy_tab_mod
    from tradinglab.gui.strategy_tab import StrategyTab
    from tradinglab.scanner.model import (
        OP_CROSSES_ABOVE,
        OP_CROSSES_BELOW,
        Condition,
        FieldRef,
        Group,
    )
    from tradinglab.strategy_tester import RunStatus

    _ET = ZoneInfo("America/New_York")
    # The committed fixture carries 6 tickers; run a representative subset
    # through the (screenshot-rendering) GUI flow to keep CI lean while still
    # exercising multi-ticker real-data e2e. The full 6-ticker set is pinned
    # by tests/_fixtures/test_market_data.py.
    _RUN_TICKERS = ("SPY", "AMD", "NVDA")
    # Allowed entry dates = the fixture's captured trading days (from manifest).
    _man = _md.manifest()
    allowed_dates = {
        d for tk_meta in _man.get("tickers", {}).values() for d in tk_meta.get("days", [])
    }

    def _ema(length: int) -> FieldRef:
        return FieldRef(kind="indicator", id="ema", params={"length": length})

    def _cross(op: str) -> Group:
        return Group(combinator="and", children=[Condition(
            left=_ema(3), op=op,
            params={"right": _ema(8), "lookback": 1}, interval="5m",
        )])

    entry = EntryStrategy(
        id="entry-ema38-real", name="3/8 EMA cross (long)",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=_RUN_TICKERS),
        trigger=EntryTrigger(kind=EntryTriggerKind.INDICATOR,
                             condition=_cross(OP_CROSSES_ABOVE), interval="5m"),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=10.0,
                          share_rounding=ShareRounding.DOWN),
        max_fires_per_session_per_symbol=10,
    )
    exit_strat = ExitStrategy(
        id="exit-ema38-real", name="3/8 EMA cross (exit)",
        legs=[ExitLeg(id="leg", triggers=[ExitTrigger(
            kind=ExitTriggerKind.INDICATOR, condition=_cross(OP_CROSSES_BELOW),
            interval="5m", qty_pct=100.0,
        )])],
        eod_kill_switch=True,
    )

    class _FakeEntries:
        @staticmethod
        def load_all():
            return ([entry], [])

    class _FakeExits:
        @staticmethod
        def load_all():
            return ([exit_strat], [])

    class _FakeWatchlists:
        @staticmethod
        def load_all():
            return ([], [])

    orig_info = _strategy_tab_mod.messagebox.showinfo
    orig_err = _strategy_tab_mod.messagebox.showerror
    _strategy_tab_mod.messagebox.showinfo = lambda *a, **k: "ok"  # type: ignore[assignment]
    _strategy_tab_mod.messagebox.showerror = lambda *a, **k: "ok"  # type: ignore[assignment]

    top = tk.Toplevel(app)
    top.withdraw()
    try:
        tab = StrategyTab(
            top, entries_storage=_FakeEntries(), exits_storage=_FakeExits(),
            watchlists_storage=_FakeWatchlists(),
            candles_fetcher=_md.fetcher,  # the committed real-data test source
        )
        tab.pack(fill="both", expand=True)
        for _ in range(5):
            app.update()

        tab._var_universe_kind.set("symbols")
        tab._var_universe_symbols.set(", ".join(_RUN_TICKERS))
        tab._on_universe_kind_change()
        tab._var_date_preset.set("custom")
        tab._on_date_preset_change()
        tab._var_start_date.set("2026-07-01")
        tab._var_end_date.set("2026-07-31")
        tab._var_interval.set("5m")
        tab._var_screenshots.set(True)
        tab._var_include_extended_hours.set(False)  # fixtures are RTH-only

        assert tab._selected_entry() is not None
        assert tab._selected_exit() is not None

        tab._btn_run.invoke()

        deadline = _time.monotonic() + 120.0
        while _time.monotonic() < deadline:
            app.update()
            if tab._worker is None and tab._current_aggregate is not None:
                break
            _time.sleep(0.05)
        else:
            raise AssertionError("StrategyTab real-data Run did not complete in 120s")

        result = tab._worker_result.get("result")
        assert result is not None, f"worker error: {tab._worker_result.get('error')!r}"
        assert result.test_run.status is RunStatus.DONE, (
            f"expected DONE, got {result.test_run.status} "
            f"(error={result.test_run.error!r})"
        )
        assert result.test_run.symbol_count_done == len(_RUN_TICKERS)
        agg = tab._current_aggregate
        assert agg is not None
        # Real 5m EMA 3/8 cross across the subset over a week fires many times;
        # >=12 (avg 4/ticker) is a wide safety margin below the ~51 observed.
        assert agg.trade_count >= 12, (
            f"expected many trades from the real-data EMA cross, got {agg.trade_count}"
        )

        run_dir = tab._current_run_dir
        assert run_dir is not None
        pngs = sorted((run_dir / "screenshots").glob("*.png"))
        assert len(pngs) >= 12, f"expected >=12 per-trade screenshots, got {len(pngs)}"

        # Filenames encode each trade's entry timestamp; decode to a real
        # fixture trading day, and collect symbols to prove multi-ticker.
        name_re = re.compile(r"^(?P<sym>[A-Z]+)_t(?P<ts>\d+)_post\.png$")
        symbols_seen: set[str] = set()
        for p in pngs:
            assert p.stat().st_size > 1024, f"{p.name} looks empty"
            m = name_re.match(p.name)
            assert m, f"unexpected screenshot filename: {p.name!r}"
            symbols_seen.add(m.group("sym"))
            got_date = _dt.fromtimestamp(int(m.group("ts")), _ET).date().isoformat()
            assert got_date in allowed_dates, (
                f"{p.name}: entry date {got_date} not in fixture week {sorted(allowed_dates)}"
            )
        assert len(symbols_seen & set(_RUN_TICKERS)) >= 2, (
            f"expected screenshots across multiple tickers; saw {symbols_seen}"
        )

        # Cross-check the per-symbol trade entries are real (SPY loads + parses).
        spy = SessionResult.from_dict(json.loads(
            (run_dir / "per_symbol" / "SPY.json").read_text(encoding="utf-8")))
        assert spy.post_trades, "SPY should have closed at least one real trade"

        # Every screenshot's raw-byte content hash is unique (the §7.7
        # "every screenshot identical" regression, on real data at scale).
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in pngs]
        assert len(set(hashes)) == len(hashes), (
            f"screenshot byte-hashes not unique: {len(pngs)} PNGs, "
            f"{len(set(hashes))} distinct"
        )
    finally:
        _strategy_tab_mod.messagebox.showinfo = orig_info  # type: ignore[assignment]
        _strategy_tab_mod.messagebox.showerror = orig_err  # type: ignore[assignment]
        try:
            top.destroy()
        except Exception:  # noqa: BLE001
            pass
        gc.collect()


def test_strategy_kernel(tmp_cache_root: Path) -> None:
    check_st0_kernel_only(tmp_cache_root)


def test_strategy_screenshots(tmp_cache_root: Path) -> None:
    check_st1_screenshots_written(tmp_cache_root)


def test_strategy_aggregate_and_csv(tmp_cache_root: Path) -> None:
    check_st2_aggregate_and_csv(tmp_cache_root)


def test_strategy_tab_end_to_end(tmp_cache_root: Path) -> None:
    check_st3_strategy_tab_end_to_end(tmp_cache_root)


def test_strategy_export_html_pdf(tmp_cache_root: Path) -> None:
    check_st4_export_html_pdf(tmp_cache_root)


def test_strategy_recent_runs_sidebar(tmp_cache_root: Path) -> None:
    check_st5_recent_runs_sidebar(tmp_cache_root)


def test_strategy_universe_kind_layout(app) -> None:
    check_st6_universe_kind_layout(app)


def test_strategy_failed_status_surfaces_error(app) -> None:
    check_st7_failed_status_surfaces_error(app)


def test_strategy_ema_cross_e2e_screenshots(app) -> None:
    check_st8_ema_cross_gui_e2e_screenshots(app)


def test_strategy_ema_cross_real_data_e2e(app) -> None:
    check_st9_ema_cross_real_data_e2e(app)


def test_strategy_menu_present_in_chartapp(app) -> None:
    """The **Strategy Tester…** item lives in the consolidated **Strategies**
    cascade (audit ``strategies-menu-consolidation``), which sits between
    **Sandbox** and **View**; invoking it opens a Toplevel containing a
    :class:`StrategyTab`.
    """
    import sys
    import tkinter as tk

    menubar = app.nametowidget(app.cget("menu"))
    labels: list[str] = []
    for idx in range(menubar.index("end") + 1):
        try:
            labels.append(menubar.entrycget(idx, "label"))
        except tk.TclError:
            labels.append("")
    assert "Strategies" in labels, (
        f"Strategies menu missing from menubar (got {labels})"
    )
    # Strategies must sit between Sandbox and View.
    s_idx = labels.index("Strategies")
    assert "Sandbox" in labels and labels.index("Sandbox") < s_idx, (
        f"Strategies must come AFTER Sandbox; got order {labels}"
    )
    assert "View" in labels and s_idx < labels.index("View"), (
        f"Strategies must come BEFORE View; got order {labels}"
    )
    # The consolidated cascade exposes the Strategy Tester item.
    strat_menu = app.nametowidget(menubar.entrycget(s_idx, "menu"))
    sub_labels: list[str] = []
    for idx in range(strat_menu.index("end") + 1):
        try:
            sub_labels.append(strat_menu.entrycget(idx, "label"))
        except tk.TclError:
            sub_labels.append("")
    assert "Strategy Tester…" in sub_labels, (
        f"Strategies cascade missing 'Strategy Tester…'; got {sub_labels}"
    )

    # The Strategy popup is built lazily — until the user opens it,
    # both stash attributes stay None.
    assert getattr(app, "_strategy_dialog", "missing") is None
    assert getattr(app, "_strategy_tab", "missing") is None

    # Invoking the menu callback constructs the Toplevel + StrategyTab.
    # Skip the lift/transient + actual widget exercise on macOS so the
    # Tk modal-transient deadlock landmine (CLAUDE.md §7.1) doesn't
    # hang the headless runner.
    if sys.platform == "darwin":
        return
    try:
        app._on_open_strategy_dialog()
        assert app._strategy_dialog is not None
        assert app._strategy_tab is not None
        # Toplevel widget alive + StrategyTab is the embedded child.
        assert app._strategy_dialog.winfo_exists()
        assert app._strategy_tab.winfo_exists()
    finally:
        # Clean up so subsequent checks don't see a leftover popup.
        dlg = getattr(app, "_strategy_dialog", None)
        if dlg is not None:
            try:
                dlg.destroy()
            except Exception:  # noqa: BLE001
                pass
        app._strategy_dialog = None
        app._strategy_tab = None
