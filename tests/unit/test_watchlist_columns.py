"""Unit tests for ``watchlists/columns.py`` (column model)."""

from __future__ import annotations

from tradinglab.scanner.model import FieldRef
from tradinglab.watchlists import columns as C


def _ref_builtin(fid, **kw):
    return FieldRef(kind="builtin", id=fid, **kw)


def test_default_columns_is_todays_set():
    cols = C.default_columns()
    assert [c.id for c in cols] == list(C.SYSTEM_COLUMN_IDS)
    assert cols[0].id == "ticker" and cols[0].kind == C.KIND_SYSTEM
    assert all(c.kind == C.KIND_SYSTEM and c.ref is None for c in cols)


def test_signal_column_id_deterministic_and_unique():
    r1 = _ref_builtin("gap_pct")
    r2 = FieldRef(kind="indicator", id="rvol", params={"length": 20})
    r2b = FieldRef(kind="indicator", id="rvol", params={"length": 30})
    assert C.signal_column_id(r1) == C.signal_column_id(_ref_builtin("gap_pct"))
    assert C.signal_column_id(r2) != C.signal_column_id(r2b)
    assert C.signal_column_id(r1).startswith("sig:")


def test_system_column_round_trip():
    col = C.default_columns()[1]  # last
    back = C.column_from_dict(C.column_to_dict(col))
    assert back is not None and back.id == "last" and back.kind == C.KIND_SYSTEM


def test_signal_column_round_trip():
    ref = FieldRef(kind="indicator", id="rvol", params={"length": 20}, interval="5m")
    col = C.WatchlistColumn(
        kind=C.KIND_SIGNAL, id=C.signal_column_id(ref), ref=ref, fmt="multiplier"
    )
    back = C.column_from_dict(C.column_to_dict(col))
    assert back is not None
    assert back.kind == C.KIND_SIGNAL
    assert back.ref is not None and back.ref.id == "rvol"
    assert back.ref.params.get("length") == 20
    assert back.ref.interval == "5m"
    assert back.fmt == "multiplier"
    assert back.id == C.signal_column_id(ref)


def test_from_dict_tolerates_junk():
    assert C.column_from_dict(None) is None
    assert C.column_from_dict({"kind": "bogus"}) is None
    assert C.column_from_dict({"kind": "system", "id": "not_a_system_col"}) is None
    assert C.column_from_dict({"kind": "signal"}) is None  # no ref
    assert C.column_from_dict({"kind": "signal", "ref": {"kind": "junk"}}) is None


def test_columns_from_json_drops_bad_and_validates():
    ref = _ref_builtin("gap_pct")
    data = [
        {"kind": "system", "id": "last"},
        {"kind": "bogus"},                    # dropped
        {"kind": "signal", "ref": ref.to_dict()},
        "not a dict",                         # dropped
    ]
    cols = C.columns_from_json(data)
    ids = [c.id for c in cols]
    assert ids[0] == "ticker"                 # forced first even though absent from input
    assert "last" in ids
    assert any(c.kind == C.KIND_SIGNAL for c in cols)
    # non-list input -> just the ticker column
    assert [c.id for c in C.columns_from_json("garbage")] == ["ticker"]


def test_validate_forces_ticker_first_and_dedupes():
    ref = _ref_builtin("gap_pct")
    sig = C.WatchlistColumn(kind=C.KIND_SIGNAL, id=C.signal_column_id(ref), ref=ref)
    last = C.WatchlistColumn(kind=C.KIND_SYSTEM, id="last")
    ticker = C.WatchlistColumn(kind=C.KIND_SYSTEM, id="ticker", label="Ticker", anchor="w")
    invalid = C.WatchlistColumn(kind=C.KIND_SIGNAL, id="x", ref=None)
    out = C.validate_columns([last, sig, sig, ticker, invalid])
    assert out[0].id == "ticker"
    assert [c.id for c in out].count(sig.id) == 1                 # deduped
    assert all(not (c.kind == C.KIND_SIGNAL and c.ref is None) for c in out)


def test_validate_inserts_ticker_if_missing():
    out = C.validate_columns([C.WatchlistColumn(kind=C.KIND_SYSTEM, id="last")])
    assert out[0].id == "ticker" and out[0].kind == C.KIND_SYSTEM


def test_header_label():
    assert C.header_label(C.WatchlistColumn(kind=C.KIND_SYSTEM, id="change_pct")) == "Change%"
    # explicit label wins
    assert C.header_label(C.WatchlistColumn(kind=C.KIND_SYSTEM, id="last", label="Px")) == "Px"
    ref = FieldRef(kind="builtin", id="gap_pct", params={"length": 14}, interval="5m")
    assert C.header_label(C.WatchlistColumn(kind=C.KIND_SIGNAL, id="s", ref=ref)).endswith("(14,5m)")
    # daily interval -> no interval tag in the label
    ref_d = FieldRef(kind="builtin", id="gap_pct", params={"length": 14}, interval="1d")
    assert C.header_label(C.WatchlistColumn(kind=C.KIND_SIGNAL, id="s", ref=ref_d)).endswith("(14)")
