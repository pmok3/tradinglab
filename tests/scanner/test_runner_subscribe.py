"""Tests for ScanRunner.subscribe (entries-v1 addition)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import List, Tuple

import pytest

from tradinglab.scanner.runner import ScanResult, ScanRunner, MatchRow


def _result(scan_id: str, tick_id: int, *, rows=None, new_rows=None) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        tick_id=tick_id,
        timestamp=datetime.now(timezone.utc),
        interval="5m",
        rows=list(rows or []),
        new_rows=list(new_rows or []),
    )


def _row(symbol: str = "AAPL") -> MatchRow:
    return MatchRow(
        symbol=symbol, matched=True, values={}, rank_value=None, is_new=True,
    )


class TestSubscribeAPI:
    def test_subscribe_returns_unsub(self):
        runner = ScanRunner()
        try:
            received = []
            unsub = runner.subscribe(lambda sid, res: received.append(sid))
            assert callable(unsub)
            unsub()
            # Unsubscribing twice is harmless.
            unsub()
        finally:
            runner.shutdown()

    def test_dispatch_only_when_new_rows(self):
        runner = ScanRunner()
        try:
            received: List[Tuple[str, ScanResult]] = []
            runner.subscribe(lambda sid, res: received.append((sid, res)))

            # Empty result (no new_rows) -> no dispatch.
            results = {"scan-A": _result("scan-A", 1)}
            runner._dispatch_to_subscribers(results)
            assert received == []

            # Result with a new_row -> dispatch.
            row = _row("AAPL")
            results2 = {"scan-A": _result("scan-A", 2, rows=[row], new_rows=[row])}
            runner._dispatch_to_subscribers(results2)
            assert len(received) == 1
            assert received[0][0] == "scan-A"
            assert received[0][1].new_rows[0].symbol == "AAPL"
        finally:
            runner.shutdown()

    def test_subscriber_exception_isolated(self):
        runner = ScanRunner()
        try:
            ok = []

            def bad_sub(sid, res):
                raise RuntimeError("oops")

            def good_sub(sid, res):
                ok.append(sid)

            runner.subscribe(bad_sub)
            runner.subscribe(good_sub)

            row = _row()
            res = {"scan-A": _result("scan-A", 1, rows=[row], new_rows=[row])}
            # Should not raise.
            runner._dispatch_to_subscribers(res)
            assert ok == ["scan-A"]
        finally:
            runner.shutdown()

    def test_unsubscribe_removes_callback(self):
        runner = ScanRunner()
        try:
            received = []
            unsub = runner.subscribe(lambda sid, res: received.append(sid))
            unsub()
            row = _row()
            res = {"scan-A": _result("scan-A", 1, rows=[row], new_rows=[row])}
            runner._dispatch_to_subscribers(res)
            assert received == []
        finally:
            runner.shutdown()

    def test_multiple_subscribers_all_fired(self):
        runner = ScanRunner()
        try:
            calls = {"a": 0, "b": 0}
            runner.subscribe(lambda sid, res: calls.__setitem__("a", calls["a"] + 1))
            runner.subscribe(lambda sid, res: calls.__setitem__("b", calls["b"] + 1))
            row = _row()
            res = {"s": _result("s", 1, rows=[row], new_rows=[row])}
            runner._dispatch_to_subscribers(res)
            assert calls == {"a": 1, "b": 1}
        finally:
            runner.shutdown()

    def test_dispatch_on_caller_thread(self):
        """Subscriber runs on the thread that calls _dispatch_to_subscribers
        (not on a worker thread)."""
        runner = ScanRunner()
        try:
            seen_thread: List[int] = []
            runner.subscribe(lambda sid, res: seen_thread.append(threading.get_ident()))
            row = _row()
            res = {"s": _result("s", 1, rows=[row], new_rows=[row])}
            runner._dispatch_to_subscribers(res)
            assert seen_thread == [threading.get_ident()]
        finally:
            runner.shutdown()
