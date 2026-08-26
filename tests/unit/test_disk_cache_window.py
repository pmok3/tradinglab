"""``disk_cache.load_window`` — bounded reads for sandbox universe warms.

The sandbox market feed (``backtest/sandbox_feed.py``) registers hundreds
of symbols at session start. ``load`` would materialise every bar ever
fetched for each key (~4,700 records for a 60-day 5m file), so the warm
reads a window instead.

Two properties are load-bearing and each has a test here:

* the read is **bounded** to the requested days, and stops early because
  ``save`` always persists an ascending series;
* the read **never writes**. ``load`` heals NaN-OHLC poison by saving the
  cleaned series back; doing that from a windowed read would persist the
  window over the full file and destroy history outside it.

See ``disk_cache.spec.md``.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from tradinglab import disk_cache
from tradinglab.models import Candle


@pytest.fixture()
def _cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("tradinglab.disk_cache._cache_dir", lambda: tmp_path)
    return tmp_path


def _candles(days: list[str], *, hour: int = 14) -> list[Candle]:
    out = []
    for i, day in enumerate(days):
        d = _dt.datetime.fromisoformat(f"{day}T{hour:02d}:30:00+00:00")
        out.append(Candle(date=d, open=10.0 + i, high=11.0 + i, low=9.0 + i,
                          close=10.5 + i, volume=100 + i))
    return out


KEY = ("yfinance", "AMD", "5m")
DAYS = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]


# ---------------------------------------------------------------------------
# Windowing
# ---------------------------------------------------------------------------


def test_window_returns_only_requested_days(_cache_dir):
    disk_cache.save(*KEY, _candles(DAYS))
    got = disk_cache.load_window(
        *KEY, start_day="2026-06-02", end_day="2026-06-04")
    assert [c.date.date().isoformat() for c in got] == [
        "2026-06-02", "2026-06-03", "2026-06-04"]


def test_window_bounds_are_inclusive(_cache_dir):
    disk_cache.save(*KEY, _candles(DAYS))
    got = disk_cache.load_window(
        *KEY, start_day="2026-06-01", end_day="2026-06-01")
    assert len(got) == 1
    assert got[0].date.date().isoformat() == "2026-06-01"


def test_window_outside_range_returns_none(_cache_dir):
    disk_cache.save(*KEY, _candles(DAYS))
    assert disk_cache.load_window(
        *KEY, start_day="2027-01-01", end_day="2027-01-31") is None


def test_missing_file_returns_none(_cache_dir):
    assert disk_cache.load_window(
        "yfinance", "NOPE", "5m",
        start_day="2026-06-01", end_day="2026-06-30") is None


def test_window_matches_load_when_window_covers_everything(_cache_dir):
    disk_cache.save(*KEY, _candles(DAYS))
    full = disk_cache.load(*KEY)
    windowed = disk_cache.load_window(
        *KEY, start_day="2000-01-01", end_day="2099-12-31")
    assert [c.date for c in windowed] == [c.date for c in full]
    assert [c.close for c in windowed] == [c.close for c in full]


# ---------------------------------------------------------------------------
# Early break — the reason this exists
# ---------------------------------------------------------------------------


def test_stops_reading_after_the_window(_cache_dir, monkeypatch):
    """A window at the head of the file must not parse the whole tail."""
    disk_cache.save(*KEY, _candles(DAYS))
    calls = {"n": 0}
    real_from_dict = disk_cache._candle_from_dict

    def _counting(d):
        calls["n"] += 1
        return real_from_dict(d)

    monkeypatch.setattr(disk_cache, "_candle_from_dict", _counting)
    disk_cache.load_window(
        *KEY, start_day="2026-06-01", end_day="2026-06-02")
    # Only the two in-window records are parsed; the trailing three are
    # short-circuited by the ISO-prefix compare + early break.
    assert calls["n"] == 2


def test_fast_path_reads_the_format_save_writes(_cache_dir):
    """``save`` uses compact separators — the prefix scan must match it."""
    disk_cache.save(*KEY, _candles(DAYS[:1]))
    line = disk_cache._path_for(*KEY).read_text(
        encoding="utf-8").splitlines()[0]
    assert disk_cache._line_iso_day(line) == "2026-06-01"


def test_fast_path_accepts_spaced_separator():
    assert disk_cache._line_iso_day(
        '{"d": "2026-06-01T14:30:00+00:00","o":1.0}') == "2026-06-01"


def test_fast_path_rejects_unknown_layout():
    assert disk_cache._line_iso_day('{"open":1.0,"d":"2026-06-01"}') is None
    assert disk_cache._line_iso_day('') is None


def test_unknown_layout_still_windows_correctly(_cache_dir):
    """Fallback path: no fast prefix, so filter on the parsed date."""
    path = disk_cache._path_for(*KEY)
    path.parent.mkdir(parents=True, exist_ok=True)
    # "d" is not first → _line_iso_day returns None for every line.
    path.write_text("\n".join([
        '{"o":1.0,"h":2.0,"l":0.5,"c":1.5,"v":10,"s":"regular","d":"2026-06-01T14:30:00+00:00"}',
        '{"o":2.0,"h":3.0,"l":1.5,"c":2.5,"v":20,"s":"regular","d":"2026-06-03T14:30:00+00:00"}',
    ]) + "\n", encoding="utf-8")
    got = disk_cache.load_window(
        *KEY, start_day="2026-06-03", end_day="2026-06-03")
    assert len(got) == 1
    assert got[0].close == 2.5


# ---------------------------------------------------------------------------
# Never writes — a windowed read must not truncate the series
# ---------------------------------------------------------------------------


def test_poison_bar_is_dropped_but_file_is_not_rewritten(_cache_dir):
    path = disk_cache._path_for(*KEY)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '{"d":"2026-06-01T14:30:00+00:00","o":10.0,"h":11.0,"l":9.0,"c":10.5,"v":100,"s":"regular"}',
        '{"d":"2026-06-02T14:30:00+00:00","o":null,"h":null,"l":null,"c":null,"v":200,"s":"regular"}',
        '{"d":"2026-06-03T14:30:00+00:00","o":12.0,"h":13.0,"l":11.0,"c":12.5,"v":300,"s":"regular"}',
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    got = disk_cache.load_window(
        *KEY, start_day="2026-06-01", end_day="2026-06-02")
    assert [c.close for c in got] == [10.5]          # poison dropped
    assert path.read_text(encoding="utf-8") == before  # file untouched


def test_narrow_window_does_not_truncate_the_file(_cache_dir):
    """The regression this guard exists for: window must not become the file."""
    disk_cache.save(*KEY, _candles(DAYS))
    disk_cache.load_window(*KEY, start_day="2026-06-03", end_day="2026-06-03")
    assert len(disk_cache.load(*KEY)) == len(DAYS)


def test_never_raises_on_corrupt_file(_cache_dir):
    path = disk_cache._path_for(*KEY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json\n{{{\n", encoding="utf-8")
    assert disk_cache.load_window(
        *KEY, start_day="2026-06-01", end_day="2026-06-30") is None


# ---------------------------------------------------------------------------
# Shared guards with load()
# ---------------------------------------------------------------------------


def test_ratio_ticker_returns_none(_cache_dir):
    assert disk_cache.load_window(
        "yfinance", "AMD/NVDA", "5m",
        start_day="2026-06-01", end_day="2026-06-30") is None


def test_no_persist_source_returns_none(_cache_dir):
    disk_cache.save(*KEY, _candles(DAYS))
    disk_cache.mark_no_persist("yfinance")
    try:
        assert disk_cache.load_window(
            *KEY, start_day="2026-06-01", end_day="2026-06-30") is None
    finally:
        disk_cache.clear_no_persist()
