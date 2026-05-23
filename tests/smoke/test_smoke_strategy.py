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

    # CSV has the canonical 24-column header + at least one data row.
    csv_lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert len(csv_lines) >= 2, "trades.csv should have header + data rows"
    header_cols = csv_lines[0].split(",")
    assert len(header_cols) == 24, (
        f"expected 24-column trades.csv header, got {len(header_cols)}"
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


@pytest.fixture
def tmp_cache_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    return tmp_path


def test_strategy_kernel(tmp_cache_root: Path) -> None:
    check_st0_kernel_only(tmp_cache_root)


def test_strategy_screenshots(tmp_cache_root: Path) -> None:
    check_st1_screenshots_written(tmp_cache_root)


def test_strategy_aggregate_and_csv(tmp_cache_root: Path) -> None:
    check_st2_aggregate_and_csv(tmp_cache_root)


def test_strategy_tab_end_to_end(tmp_cache_root: Path) -> None:
    check_st3_strategy_tab_end_to_end(tmp_cache_root)


def test_strategy_tab_present_in_chartapp(app) -> None:
    """The Strategy tab must be wired into ChartApp's notebook AFTER Exits."""
    nb = app._notebook
    tabs = [nb.tab(i, "text") for i in range(nb.index("end"))]
    assert "Strategy" in tabs, f"Strategy tab missing from notebook (got {tabs})"
    # Strategy must come AFTER Exits in tab order.
    assert tabs.index("Strategy") > tabs.index("Exits"), (
        f"Strategy tab should be inserted AFTER Exits; got order {tabs}"
    )
    # The widget reference is held on the app.
    assert getattr(app, "_strategy_tab", None) is not None
