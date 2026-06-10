"""``disk_cache.load`` drops poison (non-finite-OHLC) bars on read.

Regression for "today's 1d bar is all NaN": Yahoo occasionally emits a
corrupt daily row with ``null`` OHLC but a real volume for a day that
clearly traded. The fetch normalizer drops it from fresh data, but once
it is on disk (written by an older build, or perpetuated by merges),
fresh fetches never carry that date again to overwrite it and
``merge_candles`` would retain the non-overlapping NaN bar forever — it
then renders as an invisible NaN candle behind a visible volume bar.

``disk_cache.load`` now filters non-finite-OHLC bars on read so the
poison can never reach ``_full_cache`` or the chart. See
``disk_cache.spec.md``.
"""

from __future__ import annotations

import math

import pytest

from tradinglab import disk_cache


@pytest.fixture()
def _cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("tradinglab.disk_cache._cache_dir", lambda: tmp_path)
    return tmp_path


def _write_lines(path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_load_drops_null_ohlc_bar(_cache_dir):
    key = ("yfinance", "AMD", "1d")
    path = disk_cache._path_for(*key)
    # Mirror the exact on-disk shape observed in the real bug: a trailing
    # row with null OHLC and a real volume.
    _write_lines(path, [
        '{"d":"2026-06-08T00:00:00-04:00","o":485.0,"h":494.97,"l":477.7,"c":490.33,"v":25158800,"s":"regular"}',
        '{"d":"2026-06-09T00:00:00-04:00","o":null,"h":null,"l":null,"c":null,"v":37157055,"s":"regular"}',
    ])

    loaded = disk_cache.load(*key)

    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0].close == 490.33
    assert all(
        math.isfinite(c.open) and math.isfinite(c.high)
        and math.isfinite(c.low) and math.isfinite(c.close)
        for c in loaded
    )


def test_load_all_poison_returns_none(_cache_dir):
    key = ("yfinance", "AMD", "1d")
    path = disk_cache._path_for(*key)
    _write_lines(path, [
        '{"d":"2026-06-09T00:00:00-04:00","o":null,"h":null,"l":null,"c":null,"v":1,"s":"regular"}',
    ])

    # Every bar was poison → behaves like an empty/missing cache so the
    # caller re-fetches rather than rendering nothing-but-NaN.
    assert disk_cache.load(*key) is None


def test_save_then_load_heals_existing_poison(_cache_dir):
    """A finite save followed by a hand-poisoned append still loads clean."""
    from datetime import datetime

    from tradinglab.models import Candle

    key = ("yfinance", "TESTHEAL", "1d")
    good = Candle(
        date=datetime(2026, 6, 8), open=10.0, high=11.0, low=9.0,
        close=10.5, volume=100, session="regular",
    )
    disk_cache.save(*key, [good])
    # Simulate a poison bar that slipped onto disk via an older path.
    path = disk_cache._path_for(*key)
    with path.open("a", encoding="utf-8") as f:
        f.write(
            '{"d":"2026-06-09T00:00:00-04:00","o":null,"h":null,'
            '"l":null,"c":null,"v":50,"s":"regular"}\n'
        )

    loaded = disk_cache.load(*key)

    assert loaded is not None
    assert [c.close for c in loaded] == [10.5]


def test_load_persists_cleaned_file_when_poison_dropped(_cache_dir):
    """Heal-on-load PERSISTS: the poison line is erased from disk on read."""
    key = ("yfinance", "HEALPERSIST", "1d")
    path = disk_cache._path_for(*key)
    _write_lines(path, [
        '{"d":"2026-06-08T00:00:00-04:00","o":485.0,"h":494.97,"l":477.7,"c":490.33,"v":25158800,"s":"regular"}',
        '{"d":"2026-06-09T00:00:00-04:00","o":null,"h":null,"l":null,"c":null,"v":37157055,"s":"regular"}',
    ])
    # Raw file initially carries the poison row.
    assert "null" in path.read_text(encoding="utf-8")

    loaded = disk_cache.load(*key)
    assert loaded is not None and len(loaded) == 1

    # After the read, the poison line is gone FROM DISK, not just filtered.
    healed = path.read_text(encoding="utf-8")
    assert "null" not in healed
    assert len([ln for ln in healed.splitlines() if ln.strip()]) == 1
    # A second load is a clean no-op and returns the same single bar.
    again = disk_cache.load(*key)
    assert again is not None and [c.close for c in again] == [490.33]


def test_load_does_not_rewrite_clean_file(_cache_dir, monkeypatch):
    """A finite-only file is NOT rewritten on load (no spurious save)."""
    saves: list[tuple] = []
    real_save = disk_cache.save

    def _counting_save(*args, **kwargs):
        saves.append(args)
        return real_save(*args, **kwargs)

    monkeypatch.setattr(disk_cache, "save", _counting_save)

    clean = ("yfinance", "CLEANFILE", "1d")
    _write_lines(disk_cache._path_for(*clean), [
        '{"d":"2026-06-08T00:00:00-04:00","o":1.0,"h":2.0,"l":0.5,"c":1.5,"v":10,"s":"regular"}',
    ])
    disk_cache.load(*clean)
    assert saves == []  # no heal-rewrite for a clean file

    poison = ("yfinance", "POISONFILE", "1d")
    _write_lines(disk_cache._path_for(*poison), [
        '{"d":"2026-06-08T00:00:00-04:00","o":1.0,"h":2.0,"l":0.5,"c":1.5,"v":10,"s":"regular"}',
        '{"d":"2026-06-09T00:00:00-04:00","o":null,"h":null,"l":null,"c":null,"v":5,"s":"regular"}',
    ])
    disk_cache.load(*poison)
    assert len(saves) == 1  # exactly one heal-rewrite for the poison file
