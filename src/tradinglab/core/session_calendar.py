"""Single source of truth for US-equity trading-session boundaries.

The repository historically re-hardcoded the "regular trading hours"
predicate (Mon-Fri, 09:30-16:00 ET) and the four session boundaries
(04:00 / 09:30 / 16:00 / 20:00 ET) at ~7 independent sites, each with
its own copy of the boundary numbers and a slightly different comparison
policy. ``constants.classify_session`` even carried a docstring warning
its vectorized twin to "keep the two functions in lockstep". The
"include extended hours" toggle travelled under four different names
(``prepost`` / ``include_extended`` / ``include_extended_hours`` /
``include_ext``) for the same boolean.

This module gives every caller one place to import from — the direct
analogue of :mod:`tradinglab.core.timezones` (which consolidated the
scattered Eastern-time ``ZoneInfo`` construction) and
:mod:`tradinglab.core.view_intent` (which consolidated the scattered
render-preservation booleans).

Two DELIBERATELY distinct RTH predicates live here because the codebase
genuinely needs both — sharing the boundary numbers, differing only at
the closing minute:

* :func:`classify_session` / :func:`classify_session_arr` — bucket a
  bar into ``"pre"`` / ``"regular"`` / ``"post"`` using **half-open**
  intervals, so a bar timestamped exactly ``16:00`` is the first
  ``"post"`` bar. This is the data-layer bar-tagging convention (flows
  into ``Candle.session``).
* :func:`is_regular_session` — the trading-engine RTH-membership
  predicate, a **closed** interval ``[09:30, 16:00]`` so the ``16:00``
  bar still counts as regular (matches the strategy-tester evaluator's
  historical ``_is_regular_session`` bit-for-bit).
* :func:`is_rth_now` — a **half-open** wall-clock check ``[09:30, 16:00)``
  used by the pollers/schedulers; a clock at exactly ``16:00:00`` is
  "closed".

Adoption sites: ``constants`` (re-exports the classifiers),
``strategy_tester.evaluator`` (predicate + second-of-day boundaries),
``gui.polling`` (:func:`market_window`), ``updates`` +
``gui.watchlist_tab`` (:func:`is_rth_now`), ``gui.volume_tod_overlay``
(minute-of-day boundaries).
"""

from __future__ import annotations

from datetime import datetime, time

# ---------------------------------------------------------------------------
# Boundary constants (US equities, Eastern time). These are the ONE
# definition of the session edges; every consumer imports from here.
#
#   pre-market   04:00 - 09:30
#   regular      09:30 - 16:00
#   post-market  16:00 - 20:00
# ---------------------------------------------------------------------------

#: Minute-of-day (ET) boundaries.
PRE_OPEN_MIN: int = 4 * 60          # 240
RTH_OPEN_MIN: int = 9 * 60 + 30     # 570
RTH_CLOSE_MIN: int = 16 * 60        # 960
POST_CLOSE_MIN: int = 20 * 60       # 1200
#: Length of the regular session in minutes (390).
RTH_SPAN_MIN: int = RTH_CLOSE_MIN - RTH_OPEN_MIN

#: Second-of-day (ET) boundaries — consumed by the vectorized evaluator
#: kernel (``strategy_tester.evaluator._compute_et_arrays``).
RTH_OPEN_SEC: int = RTH_OPEN_MIN * 60    # 34200
RTH_CLOSE_SEC: int = RTH_CLOSE_MIN * 60  # 57600

#: :class:`datetime.time` forms — consumed by the wall-clock schedulers
#: (``gui.polling``, ``updates``, ``gui.watchlist_tab``).
PRE_OPEN_TIME: time = time(4, 0)
RTH_OPEN_TIME: time = time(9, 30)
RTH_CLOSE_TIME: time = time(16, 0)
POST_CLOSE_TIME: time = time(20, 0)

_SESSION_LABELS = ("pre", "regular", "post")


# ---------------------------------------------------------------------------
# Bar-tagging classifier (half-open) — pre / regular / post
# ---------------------------------------------------------------------------
def classify_session(hour: int, minute: int) -> str:
    """Classify a wall-clock time (US Eastern) into ``pre``/``regular``/``post``.

    Half-open intervals: regular is ``[09:30, 16:00)`` and post is
    ``[16:00, 20:00)``; anything else (pre-market and overnight) is
    ``"pre"``. A bar timestamped exactly ``16:00`` is the first ``"post"``
    bar (contrast :func:`is_regular_session`, which is closed at 16:00).
    """
    minutes = hour * 60 + minute
    if RTH_OPEN_MIN <= minutes < RTH_CLOSE_MIN:
        return "regular"
    if RTH_CLOSE_MIN <= minutes < POST_CLOSE_MIN:
        return "post"
    return "pre"


