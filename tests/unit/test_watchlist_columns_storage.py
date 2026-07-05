"""Tests for watchlist column persistence (storage v3 + manager accessors)."""

from __future__ import annotations

from tradinglab.scanner.model import FieldRef
from tradinglab.watchlists import columns as C
from tradinglab.watchlists import storage as ST
from tradinglab.watchlists.manager import WatchlistManager
from tradinglab.watchlists.storage import Watchlist


def _sig_cols():
    ref = FieldRef(kind="indicator", id="rvol", params={"length": 20}, interval="5m")
    return C.validate_columns([
        C.WatchlistColumn(kind=C.KIND_SYSTEM, id="ticker", label="Ticker", anchor="w"),
        C.WatchlistColumn(
            kind=C.KIND_SIGNAL, id=C.signal_column_id(ref), ref=ref, fmt="multiplier"),
    ])


def test_export_read_roundtrips_display(tmp_path):
    p = tmp_path / "wl.json"
    display = {"default_columns": [], "by_watchlist": {"Longs": C.columns_to_json(_sig_cols())}}
    ST.export_to_file([Watchlist(name="Longs", tickers=["AAPL"])], p, ["Longs"], display)
    disp = ST.read_display(p)
    assert "Longs" in disp["by_watchlist"]
    back = C.columns_from_json(disp["by_watchlist"]["Longs"])
    assert any(c.kind == C.KIND_SIGNAL and c.ref.id == "rvol" for c in back)


def test_v2_file_has_empty_display_and_still_imports(tmp_path):
    p = tmp_path / "old.json"
    p.write_text(
        '{"version":2,"watchlists":[{"name":"A","tickers":["X"]}],"pinned":["A"]}',
        encoding="utf-8")
    assert ST.read_display(p) == {"default_columns": [], "by_watchlist": {}}
    wls, pinned = ST.import_from_file(p)
    assert wls[0].name == "A" and pinned == ["A"]


def test_manager_default_and_per_watchlist_columns():
    m = WatchlistManager()
    m.create("Longs", ["AAPL"])
    assert [c.id for c in m.columns_for("Longs")] == list(C.SYSTEM_COLUMN_IDS)
    m.set_columns("Longs", _sig_cols())
    got = m.columns_for("Longs")
    assert got[0].id == "ticker"
    assert any(c.kind == C.KIND_SIGNAL for c in got)
    m.set_default_columns([
        C.WatchlistColumn(kind=C.KIND_SYSTEM, id="ticker", anchor="w"),
        C.WatchlistColumn(kind=C.KIND_SYSTEM, id="change_pct"),
    ])
    m.create("New", [])
    assert [c.id for c in m.columns_for("New")] == ["ticker", "change_pct"]


def test_manager_save_load_preserves_columns(tmp_path):
    p = tmp_path / "wl.json"
    m = WatchlistManager()
    m.create("Longs", ["AAPL"])
    m.pin("Longs")
    m.set_columns("Longs", _sig_cols())
    m.save_to_file(p)

    m2 = WatchlistManager()
    m2.load_from_file(p)
    got = m2.columns_for("Longs")
    assert any(c.kind == C.KIND_SIGNAL and c.ref.id == "rvol" for c in got)


def test_manager_delete_and_rename_update_columns():
    m = WatchlistManager()
    m.create("A", [])
    m.set_columns("A", _sig_cols())
    m.rename("A", "B")
    assert any(c.kind == C.KIND_SIGNAL for c in m.columns_for("B"))
    m.delete("B")
    assert [c.id for c in m.columns_for("B")] == list(C.SYSTEM_COLUMN_IDS)
