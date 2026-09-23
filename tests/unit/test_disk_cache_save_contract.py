"""``disk_cache.save`` failure contract + volume hardening.

Regression tests for the P0 where every write failure was swallowed by
``except Exception: pass`` (callers could never tell success from
failure) and a single NaN/Inf volume aborted the *entire* write via
``int(c.volume)`` — one bad bar lost the whole cache entry.

Contract under test:

* ``save()`` returns ``True`` when the bars landed on disk (or when
  there was nothing to persist: BYOD / ratio-ticker no-ops) and
  ``False`` on write failure — and logs the failure.
* Non-finite volumes (NaN/Inf) are coerced to 0 at serialisation time,
  so one poison bar can no longer abort the write.
* Downstream ordering: a failed overwrite leaves the previous complete
  file intact (temp-file + ``os.replace`` atomicity — a mid-write crash
  is never served as a torn/phantom series); a successful write after a
  failure recovers fully.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import os

import pytest

from tradinglab import disk_cache
from tradinglab.models import Candle


@pytest.fixture()
def _cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("tradinglab.disk_cache._cache_dir", lambda: tmp_path)
    return tmp_path


def _candles(n: int, *, start_day: int = 1, volume=100) -> list[Candle]:
    out = []
    for i in range(n):
        d = _dt.datetime(2026, 6, start_day + i, 14, 30, tzinfo=_dt.timezone.utc)
        out.append(Candle(date=d, open=10.0 + i, high=11.0 + i, low=9.0 + i,
                          close=10.5 + i, volume=volume))
    return out


KEY = ("yfinance", "AMD", "5m")


def _cache_file(cache_dir, key=KEY) -> os.PathLike[str]:
    return cache_dir / f"{key[0]}__{key[1]}__{key[2]}.jsonl"


# ---------------------------------------------------------------------------
# 1. Failure is observable and distinguishable from success
# ---------------------------------------------------------------------------


def test_save_returns_true_and_round_trips(_cache_dir) -> None:
    assert disk_cache.save(*KEY, _candles(3)) is True
    got = disk_cache.load(*KEY)
    assert got is not None and len(got) == 3
    assert [c.volume for c in got] == [100, 100, 100]


def test_save_returns_false_and_logs_on_os_error(_cache_dir, monkeypatch, caplog) -> None:
    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("tradinglab.disk_cache.tempfile.mkstemp", _boom)
    with caplog.at_level(logging.WARNING, logger="tradinglab.disk_cache"):
        assert disk_cache.save(*KEY, _candles(3)) is False
    assert any("disk_cache.save failed" in r.message for r in caplog.records)
    # Nothing was persisted — a later read must not see phantom data.
    assert disk_cache.load(*KEY) is None


def test_save_noop_kinds_return_true(_cache_dir) -> None:
    # BYOD sources and derived ratio tickers have nothing to persist;
    # that is success, not failure.
    disk_cache.mark_no_persist("byod")
    try:
        assert disk_cache.save("byod", "AMD", "5m", _candles(2)) is True
    finally:
        disk_cache.unmark_no_persist("byod")
    assert disk_cache.save("yfinance", "AAPL/MSFT", "5m", _candles(2)) is True
    assert list(_cache_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# 2. NaN/Inf volume can no longer abort the whole write
# ---------------------------------------------------------------------------


def test_nan_volume_coerced_whole_series_persists(_cache_dir) -> None:
    bars = _candles(3)
    bars[1] = Candle(date=bars[1].date, open=11.0, high=12.0, low=10.0,
                     close=11.5, volume=float("nan"))
    assert disk_cache.save(*KEY, bars) is True
    got = disk_cache.load(*KEY)
    assert got is not None and len(got) == 3
    assert [c.volume for c in got] == [100, 0, 100]


def test_inf_volume_coerced(_cache_dir) -> None:
    bars = _candles(2)
    bars[0] = Candle(date=bars[0].date, open=10.0, high=11.0, low=9.0,
                     close=10.5, volume=float("inf"))
    bars[1] = Candle(date=bars[1].date, open=11.0, high=12.0, low=10.0,
                     close=11.5, volume=float("-inf"))
    assert disk_cache.save(*KEY, bars) is True
    got = disk_cache.load(*KEY)
    assert [c.volume for c in got] == [0, 0]


def test_volume_coercion_table(_cache_dir) -> None:
    # None / junk / numeric strings keep their historical mapping;
    # only non-finite floats changed (they used to raise).
    cases = [
        (None, 0), ("junk", 0), ("123", 123), ("45.9", 45), (45.9, 45),
        (2**53 + 1, 2**53 + 1), (10**400, 10**400),
        (str(2**53 + 1), 2**53 + 1),
    ]
    for raw, expected in cases:
        bars = _candles(1)
        bars[0] = Candle(date=bars[0].date, open=1.0, high=2.0, low=0.5,
                         close=1.5, volume=raw)
        assert disk_cache.save(*KEY, bars) is True
        assert disk_cache.load(*KEY)[0].volume == expected


def test_volume_normalization_produces_strict_json(_cache_dir) -> None:
    bars = _candles(3)
    for bar, volume in zip(bars, [float("nan"), float("inf"), float("-inf")], strict=True):
        bar.volume = volume
    assert disk_cache.save(*KEY, bars) is True
    lines = _cache_file(_cache_dir).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["v"] for line in lines] == [0, 0, 0]
    assert all("NaN" not in line and "Infinity" not in line for line in lines)


# ---------------------------------------------------------------------------
# 3. Downstream ordering: failure → read, success-after-failure
# ---------------------------------------------------------------------------


def test_failed_overwrite_leaves_previous_file_intact(_cache_dir, monkeypatch) -> None:
    """Mid-write crash must not serve a torn series as fresh.

    The write goes to a temp file + ``os.replace``; if serialisation
    blows up on bar 2 of 5, the previous complete file must be
    byte-identical afterwards and ``load`` must return the old bars.
    """
    assert disk_cache.save(*KEY, _candles(3)) is True
    before = _cache_file(_cache_dir).read_bytes()

    real_to_dict = disk_cache._candle_to_dict
    calls = {"n": 0}

    def _flaky(c):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom mid-write")
        return real_to_dict(c)

    monkeypatch.setattr(disk_cache, "_candle_to_dict", _flaky)
    assert disk_cache.save(*KEY, _candles(5, start_day=10)) is False

    # No torn file, no phantom new data: the old series is intact.
    assert _cache_file(_cache_dir).read_bytes() == before
    assert not list(_cache_dir.glob("*.tmp")), "temp file must be cleaned up"
    got = disk_cache.load(*KEY)
    assert [c.date.day for c in got] == [1, 2, 3]


def test_success_after_failure_recovers(_cache_dir, monkeypatch) -> None:
    """A failed write followed by a good one leaves the good data."""
    def _boom(*_a, **_k):
        raise OSError("disk full")

    with monkeypatch.context() as failure:
        failure.setattr("tradinglab.disk_cache.tempfile.mkstemp", _boom)
        assert disk_cache.save(*KEY, _candles(3)) is False
        assert disk_cache.load(*KEY) is None

    assert disk_cache.save(*KEY, _candles(4, start_day=10)) is True
    assert disk_cache._path_for(*KEY).parent == _cache_dir
    assert _cache_file(_cache_dir).exists()
    got = disk_cache.load(*KEY)
    assert [c.date.day for c in got] == [10, 11, 12, 13]


def test_failure_does_not_shadow_later_success_for_same_key(
    _cache_dir, monkeypatch
) -> None:
    """Write v1 OK → write v2 fails → write v3 OK: the read sees v3,
    never a mix of v1/v2."""
    assert disk_cache.save(*KEY, _candles(2)) is True

    def _boom(*_a, **_k):
        raise OSError("transient")

    with monkeypatch.context() as failure:
        failure.setattr("tradinglab.disk_cache.tempfile.mkstemp", _boom)
        assert disk_cache.save(*KEY, _candles(5, start_day=10)) is False
        assert [c.date.day for c in disk_cache.load(*KEY)] == [1, 2]

    assert disk_cache.save(*KEY, _candles(3, start_day=20)) is True
    assert disk_cache._path_for(*KEY).parent == _cache_dir
    assert _cache_file(_cache_dir).exists()
    assert [c.date.day for c in disk_cache.load(*KEY)] == [20, 21, 22]
