"""Unit tests for :mod:`tradinglab.events.cache`.

Locks in the disk-cache invariants:

* Atomic save (temp file + ``os.replace`` — no half-written pickles)
* Corrupt-pickle non-fatal (load returns ``None``)
* Cache misses return ``None`` quietly
* Round-trip preserves record values
* :func:`merge_bundle` deduplicates by primary key (ts / ex_ts) with the
  new bundle winning, output sorted ascending
* :func:`merge_bundle` handles None-on-either-side cases
* ``TRADINGLAB_CACHE_DIR`` env var redirects the directory (so tests
  don't pollute the user's real cache)
* ``fetched_at = 0`` on save gets stamped with the current time

Uses ``tmp_path`` + monkeypatch to redirect the cache directory; no
filesystem side-effects leak across tests.
"""
from __future__ import annotations

import math
import os
import pickle
import time

import pytest

from tradinglab.events import cache as events_cache
from tradinglab.events.base import (
    DividendRecord,
    EarningsRecord,
    EventBundle,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_cache_dir(tmp_path, monkeypatch):
    """Redirect ``TRADINGLAB_CACHE_DIR`` to a tmp path."""
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    yield tmp_path


def _sample_bundle(symbol="X"):
    return EventBundle(
        symbol=symbol,
        earnings=[
            EarningsRecord(ts=10_000, symbol=symbol, when="AMC",
                           eps_estimate=1.0, eps_actual=1.05),
            EarningsRecord(ts=20_000, symbol=symbol, when="BMO",
                           eps_estimate=1.1),
        ],
        dividends=[
            DividendRecord(ex_ts=15_000, symbol=symbol,
                           amount=0.25, kind="cash"),
        ],
        fetched_at=999,
    )


# ---------------------------------------------------------------------------
# load: misses + corruption
# ---------------------------------------------------------------------------

def test_load_returns_none_on_cache_miss(isolated_cache_dir):
    assert events_cache.load("yfinance", "NEVER_FETCHED") is None


def test_load_corrupt_pickle_returns_none(isolated_cache_dir):
    bundle = _sample_bundle("X")
    events_cache.save("yfinance", "X", bundle)
    # Overwrite the file with garbage. The cache format is now JSON
    # rather than pickle, but the freedom-from-RCE assertion is the
    # same: an attacker-controlled byte stream must NOT execute code
    # and load() must NOT raise.
    cache_root = isolated_cache_dir / "events"
    files = list(cache_root.glob("*.json"))
    assert files, "save() should have produced a JSON file"
    files[0].write_bytes(b"not valid json \x00\x01\x02")
    # load() must NOT raise; corrupt → None.
    assert events_cache.load("yfinance", "X") is None


def test_load_wrong_type_returns_none(isolated_cache_dir):
    # Drop a JSON blob that decodes to something that ISN'T an event
    # bundle. Must be ignored, not crashed on.
    cache_root = isolated_cache_dir / "events"
    cache_root.mkdir(parents=True, exist_ok=True)
    path = cache_root / "yfinance__X.json"
    path.write_text('{"not": "a bundle"}', encoding="utf-8")
    assert events_cache.load("yfinance", "X") is None


# ---------------------------------------------------------------------------
# save: atomicity + round-trip
# ---------------------------------------------------------------------------

def test_save_then_load_round_trips(isolated_cache_dir):
    bundle = _sample_bundle("AAPL")
    events_cache.save("yfinance", "AAPL", bundle)
    loaded = events_cache.load("yfinance", "AAPL")
    assert loaded is not None
    assert loaded.symbol == "AAPL"
    assert len(loaded.earnings) == 2
    assert len(loaded.dividends) == 1
    assert loaded.earnings[0].ts == 10_000
    assert loaded.dividends[0].amount == pytest.approx(0.25)


def test_save_writes_atomically_via_temp_file(isolated_cache_dir):
    bundle = _sample_bundle("X")
    events_cache.save("yfinance", "X", bundle)
    cache_root = isolated_cache_dir / "events"
    # No .tmp residue should remain after a successful save.
    tmp_residue = list(cache_root.glob("*.tmp"))
    assert tmp_residue == []
    blobs = list(cache_root.glob("*.json"))
    assert len(blobs) == 1


def test_save_stamps_fetched_at_when_zero(isolated_cache_dir):
    bundle = EventBundle(symbol="X", fetched_at=0)
    before_ms = int(time.time() * 1000)
    events_cache.save("yfinance", "X", bundle)
    after_ms = int(time.time() * 1000)
    loaded = events_cache.load("yfinance", "X")
    assert loaded is not None
    # Stamped to "now-ish" — within a 5s window of when save() ran.
    assert before_ms - 1 <= loaded.fetched_at <= after_ms + 5_000


def test_save_preserves_nonzero_fetched_at(isolated_cache_dir):
    bundle = _sample_bundle("X")
    original = bundle.fetched_at
    events_cache.save("yfinance", "X", bundle)
    loaded = events_cache.load("yfinance", "X")
    assert loaded.fetched_at == original


def test_save_swallows_oserror(monkeypatch, isolated_cache_dir):
    """save() is best-effort: a bad target dir must not raise."""
    bundle = _sample_bundle("X")
    # Monkeypatch _cache_dir to point at a path that can't be written.
    monkeypatch.setattr(events_cache, "_cache_dir",
                        lambda: isolated_cache_dir / "does" / "not" / "exist")
    # Should not raise.
    events_cache.save("yfinance", "X", bundle)


# ---------------------------------------------------------------------------
# Multi-symbol / multi-source isolation
# ---------------------------------------------------------------------------

def test_save_load_isolates_per_source_and_ticker(isolated_cache_dir):
    b_aapl_yf = _sample_bundle("AAPL")
    b_aapl_syn = _sample_bundle("AAPL")
    b_msft_yf = _sample_bundle("MSFT")
    # Tag each so we can distinguish at load time.
    b_aapl_yf.earnings[0].eps_estimate = 1.11
    b_aapl_syn.earnings[0].eps_estimate = 2.22
    b_msft_yf.earnings[0].eps_estimate = 3.33

    events_cache.save("yfinance", "AAPL", b_aapl_yf)
    events_cache.save("synthetic", "AAPL", b_aapl_syn)
    events_cache.save("yfinance", "MSFT", b_msft_yf)

    assert events_cache.load("yfinance", "AAPL").earnings[0].eps_estimate \
        == pytest.approx(1.11)
    assert events_cache.load("synthetic", "AAPL").earnings[0].eps_estimate \
        == pytest.approx(2.22)
    assert events_cache.load("yfinance", "MSFT").earnings[0].eps_estimate \
        == pytest.approx(3.33)


def test_save_handles_slash_in_ticker(isolated_cache_dir):
    """Some providers ship ticker symbols with `/` or `\\`."""
    bundle = _sample_bundle("BRK/B")
    events_cache.save("yfinance", "BRK/B", bundle)
    loaded = events_cache.load("yfinance", "BRK/B")
    assert loaded is not None
    assert loaded.symbol == "BRK/B"


# ---------------------------------------------------------------------------
# merge_bundle
# ---------------------------------------------------------------------------

def test_merge_both_none_returns_empty_bundle():
    out = events_cache.merge_bundle(None, None)
    assert isinstance(out, EventBundle)
    assert out.earnings == []
    assert out.dividends == []


def test_merge_old_none_returns_new():
    new = _sample_bundle("X")
    out = events_cache.merge_bundle(None, new)
    assert out is new


def test_merge_new_none_returns_old():
    old = _sample_bundle("X")
    out = events_cache.merge_bundle(old, None)
    assert out is old


def test_merge_new_wins_on_overlapping_ts():
    old = EventBundle(
        symbol="X",
        earnings=[
            EarningsRecord(ts=100, symbol="X", when="AMC",
                           eps_estimate=1.0, eps_actual=1.0),
            EarningsRecord(ts=200, symbol="X", when="AMC",
                           eps_estimate=2.0),
        ],
        fetched_at=10,
    )
    # New bundle has an updated estimate on ts=200 (forward row revised).
    new = EventBundle(
        symbol="X",
        earnings=[
            EarningsRecord(ts=200, symbol="X", when="AMC",
                           eps_estimate=2.5),
            EarningsRecord(ts=300, symbol="X", when="BMO",
                           eps_estimate=3.0),
        ],
        fetched_at=20,
    )
    merged = events_cache.merge_bundle(old, new)
    by_ts = {r.ts: r for r in merged.earnings}
    assert by_ts[100].eps_actual == pytest.approx(1.0)  # preserved
    assert by_ts[200].eps_estimate == pytest.approx(2.5)  # new wins
    assert 300 in by_ts  # new adds


def test_merge_sorts_output_ascending():
    old = EventBundle(
        symbol="X",
        earnings=[
            EarningsRecord(ts=500, symbol="X", when="AMC"),
            EarningsRecord(ts=100, symbol="X", when="AMC"),
        ],
        dividends=[
            DividendRecord(ex_ts=400, symbol="X", amount=0.1),
        ],
    )
    new = EventBundle(
        symbol="X",
        earnings=[
            EarningsRecord(ts=300, symbol="X", when="BMO"),
        ],
        dividends=[
            DividendRecord(ex_ts=200, symbol="X", amount=0.2),
            DividendRecord(ex_ts=600, symbol="X", amount=0.3),
        ],
    )
    merged = events_cache.merge_bundle(old, new)
    assert [r.ts for r in merged.earnings] == sorted(r.ts for r in merged.earnings)
    assert [d.ex_ts for d in merged.dividends] == sorted(
        d.ex_ts for d in merged.dividends)


def test_merge_takes_max_fetched_at():
    old = EventBundle(symbol="X", fetched_at=10)
    new = EventBundle(symbol="X", fetched_at=20)
    assert events_cache.merge_bundle(old, new).fetched_at == 20
    # And the reverse order.
    old2 = EventBundle(symbol="X", fetched_at=50)
    new2 = EventBundle(symbol="X", fetched_at=30)
    assert events_cache.merge_bundle(old2, new2).fetched_at == 50
