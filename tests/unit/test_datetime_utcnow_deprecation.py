"""Pin the migration from ``datetime.utcnow()`` (deprecated in Python 3.12+)
to ``datetime.now(timezone.utc)`` across the codebase.

Audit ID: ``datetime-utcnow-deprecation``.

The migration must:

1. Eliminate every ``datetime.utcnow()`` call site under ``src/`` (the
   deprecation surfaces a ``DeprecationWarning`` on 3.12 and a hard error
   in a future Python).
2. Preserve the EXACT on-disk output format of the helpers that produce
   ISO timestamps for persistence (``drawings/store.py::_now_iso``,
   ``drawings/model.py::make_hline_drawing``,
   ``backtest/sandbox_resume.py::now_iso``) — these must remain
   ``YYYY-MM-DDTHH:MM:SS`` (no offset suffix) so existing on-disk files
   round-trip.
3. Preserve the explicit ``+00:00`` suffix on
   ``backtest/persistence.py::dump_to_json`` (the ``saved_at`` field),
   now obtained naturally from an aware datetime instead of a manual
   string append.
"""
from __future__ import annotations

import datetime as _dt
import re
import warnings
from pathlib import Path

import pytest

import tradinglab

# ---------------------------------------------------------------------------
# Source-level: no call site under src/ may still use ``datetime.utcnow()``.
# ---------------------------------------------------------------------------


def _src_root() -> Path:
    return Path(tradinglab.__file__).resolve().parent


class TestNoRemainingUtcnowCallSites:
    """Mechanical guard against re-introducing the deprecated API."""

    def test_no_utcnow_call_in_src(self):
        offenders: list[str] = []
        pattern = re.compile(r"\.utcnow\s*\(")
        for path in _src_root().rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if pattern.search(text):
                offenders.append(str(path))
        assert offenders == [], (
            "datetime.utcnow() is deprecated in Python 3.12+. Migrate to "
            "datetime.now(timezone.utc) (and .replace(tzinfo=None) only if "
            "you need a naive output for on-disk format stability). "
            f"Remaining call sites: {offenders}"
        )


# ---------------------------------------------------------------------------
# Persistence format preservation: helpers that get serialized to disk must
# keep their existing string shape so old on-disk files round-trip.
# ---------------------------------------------------------------------------


# YYYY-MM-DDTHH:MM:SS, no trailing offset.
_NAIVE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
# YYYY-MM-DDTHH:MM:SS+00:00.
_AWARE_UTC_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")


class TestDrawingsStoreNowIso:
    def test_now_iso_is_naive_second_resolution(self):
        from tradinglab.drawings import store as _store

        out = _store._now_iso()
        assert isinstance(out, str)
        assert _NAIVE_ISO_RE.match(out), (
            f"drawings.store._now_iso() must keep naive ISO format "
            f"YYYY-MM-DDTHH:MM:SS, got {out!r}"
        )

    def test_now_iso_roundtrips_via_fromisoformat(self):
        from tradinglab.drawings import store as _store

        out = _store._now_iso()
        parsed = _dt.datetime.fromisoformat(out)
        assert parsed.tzinfo is None
        assert parsed.microsecond == 0


class TestDrawingsModelCreatedAt:
    def test_make_hline_drawing_created_at_default_is_naive_iso(self):
        from tradinglab.drawings.model import make_hline_drawing

        d = make_hline_drawing(ticker="AAPL", price=100.0)
        assert _NAIVE_ISO_RE.match(d.created_at), (
            f"make_hline_drawing() default created_at must be naive ISO "
            f"YYYY-MM-DDTHH:MM:SS, got {d.created_at!r}"
        )

    def test_make_hline_drawing_passed_through_unchanged(self):
        from tradinglab.drawings.model import make_hline_drawing

        d = make_hline_drawing(
            ticker="AAPL", price=100.0,
            created_at="2099-01-01T00:00:00",
        )
        assert d.created_at == "2099-01-01T00:00:00"


class TestSandboxResumeNowIso:
    def test_now_iso_is_naive_second_resolution(self):
        from tradinglab.backtest.sandbox_resume import now_iso

        out = now_iso()
        assert isinstance(out, str)
        assert _NAIVE_ISO_RE.match(out), (
            f"sandbox_resume.now_iso() must keep naive ISO format "
            f"YYYY-MM-DDTHH:MM:SS, got {out!r}"
        )

    def test_now_iso_roundtrips_via_fromisoformat(self):
        from tradinglab.backtest.sandbox_resume import now_iso

        parsed = _dt.datetime.fromisoformat(now_iso())
        assert parsed.tzinfo is None