def classify_session_arr(hours, minutes) -> list[str]:
    """Vectorized :func:`classify_session` over numpy hour + minute arrays.

    Returns a ``list[str]`` that is **bit-for-bit identical** to calling
    :func:`classify_session` element-by-element. Both functions read the
    same module-level boundary constants, so there is no longer a
    "keep the thresholds in lockstep" hazard — only the scalar-vs-vector
    dispatch differs.

    Used by the data normalizers so large intraday fetches (multi-year
    1m, intraday universe preloads) don't pay a per-bar Python call.
    numpy is imported lazily since this is called once per fetch, not
    per bar.
    """
    import numpy as np

    total = np.asarray(hours, dtype=np.int32) * 60 + np.asarray(minutes, dtype=np.int32)
    # Integer category codes (0=pre, 1=regular, 2=post) computed with two
    # vectorized masked assignments, then mapped back to THREE shared
    # string objects. Going via ``codes.tolist()`` (cached small-int refs)
    # + tuple indexing keeps every label a shared reference — a numpy
    # ``"<U7"`` array ``.tolist()`` would instead allocate one fresh
    # ``str`` per bar.
    codes = np.zeros(total.shape, dtype=np.int8)
    codes[(total >= RTH_OPEN_MIN) & (total < RTH_CLOSE_MIN)] = 1
    codes[(total >= RTH_CLOSE_MIN) & (total < POST_CLOSE_MIN)] = 2
    return [_SESSION_LABELS[c] for c in codes.tolist()]


# ---------------------------------------------------------------------------
# Trading-engine RTH membership (closed interval)
# ---------------------------------------------------------------------------
def is_regular_session(dt: datetime) -> bool:
    """True iff ``dt`` is Mon-Fri AND ``09:30 <= time <= 16:00`` ET.

    **Closed** at 16:00 (the 16:00 bar counts as regular) — this matches
    the strategy-tester evaluator's historical ``_is_regular_session``
    semantics used for ``require_market_open`` and the EOD-kill-switch
    RTH walk-back. Operates on whatever local time ``dt`` represents; the
    trading kernel passes ET-aware datetimes. Holidays are not enforced
    (the data layer owns that).
    """
    if dt.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    local_t = dt.time()
    return RTH_OPEN_TIME <= local_t <= RTH_CLOSE_TIME


# ---------------------------------------------------------------------------
# Wall-clock RTH-now (half-open) — pollers / schedulers
# ---------------------------------------------------------------------------
def is_rth_now(now: datetime | None = None) -> bool:
    """True iff the wall clock is inside US RTH (Mon-Fri, ``[09:30, 16:00)`` ET).

    **Half-open** at the close: a clock reading exactly ``16:00:00`` is
    "closed". When ``now`` is omitted the current ET wall-clock is used;
    if ``tzdata`` is missing on the host (rare) the function conservatively
    returns ``True`` so pollers keep live cadence rather than silently
    downgrading to off-hours intervals.

    The ``datetime`` and ``ET`` lookups are function-local **on purpose**:
    the ``updates`` / ``gui.watchlist_tab`` unit tests drive this gate by
    patching ``datetime.datetime`` and ``core.timezones.ET`` at call time,
    and delegating call sites rely on that resolution happening here.
    """
    from datetime import datetime as _datetime

    from .timezones import ET

    if now is None:
        if ET is None:
            return True
        now = _datetime.now(ET)
    if now.weekday() >= 5:  # Saturday / Sunday
        return False
    return RTH_OPEN_TIME <= now.time() < RTH_CLOSE_TIME


# ---------------------------------------------------------------------------
# Scheduler open/close window
# ---------------------------------------------------------------------------
def market_window(include_extended: bool) -> tuple[time, time]:
    """Return the ``(open, close)`` ET time pair for a regular weekday.

    Extended hours on NYSE/NASDAQ run 04:00-20:00 ET; regular hours
    09:30-16:00 ET. This replaces the four differently-named
    ``include_extended`` booleans with one policy function.
    """
    if include_extended:
        return PRE_OPEN_TIME, POST_CLOSE_TIME
    return RTH_OPEN_TIME, RTH_CLOSE_TIME
