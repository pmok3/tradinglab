"""Tests for tradinglab.entries.audit — JSONL append-only audit log."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tradinglab.core import thread_guard
from tradinglab.entries.audit import KNOWN_KINDS, AuditLog


@pytest.fixture(autouse=True)
def _no_tk(monkeypatch):
    """Disable Tk-thread enforcement for these tests."""
    with thread_guard.tk_thread_check_disabled():
        yield


@pytest.fixture
def log(tmp_path: Path) -> AuditLog:
    return AuditLog(root=tmp_path / "entries_audit")


class TestAppend:
    def test_round_trip_basic(self, log, tmp_path):
        rec = log.append(
            "entry_fire",
            strategy_id="s-1",
            symbol="AAPL",
            trigger_id="t-1",
            qty=100,
            price=150.0,
        )
        assert rec["kind"] == "entry_fire"
        assert rec["symbol"] == "AAPL"
        assert rec["qty"] == 100.0

        # Read it back via tail.
        tail = log.tail(1)
        assert len(tail) == 1
        assert tail[0]["kind"] == "entry_fire"
        assert tail[0]["symbol"] == "AAPL"
        log.close()

    def test_unknown_kind_raises(self, log):
        with pytest.raises(ValueError, match="unknown kind"):
            log.append("not_a_real_kind", strategy_id="s-1")
        log.close()

    def test_all_known_kinds_accepted(self, log):
        for kind in KNOWN_KINDS:
            log.append(kind, strategy_id="s-1")
        assert len(log.tail(100)) == len(KNOWN_KINDS)
        log.close()

    def test_meta_persisted(self, log):
        log.append("entry_blocked", strategy_id="s", meta={"gate": "max_concurrent"})
        rec = log.tail(1)[0]
        assert rec["meta"] == {"gate": "max_concurrent"}
        log.close()

    def test_explicit_ts_used(self, log):
        when = datetime(2024, 1, 15, 9, 35, tzinfo=timezone.utc)
        log.append("entry_arm", strategy_id="s", ts=when)
        rec = log.tail(1)[0]
        assert rec["ts"].startswith("2024-01-15T09:35")
        log.close()

    def test_naive_ts_assumed_utc(self, log):
        when = datetime(2024, 1, 15, 9, 35)  # naive
        log.append("entry_arm", strategy_id="s", ts=when)
        rec = log.tail(1)[0]
        assert "+00:00" in rec["ts"] or "Z" in rec["ts"]
        log.close()


class TestRotation:
    def test_per_day_files(self, log, monkeypatch):
        days = [
            datetime(2024, 1, 1, 12, tzinfo=timezone.utc),
            datetime(2024, 1, 2, 12, tzinfo=timezone.utc),
            datetime(2024, 1, 3, 12, tzinfo=timezone.utc),
        ]
        for d in days:
            log.append("entry_arm", strategy_id="s", ts=d)
        log.close()
        files = sorted(log.root.iterdir())
        assert len(files) == 3
        assert {f.name for f in files} == {
            "2024-01-01.jsonl", "2024-01-02.jsonl", "2024-01-03.jsonl",
        }

    def test_list_dates_newest_first(self, log):
        for d in [datetime(2024, 1, 1, 12, tzinfo=timezone.utc),
                  datetime(2024, 1, 5, 12, tzinfo=timezone.utc),
                  datetime(2024, 1, 3, 12, tzinfo=timezone.utc)]:
            log.append("entry_arm", strategy_id="s", ts=d)
        log.close()
        assert log.list_dates() == ["2024-01-05", "2024-01-03", "2024-01-01"]

    def test_read_date_filters(self, log):
        for d in [datetime(2024, 1, 1, 12, tzinfo=timezone.utc),
                  datetime(2024, 1, 2, 12, tzinfo=timezone.utc)]:
            log.append("entry_arm", strategy_id="s", ts=d)
        log.close()
        recs = log.read_date("2024-01-01")
        assert len(recs) == 1

    def test_read_date_bad_format_raises(self, log):
        with pytest.raises(ValueError, match="invalid date string"):
            log.read_date("not-a-date")
        log.close()


class TestCorruption:
    def test_corrupt_line_skipped(self, log, tmp_path):
        log.append("entry_arm", strategy_id="s")
        log.close()
        # Inject a corrupt line manually.
        f = next(log.root.iterdir())
        with f.open("a", encoding="utf-8") as fh:
            fh.write("this is not json\n")
            fh.write('{"kind": "entry_disarm", "strategy_id": "s2", "ts": "2024-01-01T00:00:00+00:00"}\n')
        log2 = AuditLog(root=log.root)
        tail = log2.tail(10)
        log2.close()
        # The corrupt line is skipped; the two valid records survive.
        assert len(tail) == 2
        assert tail[1]["strategy_id"] == "s2"


class TestTail:
    def test_zero_returns_empty(self, log):
        log.append("entry_arm", strategy_id="s")
        assert log.tail(0) == []
        log.close()

    def test_tail_oldest_first_within_n(self, log):
        for i in range(5):
            log.append("entry_arm", strategy_id=f"s-{i}")
        out = log.tail(3)
        log.close()
        assert [r["strategy_id"] for r in out] == ["s-2", "s-3", "s-4"]
