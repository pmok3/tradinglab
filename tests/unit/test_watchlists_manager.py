"""Unit tests for `watchlists/manager.py` + `watchlists/storage.py`.

Batch 9 of the test-coverage audit. Covers ticker normalization,
max-pinned enforcement, replace-vs-merge import, v1 schema migration
(seed-first-pin), and reorder_pins permutation validation.

No real disk I/O against the cache dir — file-based tests use the
`tmp_path` pytest fixture and the explicit `load_from_file` /
`save_to_file` API.
"""

from __future__ import annotations

import json

import pytest

from tradinglab.watchlists.manager import WatchlistManager
from tradinglab.watchlists.storage import Watchlist, normalize_tickers

# ---------------------------------------------------------------------------
# 1. normalize_tickers — drops blanks, uppercases, dedupes, preserves order.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Canonical audit case.
        ([" aapl ", "MSFT", None, "", "aapl", "TSLA"], ["AAPL", "MSFT", "TSLA"]),
        # Empty / None inputs.
        ([], []),
        (None, []),
        # All-blank / None entries collapse to empty.
        ([None, "", "  ", "\t"], []),
        # Dedup is case-insensitive (via upper()) and order is preserved
        # by *first* occurrence.
        (["amd", "AMD", "Amd"], ["AMD"]),
        (["c", "b", "a", "B", "C"], ["C", "B", "A"]),
        # Non-string entries coerced via str() at the storage boundary.
        ([123, "msft"], ["123", "MSFT"]),
    ],
)
def test_normalize_tickers_drops_blanks_uppercases_dedupes_preserves_order(
    raw, expected
):
    assert normalize_tickers(raw) == expected


# ---------------------------------------------------------------------------
# 2. max-pinned enforcement.
# ---------------------------------------------------------------------------


def test_max_pinned_enforcement():
    mgr = WatchlistManager()
    names = [f"WL{i}" for i in range(WatchlistManager.MAX_PINNED)]
    for n in names:
        mgr.create(n, ["AAPL"])
        mgr.pin(n)

    assert mgr.pinned_names() == names
    assert len(mgr.pinned_names()) == WatchlistManager.MAX_PINNED

    # 6th pin should raise (the spec uses ValueError, with a meaningful msg).
    mgr.create("Overflow", ["MSFT"])
    with pytest.raises(ValueError, match=str(WatchlistManager.MAX_PINNED)):
        mgr.pin("Overflow")

    # Pin order must be unchanged after the failed attempt.
    assert mgr.pinned_names() == names
    assert "Overflow" not in mgr.pinned_names()


# ---------------------------------------------------------------------------
# 3. import_watchlists — replace vs. merge mode.
# ---------------------------------------------------------------------------


def _seeded_manager_ab():
    mgr = WatchlistManager()
    mgr.create("A", ["AAPL"])
    mgr.create("B", ["BIDU"])
    return mgr


def test_import_replace_vs_merge_mode():
    # --- replace -----------------------------------------------------------
    mgr = _seeded_manager_ab()
    incoming_replace = [
        Watchlist(name="B", tickers=["BABA"]),  # different tickers from seed
        Watchlist(name="C", tickers=["CSCO"]),
    ]
    written = mgr.import_watchlists(incoming_replace, mode="replace")
    assert written == 2
    assert set(mgr.list_names()) == {"B", "C"}
    # Replace overwrote seed-B's tickers with the incoming payload.
    assert mgr.get("B").tickers == ["BABA"]
    assert mgr.get("C").tickers == ["CSCO"]
    # A is gone entirely.
    assert mgr.get("A") is None

    # --- merge -------------------------------------------------------------
    mgr2 = _seeded_manager_ab()
    incoming_merge = [
        Watchlist(name="B", tickers=["BABA"]),  # incoming wins on overwrite
        Watchlist(name="C", tickers=["CSCO"]),
    ]
    written2 = mgr2.import_watchlists(incoming_merge, mode="merge")
    assert written2 == 2
    assert set(mgr2.list_names()) == {"A", "B", "C"}
    # Original A untouched.
    assert mgr2.get("A").tickers == ["AAPL"]
    # B overwritten by incoming (per the loop body in import_watchlists).
    assert mgr2.get("B").tickers == ["BABA"]
    assert mgr2.get("C").tickers == ["CSCO"]

    # Unknown mode → ValueError.
    with pytest.raises(ValueError, match="unknown mode"):
        WatchlistManager().import_watchlists([], mode="bogus")


# ---------------------------------------------------------------------------
# 4. load_from_file — v1 schema (no `pinned` key) auto-seeds first pin.
# ---------------------------------------------------------------------------


def test_load_from_file_v1_schema_seeds_first_pin(tmp_path):
    # v1 schema: `version: 1`, `watchlists: [...]`, NO `pinned` field.
    v1_path = tmp_path / "watchlists_v1.json"
    v1_payload = {
        "version": 1,
        "watchlists": [
            {"name": "Alpha", "tickers": ["AAPL", "AMD"]},
            {"name": "Beta",  "tickers": ["BAC"]},
            {"name": "Gamma", "tickers": ["GOOG"]},
        ],
        # NOTE: deliberately no "pinned" key — that's the whole point.
    }
    v1_path.write_text(json.dumps(v1_payload), encoding="utf-8")

    mgr = WatchlistManager()
    count = mgr.load_from_file(v1_path)

    # All three lists loaded.
    assert count == 3
    assert mgr.list_names() == ["Alpha", "Beta", "Gamma"]

    # Seed-first-pin invariant: with no pinned key in the file, the first
    # list (deterministic insertion order) is auto-pinned so the UI is
    # never empty after v1 migration.
    assert mgr.pinned_names() == ["Alpha"]

    # load_from_file resets dirty and stamps loaded_path.
    assert mgr.is_dirty() is False
    assert mgr.loaded_path() == v1_path


# ---------------------------------------------------------------------------
# 5. reorder_pins — must be a permutation of current pins.
# ---------------------------------------------------------------------------


def test_reorder_pins_permutation():
    mgr = WatchlistManager()
    for n in ("A", "B", "C"):
        mgr.create(n, [])
        mgr.pin(n)
    assert mgr.pinned_names() == ["A", "B", "C"]

    # Valid permutation: rotates the order.
    mgr.reorder_pins(["C", "A", "B"])
    assert mgr.pinned_names() == ["C", "A", "B"]

    # Missing pin → not a permutation, must raise (and not mutate state).
    with pytest.raises(ValueError, match="permutation"):
        mgr.reorder_pins(["A"])
    assert mgr.pinned_names() == ["C", "A", "B"]

    # Extra / unknown pin → also not a permutation.
    with pytest.raises(ValueError, match="permutation"):
        mgr.reorder_pins(["C", "A", "B", "D"])
    assert mgr.pinned_names() == ["C", "A", "B"]
