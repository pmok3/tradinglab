"""Tests for forming-bar (intrabar) evaluation in :class:`ScanRunner`.

Phase 1 of the live-tick slice. Validates:

* :class:`MatchHistory` semantics: ``forming=True`` never sets
  ``is_new`` and never mutates committed state. Closed bars own
  promotion/clearing.
* :meth:`ScanRunner.run` ``last_bar_forming`` kwarg propagates to
  :attr:`MatchRow.is_forming` and to history.
* The reconcile path uses :meth:`BarsBuffer.update_last` (not full
  rebuild) when a same-id same-length tick arrives with
  ``forming=True``. Counter ``forming_updates`` ticks; ``buffer_rebuilds``
  does not.
* The richer last-bar fingerprint (full OHLCV) detects a volume-only
  change with the same close — important for RVOL-style indicators.
* Default behavior (``last_bar_forming=False``) is unchanged.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import List

import tradinglab.indicators  # noqa: F401
from tradinglab.models import Candle
from tradinglab.scanner.model import (
    Condition,
    FieldRef,
    Group,
    OP_GT,
    ScanDefinition,
    UniverseFilter,
)
from tradinglab.scanner.runner import MatchHistory, ScanRunner


# ----- helpers ---------------------------------------------------------------


def _candles(n: int, *, start_close: float = 100.0) -> List[Candle]:
    base = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)
    out: List[Candle] = []
    for i in range(n):
        c = start_close + i
        out.append(Candle(
            date=base + timedelta(minutes=i),
            open=c - 0.5, high=c + 1.0, low=c - 1.0, close=c,
            volume=1000.0 + i, session="regular",
        ))
    return out


def _scan_close_gt(threshold: float) -> ScanDefinition:
    return ScanDefinition(
        name="close_gt",
        primary_interval="1m",
        universe_filter=UniverseFilter.all(),
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(threshold)},
                      interval="1m"),
        ]),
    )


# ----- MatchHistory semantics ------------------------------------------------


def test_history_forming_true_never_sets_is_new():
    h = MatchHistory()
    # 5 successive forming ticks on the same un-confirmed match.
    for tick in range(5):
        assert h.update("AAA", tick_id=tick, matched=True, forming=True) is False
    # Committed state untouched.
    assert "AAA" not in h.last_matched
    assert "AAA" not in h.last_matched_tick


def test_history_forming_match_then_close_fires_is_new_once():
    h = MatchHistory()
    # Provisional matches over multiple forming ticks…
    h.update("AAA", tick_id=1, matched=True, forming=True)
    h.update("AAA", tick_id=2, matched=True, forming=True)
    # …followed by the closed bar — THIS is the only is_new=True.
    assert h.update("AAA", tick_id=3, matched=True, forming=False) is True
    # Subsequent closed True ticks don't refire.
    assert h.update("AAA", tick_id=4, matched=True, forming=False) is False


def test_history_forming_match_then_close_false_no_state_change():
    """Provisional match that fails to confirm: history stays empty."""
    h = MatchHistory()
    h.update("AAA", tick_id=1, matched=True, forming=True)
    # Bar closes False (didn't actually match at close).
    assert h.update("AAA", tick_id=2, matched=False, forming=False) is False
    # Committed: explicitly False (not "never seen") because closed False ticks set it.
    assert h.last_matched.get("AAA") is False


def test_history_committed_true_then_forming_swing_no_renew():
    """Once committed True, subsequent provisional ticks must not refire is_new."""
    h = MatchHistory()
    assert h.update("AAA", tick_id=1, matched=True, forming=False) is True
    # Provisional ticks oscillate.
    assert h.update("AAA", tick_id=2, matched=False, forming=True) is False
    assert h.update("AAA", tick_id=3, matched=True, forming=True) is False
    # Closed True again: not is_new (still committed True).
    assert h.update("AAA", tick_id=4, matched=True, forming=False) is False


def test_history_committed_true_then_close_false_clears():
    h = MatchHistory()
    h.update("AAA", tick_id=1, matched=True, forming=False)
    assert h.last_matched["AAA"] is True
    h.update("AAA", tick_id=2, matched=False, forming=False)
    assert h.last_matched["AAA"] is False


# ----- run() plumbing --------------------------------------------------------


def test_run_default_unchanged_no_is_forming():
    """``last_bar_forming`` defaults False; rows have ``is_forming=False``."""
    runner = ScanRunner()
    try:
        scans = [_scan_close_gt(102.0)]
        candles = _candles(5)
        # Last close = 104, matches.
        results = runner.run(
            scans=scans, candles_by_symbol={"AAA": candles},
            interval="1m", tick_id=1,
        )
        rows = results[scans[0].id].rows
        assert len(rows) == 1
        assert rows[0].is_forming is False
        assert rows[0].matched is True
        assert rows[0].is_new is True
        assert rows[0] in results[scans[0].id].new_rows
    finally:
        runner.shutdown()


def test_run_forming_true_tags_rows_no_new_rows():
    """Forming ticks emit rows with ``is_forming=True`` and never populate ``new_rows``."""
    runner = ScanRunner()
    try:
        scans = [_scan_close_gt(102.0)]
        candles = _candles(5)
        # First forming tick on a never-seen symbol with matched=True.
        results = runner.run(
            scans=scans, candles_by_symbol={"AAA": candles},
            interval="1m", tick_id=1, last_bar_forming=True,
        )
        sr = results[scans[0].id]
        assert sr.rows[0].is_forming is True
        assert sr.rows[0].matched is True
        assert sr.rows[0].is_new is False
        assert sr.new_rows == []
    finally:
        runner.shutdown()


def test_run_forming_then_close_fires_new_once():
    """Stream of forming ticks → final closed tick is the one that promotes."""
    runner = ScanRunner()
    try:
        scans = [_scan_close_gt(102.0)]
        candles = _candles(5)
        # Forming wobble: same length, varying last close.
        for tick, last_close in enumerate([103.5, 103.8, 104.0, 103.9], start=1):
            candles[-1] = replace(candles[-1], close=last_close)
            r = runner.run(
                scans=scans, candles_by_symbol={"AAA": candles},
                interval="1m", tick_id=tick, last_bar_forming=True,
            )
            sr = r[scans[0].id]
            assert sr.rows[0].is_forming is True
            assert sr.new_rows == []
        # Closed bar: same length, last_bar_forming=False.
        r = runner.run(
            scans=scans, candles_by_symbol={"AAA": candles},
            interval="1m", tick_id=99, last_bar_forming=False,
        )
        sr = r[scans[0].id]
        assert sr.rows[0].is_forming is False
        assert sr.rows[0].is_new is True
        assert len(sr.new_rows) == 1
    finally:
        runner.shutdown()


# ----- reconcile fast path: update_last ---------------------------------------


def test_reconcile_forming_uses_update_last_not_rebuild():
    """Same-id same-length forming tick → ``update_last``, not full rebuild."""
    runner = ScanRunner()
    try:
        scans = [_scan_close_gt(102.0)]
        candles = _candles(5)
        # First run: cold build (1 rebuild).
        runner.run(scans=scans, candles_by_symbol={"AAA": candles},
                   interval="1m", tick_id=1)
        s0 = runner.stats()
        assert s0["buffer_rebuilds"] == 1
        assert s0["forming_updates"] == 0
        assert s0["buffer_appends"] == 0

        # Mutate last bar in place (same length); call with forming=True.
        candles[-1] = replace(candles[-1], close=999.0, volume=42000.0)
        runner.run(scans=scans, candles_by_symbol={"AAA": candles},
                   interval="1m", tick_id=2, last_bar_forming=True)
        s1 = runner.stats()
        # No additional rebuild, exactly one forming update.
        assert s1["buffer_rebuilds"] == 1
        assert s1["forming_updates"] == 1
        assert s1["buffer_appends"] == 0
    finally:
        runner.shutdown()


def test_reconcile_forming_false_falls_back_to_rebuild_on_last_bar_mutation():
    """Without the forming flag, a same-len last-bar mutation still rebuilds."""
    runner = ScanRunner()
    try:
        scans = [_scan_close_gt(102.0)]
        candles = _candles(5)
        runner.run(scans=scans, candles_by_symbol={"AAA": candles},
                   interval="1m", tick_id=1)
        candles[-1] = replace(candles[-1], close=999.0)
        runner.run(scans=scans, candles_by_symbol={"AAA": candles},
                   interval="1m", tick_id=2, last_bar_forming=False)
        s = runner.stats()
        assert s["buffer_rebuilds"] == 2
        assert s["forming_updates"] == 0
    finally:
        runner.shutdown()


def test_fingerprint_detects_volume_only_change():
    """Same close+ts but different volume must NOT register as a memo reuse."""
    runner = ScanRunner()
    try:
        scans = [_scan_close_gt(102.0)]
        candles = _candles(5)
        runner.run(scans=scans, candles_by_symbol={"AAA": candles},
                   interval="1m", tick_id=1)
        s0 = runner.stats()
        # Mutate volume only — close, ts, OHL identical to prior.
        candles[-1] = replace(candles[-1], volume=99999.0)
        runner.run(scans=scans, candles_by_symbol={"AAA": candles},
                   interval="1m", tick_id=2, last_bar_forming=True)
        s1 = runner.stats()
        # memo_reuses must NOT have ticked (fingerprint changed).
        assert s1["memo_reuses"] == s0["memo_reuses"]
        assert s1["forming_updates"] == 1
    finally:
        runner.shutdown()


def test_stats_text_format():
    runner = ScanRunner()
    try:
        # Empty: zero denominator handled.
        assert "reuse 0%" in runner.stats_text()
        scans = [_scan_close_gt(102.0)]
        candles = _candles(5)
        runner.run(scans=scans, candles_by_symbol={"AAA": candles},
                   interval="1m", tick_id=1)
        # Identical re-run: fingerprint match → memo_reuses ticks.
        runner.run(scans=scans, candles_by_symbol={"AAA": candles},
                   interval="1m", tick_id=2)
        text = runner.stats_text()
        assert "reuse" in text and "%" in text
        assert "appends" in text and "rebuilds" in text and "forming" in text
        # Sanity: 1 rebuild + 1 reuse → 50%.
        assert "50%" in text
    finally:
        runner.shutdown()
