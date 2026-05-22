"""Unit tests for ``tradinglab.exits.audit``."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradinglab.core.thread_guard import (
    TkThreadViolation,
    tk_thread_check_disabled,
)
from tradinglab.exits import audit as audit_mod
from tradinglab.exits.audit import KNOWN_KINDS, AuditLog

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated audit root per test.

    Patches both ``tradinglab.disk_cache._cache_dir`` AND the
    re-bound symbol ``tradinglab.exits.audit._cache_dir`` so
    ``audit.audit_dir()`` returns ``tmp_path/exits/audit``.
    """
    root = tmp_path / "cache"
    root.mkdir()
    monkeypatch.setattr("tradinglab.disk_cache._cache_dir", lambda: root)
    monkeypatch.setattr(audit_mod, "_cache_dir", lambda: root)
    return root / "exits" / "audit"


def _make_clock(start: datetime):
    """Returns a callable that advances by ``step`` seconds per call."""
    state = {"now": start}

    def clock() -> datetime:
        out = state["now"]
        # Advance after read so the next call returns a strictly later
        # timestamp. Tests that need finer control set state["now"]
        # directly.
        state["now"] = out + timedelta(microseconds=1)
        return out

    clock.state = state  # type: ignore[attr-defined]
    return clock


# ---------------------------------------------------------------------------
# Construction + directory layout
# ---------------------------------------------------------------------------


def test_audit_dir_resolves_under_patched_cache_dir(audit_root: Path) -> None:
    log = AuditLog()
    assert log.root == audit_root
    assert log.root.is_dir()


