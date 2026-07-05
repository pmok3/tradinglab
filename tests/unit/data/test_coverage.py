"""Unit tests for data/coverage.py (fetch-coverage sidecar)."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from tradinglab.data import coverage as C

# ---------------------------------------------------------------------------
# Interval arithmetic (pure)
# ---------------------------------------------------------------------------


def test_merge_segments_overlap_and_adjacent():
    assert C._merge_segments([(10, 20), (15, 25), (25, 30), (40, 50)]) == [(10, 30), (40, 50)]


def test_merge_segments_drops_degenerate():
    assert C._merge_segments([(10, 10), (30, 20), (5, 8)]) == [(5, 8)]


def test_missing_ranges_empty_record():
    rec = C.CoverageRecord()
    assert C.missing_ranges(rec, 100, 200) == [(100, 200)]
    assert C.covered(rec, 100, 200) is False


def test_missing_ranges_partial_and_gaps():
    rec = C.CoverageRecord(segments=[(100, 150), (170, 200)])
    assert C.missing_ranges(rec, 120, 190) == [(150, 170)]
    assert C.missing_ranges(rec, 90, 210) == [(90, 100), (150, 170), (200, 210)]


def test_covered_full_range():
    rec = C.CoverageRecord(segments=[(100, 300)])
    assert C.covered(rec, 120, 280) is True
    assert C.missing_ranges(rec, 120, 280) == []


def test_missing_ranges_degenerate_inputs():
    rec = C.CoverageRecord(segments=[(100, 200)])
    assert C.missing_ranges(rec, 200, 100) == []
    assert C.missing_ranges(rec, 150, 150) == []


# ---------------------------------------------------------------------------
# record_fetch + watermark learning
# ---------------------------------------------------------------------------


def test_record_fetch_adds_and_is_idempotent(tmp_path):
    src, tkr, itv = "alpaca", "AAPL", "5m"
    assert C.record_fetch(src, tkr, itv, 1000, 2000, 1000, 2000, root=tmp_path).segments == [
        (1000, 2000)
    ]
    # same range again → no growth
    assert C.record_fetch(src, tkr, itv, 1000, 2000, 1000, 2000, root=tmp_path).segments == [
        (1000, 2000)
    ]
    # overlapping request extends the segment
    assert C.record_fetch(src, tkr, itv, 1500, 2500, 1500, 2500, root=tmp_path).segments == [
        (1000, 2500)
    ]


def test_record_fetch_learns_data_start_watermark(tmp_path):
    req_start = 1_000_000
    returned_start = req_start + 30 * 86_400  # 30 days later than requested
    rec = C.record_fetch(
        "alpaca", "MSFT", "5m", req_start, req_start + 60 * 86_400,
        returned_start, req_start + 60 * 86_400, root=tmp_path,
    )
    assert rec.data_start_ts == returned_start
    assert rec.exhausted_start is True
    assert C.data_start(rec) == returned_start


def test_record_fetch_no_watermark_within_margin(tmp_path):
    # A weekend-sized gap at the left edge must NOT be mistaken for exhaustion.
    req_start = 1_000_000
    returned_start = req_start + 2 * 86_400
    rec = C.record_fetch(
        "alpaca", "NVDA", "5m", req_start, req_start + 10 * 86_400,
        returned_start, req_start + 10 * 86_400, root=tmp_path,
    )
    assert rec.data_start_ts is None
    assert rec.exhausted_start is False


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_load_save_round_trip(tmp_path):
    rec = C.CoverageRecord(
        data_start_ts=555, exhausted_start=True, segments=[(10, 20), (30, 40)]
    )
    C.save("alpaca", "AMD", "1m", rec, root=tmp_path)
    got = C.load("alpaca", "AMD", "1m", root=tmp_path)
    assert got.data_start_ts == 555
    assert got.exhausted_start is True
    assert got.segments == [(10, 20), (30, 40)]


def test_load_missing_returns_empty(tmp_path):
    got = C.load("alpaca", "NONE", "5m", root=tmp_path)
    assert got.segments == [] and got.data_start_ts is None


def test_load_corrupt_returns_empty(tmp_path):
    C._coverage_path("alpaca", "BAD", "5m", root=tmp_path).write_text(
        "{ not json", encoding="utf-8"
    )
    assert C.load("alpaca", "BAD", "5m", root=tmp_path).segments == []


def test_coverage_path_sanitizes_ticker(tmp_path):
    p = C._coverage_path("alpaca", "BRK/B", "5m", root=tmp_path)
    assert p.name == "alpaca__BRK_B__5m.coverage.json"


def test_bootstrap_from_cache(monkeypatch, tmp_path):
    def _c(ts):
        return SimpleNamespace(date=dt.datetime.fromtimestamp(ts, dt.timezone.utc))

    import tradinglab.disk_cache as dc
    monkeypatch.setattr(dc, "load", lambda _s, _t, _i: [_c(1000), _c(2000), _c(3000)])
    rec = C.bootstrap_from_cache("alpaca", "AAPL", "5m", root=tmp_path)
    assert rec.segments == [(1000, 3001)]  # half-open, inclusive of last bar
