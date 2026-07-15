"""Unit tests for :mod:`tradinglab.core.session_calendar`.

``session_calendar`` is the single source of truth for US-equity
trading-session boundaries (pre / regular / post) and the two RTH
predicates that were historically re-hardcoded in ~7 modules:

* the ``09:30``/``16:00`` open/close boundaries,
* :func:`is_regular_session` (closed interval, trading-engine RTH
  membership — was ``strategy_tester.evaluator._is_regular_session``),
* :func:`is_rth_now` (half-open wall-clock RTH — was
  ``updates._is_rth_now`` + ``watchlist_tab._watchlist_poll_in_rth_now``),
* :func:`market_window` (was ``gui.polling._market_window_et``),
* :func:`classify_session` / :func:`classify_session_arr` (moved here
  from ``constants``; still re-exported there for back-compat).

These tests pin the *exact* current semantics of each so the
consolidation is behaviour-preserving — in particular the intentional
difference that :func:`classify_session` buckets exactly ``16:00`` as
``"post"`` (half-open) while :func:`is_regular_session` counts the
``16:00`` bar as regular (closed).
"""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

import numpy as np
import pytest

from tradinglab.core import session_calendar as sc

# 2025-05-12 is a Monday; 05-13 Tuesday; 05-17 Saturday; 05-18 Sunday.
_MON = (2025, 5, 12)
_SAT = (2025, 5, 17)
_SUN = (2025, 5, 18)


