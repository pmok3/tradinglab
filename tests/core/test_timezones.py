"""Unit tests for :mod:`tradinglab.core.timezones`."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradinglab.core import timezones


class TestET:
    def test_et_constant_is_zoneinfo_or_none(self):
        # In any reasonable test env tzdata is present.
        assert timezones.ET is not None
        # Has the canonical name.
        assert "New_York" in str(timezones.ET)

    def test_get_et_returns_cached_identity(self):
        a = timezones.get_et()
        b = timezones.get_et()
        assert a is b
        assert a is timezones.ET

    def test_now_et_is_tz_aware(self):
        now = timezones.now_et()
        assert isinstance(now, datetime)
        assert now.tzinfo is not None or timezones.ET is None

    def test_to_et_roundtrip_for_known_epoch(self):
        # 2024-06-03 13:30:00 UTC = 09:30 ET (summer DST).
        et_dt = timezones.to_et(1717421400)
        assert et_dt.tzinfo is not None
        # Should be 09:30 ET that Monday.
        assert et_dt.hour == 9
        assert et_dt.minute == 30
        assert et_dt.year == 2024
        assert et_dt.month == 6
        assert et_dt.day == 3

    def test_to_et_winter_offset_is_five_hours(self):
        # 2024-01-08 14:30:00 UTC = 09:30 ET (winter EST).
        et_dt = timezones.to_et(1704724200)
        assert et_dt.hour == 9
        assert et_dt.minute == 30
        # Winter offset -5h
        offset = et_dt.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == -5 * 3600


class TestMissingTzdataFallback:
    def test_get_et_returns_none_when_zoneinfo_raises(self, monkeypatch):
        # Force a "missing tzdata" simulation by resetting the cache
        # and monkey-patching ZoneInfo to raise.
        monkeypatch.setattr(timezones, "_ET_CACHE", None)
        monkeypatch.setattr(timezones, "_ET_RESOLVED", False)
        # Replace the module-level ZoneInfo with one that raises
        class _Bad:
            def __init__(self, *_a, **_kw):
                raise Exception("simulated missing tzdata")
        monkeypatch.setattr(timezones, "ZoneInfo", _Bad)
        # Now get_et should swallow the exception and return None
        assert timezones.get_et() is None

    def test_to_et_falls_back_to_utc_when_et_unavailable(self, monkeypatch):
        # Simulate missing tzdata for to_et()
        monkeypatch.setattr(timezones, "_ET_CACHE", None)
        monkeypatch.setattr(timezones, "_ET_RESOLVED", True)
        # Should return a UTC datetime
        dt = timezones.to_et(1717421400)
        assert dt.tzinfo is timezone.utc

    def test_now_et_returns_naive_when_et_unavailable(self, monkeypatch):
        monkeypatch.setattr(timezones, "_ET_CACHE", None)
        monkeypatch.setattr(timezones, "_ET_RESOLVED", True)
        dt = timezones.now_et()
        assert dt.tzinfo is None


@pytest.fixture(autouse=True)
def reset_timezone_cache():
    """Ensure each test starts with the cache resolved to the real ET."""
    yield
    # Restore real ET after any monkeypatch
    timezones._ET_CACHE = None
    timezones._ET_RESOLVED = False
    timezones.ET = timezones.get_et()