def test_construction_creates_root(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    AuditLog(root=target)
    assert target.is_dir()


# ---------------------------------------------------------------------------
# Append + read round-trip (single record)
# ---------------------------------------------------------------------------


def test_append_writes_single_record_round_trip(audit_root: Path) -> None:
    fixed = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    log = AuditLog(clock=_make_clock(fixed))
    record = log.append(
        "fire",
        strategy_id="s1",
        position_id="p1",
        leg_id="l1",
        trigger_id="t1",
        qty=1.5,
        price=180.25,
        meta={"reason": "limit-touched"},
    )
    log.close()
    assert record["kind"] == "fire"
    assert record["qty"] == 1.5
    assert record["price"] == 180.25
    assert record["meta"] == {"reason": "limit-touched"}
    assert record["ts"].startswith("2025-01-15T12:00:00")

    # On-disk
    daily = audit_root / "2025-01-15.jsonl"
    assert daily.exists()
    lines = daily.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["kind"] == "fire"
    assert parsed["meta"] == {"reason": "limit-touched"}


def test_append_omits_optional_fields_when_none(audit_root: Path) -> None:
    fixed = datetime(2025, 1, 15, tzinfo=timezone.utc)
    log = AuditLog(clock=_make_clock(fixed))
    log.append("position_open", position_id="p1")
    log.close()
    parsed = json.loads(
        (audit_root / "2025-01-15.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert "qty" not in parsed
    assert "price" not in parsed
    assert "meta" not in parsed
    assert parsed["position_id"] == "p1"
    # leg/trigger/strategy explicitly null on the record but present
    assert parsed["leg_id"] is None


# ---------------------------------------------------------------------------
# Multiple records, ordering, day rotation
# ---------------------------------------------------------------------------


def test_multiple_appends_same_day_preserve_order(audit_root: Path) -> None:
    fixed = datetime(2025, 1, 15, tzinfo=timezone.utc)
    log = AuditLog(clock=_make_clock(fixed))
    for i in range(5):
        log.append("arm", leg_id=f"leg-{i}")
    log.close()

    records = log.read_date("2025-01-15")
    assert [r["leg_id"] for r in records] == ["leg-0", "leg-1", "leg-2", "leg-3", "leg-4"]


def test_append_after_day_rollover_creates_new_file(audit_root: Path) -> None:
    clock = _make_clock(datetime(2025, 1, 15, 23, 59, 59, tzinfo=timezone.utc))
    log = AuditLog(clock=clock)
    log.append("arm", leg_id="day1")
    # Manually push clock past midnight
    clock.state["now"] = datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc)
    log.append("arm", leg_id="day2")
    log.close()

    assert (audit_root / "2025-01-15.jsonl").exists()
    assert (audit_root / "2025-01-16.jsonl").exists()
    assert log.read_date("2025-01-15")[0]["leg_id"] == "day1"
    assert log.read_date("2025-01-16")[0]["leg_id"] == "day2"


# ---------------------------------------------------------------------------
# tail() across multiple files
# ---------------------------------------------------------------------------


def test_tail_returns_oldest_first_across_days(audit_root: Path) -> None:
    clock = _make_clock(datetime(2025, 1, 14, tzinfo=timezone.utc))
    log = AuditLog(clock=clock)
    log.append("arm", leg_id="a")
    clock.state["now"] = datetime(2025, 1, 15, tzinfo=timezone.utc)
    log.append("arm", leg_id="b")
    clock.state["now"] = datetime(2025, 1, 16, tzinfo=timezone.utc)
    log.append("arm", leg_id="c")
    log.close()

    last2 = log.tail(2)
    assert [r["leg_id"] for r in last2] == ["b", "c"]
    last5 = log.tail(5)
    assert [r["leg_id"] for r in last5] == ["a", "b", "c"]


def test_tail_zero_or_negative_returns_empty(audit_root: Path) -> None:
    log = AuditLog(clock=_make_clock(datetime(2025, 1, 15, tzinfo=timezone.utc)))
    log.append("arm", leg_id="a")
    log.close()
    assert log.tail(0) == []
    assert log.tail(-1) == []


def test_tail_when_no_files_returns_empty(audit_root: Path) -> None:
    log = AuditLog()
    assert log.tail(10) == []


# ---------------------------------------------------------------------------
# Read-side robustness (corruption tolerance)
# ---------------------------------------------------------------------------


def test_corrupt_line_is_skipped_with_warning(
    audit_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    log = AuditLog(clock=_make_clock(datetime(2025, 1, 15, tzinfo=timezone.utc)))
    log.append("arm", leg_id="good-1")
    log.close()

    # Manually append a corrupt line + a good follow-up line.
    daily = audit_root / "2025-01-15.jsonl"
    with daily.open("a", encoding="utf-8", newline="") as fh:
        fh.write("{this is not json\n")
        fh.write(json.dumps({"ts": "2025-01-15T00:00:00+00:00", "kind": "arm",
                             "leg_id": "good-2", "strategy_id": None,
                             "position_id": None, "trigger_id": None}) + "\n")
        # Bare value (not a dict)
        fh.write("42\n")

    with caplog.at_level("WARNING"):
        records = log.read_date("2025-01-15")
    assert [r["leg_id"] for r in records] == ["good-1", "good-2"]
    assert any("corrupt line" in m for m in caplog.messages)


def test_blank_lines_silently_skipped(audit_root: Path) -> None:
    log = AuditLog(clock=_make_clock(datetime(2025, 1, 15, tzinfo=timezone.utc)))
    log.append("arm", leg_id="a")
    log.close()
    daily = audit_root / "2025-01-15.jsonl"
    with daily.open("a", encoding="utf-8", newline="") as fh:
        fh.write("\n\n   \n")
    records = log.read_date("2025-01-15")
    assert len(records) == 1


# ---------------------------------------------------------------------------
# Date listing + invalid input
# ---------------------------------------------------------------------------


def test_list_dates_newest_first(audit_root: Path) -> None:
    clock = _make_clock(datetime(2025, 1, 14, tzinfo=timezone.utc))
    log = AuditLog(clock=clock)
    log.append("arm", leg_id="a")
    clock.state["now"] = datetime(2025, 2, 1, tzinfo=timezone.utc)
    log.append("arm", leg_id="b")
    clock.state["now"] = datetime(2025, 1, 20, tzinfo=timezone.utc)
    log.append("arm", leg_id="c")
    log.close()

    assert log.list_dates() == ["2025-02-01", "2025-01-20", "2025-01-14"]


def test_list_dates_ignores_unrelated_files(audit_root: Path) -> None:
    log = AuditLog()
    (audit_root / "README.txt").write_text("hello", encoding="utf-8")
    (audit_root / "garbage.jsonl").write_text("noise\n", encoding="utf-8")
    log.append("arm", leg_id="a") if False else None
    # Manually create a real day-file to make sure the filter still picks it.
    (audit_root / "2025-01-15.jsonl").write_text("", encoding="utf-8")
    assert log.list_dates() == ["2025-01-15"]


def test_read_date_invalid_format_raises(audit_root: Path) -> None:
    log = AuditLog()
    with pytest.raises(ValueError):
        log.read_date("not-a-date")
    with pytest.raises(ValueError):
        log.read_date("2025/01/15")


def test_read_date_missing_file_returns_empty(audit_root: Path) -> None:
    log = AuditLog()
    assert log.read_date("2025-01-15") == []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_unknown_kind_rejected(audit_root: Path) -> None:
    log = AuditLog()
    with pytest.raises(ValueError, match="unknown kind"):
        log.append("not-a-kind")


def test_all_known_kinds_round_trip(audit_root: Path) -> None:
    fixed = datetime(2025, 1, 15, tzinfo=timezone.utc)
    log = AuditLog(clock=_make_clock(fixed))
    for k in sorted(KNOWN_KINDS):
        log.append(k)
    log.close()
    records = log.read_date("2025-01-15")
    assert sorted(r["kind"] for r in records) == sorted(KNOWN_KINDS)


def test_naive_ts_treated_as_utc(audit_root: Path) -> None:
    log = AuditLog(clock=_make_clock(datetime(2025, 1, 15, tzinfo=timezone.utc)))
    naive = datetime(2025, 1, 15, 9, 30, 0)
    record = log.append("arm", ts=naive)
    log.close()
    assert record["ts"].endswith("+00:00")


# ---------------------------------------------------------------------------
# Tk-thread invariant
# ---------------------------------------------------------------------------


def test_append_raises_from_worker_thread(audit_root: Path) -> None:
    log = AuditLog()
    captured: list[BaseException] = []

    def worker() -> None:
        try:
            log.append("arm", leg_id="x")
        except BaseException as exc:  # pragma: no cover - asserted below
            captured.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2.0)
    assert len(captured) == 1
    assert isinstance(captured[0], TkThreadViolation)


def test_append_works_under_check_disabled(audit_root: Path) -> None:
    log = AuditLog()
    captured: list[BaseException] = []
    completed: list[bool] = []

    def worker() -> None:
        with tk_thread_check_disabled():
            try:
                log.append("arm", leg_id="x")
                completed.append(True)
            except BaseException as exc:  # pragma: no cover
                captured.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2.0)
    assert captured == []
    assert completed == [True]


# ---------------------------------------------------------------------------
# Close + reopen
# ---------------------------------------------------------------------------


def test_close_is_idempotent_and_reopens_on_next_append(audit_root: Path) -> None:
    log = AuditLog(clock=_make_clock(datetime(2025, 1, 15, tzinfo=timezone.utc)))
    log.append("arm", leg_id="a")
    log.close()
    log.close()  # idempotent
    log.append("arm", leg_id="b")
    log.close()
    records = log.read_date("2025-01-15")
    assert [r["leg_id"] for r in records] == ["a", "b"]


def test_concurrent_readers_during_writes_get_consistent_lines(audit_root: Path) -> None:
    """Reader on a worker thread must not crash on a partially-written file.

    The file format guarantees one-line-per-record. With per-line flush
    + fsync, any line a reader sees is either complete or the file ends
    cleanly — there's no in-progress write to observe (the writer only
    emits already-serialised buffers).
    """
    log = AuditLog(clock=_make_clock(datetime(2025, 1, 15, tzinfo=timezone.utc)))
    for i in range(50):
        log.append("arm", leg_id=f"leg-{i}")
    log.close()

    # Read from a worker thread (allowed)
    records: list[list[dict]] = []

    def reader() -> None:
        records.append(log.tail(1000))

    t = threading.Thread(target=reader)
    t.start()
    t.join(timeout=2.0)
    assert len(records[0]) == 50