# ---------------------------------------------------------------------------
# 1. Boundary constants — single source of truth
# ---------------------------------------------------------------------------
class TestBoundaryConstants:
    def test_minute_of_day_boundaries(self):
        assert sc.PRE_OPEN_MIN == 4 * 60          # 240
        assert sc.RTH_OPEN_MIN == 9 * 60 + 30     # 570
        assert sc.RTH_CLOSE_MIN == 16 * 60        # 960
        assert sc.POST_CLOSE_MIN == 20 * 60       # 1200
        assert sc.RTH_SPAN_MIN == 390

    def test_second_of_day_boundaries(self):
        # Consumed by the vectorized evaluator kernel (_compute_et_arrays).
        assert sc.RTH_OPEN_SEC == 9 * 3600 + 30 * 60   # 34200
        assert sc.RTH_CLOSE_SEC == 16 * 3600           # 57600

    def test_time_forms(self):
        assert sc.PRE_OPEN_TIME == time(4, 0)
        assert sc.RTH_OPEN_TIME == time(9, 30)
        assert sc.RTH_CLOSE_TIME == time(16, 0)
        assert sc.POST_CLOSE_TIME == time(20, 0)

    def test_derived_constants_are_internally_consistent(self):
        assert sc.RTH_OPEN_SEC == sc.RTH_OPEN_MIN * 60
        assert sc.RTH_CLOSE_SEC == sc.RTH_CLOSE_MIN * 60
        assert sc.RTH_SPAN_MIN == sc.RTH_CLOSE_MIN - sc.RTH_OPEN_MIN
        assert sc.RTH_OPEN_TIME == time(sc.RTH_OPEN_MIN // 60, sc.RTH_OPEN_MIN % 60)
        assert sc.RTH_CLOSE_TIME == time(sc.RTH_CLOSE_MIN // 60, sc.RTH_CLOSE_MIN % 60)


# ---------------------------------------------------------------------------
# 2. classify_session (half-open) — pre / regular / post
# ---------------------------------------------------------------------------
class TestClassifySession:
    @pytest.mark.parametrize(
        "hour,minute,expected",
        [
            # Regular session is [09:30, 16:00).
            (9, 30, "regular"),
            (9, 29, "pre"),
            (10, 0, "regular"),
            (15, 59, "regular"),
            # Post session is [16:00, 20:00).
            (16, 0, "post"),
            (16, 30, "post"),
            (19, 59, "post"),
            # 20:00 onward and pre-market (< 09:30) collapse to "pre".
            (20, 0, "pre"),
            (4, 0, "pre"),
            (3, 30, "pre"),
            (0, 0, "pre"),
        ],
    )
    def test_boundaries(self, hour, minute, expected):
        assert sc.classify_session(hour, minute) == expected


class TestClassifySessionArr:
    def test_bit_for_bit_identical_to_scalar_across_full_day(self):
        """The lockstep invariant: the vectorized twin must match the scalar
        classifier for every (hour, minute) of a full day."""
        hours = np.repeat(np.arange(24), 60)
        minutes = np.tile(np.arange(60), 24)
        got = sc.classify_session_arr(hours, minutes)
        expected = [
            sc.classify_session(int(h), int(m))
            for h, m in zip(hours, minutes, strict=True)
        ]
        assert got == expected

    def test_returns_plain_python_str(self):
        got = sc.classify_session_arr(np.array([10]), np.array([0]))
        assert type(got[0]) is str  # not numpy.str_ (would break json.dumps)

    def test_labels_are_shared_objects(self):
        hours = np.repeat(np.arange(24), 60)
        minutes = np.tile(np.arange(60), 24)
        got = sc.classify_session_arr(hours, minutes)
        assert len({id(s) for s in got}) <= 3

    def test_empty_input(self):
        assert sc.classify_session_arr(
            np.array([], dtype=int), np.array([], dtype=int)
        ) == []


# ---------------------------------------------------------------------------
# 3. is_regular_session (closed interval) — trading-engine RTH membership
# ---------------------------------------------------------------------------
class TestIsRegularSession:
    @pytest.mark.parametrize(
        "hm,expected",
        [
            ((9, 29), False),
            ((9, 30), True),    # inclusive open
            ((12, 0), True),
            ((15, 59), True),
            ((16, 0), True),    # inclusive CLOSE (differs from classify_session)
            ((16, 1), False),
            ((4, 0), False),
            ((19, 55), False),
        ],
    )
    def test_weekday_boundaries(self, hm, expected):
        dt = datetime(*_MON, hm[0], hm[1])
        assert sc.is_regular_session(dt) is expected

    def test_saturday_is_never_regular(self):
        assert sc.is_regular_session(datetime(*_SAT, 12, 0)) is False

    def test_sunday_is_never_regular(self):
        assert sc.is_regular_session(datetime(*_SUN, 12, 0)) is False

    def test_works_with_tz_aware_et_datetime(self):
        et = sc_et()
        if et is None:  # pragma: no cover - tzdata missing
            pytest.skip("tzdata unavailable")
        dt = datetime(*_MON, 9, 30, tzinfo=et)
        assert sc.is_regular_session(dt) is True


# ---------------------------------------------------------------------------
# 4. is_rth_now (half-open wall-clock) — polling / watchlist / updates gate
# ---------------------------------------------------------------------------
class TestIsRthNow:
    @pytest.mark.parametrize(
        "hm,expected",
        [
            ((9, 29), False),
            ((9, 30), True),    # inclusive open
            ((13, 0), True),
            ((15, 59), True),
            ((16, 0), False),   # EXCLUSIVE close (half-open)
            ((6, 0), False),
            ((19, 0), False),
        ],
    )
    def test_injected_now_weekday(self, hm, expected):
        now = datetime(*_MON, hm[0], hm[1])
        assert sc.is_rth_now(now=now) is expected

    def test_injected_now_weekend_false(self):
        assert sc.is_rth_now(now=datetime(*_SAT, 13, 0)) is False
        assert sc.is_rth_now(now=datetime(*_SUN, 13, 0)) is False

    def test_missing_tzdata_returns_true_conservatively(self, monkeypatch):
        """now=None + ET unavailable ⇒ conservative True (poll at live
        cadence rather than silently downgrade to off-hours)."""
        from tradinglab.core import timezones as _tz
        monkeypatch.setattr(_tz, "ET", None)
        assert sc.is_rth_now() is True

    def test_default_now_reads_patched_stdlib_clock(self, monkeypatch):
        """now=None must resolve the clock at CALL time so the existing
        ``updates``/``watchlist`` tests that patch ``datetime.datetime``
        keep working after those methods delegate here.
        """
        from tradinglab.core import timezones as _tz
        et = _tz.get_et()
        if et is None:  # pragma: no cover - tzdata missing
            pytest.skip("tzdata unavailable")

        real = datetime

        class _FakeDT(real):
            @classmethod
            def now(cls, tz=None):
                fixed = real(*_MON, 13, 0, tzinfo=et)
                return fixed if tz is None else fixed.astimezone(tz)

        monkeypatch.setattr("datetime.datetime", _FakeDT)
        assert sc.is_rth_now() is True

    def test_default_now_returns_bool_without_error(self):
        # Real wall clock — can't assert the value, but it must not raise.
        assert isinstance(sc.is_rth_now(), bool)


# ---------------------------------------------------------------------------
# 5. market_window — polling scheduler open/close pair
# ---------------------------------------------------------------------------
class TestMarketWindow:
    def test_regular_hours(self):
        assert sc.market_window(include_extended=False) == (time(9, 30), time(16, 0))

    def test_extended_hours(self):
        assert sc.market_window(include_extended=True) == (time(4, 0), time(20, 0))


# ---------------------------------------------------------------------------
# 6. Intentional half-open vs closed difference at exactly 16:00
# ---------------------------------------------------------------------------
class TestClosedVsHalfOpenAtClose:
    def test_agree_everywhere_inside_rth_except_the_close_minute(self):
        """classify_session (half-open) and is_regular_session (closed) must
        agree for every RTH minute EXCEPT exactly 16:00, where the closed
        predicate says regular and the half-open classifier says post.
        """
        disagreements = []
        for minutes in range(sc.RTH_OPEN_MIN, sc.RTH_CLOSE_MIN + 1):
            h, m = divmod(minutes, 60)
            classified_regular = sc.classify_session(h, m) == "regular"
            predicate_regular = sc.is_regular_session(datetime(*_MON, h, m))
            if classified_regular != predicate_regular:
                disagreements.append(minutes)
        assert disagreements == [sc.RTH_CLOSE_MIN]  # only 16:00

    def test_close_minute_specifics(self):
        assert sc.classify_session(16, 0) == "post"
        assert sc.is_regular_session(datetime(*_MON, 16, 0)) is True


# ---------------------------------------------------------------------------
# 7. Adoption invariant — RTH boundary literals live only here
# ---------------------------------------------------------------------------
class TestAdoptionInvariant:
    """Mirrors the ``core.timezones`` adoption invariant (test_timezones.py):
    the RTH open/close boundaries must be defined in exactly ONE production
    module so a future edit can't reintroduce the drift Finding #1 retired.
    """

    _RTH_LITERALS = ("9*60+30", "9*3600+30*60")

    def test_rth_open_literals_only_in_session_calendar(self):
        src_root = Path(__file__).resolve().parents[2] / "src" / "tradinglab"
        allowed = src_root / "core" / "session_calendar.py"
        offenders: list[str] = []

        for path in sorted(src_root.rglob("*.py")):
            if path == allowed:
                continue
            rel = path.relative_to(src_root)
            for lineno, raw in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                collapsed = "".join(raw.split())
                if any(lit in collapsed for lit in self._RTH_LITERALS):
                    offenders.append(f"{rel}:{lineno}: {raw.strip()}")

        assert offenders == [], (
            "RTH-open boundary literals must live only in "
            "core/session_calendar.py; found:\n" + "\n".join(offenders)
        )


def sc_et():
    from tradinglab.core.timezones import get_et
    return get_et()
