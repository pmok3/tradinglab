"""Unit tests for strategy_tester.storage."""

from __future__ import annotations

import json

from tradinglab.backtest.session import SessionResult, SessionSpec
from tradinglab.strategy_tester import (
    CostModel,
    DatePreset,
    RunStatus,
    TestConfig,
    TestRun,
    UniverseKind,
    UniverseSpec,
    storage,
)


def _config() -> TestConfig:
    return TestConfig(
        entry_strategy_id="e1",
        exit_strategy_id="x1",
        universe=UniverseSpec(kind=UniverseKind.SYMBOLS, symbols=("A",)),
        start_date="2020-01-01",
        end_date="2024-12-31",
        date_preset=DatePreset.CUSTOM,
    )


def test_run_dir_for_creates_subdirs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    d = storage.run_dir_for("abc12345", started_iso="20260101T000000Z")
    assert d.name == "abc12345-20260101T000000Z"
    assert (d / "per_symbol").is_dir()
    assert (d / "screenshots").is_dir()


def test_save_and_load_manifest_round_trip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    cfg = _config()
    run = TestRun(
        run_id="abc12345",
        config=cfg,
        status=RunStatus.DONE,
        symbol_count_total=1,
        symbol_count_done=1,
        trade_count=2,
        app_version="0.1.1",
        engine_version="sandbox-1d",
    )
    d = storage.run_dir_for("abc12345", started_iso="20260101T000000Z")
    storage.save_manifest(d, run)
    loaded = storage.load_manifest(d)
    assert loaded is not None
    assert loaded.run_id == "abc12345"
    assert loaded.status is RunStatus.DONE
    assert loaded.trade_count == 2


def test_save_config_writes_valid_json(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    cfg = _config()
    d = storage.run_dir_for("abc12345", started_iso="20260101T000000Z")
    storage.save_config(d, cfg)
    payload = json.loads((d / "config.json").read_text(encoding="utf-8"))
    assert payload["entry_strategy_id"] == "e1"


def test_save_and_load_session_result_for_symbol(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    spec = SessionSpec(
        deck_seed=0, tickers=("AAPL",), start_clock_iso="",
        slippage_bps=5.0, commission=0.0,
    )
    sr = SessionResult(spec=spec)
    d = storage.run_dir_for("abc12345", started_iso="20260101T000000Z")
    storage.save_session_result_for_symbol(d, "AAPL", sr)
    loaded = storage.load_session_result_for_symbol(d, "AAPL")
    assert loaded is not None
    assert loaded.spec.tickers == ("AAPL",)


def test_list_runs_returns_newest_first(monkeypatch, tmp_path) -> None:
    """Runs sort by the manifest ``started_at`` (the Recent Runs "Started"
    column), descending — NOT by the run_id-fingerprint-prefixed directory
    name. The run_ids here ascend while ``started_at`` descends, so a
    dir-name sort and a started_at sort give OPPOSITE results; the assert
    pins that ``started_at`` wins.
    """
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    cfg = _config()
    for run_id, started_at in (("aaa11111", "2026-03-01T00:00:00Z"),
                               ("bbb22222", "2026-02-01T00:00:00Z"),
                               ("ccc33333", "2026-01-01T00:00:00Z")):
        d = storage.run_dir_for(run_id, started_iso="20260101T000000Z")
        run = TestRun(run_id=run_id, config=cfg, status=RunStatus.DONE,
                      started_at=started_at,
                      app_version="0.1.1", engine_version="sandbox-1d")
        storage.save_manifest(d, run)
    runs = storage.list_runs()
    # Newest started_at first: aaa (Mar) > bbb (Feb) > ccc (Jan). A
    # dir-name sort would have produced the reverse (ccc, bbb, aaa).
    assert [r.run_id for r in runs] == ["aaa11111", "bbb22222", "ccc33333"]


def test_list_runs_with_paths_pairs_dir_and_manifest(
    monkeypatch, tmp_path,
) -> None:
    """list_runs_with_paths returns each Run alongside its on-disk dir,
    newest-first by ``started_at``. run_id ``aaa`` is the NEWER run here, so
    it must come before ``bbb`` even though a dir-name sort would reverse
    them.
    """
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    cfg = _config()
    for run_id, started_at in (("aaa11111", "2026-02-01T00:00:00Z"),
                               ("bbb22222", "2026-01-01T00:00:00Z")):
        d = storage.run_dir_for(run_id, started_iso="20260101T000000Z")
        run = TestRun(run_id=run_id, config=cfg, status=RunStatus.DONE,
                      started_at=started_at,
                      app_version="0.1.1", engine_version="sandbox-1d")
        storage.save_manifest(d, run)
    pairs = storage.list_runs_with_paths()
    # Newest started_at first (aaa = Feb, bbb = Jan).
    assert [r.run_id for _p, r in pairs] == ["aaa11111", "bbb22222"]
    # Each path exists and matches the run_id.
    for path, run in pairs:
        assert path.exists()
        assert path.is_dir()
        assert path.name.startswith(run.run_id + "-")


def test_list_runs_with_paths_skips_unparseable_dirs(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    cfg = _config()
    # One valid run + one stray empty dir without a manifest.
    d_ok = storage.run_dir_for("ok000001", started_iso="20260101T000000Z")
    storage.save_manifest(d_ok, TestRun(run_id="ok000001", config=cfg,
                                        status=RunStatus.DONE))
    stray = storage.runs_dir() / "garbage-folder"
    stray.mkdir()
    pairs = storage.list_runs_with_paths()
    assert len(pairs) == 1
    assert pairs[0][1].run_id == "ok000001"


def test_delete_run_removes_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    cfg = _config()
    d = storage.run_dir_for("abc12345", started_iso="20260101T000000Z")
    storage.save_manifest(d, TestRun(run_id="abc12345", config=cfg,
                                     status=RunStatus.DONE))
    assert d.exists()
    assert storage.delete_run(d) is True
    assert not d.exists()
