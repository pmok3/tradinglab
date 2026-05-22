"""App-level scanner wiring tests.

These tests instantiate a real :class:`ChartApp` and exercise the
scanner integration helpers (``_refresh_scanner_for_sandbox``,
``_on_scanner_row_action``, ``_reset_scanner_state``) in isolation
from a real sandbox session. The Tk main loop never runs.

Some of the test assertions hit private attributes intentionally —
this file's purpose is to lock the wiring contract between the
``SandboxController`` per-tick callback and the ``ScannerTab`` widget,
which is otherwise easy to break silently.
"""
from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace
from typing import List

import pytest

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.models import Candle
from tradinglab.scanner.model import (
    OP_GT,
    Condition,
    FieldRef,
    Group,
    ScanDefinition,
)


@pytest.fixture(scope="session")
def _app():
    # Importing inside the fixture keeps the heavy import cost off
    # collection time when this file is skipped (no Tk display).
    try:
        from tradinglab.app import ChartApp
        app = ChartApp()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Tk/ChartApp unavailable: {e}")
    app.withdraw()
    yield app
    try:
        app.destroy()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture
def app(_app):
    # Per-test reset: drop any scans the previous test added.
    _app._scanner_tab.set_library({})
    _app._reset_scanner_state()
    yield _app


def _candle(epoch_minute: int, close: float) -> Candle:
    ts = _dt.datetime(2024, 1, 2, 14, 30) + _dt.timedelta(minutes=epoch_minute)
    return Candle(date=ts, open=close - 0.5, high=close + 0.5,
                  low=close - 0.5, close=close, volume=1000)


def _bars(closes: list[float]) -> list[Candle]:
    return [_candle(i * 5, c) for i, c in enumerate(closes)]


def _make_scan(name: str, threshold: float = 100.0) -> ScanDefinition:
    return ScanDefinition(
        name=name,
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(threshold)},
                      interval="5m"),
        ]),
        primary_interval="5m",
    )


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def test_scanner_tab_present_in_notebook(app):
    labels = [app._notebook.tab(t, "text") for t in app._notebook.tabs()]
    assert "Scanner" in labels


def test_scanner_components_wired(app):
    assert app._scanner_tab is not None
    assert app._scan_runner is not None
    assert app._scan_tick_id == 0


# ---------------------------------------------------------------------------
# _refresh_scanner_for_sandbox
# ---------------------------------------------------------------------------


def test_refresh_noop_when_no_sandbox(app):
    # Add a scan; sandbox is None → must early-return without raising.
    app._scanner_tab.add_scan(_make_scan("S1"))
    app._sandbox = None
    app._refresh_scanner_for_sandbox()  # must not raise
    assert app._scan_tick_id == 0


def test_refresh_noop_when_no_scans(app):
    fake_sb = SimpleNamespace(
        visible_candles_by_symbol={"AAPL": _bars([99, 100, 101])},
        interval="5m",
        current_session_date=lambda: _dt.date(2024, 1, 2),
    )
    app._sandbox = fake_sb
    app._refresh_scanner_for_sandbox()
    assert app._scan_tick_id == 0  # no scans → no tick consumed
    app._sandbox = None


def test_refresh_runs_scans_and_pushes_results(app):
    scan = _make_scan("Above100", threshold=100.0)
    app._scanner_tab.add_scan(scan)
    fake_sb = SimpleNamespace(
        visible_candles_by_symbol={
            "WIN": _bars([101, 102, 103]),  # always > 100
            "LOSE": _bars([90, 91, 92]),    # always < 100
        },
        interval="5m",
        current_session_date=lambda: _dt.date(2024, 1, 2),
    )
    app._sandbox = fake_sb
    app._refresh_scanner_for_sandbox()
    assert app._scan_tick_id == 1
    results = app._scan_last_results
    assert scan.id in results
    matched = {r.symbol for r in results[scan.id].rows if r.matched is True}
    assert matched == {"WIN"}
    app._sandbox = None


def test_refresh_increments_tick_each_call(app):
    scan = _make_scan("Any", threshold=0.0)
    app._scanner_tab.add_scan(scan)
    fake_sb = SimpleNamespace(
        visible_candles_by_symbol={"X": _bars([1.0, 2.0, 3.0])},
        interval="5m",
        current_session_date=lambda: _dt.date(2024, 1, 2),
    )
    app._sandbox = fake_sb
    app._refresh_scanner_for_sandbox()
    app._refresh_scanner_for_sandbox()
    app._refresh_scanner_for_sandbox()
    assert app._scan_tick_id == 3
    app._sandbox = None


