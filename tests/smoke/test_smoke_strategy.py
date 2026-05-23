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


@pytest.fixture
def tmp_cache_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    return tmp_path


def test_strategy_kernel(tmp_cache_root: Path) -> None:
    check_st0_kernel_only(tmp_cache_root)


def test_strategy_screenshots(tmp_cache_root: Path) -> None:
    check_st1_screenshots_written(tmp_cache_root)
