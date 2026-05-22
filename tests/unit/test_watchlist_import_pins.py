"""Unit tests for :class:`WatchlistManager.import_watchlists` pin merge.

Covers the F1c follow-up: imported pin list was previously dropped on
merge; it now gets merged in (de-duped, capped at MAX_PINNED).
"""
from __future__ import annotations

import pytest

from tradinglab.watchlists.manager import WatchlistManager
from tradinglab.watchlists.storage import Watchlist


def _wls(*names: str) -> list[Watchlist]:
    return [Watchlist(name=n, tickers=["AAPL"]) for n in names]


def test_import_merge_no_pinned_kwarg_back_compat():
    """Default kwarg path mirrors the legacy single-positional call."""
    mgr = WatchlistManager()
    mgr.import_watchlists(_wls("Tech", "Growth"))
    # First list auto-seeded as the sole pin (legacy invariant).
    assert mgr.pinned_names() == ["Tech"]


def test_import_merge_with_pinned_seeds_pins():
    mgr = WatchlistManager()
    mgr.import_watchlists(
        _wls("Tech", "Energy", "Health"),
        mode="merge",
        pinned=["Energy", "Health"],
    )
    # Imported pin ordering preserved; "Tech" not auto-seeded because
    # the imported file declared its own pin set.
    assert mgr.pinned_names() == ["Energy", "Health"]


def test_import_merge_preserves_existing_pins():
    mgr = WatchlistManager()
    mgr.create("Existing", tickers=["AMD"])
    mgr.pin("Existing")
    mgr.import_watchlists(
        _wls("Imported"), mode="merge", pinned=["Imported"],
    )
    pins = mgr.pinned_names()
    # Existing pin remains first; imported pin appended.
    assert pins[0] == "Existing"
    assert "Imported" in pins


def test_import_merge_pin_dedupes_against_existing():
    mgr = WatchlistManager()
    mgr.create("Shared", tickers=["AMD"])
    mgr.pin("Shared")
    mgr.import_watchlists(
        _wls("Shared", "Extra"),
        mode="merge",
        pinned=["Shared", "Extra"],
    )
    # No duplicate "Shared" — pin appears once.
    assert mgr.pinned_names().count("Shared") == 1


def test_import_merge_pin_caps_at_max_pinned():
    mgr = WatchlistManager()
    incoming = _wls(*[f"W{i}" for i in range(WatchlistManager.MAX_PINNED + 3)])
    mgr.import_watchlists(
        incoming,
        mode="merge",
        pinned=[w.name for w in incoming],
    )
    assert len(mgr.pinned_names()) == WatchlistManager.MAX_PINNED


def test_import_replace_with_pinned_overrides_existing():
    mgr = WatchlistManager()
    mgr.create("Old", tickers=["AMD"])
    mgr.pin("Old")
    mgr.import_watchlists(
        _wls("New"), mode="replace", pinned=["New"],
    )
    assert mgr.pinned_names() == ["New"]
    assert "Old" not in mgr.list_names()


def test_import_merge_ignores_pin_for_nonexistent_list():
    mgr = WatchlistManager()
    mgr.import_watchlists(
        _wls("Real"), mode="merge", pinned=["Real", "DoesNotExist"],
    )
    assert mgr.pinned_names() == ["Real"]


def test_import_merge_empty_pinned_list_falls_back_to_auto_seed():
    mgr = WatchlistManager()
    mgr.import_watchlists(_wls("OnlyOne"), mode="merge", pinned=[])
    # Empty pinned list — auto-seed kicks in.
    assert mgr.pinned_names() == ["OnlyOne"]


def test_import_unknown_mode_raises():
    mgr = WatchlistManager()
    with pytest.raises(ValueError):
        mgr.import_watchlists(_wls("X"), mode="other")
