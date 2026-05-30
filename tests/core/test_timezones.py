"""Unit tests for :mod:`tradinglab.core.timezones`."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tradinglab.core import timezones
from tradinglab.core.lru_dict import LRUDict


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

    def test_get_zoneinfo_returns_named_zone_and_caches_identity(self):
        london_a = timezones.get_zoneinfo("Europe/London")
        london_b = timezones.get_zoneinfo("Europe/London")
        assert london_a is not None
        assert london_a is london_b
        assert "London" in str(london_a)

    def test_get_zoneinfo_routes_et_through_cached_et_constant(self):
        assert timezones.get_zoneinfo("America/New_York") is timezones.get_et()

    def test_get_zoneinfo_returns_none_for_blank_or_bad_name(self):
        assert timezones.get_zoneinfo("") is None
        assert timezones.get_zoneinfo("Bogus/Not_A_Zone") is None

    def test_get_zoneinfo_cache_is_bounded(self, monkeypatch):
        class _FakeZone:
            def __init__(self, name: str):
                self.name = name

        monkeypatch.setattr(timezones, "ZoneInfo", _FakeZone)
        monkeypatch.setattr(timezones, "_ZONE_CACHE", LRUDict(maxsize=3))

        for name in ("Test/A", "Test/B", "Test/C", "Test/D"):
            assert timezones.get_zoneinfo(name) is not None

        assert len(timezones._ZONE_CACHE) == 3
        assert "Test/A" not in timezones._ZONE_CACHE
        assert "Test/D" in timezones._ZONE_CACHE


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

    def test_get_zoneinfo_returns_none_when_zoneinfo_raises(self, monkeypatch):
        monkeypatch.setattr(timezones, "_ZONE_CACHE", LRUDict(maxsize=3))

        class _Bad:
            def __init__(self, *_a, **_kw):
                raise Exception("simulated missing tzdata")

        monkeypatch.setattr(timezones, "ZoneInfo", _Bad)
        assert timezones.get_zoneinfo("Europe/London") is None


class TestAdoptionInvariant:
    def test_core_timezones_is_the_only_production_zoneinfo_importer(self):
        src_root = Path(__file__).resolve().parents[2] / "src" / "tradinglab"
        allowed = {src_root / "core" / "timezones.py"}
        offenders: list[str] = []

        for path in sorted(src_root.rglob("*.py")):
            if path in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            rel = path.relative_to(src_root)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "zoneinfo":
                    names = ", ".join(alias.name for alias in node.names)
                    offenders.append(f"{rel}: imports {names} from zoneinfo")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "zoneinfo":
                            offenders.append(f"{rel}: imports zoneinfo")

        assert offenders == []

    def test_no_back_compat_et_zoneinfo_wrappers_remain(self):
        """No production module may define a thin ``_et_zoneinfo`` shim.

        These shims existed during the migration to centralise the
        ET resolution; once every caller switched to importing ``ET``
        or ``get_et`` from :mod:`tradinglab.core.timezones`, the shims
        became pure dead-code drift surface (a future agent could
        edit the shim, missing the central helper).

        Regression for CLAUDE.md §7.23 — the cleanup sprint that
        retired the wrappers in ``data/today_upsample.py``,
        ``strategy_tester/evaluator.py``, ``strategy_tester/screenshot.py``,
        and ``gui/volume_tod_overlay.py``.
        """
        src_root = Path(__file__).resolve().parents[2] / "src" / "tradinglab"
        offenders: list[str] = []
        for path in sorted(src_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            rel = path.relative_to(src_root)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "_et_zoneinfo":
                    offenders.append(str(rel))
        assert offenders == [], (
            f"_et_zoneinfo shims must be retired in favour of "
            f"core.timezones.ET / get_et; still defined in: {offenders}"
        )


@pytest.fixture(autouse=True)
def reset_timezone_cache():
    """Ensure each test starts with the cache resolved to the real ET."""
    yield
    # Restore real ET after any monkeypatch
    timezones._ET_CACHE = None
    timezones._ET_RESOLVED = False
    timezones._ZONE_CACHE = LRUDict(maxsize=timezones._ZONE_CACHE_MAX_SIZE)
    timezones.ET = timezones.get_et()