def test_refresh_handles_runner_exception_gracefully(app, monkeypatch):
    scan = _make_scan("Boom")
    app._scanner_tab.add_scan(scan)
    fake_sb = SimpleNamespace(
        visible_candles_by_symbol={"X": _bars([100, 100, 100])},
        interval="5m",
        current_session_date=lambda: _dt.date(2024, 1, 2),
    )
    app._sandbox = fake_sb

    def boom(**kwargs):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(app._scan_runner, "run", boom)
    # Must not raise even when the runner blows up.
    app._refresh_scanner_for_sandbox()
    app._sandbox = None


def test_refresh_skips_when_sandbox_universe_empty(app):
    app._scanner_tab.add_scan(_make_scan("S1"))
    fake_sb = SimpleNamespace(
        visible_candles_by_symbol={},
        interval="5m",
        current_session_date=lambda: _dt.date(2024, 1, 2),
    )
    app._sandbox = fake_sb
    app._refresh_scanner_for_sandbox()
    assert app._scan_tick_id == 0
    app._sandbox = None


# ---------------------------------------------------------------------------
# _reset_scanner_state
# ---------------------------------------------------------------------------


def test_reset_clears_runner_history_and_tick(app):
    scan = _make_scan("S")
    app._scanner_tab.add_scan(scan)
    fake_sb = SimpleNamespace(
        visible_candles_by_symbol={"X": _bars([101, 102, 103])},
        interval="5m",
        current_session_date=lambda: _dt.date(2024, 1, 2),
    )
    app._sandbox = fake_sb
    app._refresh_scanner_for_sandbox()
    app._refresh_scanner_for_sandbox()
    assert app._scan_tick_id == 2
    app._sandbox = None
    app._reset_scanner_state()
    assert app._scan_tick_id == 0
    assert app._scan_last_results == {}


# ---------------------------------------------------------------------------
# Save / delete callbacks (storage round-trip)
# ---------------------------------------------------------------------------


def test_save_then_delete_roundtrip(app, tmp_path, monkeypatch):
    # Redirect storage to a tmp dir.
    monkeypatch.setattr(
        "tradinglab.scanner.storage._cache_dir",
        lambda: tmp_path,
    )
    scan = _make_scan("Persisted")
    app._on_scanner_scan_saved(scan)
    files = list((tmp_path / "scans").iterdir())
    assert any(f.name.startswith(scan.id) for f in files)
    app._on_scanner_scan_deleted(scan.id)
    files = [f for f in (tmp_path / "scans").iterdir() if f.is_file()]
    assert all(not f.name.startswith(scan.id) for f in files)


# ---------------------------------------------------------------------------
# _on_scanner_row_action — primary path
# ---------------------------------------------------------------------------


def test_row_action_primary_outside_sandbox_sets_ticker_var(app, monkeypatch):
    app._sandbox = None  # explicit
    captured = []
    monkeypatch.setattr(app, "_load_data", lambda: captured.append(True))
    app._on_scanner_row_action("MSFT", "primary")
    assert app.ticker_var.get().upper() == "MSFT"
    assert captured == [True]


def test_row_action_primary_in_sandbox_calls_register_focus(app, monkeypatch):
    fake_sb = SimpleNamespace(
        is_active=lambda: True,
        visible_candles_by_symbol={},
        interval="5m",
        current_session_date=lambda: _dt.date(2024, 1, 2),
    )
    app._sandbox = fake_sb
    fired = []
    monkeypatch.setattr(app, "_sandbox_register_and_focus",
                        lambda sym: fired.append(sym) or True)
    app._on_scanner_row_action("nvda", "primary")
    assert fired == ["NVDA"]
    app._sandbox = None


def test_row_action_compare_outside_sandbox_sets_compare_vars(app):
    app._sandbox = None
    app._on_scanner_row_action("GOOG", "compare")
    assert app.compare_var.get() is True
    assert app.compare_ticker_var.get().upper() == "GOOG"


def test_row_action_unknown_kind_is_noop(app):
    # Should not raise.
    app._on_scanner_row_action("AAPL", "bogus-action")
