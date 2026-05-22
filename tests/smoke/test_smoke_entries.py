"""Smoke checks for the entries feature wiring inside ChartApp.

Mirrors :mod:`tests.smoke.test_smoke_exits`. Reuses the session-scoped
``app`` fixture from ``tests/smoke/conftest.py``.

Coverage:
- The ``Entries`` notebook tab is wired and visible (and inserted
  BEFORE ``Exits``).
- The entries stack (audit / paper sink / evaluator / overlay / tab)
  is constructed and reachable from the app.
- ``_redraw_entries_overlay`` is callable post-render without raising.
- ``_refresh_entries_for_sandbox`` no-ops when no sandbox session.
- Loading the four prepackaged templates from
  ``data/entry_strategy_templates`` round-trips through storage.
- The ``Entries`` cascade menu is present.
- End-to-end: arm a manual MARKET strategy, drive a sandbox tick →
  position opens via paper engine + audit logs ``entry_fire``.
- Round-trip: import / export of an entry strategy.
- Existing ``Exits`` tests still pass (entries doesn't regress exits).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tradinglab.entries import storage as _entries_storage
from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    SizingKind,
    SizingRule,
    TriggerKind,
    Universe,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_entries_storage() -> None:
    d = _entries_storage.storage_dir()
    for f in d.glob("*.json"):
        try:
            f.unlink()
        except Exception:  # noqa: BLE001
            pass


def _save_market_long(name: str = "smoke-mkt", *, symbol: str = "AAPL",
                      qty: float = 10.0) -> EntryStrategy:
    from tradinglab.entries.model import PositionAlreadyOpenPolicy

    s = EntryStrategy(
        name=name,
        direction=Direction.LONG,
        universe=Universe(symbols=(symbol,)),
        trigger=EntryTrigger(kind=TriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=qty),
        max_fires_per_session_per_symbol=1,
        position_already_open_policy=PositionAlreadyOpenPolicy.STACK,
    )
    _entries_storage.save(s)
    return s


# ---------------------------------------------------------------------------
# Wiring checks
# ---------------------------------------------------------------------------


def test_entries_tab_present_in_notebook(app):
    nb = app._notebook
    tabs = [nb.tab(i, "text") for i in range(nb.index("end"))]
    assert "Entries" in tabs
    # Entries must precede Exits in tab order.
    assert tabs.index("Entries") < tabs.index("Exits"), (
        f"Entries tab should be inserted BEFORE Exits; got order {tabs}"
    )


def test_entries_stack_constructed(app):
    assert app._entries_audit_log is not None
    assert app._entry_paper_sink is not None
    assert app._entry_evaluator is not None
    assert app._entries_tab is not None
    assert app._entries_overlay is not None


def test_redraw_entries_overlay_no_error(app):
    app._redraw_entries_overlay()


def test_refresh_entries_for_sandbox_no_session_noop(app):
    saved = app._sandbox
    app._sandbox = None
    try:
        app._refresh_entries_for_sandbox()
    finally:
        app._sandbox = saved


def test_entries_menu_cascade_present(app):
    """The Entries cascade menu item exists alongside Exits."""
    menubar = app._menubar
    end_index = menubar.index("end")
    found = False
    for i in range(0 if end_index is None else end_index + 1):
        try:
            label = menubar.entrycget(i, "label")
        except Exception:  # noqa: BLE001
            continue
        if label == "Entries":
            found = True
            break
    assert found, "menubar missing 'Entries' cascade"


def test_load_prepackaged_templates(app):
    """All four entries-v1 templates parse cleanly via storage."""
    template_dir = Path(__file__).resolve().parents[2] / "data" / \
        "entry_strategy_templates"
    paths = sorted(template_dir.glob("*.json"))
    assert len(paths) >= 4, (
        f"expected at least 4 templates in {template_dir}; got {len(paths)}"
    )
    _clear_entries_storage()
    try:
        for p in paths:
            saved = app._entries_tab.load_template_from_path(p)
            assert saved.id  # got minted
            loaded = _entries_storage.load(saved.id)
            assert loaded.name == saved.name
        # Refresh must walk the whole library cleanly.
        app._entries_tab.refresh()
        assert len(app._entries_tab.library) >= len(paths)
    finally:
        _clear_entries_storage()


def test_import_export_round_trip(app, tmp_path: Path):
    _clear_entries_storage()
    s = _save_market_long(name="round-trip", symbol="AAPL")
    out = tmp_path / "exported.json"
    _entries_storage.export_to_path(s, out)
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["name"] == "round-trip"

    _entries_storage.delete(s.id)
    app._entries_tab.refresh()
    assert all(x.id != s.id for x in app._entries_tab.library)

    _entries_storage.import_from_path(out)
    app._entries_tab.refresh()
    assert any(x.name == "round-trip" for x in app._entries_tab.library)
    _clear_entries_storage()


def test_e2e_market_entry_fires_via_sandbox_tick_loop(app):
    """End-to-end: arm a MARKET strategy, drive a sandbox tick, expect
    a position to open + ``entry_fire`` in the audit log.
    """
    from tradinglab.models import Candle

    _clear_entries_storage()
    sym = (app.ticker_var.get() or "AAPL").strip().upper()
    s = _save_market_long(name="e2e-mkt", symbol=sym, qty=10.0)
    app._entries_tab.refresh()

    # Arm + force evaluator state push.
    app._entry_evaluator.set_strategies([s])
    app._entry_evaluator.arm(s.id)

    # Stub the sandbox state (replay would call this hook).
    base_t = datetime(2024, 1, 2, 9, 30)

    def _bar(o, h, l, c, *, idx=0):
        return Candle(
            date=base_t + timedelta(minutes=5 * idx),
            open=o, high=h, low=l, close=c, volume=1000,
        )

    class _StubSandbox:
        pass

    stub_sb = _StubSandbox()
    stub_sb.visible_candles_by_symbol = {sym: [_bar(100, 101, 99, 100, idx=0)]}

    saved_sb = app._sandbox
    app._sandbox = stub_sb
    try:
        prior_open = len(list(app._position_tracker.list_open()))
        # Tick 1: MARKET trigger fires + submits pending entry order.
        app._refresh_entries_for_sandbox()
        # Tick 2: pending entry fills against this fresh bar.
        stub_sb.visible_candles_by_symbol = {
            sym: [_bar(100, 101, 99, 100, idx=0),
                  _bar(100, 101, 99, 100, idx=1)],
        }
        app._refresh_entries_for_sandbox()
        # Tick 3: belt-and-suspenders for any deferred fill path.
        stub_sb.visible_candles_by_symbol = {
            sym: [_bar(100, 101, 99, 100, idx=0),
                  _bar(100, 101, 99, 100, idx=1),
                  _bar(100, 101, 99, 100, idx=2)],
        }
        app._refresh_entries_for_sandbox()
        post_open = len(list(app._position_tracker.list_open()))
        kinds = [r.get("kind") for r in app._entries_audit_log.tail(50)]
        assert post_open >= prior_open + 1, (
            f"expected new position from MARKET entry; prior={prior_open} "
            f"post={post_open}; audit_kinds={kinds}; "
            f"engine_stats={app._paper_engine.stats()}"
        )
        assert "entry_fire" in kinds, (
            f"expected 'entry_fire' in audit; got {kinds}"
        )
        assert "entry_fill" in kinds, (
            f"expected 'entry_fill' in audit; got {kinds}"
        )
    finally:
        app._sandbox = saved_sb
        # Disarm + close any remaining positions.
        try:
            app._entry_evaluator.disarm_all()
        except Exception:  # noqa: BLE001
            pass
        for pos in list(app._position_tracker.list_open()):
            if pos.symbol == sym and pos.qty_open > 0:
                try:
                    app._position_tracker.apply_fill(
                        position_id=pos.id, qty=pos.qty_open, price=100.0,
                    )
                except Exception:  # noqa: BLE001
                    pass
        _clear_entries_storage()