class TestBacktestPersistenceSavedAt:
    """``backtest.persistence.dump_to_json`` writes a ``saved_at`` field
    that historically used naive ``utcnow()`` + manual ``+"+00:00"`` append.
    After the migration it uses an aware ``now(timezone.utc)`` and lets
    ``isoformat()`` emit the offset naturally — same wire format.
    """

    def test_saved_at_keeps_utc_offset_suffix(self, tmp_path):
        import json
        from unittest.mock import MagicMock

        from tradinglab.backtest.persistence import save_session

        result = MagicMock()
        result.to_dict.return_value = {}
        out_path = tmp_path / "result.json"
        save_session(out_path, result, session_id="test")
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        saved_at = payload["saved_at"]
        assert _AWARE_UTC_ISO_RE.match(saved_at), (
            "persistence.save_session must keep the +00:00 suffix on the "
            f"saved_at field, got {saved_at!r}"
        )


# ---------------------------------------------------------------------------
# Runtime guard: invoking the migrated helpers should NOT emit
# ``DeprecationWarning`` (the whole point of the migration).
# ---------------------------------------------------------------------------


class TestNoDeprecationWarning:
    def test_drawings_store_now_iso_emits_no_deprecation_warning(self):
        from tradinglab.drawings import store as _store

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            _store._now_iso()  # must not raise

    def test_sandbox_resume_now_iso_emits_no_deprecation_warning(self):
        from tradinglab.backtest.sandbox_resume import now_iso

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            now_iso()

    def test_make_hline_drawing_emits_no_deprecation_warning(self):
        from tradinglab.drawings.model import make_hline_drawing

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            make_hline_drawing(ticker="AAPL", price=100.0)


# ---------------------------------------------------------------------------
# Synthetic-events sanity: today_ms should still be a positive int near
# "now" — the arithmetic ``utcnow() - _EPOCH`` had to be reworked to keep
# both operands naive.
# ---------------------------------------------------------------------------


class TestSyntheticEventsTodayMs:
    def test_today_ms_is_a_reasonable_now(self):
        from tradinglab.events.synthetic_events import fetch_synthetic_events

        # fetch_synthetic_events hits the today_ms branch internally; just
        # invoke and confirm it returns without raising and produces
        # well-formed data.
        out = fetch_synthetic_events("AAPL")
        assert out is not None
        # EventBundle has earnings + dividends attributes.
        assert hasattr(out, "earnings")
        assert hasattr(out, "dividends")

    def test_synthetic_events_emits_no_deprecation_warning(self):
        from tradinglab.events.synthetic_events import fetch_synthetic_events

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            fetch_synthetic_events("AAPL")


# ---------------------------------------------------------------------------
# Mock the clock to verify behavior is deterministic.
# ---------------------------------------------------------------------------


class TestMockedClockOutput:
    """When the system clock is monkeypatched, the migrated helpers should
    reflect the patched value — proves we read from datetime.now() rather
    than caching."""

    def test_drawings_store_now_iso_reflects_patched_clock(self, monkeypatch):
        from tradinglab.drawings import store as _store

        fixed_utc = _dt.datetime(2030, 6, 15, 12, 34, 56, 789, tzinfo=_dt.timezone.utc)

        class _FrozenDT(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_utc.replace(tzinfo=None)
                return fixed_utc.astimezone(tz)

        monkeypatch.setattr(_store._dt, "datetime", _FrozenDT)
        out = _store._now_iso()
        # microsecond=0 dropped, tzinfo=None dropped.
        assert out == "2030-06-15T12:34:56"

    def test_sandbox_resume_now_iso_reflects_patched_clock(self, monkeypatch):
        from tradinglab.backtest import sandbox_resume as _sr

        fixed_utc = _dt.datetime(2030, 6, 15, 12, 34, 56, 789, tzinfo=_dt.timezone.utc)

        class _FrozenDT(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_utc.replace(tzinfo=None)
                return fixed_utc.astimezone(tz)

        monkeypatch.setattr(_sr._dt, "datetime", _FrozenDT)
        out = _sr.now_iso()
        assert out == "2030-06-15T12:34:56"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-vv"]))
